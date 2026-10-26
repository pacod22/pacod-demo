#!/bin/sh
# `make verify`: checks that the full stack runs as one platform. The Docker-level checks run
# here (one instance of every shared component, one published port, the listeners kept on
# pacod-admin-net); the rest runs on pacod-net and pacod-admin-net in the tools image
# (scripts/verify.py). Exits 1 when any check fails.
set -u
cd "$(dirname "$0")/.."
fails=0
report() { printf '  %s  %s%s\n' "$1" "$2" "${3:+  $3}"; [ "$1" = FAIL ] && fails=$((fails + 1)); return 0; }

echo "single instances (docker)"
for name in pacod-caddy pacod-postgres pacod-qdrant pacod-embeddings pacod-id pacod-gw gw-mock \
	pacod-mailpit pacod-prometheus pacod-alertmanager pacod-grafana pacod-otel; do
	n=$(docker ps --filter "name=^${name}$" --filter status=running -q | wc -l | tr -d ' ')
	if [ "$n" = 1 ]; then report PASS "$name" "1 running"; else report FAIL "$name" "$n running"; fi
done

# No second edge, database server, vector store or monitoring stack in the demo.
for image in caddy postgres prom/prometheus grafana/grafana qdrant/qdrant; do
	n=$(docker ps --filter label=com.docker.compose.project=pacod-demo --format '{{.Image}} {{.Names}}' |
		awk -v i="$image" 'index($1, i) == 1 && $2 !~ /exporter|db-init/' | wc -l | tr -d ' ')
	if [ "$n" = 1 ]; then report PASS "one $image container"; else report FAIL "one $image container" "$n running"; fi
done

published=$(docker ps --filter label=com.docker.compose.project=pacod-demo --format '{{.Names}} {{.Ports}}' | grep -- '->' | awk '{print $1}' | tr '\n' ' ')
if [ "$published" = "pacod-caddy " ]; then
	report PASS "only pacod-caddy publishes a port"
else
	report FAIL "only pacod-caddy publishes a port" "published by: $published"
fi

stopped=$(docker ps -a --filter label=com.docker.compose.project=pacod-demo --filter status=exited \
	--format '{{.Names}}' | grep -v -E '^(gw-preflight|pacod-db-init)$' | tr '\n' ' ')
if [ -z "$stopped" ]; then report PASS "no service has stopped"; else report FAIL "no service has stopped" "$stopped"; fi

echo "pacod-admin-net (docker)"
# The listeners only servers reach: on pacod-admin-net, bound to aliases that exist there alone.
if [ "$(docker network inspect -f '{{.Internal}}' pacod-admin-net 2>/dev/null)" = true ]; then
	report PASS "pacod-admin-net is internal"
else
	report FAIL "pacod-admin-net is internal" "missing or with a route out"
fi
expected=" pacod-id pacod-gw gw-console gw-analytics gw-tester ap-frontend ap-api ap-admin-api cms-studio kb-ui "
others=
for c in $(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' pacod-admin-net 2>/dev/null); do
	case "$expected" in *" $c "*) ;; *) others="$others$c " ;; esac
done
if [ -z "$others" ]; then report PASS "only the listeners and their callers join it"; else report FAIL "only the listeners and their callers join it" "also: $others"; fi
probe() { docker run --rm --network "$1" busybox:1.38.0 wget -q -T 3 -O /dev/null "$2" >/dev/null 2>&1; }
for target in http://pacod-gw:8081/readyz http://pacod-id:8071/oidc/jwks; do
	if probe pacod-net "$target"; then report FAIL "$target refused on pacod-net" "it answered"; else report PASS "$target refused on pacod-net"; fi
done
for target in http://pacod-gw-admin:8081/readyz http://pacod-id-internal:8071/oidc/jwks; do
	if probe pacod-admin-net "$target"; then report PASS "$target answers on pacod-admin-net"; else report FAIL "$target answers on pacod-admin-net"; fi
done

unhealthy=$(docker ps --filter label=com.docker.compose.project=pacod-demo --filter health=unhealthy --format '{{.Names}}' | tr '\n' ' ')
if [ -z "$unhealthy" ]; then report PASS "no container is unhealthy"; else report FAIL "no container is unhealthy" "$unhealthy"; fi

docker run --rm --network pacod-net --network pacod-admin-net \
	-v "$PWD/scripts:/work/scripts:ro" -v "$PWD/.state:/state:ro" \
	-v "$PWD/../pacod-ap:/src/pacod-ap:ro" \
	--env-file .state/env/stack.env \
	pacod-demo-tools:dev python3 -I /work/scripts/verify.py "$@" || fails=$((fails + 1))

[ "$fails" -eq 0 ] || { echo "verify: FAILED"; exit 1; }
echo "verify: OK"
