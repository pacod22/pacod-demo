# The pacod logging convention

All pacod repos (`pacod-id`, `pacod-gw`, `pacod-ap`, `pacod-cms`, `pacod-kb`) log the same way, so
one reader (a terminal, `docker compose logs`, or a log collector such as Loki or Elastic) handles
every product without a parser per product. The full stack (`pacod-demo`) sets the level and
format of all of them with two values (section 7).

## 1. Where logs go

- **stdout, one event per line.** Every process writes its log to stdout; the container runtime
  keeps it. Nothing else is required. A program whose stdout is its output (a CLI that prints
  JSON, a mock whose callers read its port from stdout) logs to stderr instead.
- **Files are optional.** A product may also write files when its log directory is set (section
  5). A file never replaces stdout.
- **HTTP access logs come from the edge (Caddy)**, section 6. An application's own per-request
  lines follow this convention like any other event.

## 2. Docker: every service rotates its log

Every service in every compose file, pacod's own and third-party ones (databases, caches, Caddy,
exporters), uses the same logging block. YAML anchors do not cross files, so each compose file
declares its own copy:

```yaml
x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"

services:
  some-service:
    logging: *logging
```

A service defined with `extends` inherits the block of the service it extends.

## 3. One switch for the level, one for the format

Each product keeps the variable names its deployments already use; the **values** are the same
everywhere:

- **Level:** `debug`, `info`, `warn`, `error`. Case does not matter, and `warning` means `warn`.
  An unknown value means `info`. Rust services, whose variable is a filter directive, also take a
  directive (`info,sqlx=warn`); an unknown value there means the product's default filter, and a
  single word must be a level (`hyper=trace`, not `hyper`).
- **Format:** `json` or `text`. Case does not matter. An unknown value means the product's default.

| Product | Process | Level | Format | Default format |
|---|---|---|---|---|
| pacod-id | pacod-id | `PID_LOG` (default `info,sqlx=warn`) | `PID_LOG_FORMAT` | json |
| pacod-gw | pacod-gw, gw-mock, lite | `RUST_LOG` | `PGW_LOG_FORMAT` | json |
| pacod-gw | gw-load-agent | `RUST_LOG` | `LOAD_AGENT_LOG_FORMAT` (compose: from `PGW_LOG_FORMAT`) | json |
| pacod-gw | gw-analytics | `RUST_LOG` | `ANALYTICS_LOG_FORMAT` (compose: from `PGW_LOG_FORMAT`) | json |
| pacod-gw | gw-tester | `TESTER_LOG_LEVEL` | `TESTER_LOG_FORMAT` (compose: from `PGW_LOG_FORMAT`) | json |
| pacod-ap | ap-api, ap-admin-api, workers, ap-mcp, ap-ingestion*, pacod-embeddings | `APP_LOG_LEVEL` | `APP_LOG_FORMAT` | json (text while `APP_ENV=development` and the format is unset) |
| pacod-ap | ap-frontend | `LOG_LEVEL` | `LOG_FORMAT` | json (text while `NODE_ENV` is not `production` and the format is unset) |
| pacod-cms | cms-site, cms-studio | `APP_LOG_LEVEL` | `APP_LOG_FORMAT` | json |
| pacod-kb | kb-pipeline, kb-ui | `LOG_LEVEL` | `LOG_FORMAT` | json |

A repo's development compose files may set `text` for the developer's terminal; its production
files keep `json`. Every compose file passes the variables through with today's value as the
default (`PID_LOG_FORMAT: ${PID_LOG_FORMAT:-text}`), so a deployment that sets nothing is unchanged
and the full stack can set them.

## 4. The event

### JSON: one object per line

```json
{"timestamp":"2026-10-23T06:41:07.695Z","level":"info","service":"pacod-id","logger":"pacod_id::app","message":"pacod-id is up","public":"0.0.0.0:8070"}
```

Five keys are always present, in this order:

| Key | Content |
|---|---|
| `timestamp` | When the event happened: RFC 3339, UTC, `Z`, exactly three fraction digits (milliseconds). |
| `level` | `trace`, `debug`, `info`, `warn` or `error`, lower case (Python's `warning` is `warn`, `critical` is `error`; Java's `fatal` is `error`). |
| `service` | The compose service name of the process (NETWORK.md: the same as its container and DNS name), e.g. `ap-api`, `cms-studio`, `pacod-gw`. Read from `SERVICE_NAME`; each process has its own name as the default. |
| `logger` | Where the event comes from: the Rust target, the Python or Java logger name, the frontend's module. |
| `message` | The human-readable message. Never empty: an event with only fields uses its name as the message. |

Then:

