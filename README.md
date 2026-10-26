# pacod-demo

The whole pacod stack on one machine: identity (pacod-id), the AI gateway (pacod-gw), the agent
platform (pacod-ap), the CMS (pacod-cms) and the knowledge base (pacod-kb), working together as
one platform. One configuration file, one `make`.

```sh
make up        # build, start, bootstrap; prints the addresses and the admin's password
make verify    # check that everything works together
make reset     # undo every change: back to the baseline (also every night, 05:00 UTC by default)
make down      # stop (data is kept)
make clean     # remove everything the demo made (IMAGES=1: its images too)
make shots     # retake the home page's screenshots from the running stack
```

Then open **https://demo.pacod.io** (or, for a demo on your machine, http://localhost:8800):
the demo's home page, with every product, what it is, a screenshot, and the links to its
administration and its websites.

## What you need

- Docker with Compose 2.24 or newer, and make. Everything else (secret generation, the
  bootstrap, the checks) runs in containers.
- The five repositories checked out next to this one, on the branch with the shared-network
  layout (`pacod-net`):

  ```
  pacod/
    pacod-demo/   (this)
    pacod-id/  pacod-gw/  pacod-ap/  pacod-cms/  pacod-kb/
  ```

- Memory: 8 GB for Docker (Docker Desktop's default setting); the whole stack with the real
  embedding model uses about 5 GiB (measured). Below that, the default `EMBEDDINGS_BACKEND=auto`
  picks a light stand-in (about 4 GiB). Disk: about 30 GiB for the
  images. The first `make up` builds every image from source (two Rust workspaces, a Maven
  build, several Python images, two SvelteKit apps): 30 to 60 minutes. Later starts take about
  two minutes.

## The addresses

Everything is behind one Caddy, as host names under `DEMO_DOMAIN` (demo.env: `demo.pacod.io`):

| Address | What |
|---|---|
| https://demo.pacod.io | the demo's home page: every product, with its links ([home/](home/)) |
| https://app.demo.pacod.io | the agent platform (pacod-ap): chat and its admin console |
| https://console.demo.pacod.io | the gateway console (pacod-gw): providers, models, keys, usage |
| https://gw.demo.pacod.io/v1 | the gateway's OpenAI-compatible API |
| https://studio.demo.pacod.io | the CMS studio (pacod-cms; `DEMO_STUDIO_SUBDOMAIN`) |
| https://cms.pacod-sites.net | a CMS demo website, "Demo Journal" (also "Demo Notes" at minimal.pacod-sites.net and "Starward", the space travels, at space.pacod-sites.net): the websites have a domain of their own, `DEMO_SITE_DOMAIN` (below), or with `DEMO_SITE_DOMAIN_SHARED=true` the demo's, cms.demo.pacod.io; the subdomains are `DEMO_CMS_SUBDOMAIN`, `DEMO_CMS_MINIMAL_SUBDOMAIN` and `DEMO_CMS_SPACE_SUBDOMAIN` |
| https://kb.demo.pacod.io | the knowledge base (pacod-kb) |
| https://id.demo.pacod.io/admin | identity administration (pacod-id) |
| https://grafana.demo.pacod.io | dashboards of every product |
| https://mail.demo.pacod.io | every mail the stack sends (invitations, confirmations) |
| https://prometheus.demo.pacod.io | the metrics, for ad-hoc queries |

Sign in anywhere as `DEMO_ADMIN_EMAIL` (default `admin@pacod.demo`); the password is in
`demo.local.env`, or generated and shown by `make urls`. One sign-in covers every product.

### The CMS's demo websites

The studio creates its three demo websites, with their sections, authors, articles and pictures,
**on its first start only, while it has no website at all**: "Demo Journal" (the editorial
template set), "Demo Notes" (minimal) and "Starward" (magazine). Starward is the showcase: an
image-led magazine with a full-width cover story, 24 long-form articles illustrated with NASA
photographs, its own pages, menus and journal issues, and five invented advertisers with animated
HTML5 creatives. A website whose demo content failed then stays empty, and is never filled again by
itself. `make up` starts the images it finds and never rebuilds them, so after pulling the
repositories run `make build` first: the studio's image carries the demo content, the template
sets and the seeds.

- **From scratch**, every website with its content (this also deletes the accounts, keys and
  data of every product; demo.env and demo.local.env stay): pull the six repositories, then
  `make build`, `make clean`, `make up`.
- **Keeping the data**: pull, `make build`, `make up`, then fill each empty website:
  `make cms-seed SITE=demo`, `make cms-seed SITE=minimal` and `make cms-seed SITE=space DEMO=space`.
  It refuses a website that has articles and writes nothing then. It keeps the website's template
  set, so Starward gets its design only in a `space` website that is already on magazine; any
  other is replaced as below.
- **Starward on a stack started before it, or with its earlier version** (on the editorial set,
  without the photographs; `make verify` says "Starward renders on the magazine set: FAIL"): demo
  content goes only into a website without articles, so the old one goes first.
  1. Pull pacod-cms, then `make build` and `make up`.
  2. In the studio, open Starward, *Settings → Delete this website*, and type its id, `space`.
  3. Wait three minutes, until the id is free again, then `make cms-space`. It creates Starward on
     the magazine set in the demo websites' organisation (`ORG=org_...` another).
  4. `make baseline`. Until then `make reset`, and the nightly reset at `DEMO_RESET_TIME`, bring the
     old Starward back: keep the whole procedure clear of that time. `make baseline` keeps
     everything else as it is at that moment too, so run it right after the step above.

### HTTPS on a domain

With `DEMO_TLS_CERT` and `DEMO_TLS_KEY` set, the stack answers on HTTPS (`DEMO_HTTPS_PORT`, 443)
with that one certificate, and plain HTTP (`DEMO_HTTP_PORT`, 80) only redirects to it. What it
needs:

- **The websites' domain:** `DEMO_SITE_DOMAIN`, another registrable domain than `DEMO_DOMAIN`'s
  (`pacod-sites.net` in these examples; not `sites.pacod.io`). A website runs its owner's
  script, which on the studio's registrable domain could set cookies the studio and the sign-in
  receive: the studio refuses to start then, and `make up` stops before it when it can tell.
  Or `DEMO_SITE_DOMAIN_SHARED=true`, for a demo whose websites are all trusted: the websites then
  answer under `DEMO_DOMAIN` itself (`cms.demo.pacod.io`), the studio starts with a warning, and
  the certificate and DNS need no other name. Over HTTPS only (demo.env.example says what it costs).
- **DNS:** `demo.pacod.io`, `*.demo.pacod.io` and `*.pacod-sites.net` pointing at the server.
  Before the DNS is set, `make hosts` prints `/etc/hosts` lines to try it: as they are on the server itself; on another
  machine, with the server's address instead of 127.0.0.1.
- **The certificate:** one for `demo.pacod.io`, `*.demo.pacod.io` and `*.pacod-sites.net`, full
  chain, in [certs/](certs/README.md). The files there now are self-signed placeholders: the edge starts,
  but browsers warn and the products' sign-in fails until the real ones replace them. They
  are not committed.
- **Ports 80 and 443** free on the server (`DEMO_BIND=0.0.0.0`: every interface).

Published beyond this machine (`DEMO_BIND` not a loopback address), the demo closes what is only
safe on a laptop: the mail viewer (it shows every reset and invitation link), Prometheus and
Grafana (a viewer can query Prometheus through it) ask for the admin's email and password, the
gateway console's `/grafana` answers 403, the home page does not name the admin, and the app's
dev tokens (sign-in without a password) are off, so `make verify` skips its chat turn. The
products' own administration (pacod-ap's `/monitoring` included) is behind their sign-in as
always.

