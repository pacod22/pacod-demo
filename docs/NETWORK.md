# The pacod network and naming convention

All pacod repos (`pacod-id`, `pacod-gw`, `pacod-ap`, `pacod-cms`, `pacod-kb`, `pacod-benchmark`)
follow this convention, so their compose files can run on their own or be put together into one full stack
(`pacod-demo`) without editing them.

## 1. Two networks: `pacod-net` and `pacod-admin-net`

- Every service in every compose file joins the external Docker network `pacod-net`.
  There are no project networks, no `agent-net`, `pacod-link`, `pacod-cms-net`, `kb-id`, `kb-ai`,
  `backend`, `edge`, `egress` or `id-edge`.
- The listeners that only servers reach are on the second external network, `pacod-admin-net`,
  which is **internal** (no route out). Today these are the gateway's management API,
  pacod-id's internal listener and pacod-benchmark's API. The services that call them join it as well; nothing else does.
- **A listener on `pacod-admin-net` binds to an alias**, never to `0.0.0.0`. Its service has the
  alias on `pacod-admin-net` alone (`pacod-gw-admin`, `pacod-id-internal`, `bench-api-internal`),
  and the listen variable names it (`PGW_ADMIN_LISTEN=pacod-gw-admin:8081`,
  `PID_INTERNAL_LISTEN=pacod-id-internal:8071`, `BENCH_LISTEN=bench-api-internal:8190`). A container that listens on `0.0.0.0` is reachable
  on every network it joins, so the binding, not the network, keeps the rest of `pacod-net` out.
- **Clients call the alias**, never `pacod-gw:8081` or `pacod-id:8071`. A container on both
  networks may resolve a service name to either of its addresses, and nothing listens there on
  the `pacod-net` one.
- A development file that publishes such a port to the host keeps `0.0.0.0`: a published port
  never reaches an internal network. The full stack publishes none of them and binds each to its
  alias (`overlays/gw.yml`, `overlays/id.yml`; pacod-benchmark's app file binds its own).
  `make verify` checks that `pacod-net` alone reaches none of them and that only their callers join `pacod-admin-net`.
- Exception: the multi-machine and load-test overlays of pacod-gw (`hub`, `node`, `scale`,
  `load-agent`, `load`) keep the extra networks they need between machines, and pacod-benchmark's
  API keeps `bench-agents` towards its load agents. In the full stack it joins pacod-gw's
  `load-out` as well, to reach the gateway's load agent (`overlays/bench.yml`).
- Compose declares them as

  ```yaml
  networks:
    pacod-net:
      external: true
    pacod-admin-net:
      external: true
  ```

  and a service with a listener there as

  ```yaml
  networks:
    pacod-net:
    pacod-admin-net:
      aliases: [pacod-gw-admin]
  ```

- Every repo's Makefile creates them before `up`, idempotently, and stops when
  `pacod-admin-net` exists without being internal (compose never checks that):

  ```sh
  docker network inspect pacod-net >/dev/null 2>&1 || docker network create pacod-net
  docker network inspect pacod-admin-net >/dev/null 2>&1 || docker network create --internal pacod-admin-net
  test "$(docker network inspect -f '{{.Internal}}' pacod-admin-net)" = true
  ```

**Trade-off.** One flat network gives up pacod-gw's `internal` backend network (databases without
egress) and pacod-kb's separation of the pipeline from pacod-id. On a single machine the
containers are still reachable only from each other and through the one published edge.
`pacod-admin-net` keeps the two server-only listeners from the rest of `pacod-net`; it is shared
too, so every caller on it reaches both, where Kubernetes network policies admit each listener's
own clients only.

## 2. One name per service, unique across all repos

- The compose service key, the `container_name` and the DNS name on `pacod-net` are the same.
- **Shared components** (used by more than one product, or a platform concern) are named
  `pacod-*`. Only one instance of each may run on a host at a time.
- **Everything else** carries its owner's prefix: `id-`, `gw-`, `ap-`, `cms-`, `kb-`, `bench-`.
- **Volume keys** follow the same rule (`pacod-postgres-data`, `gw-clickhouse-data`,
  `cms-mongo-data`, `kb-ui-data`, ...).
