#!/bin/sh
# Prints 1 when the running pacod-ap names each gateway call's agent in x-gw-agent-id (its client,
# app/services/gateway_agents.py in ap-api) and registers its profiles as the gateway's agents
# (admin_api/services/gateway_agent_sync.py in ap-admin-api); 0 otherwise, also when either
# container is not running.
#
# make bootstrap passes it to scripts/bootstrap.py as AP_GW_AGENTS: an older pacod-ap image puts
# the agent in the body only, which a key bound to agents refuses (403), so its keys are bound only
# once both images are new enough. Finding the modules does not run them.
has() {
	docker exec "$1" python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('$2') is None)" \
		>/dev/null 2>&1
}
if has ap-api app.services.gateway_agents && has ap-admin-api admin_api.services.gateway_agent_sync; then
	echo 1
else
	echo 0
fi