### A demo on this machine only

In demo.env: `DEMO_DOMAIN=localhost`, `DEMO_TLS_CERT=` and `DEMO_TLS_KEY=` (empty),
`DEMO_HTTP_PORT=8800`, `DEMO_BIND=127.0.0.1`. Then everything is plain HTTP at
http://<name>.localhost:8800 (browsers and curl resolve `*.localhost` to 127.0.0.1 without DNS;
Safari may not: use another browser, or add `make hosts`'s lines to `/etc/hosts`).

### Google Search and Analytics

The home page and Starward can each be verified in Google Search Console and counted by Google
Analytics, set in demo.env (empty: nothing of Google's on the page, the default):

| demo.env | |
|---|---|
| `DEMO_HOME_GOOGLE_TAG` | the home page's GA4 measurement id (`G-…`, loaded with gtag.js) or Tag Manager container (`GTM-…`) |
| `DEMO_HOME_GOOGLE_SITE_VERIFICATION` | the `content` of the home page's Search Console meta tag (the "HTML tag" method) |
| `DEMO_STARWARD_GTM_ID` | Starward's Tag Manager container (`GTM-…`): the CMS loads Tag Manager only, so GA4 comes through a Google tag with the measurement id inside the container |
| `DEMO_STARWARD_GOOGLE_SITE_VERIFICATION` | the `content` of Starward's Search Console meta tag |

`make up` stops on a value that is not of Google's shape. The home page's apply on `make up`
(the edge is recreated). Starward's are written into its settings in the CMS by `make up`,
`make reset` (so also every night) and `make cms-space`, and show within the CMS's 60-second
cache. demo.env owns those two fields: a value removed there is removed from Starward too.
Instead of the two verifications, a Search Console domain property (a DNS TXT record on the
registrable domain) covers both hosts. Each CMS website answers its own `/robots.txt` and
`/sitemap.xml`, which Search Console takes for Starward.

## The nightly reset

Every day at `DEMO_RESET_TIME` (demo.env, default `05:00`) in `DEMO_RESET_TIMEZONE` (default
`UTC`) the demo undoes every change made to it: accounts, agents, articles, documents, keys,
settings. Every product's data goes back to the **baseline**, and the products are unavailable for
a few minutes meanwhile. The home page announces it in a band under its header, with the time in
the visitor's own zone and how long until the next reset; `make urls` says it too.

- **The baseline** is the stack's data at one moment: the first `make up` of a fresh stack keeps
  what it made, once the CMS has written its demo websites. `make baseline` keeps the data as it is
  now instead, e.g. on a stack that existed before the reset did (it has no baseline, and no
  nightly reset until it gets one), or after adding content every visitor should find. For the
  original state from scratch: `make clean`, `make up`.
- **What it holds**: every data volume of the stack (PostgreSQL, MongoDB, Qdrant, ClickHouse,
  Redis, the knowledge base's and the app's files, Grafana, Alertmanager), the CMS's files
  (`.state/cms/storage`) and the bootstrap's credentials. Not the models, Prometheus's metrics or
  the edge's own data, nor the gateway's response cache, which is kept in memory only: every reset
  empties it. It is kept, compressed, in the Docker volume `pacod-demo-baseline`;
  `make clean` deletes it with the rest.
- **`make reset`** does the same as the nightly reset, now. Both stop every service but the edge
  (the home page stays up), put the baseline back, start the stack and run the bootstrap again,
  which applies what `demo.local.env` has gained since the baseline (a provider key). The admin's
  password is the baseline's: after changing it, `make baseline` again.
- **The scheduler** is the container `pacod-demo-reset` that `make up` starts (and `make down`
  stops). It is outside the compose project, since a reset stops the project, runs `make reset`
  from this checkout through Docker's socket, and logs to `docker logs pacod-demo-reset`. It skips a
  night when the stack is not running, or when the machine was asleep at the time (a laptop).
- **Off**: `DEMO_RESET_TIME=` (empty), then `make up`.

## Configuration: demo.env and demo.local.env

`demo.env` is the configuration. It sets the domains (the stack's and the websites') and ports,
the certificate, the first administrator's email, the nightly reset's time, the optional component
(`DEMO_PROFILES=ingestion`), the models each product uses, the embedding backend and the log
directory. Run `make up` after a change. Git
ignores it: the first `make` copies it from `demo.env.example`, the committed template; change
the template only for what every machine should get.

`demo.local.env` holds this machine's secrets: the admin's password and the LLM provider keys.
Git ignores it. It is read after `demo.env`, and what it sets wins:

```sh
# demo.local.env
DEMO_ADMIN_PASSWORD=a-long-passphrase
ANTHROPIC_API_KEY=sk-...
```

**Optional component.** `ingestion` runs the app's document ingestion service and loads the
starter agent's knowledge base (its corpus); without it the starter agent answers from its
reference facts only. It is a ~10 GB image and may use 7 GiB of memory, so it wants 16 GiB for
Docker.

**The embedding backend is fixed at the first `make up`**: the stored vectors depend on it, so
switching between `bge` and `hash` later needs `make clean`.

**Logs.** Every product logs the same way ([docs/LOGGING.md](docs/LOGGING.md)): one event per line
on its container's output (`make logs S=<service>`), with the same keys everywhere (`timestamp`,
`level`, `service`, `logger`, `message`), and Docker keeps 5 files of 10 MB per container.
`DEMO_LOG_LEVEL` (`debug`, `info`, `warn`, `error`) and `DEMO_LOG_FORMAT` (`text` to read,
`json` for a log collector) set every product and the edge's request log; `DEMO_LOG_DIR` is where
the app and the CMS also write JSON log files.

