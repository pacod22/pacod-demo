#!/bin/sh
# Checks the machine before `make up`: Docker and Compose, memory and disk, the seven sibling
# repositories (on the shared-network layout) and every file the demo mounts from them, that no
# other pacod stack holds the shared container names, the admin password's length, the HTTPS
# certificate and the demo's ports. Prints what it finds; exits 1 on a blocking problem.
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

for r in pacod-id pacod-gw pacod-ap pacod-cms pacod-kb pacod-benchmark pacod-broker; do
	if [ ! -d "../$r" ]; then
		bad "../$r is missing: the demo builds from the seven sibling checkouts"
		continue
	fi
	if [ ! -d "../$r/platform" ]; then
		bad "../$r has no platform/ directory: check out the branch with the shared-network layout (pacod-net)"
		continue
	fi
	branch=$(git -C "../$r" branch --show-current 2>/dev/null || echo '?')
	# The listeners only servers reach moved to pacod-admin-net in every repository at once; a
	# checkout from before would call addresses that no longer answer, or refuse the aliases as
	# listen addresses (pacod-id and pacod-gw before 3872f32 and the gateway's network commit).
	case $r in pacod-id) app=deploy/compose/docker-compose.yml ;; pacod-gw|pacod-broker) app=deploy/docker-compose.yml ;;
		pacod-ap) app=pacod-api/docker-compose.yml ;; *) app=docker-compose.yml ;; esac
	if ! grep -q pacod-admin-net "../$r/$app" 2>/dev/null; then
		bad "../$r ($branch) is from before pacod-admin-net: check out its branch pacod-admin-net, or one based on it (pacod-id: rebase pacod-vanga onto it)"
		continue
	fi
	ok "../$r ($branch)"
done

# Every file and folder the demo mounts from a sibling checkout (platform.yml, overlays/). A
# checkout older than the demo lacks some of them, and compose then makes an empty directory in
# the place of each: the service reading it fails, and says so only in its own log (pacod-caddy:
# "Could not import /etc/caddy/sites/ap-agents.caddy: is a directory").
# What to do about a source the sibling lacks on disk, from what its git says: restore a file the
# checkout has (git counts it deleted, so no pull brings it back), or pull a checkout that is older,
# named by branch and commit, since a pull that does not move it has a reason `git status` gives.
mount_fix() { # mount_fix REPO PATH-IN-REPO
	if git -C "../$1" cat-file -e "HEAD:$2" 2>/dev/null; then
		echo "../$1 has it, but not on disk, so a pull will not bring it back: git -C ../$1 checkout -- $2"
	elif at=$(git -C "../$1" log -1 --format=%h 2>/dev/null); then
		branch=$(git -C "../$1" branch --show-current 2>/dev/null || true)
		echo "../$1 (${branch:-detached} at $at) is older than the demo: git -C ../$1 pull; if that says up to date, git -C ../$1 status says why (another branch, local changes)"
	else
		echo "../$1 is older than the demo (git -C ../$1 pull)"
	fi
}
mount_problems=0
for src in $(sed -n 's#^[[:space:]]*-[[:space:]]*\(\.\./pacod-[a-z-]*/[^:]*\):.*#\1#p' platform.yml overlays/*.yml | sort -u); do
	repo=$(printf '%s\n' "$src" | cut -d/ -f2)
	[ -d "../$repo" ] || continue # reported above
	rel=${src#../$repo/}
	case ${src##*/} in
	*.*) # a file
		if [ -d "$src" ]; then
			bad "$src is a directory where ../$repo has a file, left by a start that found the file missing: remove it (sudo rmdir $src); then $(mount_fix "$repo" "$rel")"
			mount_problems=1
		elif [ ! -f "$src" ]; then
			bad "$src is missing: $(mount_fix "$repo" "$rel")"
			mount_problems=1
		fi ;;
	*) # a folder, which an earlier failed start may also have left empty
		if [ ! -d "$src" ] || [ -z "$(ls -A "$src" 2>/dev/null)" ]; then
			bad "$src is missing or empty: $(mount_fix "$repo" "$rel")"
			mount_problems=1
		fi ;;
	esac
