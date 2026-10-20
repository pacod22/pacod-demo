# pacod-demo

The whole pacod stack on one machine: identity (pacod-id), the AI gateway (pacod-gw), the agent
platform (pacod-ap), the CMS (pacod-cms) and the knowledge base (pacod-kb), working together as
one platform. One configuration file, one `make`.

```sh
make up        # build, start, bootstrap; prints the addresses and the admin's password
make verify    # check that everything works together
make down      # stop (data is kept)
make clean     # remove everything the demo made (IMAGES=1: its images too)
```

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

Everything is behind one Caddy on one port (`DEMO_HTTP_PORT`, default 8800), as host names under
`DEMO_DOMAIN` (default `localhost`, which browsers and curl resolve to 127.0.0.1 without DNS):

| Address | What |
|---|---|
| http://app.localhost:8800 | the agent platform (pacod-ap): chat and its admin console |
| http://console.localhost:8800 | the gateway console (pacod-gw): providers, models, keys, usage |
| http://gw.localhost:8800/v1 | the gateway's OpenAI-compatible API |
| http://studio.localhost:8800 | the CMS studio (pacod-cms) |
| http://demo.localhost:8800 | a CMS demo website (also minimal.localhost) |
| http://kb.localhost:8800 | the knowledge base (pacod-kb) |
| http://id.localhost:8800/admin | identity administration (pacod-id) |
| http://grafana.localhost:8800 | dashboards of every product (anonymous read access) |
| http://mail.localhost:8800 | every mail the stack sends (invitations, confirmations) |

Sign in anywhere as `DEMO_ADMIN_EMAIL` (default `admin@pacod.demo`); the password is in
`demo.env`, or generated and shown by `make urls`. One sign-in covers every product.

Safari may not resolve `*.localhost`: use another browser, or add `make hosts`'s lines to
`/etc/hosts`.

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
| Port 8800 is in use | Set `DEMO_HTTP_PORT` in demo.env; then `make clean` and `make up` (the address is part of the sign-in configuration). |
| Chat answers are "echo: ..." or off-topic | That is the mock provider: set a provider key in demo.env and run `make bootstrap`. |