**LLM providers.** With no key set, every model call goes to the gateway's mock provider: the
demo runs offline and `make verify` is deterministic, but chat answers are canned. Set
`ANTHROPIC_API_KEY` (the app's default models are Anthropic's) and others as you like in
`demo.local.env`, then
`make bootstrap`: the gateway is set up with the real providers.

## Updating

After pulling this repository and the five products:

```sh
make build      # make up builds only missing images: without this the old ones keep running
make up         # also makes any secret a new release needs (pacod-gw's PGW_VALKEY_PASSWORD)
make reset      # the original data, migrated by the new releases...
make baseline   # ...kept as the state every nightly reset returns to
```

Without the last two steps every reset puts back the older releases' data, which the products then
migrate again at each start.

## How it is put together

- **Two networks, unique names.** All five repositories follow one convention
  ([docs/NETWORK.md](docs/NETWORK.md)): every container joins the external network `pacod-net`
  under a name unique across the repositories. Shared components are `pacod-*`; everything else
  carries its owner's prefix (`gw-`, `ap-`, `cms-`, `kb-`). The gateway's management API and
  pacod-id's internal listener are on the internal network `pacod-admin-net` alone, as
  `pacod-gw-admin:8081` and `pacod-id-internal:8071`, and only their callers join it.
- **The repositories' own compose files.** Each repository splits its compose setup into an
  app file (its own services) and a standalone file (its own copies of the shared components,
  for running it alone). [compose.yml](compose.yml) includes the five app files unchanged; a
  small overlay per repository ([overlays/](overlays/)) only removes published ports, points
  settings at the demo's files and sets the public addresses.