done
[ "$mount_problems" = 1 ] || ok "every file the demo mounts from the sibling checkouts is there"

# The shared names (pacod-*) exist once per host: another pacod stack must be stopped first. The
# demo's nightly reset runs outside its compose project (Makefile: scheduler).
others=$(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' |
	awk '$2 != "pacod-demo" && $1 != "pacod-demo-reset" && ($1 ~ /^(pacod|gw|ap|cms|kb|bench|broker|id)-/) {print $1 " (" $2 ")"}')
if [ -n "$others" ]; then
	bad "other pacod containers are running; stop their stacks first: $(echo "$others" | tr '\n' ' ')"
else
	ok "no other pacod stack is running"
fi

# The admin's password (demo.local.env): pacod-id refuses a role holder's shorter than 12
# characters and stops at every start. Left empty, `make up` generates one of 24.
pw=${DEMO_ADMIN_PASSWORD:-}
if [ -z "$pw" ]; then
	ok "admin password: generated (make urls shows it)"
elif [ "${#pw}" -lt 12 ]; then
	bad "DEMO_ADMIN_PASSWORD has ${#pw} characters; pacod-id needs 12 or more (demo.local.env)"
else
	ok "admin password: ${#pw} characters"
fi

# HTTPS: the certificate and key demo.env names, in certs/ (certs/README.md).
cert=${DEMO_TLS_CERT:-}
key=${DEMO_TLS_KEY:-}
ports="$port"
if [ -n "$cert$key" ]; then
	ports="$port ${DEMO_HTTPS_PORT:-443}"
	if [ -z "$cert" ] || [ -z "$key" ]; then
		bad "set both DEMO_TLS_CERT and DEMO_TLS_KEY in demo.env, or neither (plain HTTP)"
	elif [ ! -f "certs/$cert" ] || [ ! -f "certs/$key" ]; then
		bad "certs/$cert and certs/$key must exist: the HTTPS certificate and its key (certs/README.md)"
	elif ! grep -q -- '-----BEGIN CERTIFICATE-----' "certs/$cert" || ! grep -q -- '-----BEGIN .*PRIVATE KEY-----' "certs/$key"; then
		bad "certs/$cert or certs/$key is not PEM (a certificate, an unencrypted private key)"
	elif grep -q '^PLACEHOLDER' "certs/$cert"; then
		warn "certs/$cert is the self-signed placeholder: browsers warn and the products' sign-in fails until the real certificate is there"
	else
		ok "certificate certs/$cert"
	fi
	# The one certificate serves every site: the stack's (*.DEMO_DOMAIN) and the websites'
	# (*.DEMO_SITE_DOMAIN).
	if [ -f "certs/$cert" ] && command -v openssl >/dev/null; then
		for h in "${DEMO_STUDIO_SUBDOMAIN:-studio}.$DEMO_DOMAIN" "${DEMO_CMS_SUBDOMAIN:-cms}.${DEMO_SITE_DOMAIN:-$DEMO_DOMAIN}"; do
			openssl x509 -noout -checkhost "$h" -in "certs/$cert" 2>/dev/null | grep -q 'does match' ||
				warn "certs/$cert does not cover $h: browsers refuse it there (certs/README.md)"
		done
	fi
fi

for pt in $ports; do
	if docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' | grep -q '^pacod-caddy pacod-demo$'; then
		ok "port $pt is the demo's own"
	elif (command -v lsof >/dev/null && lsof -nP -iTCP:"$pt" -sTCP:LISTEN >/dev/null 2>&1); then
		bad "port $pt is in use; free it or change the port in demo.env"
	else
		ok "port $pt is free"
	fi
done

exit $fail
