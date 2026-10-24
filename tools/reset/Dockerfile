# pacod-demo-reset: the nightly reset (scripts/reset-scheduler.sh) and the baseline's archiver
# (scripts/baseline.sh). The scheduler runs `make reset` from the demo's own checkout, so it needs
# what the host needs: Docker's CLI with Compose and make. GNU tar and zstd archive the volumes;
# tzdata and GNU date compute the next reset in DEMO_RESET_TIMEZONE. Scripts are mounted, never
# baked in.
FROM alpine:3.24.3

RUN apk add --no-cache docker-cli docker-cli-compose make tar zstd tzdata coreutils