- **One of each shared component** ([platform.yml](platform.yml)): one Caddy, one PostgreSQL
  (a database and owner role per product), one Qdrant, one embeddings service, one pacod-id
  (one realm for every product, [config/pacod-id.toml](config/pacod-id.toml)), one gateway, one
  mail sink, and one monitoring stack (Prometheus, Alertmanager, Grafana, the OpenTelemetry
  collector). Each `extends` the definition a repository already ships, and takes the products'
  contributions from their `platform/` directories: their Caddy site blocks, database scripts,
  Prometheus jobs, rules and dashboards.
- **Product-owned stores stay with their product**: the gateway's ClickHouse (analytics) and
  Valkey (its response cache), the CMS's MongoDB (one database per website) and Gotenberg (PDFs),
  the app's Redis and pgbouncer.
- **Generated state** lives in `.state/` (git-ignored): the secrets (made once, never changed),
  the credentials the bootstrap makes (gateway keys, service tokens), and each product's
  variables rendered from `demo.env` ([scripts/render-env.sh](scripts/render-env.sh)).
- **The bootstrap** ([scripts/bootstrap.py](scripts/bootstrap.py)) drives the products' own
  scripts: pacod-ap's gateway and identity bootstrap and pacod-cms's provisioner.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `preflight: other pacod containers are running` | A repository's own stack holds the shared names (`pacod-postgres`, ...). Stop it (`make down` or `make compose-down` there). |
| `up` stops with a container unhealthy | `make status`, then `make logs S=<service>`. A first start downloads models; `make up` again continues. |
| A model download reports "Not enough free disk space", or PostgreSQL fails to write | Docker's own disk is full (Docker Desktop: Settings, Resources, disk usage limit). `docker system df` shows what uses it; `docker builder prune` frees the build cache without touching images or data. |
| Containers exit with code 137 | Docker ran out of memory: give it more (Docker Desktop: Settings, Resources) or set `EMBEDDINGS_BACKEND=hash`. |
| `init: .state/secrets.env is missing but the demo's volumes exist` | The data needs the secrets that were deleted: `make clean`, then `make up`. |
| A port (80, 443, 8800) is in use | Change `DEMO_HTTP_PORT` or `DEMO_HTTPS_PORT` in demo.env; then `make clean` and `make up` (the address is part of the sign-in configuration). |
| `preflight: certs/... is the self-signed placeholder`, sign-in fails, browsers warn | Put the real certificate and key in certs/ ([certs/README.md](certs/README.md)), then `docker restart pacod-caddy`. |
| Caddy cannot read the key (Linux) | The edge runs as root without the capability to bypass file permissions: `sudo chown root:root certs/<key>`, or `chmod 644` it. |
| The CMS's websites have no articles | Their demo content failed on the first start, and the studio does not try again. `make logs S=cms-studio` says why ("Could not store the demo picture ..." was the CMS writing its files as another user than the one owning `.state/`, fixed). Then `make build`, `make up` and `make cms-seed` per website ([The CMS's demo websites](#the-cmss-demo-websites)). |
| `baseline: another save or restore is running` | A reset is running (`docker logs pacod-demo-reset`); if none is, one was interrupted: remove `.state/baseline.lock`. |
| The home page announces no nightly reset | There is no baseline yet (`make urls` says so): `make baseline`. |
| Chat answers are "echo: ..." or off-topic | That is the mock provider: set a provider key in demo.local.env and run `make bootstrap`. |
