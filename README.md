# pacod-demo

The whole pacod stack on one machine: identity (pacod-id), the AI gateway (pacod-gw), the agent
platform (pacod-ap), the CMS (pacod-cms) and the knowledge base (pacod-kb), working together as
one platform. One configuration file, one `make`.

```sh
make up        # build, start, bootstrap; prints the addresses and the admin's password
make verify    # check that everything works together
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
| https://studio.demo.pacod.io | the CMS studio (pacod-cms) |
| https://demo.demo.pacod.io | a CMS demo website (also minimal.demo.pacod.io) |
| https://kb.demo.pacod.io | the knowledge base (pacod-kb) |
| https://id.demo.pacod.io/admin | identity administration (pacod-id) |
| https://grafana.demo.pacod.io | dashboards of every product |
| https://mail.demo.pacod.io | every mail the stack sends (invitations, confirmations) |
| https://prometheus.demo.pacod.io | the metrics, for ad-hoc queries |

Sign in anywhere as `DEMO_ADMIN_EMAIL` (default `admin@pacod.demo`); the password is in
`demo.env`, or generated and shown by `make urls`. One sign-in covers every product.

### HTTPS on a domain

With `DEMO_TLS_CERT` and `DEMO_TLS_KEY` set, the stack answers on HTTPS (`DEMO_HTTPS_PORT`, 443)
with that one certificate, and plain HTTP (`DEMO_HTTP_PORT`, 80) only redirects to it. What it
needs:

- **DNS:** `demo.pacod.io` and `*.demo.pacod.io` pointing at the server. Before the DNS is set,
  `make hosts` prints `/etc/hosts` lines to try it: as they are on the server itself; on another
  machine, with the server's address instead of 127.0.0.1.
- **The certificate:** for `demo.pacod.io` and `*.demo.pacod.io`, full chain, in
  [certs/](certs/README.md). The files there now are self-signed placeholders: the edge starts,
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

## Configuration: demo.env

The only file to edit. It sets the domain and port, the first administrator, the optional
component (`DEMO_PROFILES=ingestion`), the LLM provider keys, the models each
product uses and the embedding backend. Run `make up` after a change.

**Optional component.** `ingestion` runs the app's document ingestion service and loads the
starter agent's knowledge base (its corpus); without it the starter agent answers from its
reference facts only. It is a ~10 GB image and may use 7 GiB of memory, so it wants 16 GiB for
Docker.

**The embedding backend is fixed at the first `make up`**: the stored vectors depend on it, so
switching between `bge` and `hash` later needs `make clean`.

**LLM providers.** With no key set, every model call goes to the gateway's mock provider: the
demo runs offline and `make verify` is deterministic, but chat answers are canned. Set
`ANTHROPIC_API_KEY` (the app's default models are Anthropic's) and others as you like, then
`make bootstrap`: the gateway is set up with the real providers.

## How it is put together

- **One network, unique names.** All five repositories follow one convention
  ([docs/NETWORK.md](docs/NETWORK.md)): every container joins the external network `pacod-net`
  under a name unique across the repositories. Shared components are `pacod-*`; everything else
  carries its owner's prefix (`gw-`, `ap-`, `cms-`, `kb-`).
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
- **Product-owned stores stay with their product**: the gateway's ClickHouse (analytics), the
  CMS's MongoDB (one database per website) and Gotenberg (PDFs), the app's Redis and pgbouncer.
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
| Chat answers are "echo: ..." or off-topic | That is the mock provider: set a provider key in demo.env and run `make bootstrap`. |
