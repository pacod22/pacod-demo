#!/bin/sh
# Checks the machine before `make up`: Docker and Compose, memory and disk, the five sibling
# repositories (on the shared-network layout), the demo's port, and that no other pacod stack
# holds the shared container names. Prints what it finds; exits 1 on a blocking problem.
set -eu
cd "$(dirname "$0")/.."

port=${DEMO_HTTP_PORT:-8800}
fail=0
ok() { printf '  ok    %s\n' "$1"; }
warn() { printf '  warn  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fail=1; }

echo "preflight:"

if ! docker info >/dev/null 2>&1; then
	bad "Docker is not running (start Docker Desktop or the daemon)"
	exit 1
fi
ok "Docker $(docker version --format '{{.Server.Version}}')"

cv=$(docker compose version --short 2>/dev/null || echo 0)
major=${cv%%.*}
minor=$(echo "$cv" | cut -d. -f2)
if [ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 24 ]; }; then
	ok "Docker Compose $cv"
else
	bad "Docker Compose $cv is too old: 2.24 or newer is needed (include, !reset, !override)"
fi

mem=$(docker info --format '{{.MemTotal}}')
gib=$(( (mem + 536870912) / 1073741824 ))
if [ "$mem" -lt 7500000000 ]; then
	warn "Docker has about ${gib} GiB of memory; the full stack with the embedding model wants 8 GB (Docker Desktop: Settings > Resources). EMBEDDINGS_BACKEND=auto picks the light stand-in below that"
else
	ok "Docker memory about ${gib} GiB"
fi
case ",${DEMO_PROFILES:-}," in *,ingestion,*)
	[ "$gib" -ge 16 ] || warn "DEMO_PROFILES has ingestion, whose service alone may use 7 GiB: with ${gib} GiB for Docker, expect containers to be killed (exit 137)" ;;
esac

free_gib=$(df -k . | awk 'NR==2 {print int($4 / 1048576)}')
if [ "$free_gib" -lt 30 ]; then
	warn "${free_gib} GiB of free disk; the first build needs about 30 GiB"
else
	ok "free disk ${free_gib} GiB"
fi

for r in pacod-id pacod-gw pacod-ap pacod-cms pacod-kb; do
	if [ ! -d "../$r" ]; then
		bad "../$r is missing: the demo builds from the five sibling checkouts"
		continue
	fi
	if [ ! -d "../$r/platform" ]; then
		bad "../$r has no platform/ directory: check out the branch with the shared-network layout (pacod-net)"
		continue
	fi
	branch=$(git -C "../$r" branch --show-current 2>/dev/null || echo '?')
	ok "../$r ($branch)"
done

# The shared names (pacod-*) exist once per host: another pacod stack must be stopped first.
others=$(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' |
	awk '$2 != "pacod-demo" && ($1 ~ /^(pacod|gw|ap|cms|kb|id)-/) {print $1 " (" $2 ")"}')
if [ -n "$others" ]; then
	bad "other pacod containers are running; stop their stacks first: $(echo "$others" | tr '\n' ' ')"
else
	ok "no other pacod stack is running"
fi

if docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' | grep -q '^pacod-caddy pacod-demo$'; then
	ok "port $port is the demo's own"
elif (command -v lsof >/dev/null && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1); then
	bad "port $port is in use; free it or set DEMO_HTTP_PORT in demo.env"
else
	ok "port $port is free"
fi

exit $fail