- **The event's fields**, flat at the top level, `snake_case`, values as JSON types (numbers stay
  numbers). The fields of enclosing spans (Rust tracing) or bound context (structlog contextvars,
  Java MDC) are included the same way; the event's own field wins over a context field of the same
  name. A field may not take one of the five names above: a formatter that meets one anyway writes
  it with a trailing underscore (`message_`).
- **`request_id`** when the event belongs to a request whose id is known.
- **`error`** (the error's one-line description, e.g. `ValueError: bad input`) and
  **`stack_trace`** (the full trace, one string with newlines) when an exception is attached.

Nothing else is added by the formatter: no host name, process id, thread name, colour codes or
nested `fields` object.

### Request lines

An application that logs its own requests (uvicorn's access log, the frontend's request hook, an
admin request middleware) uses the message `<METHOD> <path> <status>` and these fields:

| Field | Content |
|---|---|
| `method` | `GET`, `POST`, ... |
| `path` | The path **without the query string** (it can carry tokens and codes). |
| `status` | The response status, a number. |
| `duration_ms` | The time to the response, a number, when known. |
| `client_ip` | The client's address, when known. |

Health checks (`/health`, `/healthz`, ...) are logged at `debug`, so the probes Docker runs every
few seconds stay out of an `info` log.

pacod-gw lite's per-call lines (`pacod_gw_lite::request`) keep their own fields, which its smoke
test and docs/lite.md depend on.

### Outgoing requests

A library that logs the requests a service makes (Python's httpx, for example) logs their URLs
without the query string (API keys travel there, e.g. `?key=`), or stays at `warn` unless the
level is `debug`.

### Text: one line per event

```text
2026-10-23T06:41:07.695Z INFO  pacod-id pacod_id::app: pacod-id is up public=0.0.0.0:8070
```

The same content in a fixed order: `timestamp`, `LEVEL` (upper case, padded to five characters),
`service`, `logger:`, `message`, then the fields as `key=value` (values with spaces quoted). A stack
trace follows on the next lines. No ANSI colour codes unless the output is a terminal; never in a
file or a container log.

### What is never logged

Passwords, tokens, API keys, session ids, cookies, `Authorization` headers, sign-in codes and
state values, one-time links, request and response bodies. A value that must identify such a thing
is logged as a short hash or its last four characters.

## 5. Log files

Only where a product writes files (pacod-ap's Python services, pacod-cms):

- **JSON, always**, the event of section 4, whatever the format of stdout.
- **One directory per service**, set by the product's log-directory variable: pacod-ap's
  `APP_LOG_DIR` (unset: no files), pacod-cms's `LOG_DIR` (default `/data/logs`). The file is
  `<service>.log`.
- **Rotation:** at midnight UTC and at 20 MB, to `<service>.<yyyy-mm-dd>.<n>.log.gz` (gzip, `n`
  from 0, a new one for each rotation on the same day).
- **Retention:** rotated files are kept 30 days and at most 600 MB in all; the oldest go first.

## 6. Access logs (Caddy)

Each product's Caddy fragment (`platform/caddy/<product>.caddy`) logs its sites' requests with
the same block:

- `output stdout`, `format filter` wrapping `{$<PRODUCT>_CADDY_LOG_FORMAT:<default>}`: exactly
  `json` or `console` (Caddy's name for text; Caddy does not start on any other value, so the
  rule of section 3 for unknown values does not hold here). `ID_CADDY_LOG_FORMAT`, `PGW_CADDY_LOG_FORMAT`,
  `CMS_CADDY_LOG_FORMAT`, `KB_CADDY_LOG_FORMAT`; pacod-ap's site takes its log from
  `AP_SITE_OPTIONS`.
- **Redaction**, at least: the query parameters `token`, `code`, `state`, `next`, `flow`,
  `id_token_hint`, `login_hint` and `key`; the request headers `Referer`, `X-Api-Key` and
  `X-Goog-Api-Key`; the response header `Location`. (Caddy itself leaves out `Cookie`,
  `Set-Cookie` and `Authorization`.)

Caddy's events keep Caddy's own keys (`ts`, `logger`, `msg`, `request`, ...): the edge is the one
place the schema of section 4 does not apply.

## 7. In the full stack (pacod-demo)

Two values in `demo.env` set every product (scripts/render-env.sh translates them into each
product's variables):

- `DEMO_LOG_LEVEL`: `debug`, `info`, `warn` or `error` (default `info`).
- `DEMO_LOG_FORMAT`: `text` or `json` (default `text`, for reading with `make logs`).

`DEMO_LOG_DIR` sets where the products that write files write them (section 5). The demo's edge
logs its own runtime messages and every site's requests in the same format, with the redaction of
section 6.

## 8. Third-party services

Databases, caches, Caddy, Grafana, Prometheus and the other images keep their own log formats.
They follow section 2 (rotation), and their level is set where the image offers it.
