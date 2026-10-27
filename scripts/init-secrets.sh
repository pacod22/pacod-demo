#!/bin/sh
# Generates the demo's secrets into .state/secrets.env: every value that is missing, once.
# Existing values are never changed, because the databases and pacod-id's sealed keys depend on
# them. Values are never printed. Run by `make up` inside the pacod-demo-tools image (openssl).
#
# Lengths follow the strictest consumer: pacod-gw's preflight (deploy/scripts/preflight.sh)
# wants base64 keys of 32 bytes, peppers and tokens of 16+ characters, client and session
# secrets of 32+, URL-safe database passwords of 16+.
set -eu
cd "$(dirname "$0")/.."

out=.state/secrets.env
mkdir -p .state
[ -f "$out" ] || : > "$out"
chmod 600 "$out"

hex() { openssl rand -hex "$1"; }                       # 2n characters, URL-safe
b64() { openssl rand -base64 "$1" | tr -d '\n'; }       # n bytes, standard base64
fernet() { openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'; }
alnum() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c "1-$1"; }

added=0
need() { # need NAME GENERATOR [ARG]
	if ! grep -q "^$1=" "$out"; then
		name=$1
		shift
		printf '%s=%s\n' "$name" "$("$@")" >> "$out"
		added=$((added + 1))
	fi
}

# The shared PostgreSQL (pacod-postgres): its superuser and one owner role per product.
need PG_SUPERUSER_PASSWORD hex 16
need PID_DB_PASSWORD hex 16
need GW_DB_PASSWORD hex 16
need AP_DB_PASSWORD hex 16
need KB_DATABASE_PASSWORD hex 16
need BENCH_DB_PASSWORD hex 16

# pacod-id: the key-encryption key and the token pepper.
need PID_KEK b64 32
need PID_TOKEN_PEPPER b64 32

# Every client's secret in the realm (config/pacod-id.toml), and the gateway console's session.
need PACOD_WEB_CLIENT_SECRET hex 32
need PACOD_API_CLIENT_SECRET hex 32
need PGW_UI_CLIENT_SECRET hex 32
need PGW_ID_CLIENT_SECRET hex 32
need TESTER_ID_CLIENT_SECRET hex 32
need CMS_ID_STUDIO_SECRET hex 32
need CMS_ID_SERVICE_SECRET hex 32
need KB_ID_UI_SECRET hex 32
need KB_ID_SERVICE_SECRET hex 32
need BENCH_ID_UI_SECRET hex 32
need BENCH_ID_SERVICE_SECRET hex 32
need PGW_UI_SESSION_SECRET hex 32
need BENCH_UI_SESSION_SECRET hex 32
# pacod-benchmark: the key the tokens of gateways added in its UI are sealed under.
need BENCH_KEK b64 32

# pacod-gw.
need PGW_KEK b64 32
need PGW_KEY_PEPPER hex 24
need PGW_INTERNAL_TOKEN hex 24
need CLICKHOUSE_PASSWORD hex 16
# Valkey, the response cache's store (gw-valkey).
need PGW_VALKEY_PASSWORD hex 24
# The load agent's token, shared by the gateway's tester and pacod-benchmark.
need TESTER_LOAD_TOKEN hex 32
need GRAFANA_ADMIN_PASSWORD alnum 24

# pacod-ap.
need APP_SECRETS_KEY fernet
need SESSION_SECRET hex 32
need CRAWLER_INGEST_TOKEN hex 24
need MCP_SHARED_SECRET hex 24

# pacod-cms.
need MONGO_ROOT_PASSWORD hex 16
need MONGO_PASSWORD hex 16
need PACOD_PREVIEW_SECRET hex 32
need METRICS_VISITOR_SALT hex 24

# pacod-kb.
need PIPELINE_API_SECRET hex 32
need UI_SESSION_SECRET hex 32

# The first administrator's password, when demo.env leaves DEMO_ADMIN_PASSWORD empty.
need DEMO_ADMIN_PASSWORD_GENERATED alnum 24

if [ "$added" -gt 0 ]; then
	echo "secrets: generated $added value(s) in $out"
else
	echo "secrets: $out is complete"
fi
