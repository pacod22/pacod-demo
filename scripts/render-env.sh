#!/bin/sh
# Renders the central configuration (demo.env), the generated secrets (.state/secrets.env) and
# the bootstrap's credentials (.state/bootstrap.env) into the variable names each repository's
# compose file reads. Nothing in the repositories is edited: compose.yml passes these files to
# each `include` as its env_file.
#
#   .state/env/stack.env           every include: addresses, sites, secrets, credentials
#   .state/env/<product>.env       the product's own names (later file wins), e.g. cms's and
#                                  kb's PACOD_GW_API_KEY, each its own gateway key
#   .state/env/id.container.env    pacod-id's container environment (its config's ${...})
#   .state/env/bootstrap-id.env    what pacod-ap's bootstrap scripts read as deploy/secrets/id.env
#   .state/env/providers.env       the provider keys, read by the gateway bootstrap only
#
# Run by `make up` and `make bootstrap`; safe to run any time. Prints no secret.
set -eu
cd "$(dirname "$0")/.."
home=$(pwd)
state=$home/.state
mkdir -p "$state/env" "$state/cms/storage" "$state/cms/backup"
umask 077

[ -f "$state/secrets.env" ] || { echo "render-env: .state/secrets.env is missing; run make init" >&2; exit 1; }
touch "$state/bootstrap.env"

set -a
. ./demo.env
# The machine's own values (the admin's password, provider keys), never committed: they win.
if [ -f ./demo.local.env ]; then . ./demo.local.env; fi
. "$state/secrets.env"
. "$state/bootstrap.env"
set +a

