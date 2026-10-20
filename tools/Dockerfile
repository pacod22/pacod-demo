# pacod-demo-tools: what the demo's scripts need, so the host needs only Docker and make.
# Used for secret generation, the bootstrap (pacod-ap's gw_bootstrap.py and id_bootstrap.py,
# pacod-cms's id-provisioner.mjs) and `make verify`. Scripts are mounted, never baked in.
FROM node:24.21.0-alpine3.24

RUN apk add --no-cache python3 curl openssl jq bash

WORKDIR /work