- **One shared volume is external**, like the networks: `pacod-hf-cache`, the embedding models
  (`pacod-embeddings`, and pacod-kb's tokenizer in the full stack). Declared `external: true` in
  pacod-ap's app file and in `platform.yml`, and created by the Makefiles next to the networks
  (`make ensure-pacod-net`, `make network`), it belongs to no compose project: no `down -v` or
  `make clean` deletes it, so a teardown never costs the 2–4 GB download again. Only
  `make clean MODELS=1` removes it.
- Host ports are unchanged in each repo's standalone mode; the full stack publishes only its edge.

### Shared components

| Name | What | Ports on `pacod-net` | Standalone copies in |
|---|---|---|---|
| `pacod-caddy` | the edge (Caddy 2.11.6) | as configured | gw, ap, cms, bench |
| `pacod-postgres` | PostgreSQL 18.6 (kb standalone: 16) | 5432 | id, gw, ap, cms, kb, bench |
| `pacod-db-init` | one-shot: runs every `platform/postgres/*-db.sh` against `pacod-postgres` | - | gw, ap, cms, kb, id, bench |
| `pacod-qdrant` | Qdrant v1.19.2 | 6333, 6334 | ap, kb |
| `pacod-embeddings` | bge-m3 embeddings + reranker (`/embed`, `/rerank`) | 8080 | defined in ap's app file |
| `pacod-id` | identity provider | 8070 public, 8071 internal (`pacod-id-internal` on `pacod-admin-net`), 9170 metrics | gw, ap, cms, kb, bench (pacod-id: its app file) |
| `pacod-gw` | the gateway: full core, or gw-lite in cms/kb standalone | 8080 data plane, 8081 management (`pacod-gw-admin` on `pacod-admin-net`), 9100 metrics | cms, kb (lite) (pacod-gw: its app file) |
| `gw-mock` | the gateway's mock LLM provider | 9000 | gw (dev), cms, kb |
| `pacod-mailpit` | mail sink | 1025 SMTP, 8025 web | id, gw, ap, cms, kb, bench |
| `pacod-prometheus`, `pacod-alertmanager`, `pacod-grafana`, `pacod-otel` | monitoring | 9090, 9093, 3000, 4317/4318 | gw, ap |
| `pacod-node-exporter`, `pacod-cadvisor`, `pacod-postgres-exporter` | platform exporters | 9100, 8080, 9187 | ap |

### Product-owned stores (not shared, and why)

| Name | Why it stays with its product |
|---|---|
| `gw-clickhouse` | the gateway's columnar analytics store; PostgreSQL cannot replace it |
| `gw-valkey` | the gateway's response cache: the answers its instances share, in memory only and behind a password of its own, the least recently used dropped first |
| `cms-mongo` | cms's data model is MongoDB, with one database per website |
| `cms-gotenberg` | cms's PDF renderer; nothing else uses it |
| `ap-redis` | ap's own caches and counters; the gateway's answers stay in `gw-valkey` |
| `ap-pgbouncer` | ap's connection pooler, in front of `pacod-postgres` |

### Addresses products use for shared components

The variable names stay what each product already calls them; only their defaults change.

| Component | Default address | Variables |
|---|---|---|
| pacod-id, internal | `http://pacod-id-internal:8071` (on `pacod-admin-net`) | ap `ID_INTERNAL_URL`, gw `PGW_ID_INTERNAL_URL`, cms and kb `PACOD_ID_INTERNAL_URL`, bench `BENCH_ID_INTERNAL_URL` |
| pacod-gw, data plane | `http://pacod-gw:8080` | ap `GATEWAY_URL`, cms and kb `PACOD_GW_URL` |
| pacod-gw, management | `http://pacod-gw-admin:8081` (on `pacod-admin-net`) | ap `GATEWAY_ADMIN_URL`, gw `GATEWAY_API_URL`, `PGW_CORE_ADMIN_URL`, bench `BENCH_GW_ADMIN_URL` |
| PostgreSQL | `pacod-postgres:5432` | ap via `ap-pgbouncer`, kb `KB_DATABASE_HOST`, gw `DATABASE_URL`, id `PID_DATABASE_URL`, bench `DATABASE_URL` |
| Qdrant | `http://pacod-qdrant:6333` | ap and kb `QDRANT_URL` |
| Embeddings | `http://pacod-embeddings:8080` | ap `EMBEDDINGS_URL`; kb `EMBEDDINGS_URL` (unset: in-process model) |
| Mail | `pacod-mailpit:1025` | `PID_SMTP_HOST`, `SMTP_HOST` |

## 3. Two compose files per repo

- **`docker-compose.yml`** (the "app file") holds only the repo's own services. They reach shared
  components by their `pacod-*` names. Every `depends_on` on a shared component carries
  `required: false`, so the file works whether that component comes from the repo's standalone
  file or from the full stack. (Compose still wants the service *defined* by one of the loaded
  files; a defined service whose profile is off is fine. The app file is therefore never loaded
  alone.)
- **`docker-compose.standalone.yml`** holds the repo's own copies of the shared components it
  needs, under the same `pacod-*` names (profiles only where the repo's standalone mode already
  toggles an optional part, such as cms's dev gateway). The repo's `make up` loads both files.
  The full stack loads only the app file and provides the shared components once.
- The full stack reuses the standalone definitions through `extends` (resetting `depends_on`,
  `ports` and `profiles`), so the image, healthcheck and settings of a shared component are
  defined in one place.

| Repo | App file | Standalone file |
|---|---|---|
| pacod-id | `deploy/compose/docker-compose.yml` | `deploy/compose/docker-compose.standalone.yml` |
| pacod-gw | `deploy/docker-compose.yml` (+ `.dev.yml` and the other overlays) | `deploy/docker-compose.standalone.yml` (+ `.standalone.dev.yml`) |
| pacod-ap | `pacod-api/docker-compose.yml` (+ `.local.yml`, `.gpu.yml`) | `pacod-api/docker-compose.standalone.yml` (+ `.standalone.local.yml`); monitoring in `monitoring/` |
| pacod-cms | `docker-compose.yml` | `docker-compose.standalone.yml` |
| pacod-kb | `docker-compose.yml` | `docker-compose.standalone.yml` |
| pacod-benchmark | `docker-compose.yml` (+ `.dev.yml`) | `docker-compose.standalone.yml` |

## 4. Fragments: each repo's share of the shared configuration

Each repo keeps the pieces of shared configuration that belong to it under `platform/` at its
root. Its standalone copies consume them, and so does the full stack: nothing is copied.

### Caddy: `platform/caddy/<product>.caddy`

- Site blocks only, no global options block (an imported file cannot have one).
- Addresses come from environment placeholders with the standalone value as default, e.g.
  `{$KB_SITE::8080}` or `{$AP_SITE:http://localhost:8080}`. The full stack sets them to
  `https://<host>.<domain>` (a certificate in pacod-demo's certs/) or
  `http://<host>.<domain>:<port>`; the full stack's Caddyfile loads the one certificate, and every
  site whose name it covers is served with it.
- Upstreams are the service names (`pacod-id:8070`, `cms-studio:7443`, ...).
- A standalone Caddyfile is its global options plus `import /etc/caddy/sites/*.caddy`.
- Only cms's public-site block catches every host.

| Repo | Site variables |
|---|---|
| pacod-id | `ID_SITE` |
| pacod-gw | `GW_CONSOLE_SITE`, `GW_API_SITE` |
| pacod-ap | `AP_SITE` |
| pacod-cms | `CMS_STUDIO_SITE`, `CMS_PUBLIC_SITE` (catch-all) |
| pacod-kb | `KB_SITE` |
| pacod-benchmark | `BENCH_SITE` |

### PostgreSQL: `platform/postgres/<product>-db.sh`

- Run by `pacod-db-init` (image `postgres:18.6-alpine3.24`, used as a client) at **every** start,
  in file-name order, with `PGHOST`, `PGPORT`, `PGUSER` (a superuser) and `PGPASSWORD` set, plus
  the variables the script names.
- Idempotent. It creates the product's role (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`) when
  missing and sets its password at every run, unless the role is the connecting superuser itself
  (existing installations whose app connects as the superuser keep working). It creates the
  database owned by that role when missing, then applies the product's SQL idempotently.
- `psql -v ON_ERROR_STOP=1`; secrets passed with `-v`, never interpolated into SQL text.

| Repo | Role and database | Password variable |
|---|---|---|
| pacod-id | `pacod_id` | `PID_DB_PASSWORD` |
| pacod-gw | `pacod_gw` | `GW_DB_PASSWORD` |
| pacod-ap | `pacod` | `AP_DB_PASSWORD` |
| pacod-kb | `kb` | `KB_DATABASE_PASSWORD` |
| pacod-benchmark | `bench` | `BENCH_DB_PASSWORD` |

### Monitoring: `platform/prometheus/<product>.yml`

- A file with a top-level `scrape_configs:` list, loaded through Prometheus's
  `scrape_config_files` glob. Targets are service names on `pacod-net`.
- **`job_name` values never change**: dashboards, alerts and the gateway console's queries
  select on them.
- Platform jobs (`prometheus`, `caddy`, `qdrant`, `postgres`, `node`, `cadvisor`) are defined
  once, by whoever runs the shared monitoring.
- Rules and dashboards stay where each repo keeps them today:

| Repo | Scrape jobs | Rules | Dashboards |
|---|---|---|---|
| pacod-id | `pacod-id` | - | - |
| pacod-gw | `pacod-gw-core`, `otel-collector` | `deploy/prometheus/rules/` | `deploy/grafana/dashboards/` |
| pacod-ap | `api`, `admin-api`, `mcp-server`, `embeddings`, `ingestion`, `frontend`, `pgbouncer`, `redis` | `monitoring/prometheus/alerts/` (its `gw.rules.yml` is a copy of the gateway's) | `monitoring/grafana/dashboards/` |
| pacod-cms, pacod-kb | none (no metrics endpoints; cAdvisor covers their containers) | - | - |
| pacod-benchmark | `pacod-bench` | - | - |

- Grafana's Prometheus datasource has the uid `prometheus` everywhere.