# The log files (demo.env's DEMO_LOG_DIR), one directory per service. Absolute: a relative bind
# source in an included compose file is read against that file's directory, not pacod-demo. Made
# here, by the user who runs make, so that Docker does not create them as root (and `make clean`
# can delete them).
log_dir=${DEMO_LOG_DIR:-$state/logs}
case $log_dir in /*) ;; *) log_dir=$home/$log_dir ;; esac
ap_logs=$log_dir/ap
if ! (umask 022 && mkdir -p "$log_dir/cms" "$ap_logs/api" "$ap_logs/admin-api" "$ap_logs/ingestion" \
	"$ap_logs/ingestion-cpu" "$ap_logs/improvement-loop" "$ap_logs/improvement-loop-worker" \
	"$ap_logs/newsletter-worker" 2>/dev/null); then
	echo "render-env: cannot create the log directories under $log_dir (DEMO_LOG_DIR)" >&2
	exit 1
fi
# The level and format of every product (demo.env's DEMO_LOG_LEVEL and DEMO_LOG_FORMAT,
# docs/LOGGING.md), written below in each product's own variables and spelling.
log_level=$(printf %s "${DEMO_LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
case $log_level in
	warning) log_level=warn ;;
	debug | info | warn | error) ;;
	*) echo "render-env: DEMO_LOG_LEVEL=$DEMO_LOG_LEVEL is not a level: debug, info, warn or error" >&2; exit 1 ;;
esac
log_format=$(printf %s "${DEMO_LOG_FORMAT:-text}" | tr '[:upper:]' '[:lower:]')
case $log_format in
	json) caddy_log_format=json ;;
	# Caddy's name for its readable format.
	text) caddy_log_format=console ;;
	*) echo "render-env: DEMO_LOG_FORMAT=$DEMO_LOG_FORMAT is not a format: text or json" >&2; exit 1 ;;
esac
# Python's spelling (pacod-ap's APP_LOG_LEVEL).
log_level_upper=$(printf %s "$log_level" | tr '[:lower:]' '[:upper:]')

d=$DEMO_DOMAIN
# The CMS's subdomains (demo.env): the studio's under DEMO_DOMAIN, the three demo websites' under
# DEMO_SITE_DOMAIN. One DNS label each.
studio=${DEMO_STUDIO_SUBDOMAIN:-studio}
cms=${DEMO_CMS_SUBDOMAIN:-cms}
minimal=${DEMO_CMS_MINIMAL_SUBDOMAIN:-minimal}
space=${DEMO_CMS_SPACE_SUBDOMAIN:-space}
for pair in "DEMO_STUDIO_SUBDOMAIN=$studio" "DEMO_CMS_SUBDOMAIN=$cms" "DEMO_CMS_MINIMAL_SUBDOMAIN=$minimal" \
	"DEMO_CMS_SPACE_SUBDOMAIN=$space"; do
	case ${pair#*=} in
		*[!a-z0-9-]* | -* | *-) echo "render-env: $pair is not a subdomain: lowercase letters, digits and inner hyphens" >&2; exit 1 ;;
	esac
done
[ "$cms" != "$minimal" ] && [ "$cms" != "$space" ] && [ "$minimal" != "$space" ] || {
	echo "render-env: DEMO_CMS_SUBDOMAIN ($cms), DEMO_CMS_MINIMAL_SUBDOMAIN ($minimal) and" \
		"DEMO_CMS_SPACE_SUBDOMAIN ($space) must differ" >&2
	exit 1
}
# The studio sits beside the other products under DEMO_DOMAIN: never on one of their subdomains.
for taken in id app chat console gw kb grafana mail prometheus; do
	[ "$studio" != "$taken" ] || {
		echo "render-env: DEMO_STUDIO_SUBDOMAIN=$studio is already the edge's ($taken.$DEMO_DOMAIN): choose another" >&2
		exit 1
	}
done
# The CMS's websites (demo.env's DEMO_SITE_DOMAIN): <id>.<sd>, the demo's <cms>.<sd>,
# <minimal>.<sd> and <space>.<sd>. A website runs its owner's script, so the studio refuses to start when <sd> shares
# a registrable domain with <studio>.<d> and id.<d> -- unless it is a public suffix, as localhost
# is, or DEMO_SITE_DOMAIN_SHARED=true accepts the risk. What can be told here, a domain the same as
# the other or under it, stops the render instead; the studio checks the rest (another name under
# the same registrable domain) when it starts.
sd=${DEMO_SITE_DOMAIN:-$d}
site_domain_shared=${DEMO_SITE_DOMAIN_SHARED:-false}
case $site_domain_shared in
	true | false) ;;
	*) echo "render-env: DEMO_SITE_DOMAIN_SHARED=$site_domain_shared is not true or false" >&2; exit 1 ;;
esac
shared=false
if [ "$sd" != localhost ] && [ "$d" != localhost ]; then
	case $sd in "$d" | *."$d") shared=true ;; esac
	case $d in *."$sd") shared=true ;; esac
fi
if [ "$shared" = true ] && [ "$site_domain_shared" != true ]; then
	echo "render-env: the CMS's websites (*.$sd) would share the domain of $studio.$d and id.$d, and the" \
		"studio refuses to start then: set DEMO_SITE_DOMAIN in demo.env to a domain of its own, or" \
		"DEMO_SITE_DOMAIN_SHARED=true to accept that (demo.env.example)" >&2
	exit 1
fi
if [ "$site_domain_shared" = true ] && [ "$d" != localhost ]; then
	# Shared on purpose: only over HTTPS, where the studio's and pacod-id's cookies are __Host-
	# cookies a website's script cannot set.
	if [ -z "${DEMO_TLS_CERT:-}${DEMO_TLS_KEY:-}" ]; then
		echo "render-env: DEMO_SITE_DOMAIN_SHARED=true needs HTTPS (DEMO_TLS_CERT, DEMO_TLS_KEY): over plain HTTP" \
			"the websites could set the cookies the studio and the sign-in receive" >&2
		exit 1
	fi
fi
# On the very domain (localhost's default, or shared on purpose), a website's subdomain must not be
# one the edge already routes: Caddy's own site would hide the website, and the demo websites are
# created at these hosts unchecked.
if [ "$sd" = "$d" ]; then
	for h in "$cms" "$minimal" "$space"; do
		for taken in "$studio" id app chat console gw kb grafana mail prometheus; do
			[ "$h" != "$taken" ] || {
				echo "render-env: the CMS website subdomain $h.$d is already the edge's ($taken):" \
					"choose another DEMO_CMS_SUBDOMAIN, DEMO_CMS_MINIMAL_SUBDOMAIN or DEMO_CMS_SPACE_SUBDOMAIN" >&2
				exit 1
			}
		done
	done
fi
p=$DEMO_HTTP_PORT
hp=${DEMO_HTTPS_PORT:-443}
# HTTPS when demo.env names a certificate (certs/): every address is https://, on DEMO_HTTPS_PORT,
# and plain HTTP on DEMO_HTTP_PORT only redirects. Without one, plain HTTP on DEMO_HTTP_PORT.
cert=${DEMO_TLS_CERT:-}
key=${DEMO_TLS_KEY:-}
if [ -n "$cert$key" ]; then
	for f in "$cert" "$key"; do
		[ -n "$f" ] && [ -f "certs/$f" ] || {
			echo "render-env: DEMO_TLS_CERT and DEMO_TLS_KEY must both name a file in certs/ (see certs/README.md)" >&2
			exit 1
		}
	done
	scheme=https port=$hp default=443
	auto_https=disable_certs
	tls="tls /etc/caddy/tls/$cert /etc/caddy/tls/$key"
else
	scheme=http port=$p default=80
	auto_https=off
	tls=
fi
if [ "$scheme" = https ]; then
	cms_host_cookies=true cms_session_cookie=__Host-studio-session
else
	cms_host_cookies=false cms_session_cookie=JSESSIONID
fi
# The port in addresses: none when it is the scheme's default.
if [ "$port" = "$default" ]; then url_port=; else url_port=$port; fi
url() { printf '%s://%s.%s%s' "$scheme" "$1" "$d" "${url_port:+:$url_port}"; }
site_url() { printf '%s://%s.%s%s' "$scheme" "$1" "$sd" "${url_port:+:$url_port}"; }
origin=$scheme://$d${url_port:+:$url_port}
# Published beyond this machine (DEMO_BIND not a loopback address): what is safe on a laptop is
# closed. The mail viewer (it shows every reset and invitation link), Prometheus and Grafana (a
# viewer can query Prometheus through it) ask for the admin's sign-in, the gateway console's
# /grafana answers 403, and the app's dev tokens (dev-token-<user>, no password) are off.
case $DEMO_BIND in
	127.* | ::1 | localhost) public=false ;;
	*) public=true ;;
esac
admin_password=${DEMO_ADMIN_PASSWORD:-$DEMO_ADMIN_PASSWORD_GENERATED}
# The admin password's bcrypt hash, for the edge's basic auth. Made with the edge's own image and
# kept in .state with the password's SHA-256, so it is made again only when the password changes
# (a new hash on every run would recreate the edge on every `make up`).
password_hash=
private=
admin_cidrs=0.0.0.0/0
if [ "$public" = true ]; then
	admin_cidrs=127.0.0.1/32
	password_hash=$(PW=$admin_password docker run --rm -e PW --user "$(id -u):$(id -g)" \
		-v "$state:/state" caddy:2.11.6-alpine sh -c '
			umask 077
			sum=$(printf %s "$PW" | sha256sum | cut -d" " -f1)
			if [ "$(sed -n 1p /state/admin-password.bcrypt 2>/dev/null)" != "$sum" ]; then
				printf "%s\n%s\n" "$sum" "$(caddy hash-password --plaintext "$PW")" > /state/admin-password.bcrypt
			fi
			sed -n 2p /state/admin-password.bcrypt')
	[ -n "$password_hash" ] || { echo "render-env: could not hash the admin password" >&2; exit 1; }
	private="import demo_private"
fi
# The embedding backend is fixed when the stack is first made: the vectors already stored in
# Qdrant and the app's caches come from it, and the other backend's vectors live in a different
# space (search would quietly return nonsense). auto picks the real model when Docker has the
# memory for the whole stack with it, else the stand-in.
if [ -f "$state/embeddings-backend" ]; then
	frozen=$(cat "$state/embeddings-backend")
	if [ "$EMBEDDINGS_BACKEND" != auto ] && [ "$EMBEDDINGS_BACKEND" != "$frozen" ]; then
		echo "render-env: the stack's vectors were made with EMBEDDINGS_BACKEND=$frozen; switching to" \
			"$EMBEDDINGS_BACKEND needs a fresh stack: make clean, then make up" >&2
		exit 1
	fi
	EMBEDDINGS_BACKEND=$frozen
else
	if [ "$EMBEDDINGS_BACKEND" = auto ]; then
		mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
		# Measured: the whole stack with bge uses about 5 GiB; Docker Desktop's 8 GB setting fits it.
		if [ "$mem" -ge 7500000000 ]; then EMBEDDINGS_BACKEND=bge; else EMBEDDINGS_BACKEND=hash; fi
	fi
	printf '%s\n' "$EMBEDDINGS_BACKEND" > "$state/embeddings-backend"
fi

# ---- every include ----------------------------------------------------------------------------
{
	cat <<EOF
# Generated by scripts/render-env.sh from demo.env, .state/secrets.env and .state/bootstrap.env.
# Do not edit; edit demo.env and run make up.
DEMO_HOME=$home
DEMO_STATE=$state
DEMO_DOMAIN=$d
DEMO_SITE_DOMAIN=$sd
DEMO_HTTP_PORT=$p
DEMO_HTTPS_PORT=$hp
DEMO_BIND=$DEMO_BIND
DEMO_PUBLIC=$public
# How addresses are spelled: the scheme, and the port when it is not the scheme's default.
DEMO_URL_SCHEME=$scheme
DEMO_URL_PORT=$url_port
DEMO_ORIGIN=$origin
DEMO_ADMIN_EMAIL=$DEMO_ADMIN_EMAIL
DEMO_ADMIN_PASSWORD=$admin_password
DEMO_PROFILES=$DEMO_PROFILES
EMBEDDINGS_BACKEND=$EMBEDDINGS_BACKEND
TORCH_NUM_THREADS=$TORCH_NUM_THREADS
CMS_AI_MODEL=$CMS_AI_MODEL
# The log files' directory on this machine (overlays/ap.yml, cms's LOG_DIR_HOST).
DEMO_LOG_DIR=$log_dir
# Every product's log level and format (docs/LOGGING.md); the product files below spell them out.
DEMO_LOG_LEVEL=$log_level
DEMO_LOG_FORMAT=$log_format
# The edge's request logs: the demo's sites (config/Caddyfile), pacod-ap's site (AP_SITE_OPTIONS,
# the demo's access log), and the products' fragments, each with its own variable.
DEMO_CADDY_LOG_FORMAT=$caddy_log_format
ID_CADDY_LOG_FORMAT=$caddy_log_format
PGW_CADDY_LOG_FORMAT=$caddy_log_format
CMS_CADDY_LOG_FORMAT=$caddy_log_format
KB_CADDY_LOG_FORMAT=$caddy_log_format
AP_SITE_OPTIONS='import demo_access_log'

# The public addresses, all through pacod-caddy.
PACOD_ID_ISSUER=$(url id)
AP_ORIGIN=$(url app)
GW_CONSOLE_ORIGIN=$(url console)
GW_API_ORIGIN=$(url gw)
CMS_STUDIO_ORIGIN=$(url "$studio")
CMS_DEMO_ORIGIN=$(site_url "$cms")
CMS_MINIMAL_ORIGIN=$(site_url "$minimal")
CMS_SPACE_ORIGIN=$(site_url "$space")
KB_ORIGIN=$(url kb)
GRAFANA_ORIGIN=$(url grafana)
MAIL_ORIGIN=$(url mail)
PROMETHEUS_ORIGIN=$(url prometheus)

# The products' Caddy fragments (platform/caddy/*.caddy) read their addresses from these.
ID_SITE=$(url id)
AP_SITE=$(url app)
GW_CONSOLE_SITE=$(url console)
GW_API_SITE=$(url gw)
CMS_STUDIO_SITE=$(url "$studio")
CMS_PUBLIC_SITE=$scheme://:$port
# The CMS's hosts, for the edge's network aliases (platform.yml) and make hosts.
CMS_STUDIO_HOST=$studio.$d
CMS_DEMO_HOST=$cms.$sd
CMS_MINIMAL_HOST=$minimal.$sd
CMS_SPACE_HOST=$space.$sd
# The studio's cookies: __Host- ones over HTTPS, which no other host can set (the dev profile the
# demo runs leaves them plain; overlays/cms.yml).
CMS_HOST_COOKIES=$cms_host_cookies
CMS_SESSION_COOKIE=$cms_session_cookie
# Who the CMS's containers run as (overlays/cms.yml): the user who runs make, who owns the
# directories they write under .state and DEMO_LOG_DIR.
DEMO_UID=$(id -u)
DEMO_GID=$(id -g)
KB_SITE=$(url kb)

# The demo's own sites (config/Caddyfile). Quoted where a value has spaces or \$: compose reads
# single-quoted values literally, and the Makefile sources this file.
DEMO_HOME_SITE=$origin
GRAFANA_SITE=$(url grafana)
MAIL_SITE=$(url mail)
PROMETHEUS_SITE=$(url prometheus)
DEMO_AUTO_HTTPS=$auto_https
DEMO_TLS='$tls'
DEMO_PRIVATE='$private'
DEMO_ADMIN_PASSWORD_HASH='$password_hash'
# Who may open the gateway console's /grafana (pacod-gw's gw.caddy).
DEMO_ADMIN_ALLOW_CIDRS=$admin_cidrs
EOF
	echo
	echo "# Secrets (.state/secrets.env)."
	grep -E '^[A-Z0-9_]+=' "$state/secrets.env"
	echo
	echo "# Credentials made by the bootstrap (.state/bootstrap.env)."
	grep -E '^[A-Z0-9_]+=' "$state/bootstrap.env" || true
} > "$state/env/stack.env"

# ---- pacod-id ---------------------------------------------------------------------------------
cat > "$state/env/id.env" <<EOF
PID_BOOTSTRAP_ADMIN_EMAIL=$DEMO_ADMIN_EMAIL
PID_BOOTSTRAP_ADMIN_REALM=pacod
PID_BOOTSTRAP_ADMIN_ROLES=id.admin,pacod.superuser,pacod.admin,gw.platform_admin,cms.platform_admin,kb.admin
PID_EMAIL_FROM_ADDRESS=id@$d
PID_EMAIL_FROM_NAME=pacod (demo)
PID_LOG=$log_level,sqlx=warn
PID_LOG_FORMAT=$log_format
EOF

cat > "$state/env/id.container.env" <<EOF
PID_KEK=$PID_KEK
PID_TOKEN_PEPPER=$PID_TOKEN_PEPPER
PID_BOOTSTRAP_ADMIN_PASSWORD=$admin_password
PACOD_ID_ISSUER=$(url id)
AP_ORIGIN=$(url app)
GW_CONSOLE_ORIGIN=$(url console)
CMS_STUDIO_ORIGIN=$(url "$studio")
KB_ORIGIN=$(url kb)
PACOD_WEB_CLIENT_SECRET=$PACOD_WEB_CLIENT_SECRET
PACOD_API_CLIENT_SECRET=$PACOD_API_CLIENT_SECRET
PGW_UI_CLIENT_SECRET=$PGW_UI_CLIENT_SECRET
PGW_ID_CLIENT_SECRET=$PGW_ID_CLIENT_SECRET
TESTER_ID_CLIENT_SECRET=$TESTER_ID_CLIENT_SECRET
CMS_ID_STUDIO_SECRET=$CMS_ID_STUDIO_SECRET
CMS_ID_SERVICE_SECRET=$CMS_ID_SERVICE_SECRET
KB_ID_UI_SECRET=$KB_ID_UI_SECRET
KB_ID_SERVICE_SECRET=$KB_ID_SERVICE_SECRET
EOF

# ---- pacod-gw ---------------------------------------------------------------------------------
cat > "$state/env/gw.env" <<EOF
PGW_AUTH=oidc
RUST_LOG=$log_level
PGW_LOG_FORMAT=$log_format
TESTER_LOG_LEVEL=$log_level
EOF

# ---- pacod-cms --------------------------------------------------------------------------------
cat > "$state/env/cms.env" <<EOF
HTTP_PORT=$p
SPRING_PROFILE=dev
ID_ISSUER=$(url id)
STUDIO_ORIGIN=$(url "$studio")
PACOD_SITE_DOMAIN=$sd
PACOD_SITE_DOMAIN_SHARED=$site_domain_shared
DEMO_WEBSITES=true
DEMO_HOSTS=$cms.$sd
DEMO_MINIMAL_HOSTS=$minimal.$sd
DEMO_SPACE_HOSTS=$space.$sd
MONGO_DATA_DIR=cms-mongo-data
MONGO_BACKUP_DIR=$state/cms/backup
STORAGE_HOST=$state/cms/storage
LOG_DIR_HOST=$log_dir/cms
APP_LOG_LEVEL=$log_level
APP_LOG_FORMAT=$log_format
PACOD_GW_API_KEY=${CMS_GW_KEY:-}
PACOD_AI_MODEL=$CMS_AI_MODEL
PACOD_ID_PROVISIONER_TOKEN=${PACOD_ID_PROVISIONER_TOKEN:-}
EOF

# ---- pacod-kb ---------------------------------------------------------------------------------
cat > "$state/env/kb.env" <<EOF
QDRANT_MODE=server
KB_STORAGE_BACKEND=database
KB_ID_ISSUER=$(url id)
KB_UI_ORIGIN=$(url kb)
UI_FORWARDED_ALLOW_IPS=*
KB_DATA=kb-data
KB_MODEL_CACHE=pacod-hf-cache
EMBEDDINGS_URL=http://pacod-embeddings:8080
PACOD_GW_API_KEY=${KB_GW_KEY:-}
LLM_CONVERTER_ENABLED=false
LLM_CONVERTER_MODEL=$KB_LLM_MODEL
CONTENT_SECURITY_LLM_MODEL=$KB_LLM_MODEL
LOG_LEVEL=$log_level
LOG_FORMAT=$log_format
EOF

# ---- pacod-ap ---------------------------------------------------------------------------------
# Compose's side: the names pacod-ap's compose files interpolate.
cat > "$state/env/ap.env" <<EOF
POSTGRES_USER=pacod
POSTGRES_DB=pacod
POSTGRES_PASSWORD=$AP_DB_PASSWORD
GATEWAY_CONSOLE_URL=$(url console)
PACOD_SITE_URL=$(url app)
LOCAL_PACOD_SITE_URL=$(url app)
LOCAL_PACOD_SITE_INDEXING_ENABLED=false
LOCAL_CRAWLER_VERIFY_ENABLED=false
LOCAL_EMBEDDINGS_BACKEND=$EMBEDDINGS_BACKEND
# The frontend's (its compose environment is a fixed list) and the services' without an env_file.
FRONTEND_LOG_LEVEL=$log_level
FRONTEND_LOG_FORMAT=$log_format
APP_LOG_LEVEL=$log_level_upper
APP_LOG_FORMAT=$log_format
EOF

# The containers' side: the app's own .env.example as the base (every setting it documents, with
# its default), then what the demo changes. In an env file the last occurrence of a name wins.
{
	echo "# Generated by scripts/render-env.sh: ../pacod-ap/pacod-api/.env.example, then the demo's values."
	grep -E '^[A-Z0-9_]+=' ../pacod-ap/pacod-api/.env.example
	cat <<EOF

# ---- pacod-demo ----
POSTGRES_USER=pacod
POSTGRES_DB=pacod
POSTGRES_PASSWORD=$AP_DB_PASSWORD
APP_ENV=development
# Dev tokens (dev-token-<user>) let \`make verify\` run a chat turn without a browser. They need no
# password, so only a demo kept on this machine (DEMO_BIND a loopback address) has them.
AUTH_ALLOW_DEV_TOKENS=$([ "$public" = true ] && echo false || echo true)
APP_LOG_LEVEL=$log_level_upper
APP_LOG_FORMAT=$log_format
APP_SECRETS_KEY=$APP_SECRETS_KEY
AGENT_PRIMARY_MODEL=$AP_PRIMARY_MODEL
AGENT_CLASSIFIER_MODEL=$AP_CLASSIFIER_MODEL
TAVILY_API_KEY=
LOOP_SCHEDULE_ENABLED=false
LOOP_VERIFICATION_ENABLED=false
SMTP_HOST=pacod-mailpit
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=false
SMTP_USE_SSL=false
EMAIL_FROM_ADDRESS=app@$d
APP_PUBLIC_BASE_URL=$(url app)
SESSION_SECRET=$SESSION_SECRET
PACOD_SITE_URL=$(url app)
PACOD_SITE_INDEXING_ENABLED=false
CRAWLER_VERIFY_ENABLED=false
CRAWLER_INGEST_TOKEN=$CRAWLER_INGEST_TOKEN
MCP_SHARED_SECRET=$MCP_SHARED_SECRET
EOF
} > "$state/env/ap.container.env"

# Compose profiles: the app's frontend always; its CPU ingestion on request.
profiles=ui
case ",$DEMO_PROFILES," in *,ingestion,*) profiles="$profiles,cpu" ;; esac
printf '%s\n' "$profiles" > "$state/profiles"

# ---- the bootstrap (pacod-ap's scripts, run by scripts/bootstrap.py) ----------------------------
cat > "$state/env/bootstrap-id.env" <<EOF
PID_BOOTSTRAP_ADMIN_EMAIL=$DEMO_ADMIN_EMAIL
PID_BOOTSTRAP_ADMIN_PASSWORD=$admin_password
PACOD_WEB_CLIENT_SECRET=$PACOD_WEB_CLIENT_SECRET
PACOD_API_CLIENT_SECRET=$PACOD_API_CLIENT_SECRET
EOF

cat > "$state/env/providers.env" <<EOF
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
OPENAI_API_KEY=$OPENAI_API_KEY
GEMINI_API_KEY=$GEMINI_API_KEY
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY
GLM_API_KEY=$GLM_API_KEY
EOF

echo "render-env: wrote .state/env/ (issuer $(url id))"
