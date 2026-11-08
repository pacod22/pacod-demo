#!/bin/sh
# pacod-ap's starter agent on pacod-broker (demo.env's AP_BROKER_OFFER: platform, public, or empty
# for not offered), written into the starter's published version by pacod-ap's own command:
#
#   python -m scripts.agent_editor offer starter --offer platform|public|none --summary …
#
# which publishes a release that is the published version with only its offer changed (and the
# same change in the draft), handing the agent to the agent editor first if it is not there yet,
# and changes nothing when the published version already says so. ap-admin-api then announces the
# agent at agents.<domain> within its refresh (60 s), and the broker lists it (make verify checks).
#
#   sh scripts/broker-offer.sh    run by make bootstrap (so by make up and make reset), after the
#                                 app's services were recreated with the broker's token
#
# demo.env owns the offer: an empty value withdraws it. Without ap-api it says so and changes
# nothing; a failure is reported and left to make verify, never stopping the bootstrap.
set -eu
cd "$(dirname "$0")/.."

offer=$(sed -n 's/^AP_BROKER_OFFER=//p' .state/env/stack.env)
summary="The help desk of Nimbus Notes, a note-taking service: plans, sync, sharing and billing."

# Both must run (demo.env's DEMO_PRODUCTS). Without the broker the offer is left as it is rather
# than withdrawn: each change publishes a release of the starter.
products=" $(cat .state/products 2>/dev/null || echo ap broker) "
case $products in *" ap "*) ;; *) exit 0 ;; esac
case $products in
	*" broker "*) ;;
	*) echo "broker-offer: pacod-broker does not run (DEMO_PRODUCTS); the starter's offer is left as it is"
		exit 0 ;;
esac

if ! docker ps --format '{{.Names}}' | grep -qx ap-api; then
	echo "broker-offer: ap-api is not running; the starter's offer on pacod-broker is left as it is"
	exit 0
fi

if ! docker exec ap-api python -m scripts.agent_editor offer starter --offer "${offer:-none}" \
	--summary "$summary"; then
	echo "broker-offer: pacod-ap did not take the offer (an ap-api image older than its offer" \
		"command: make redeploy S=\"ap-api ap-admin-api\"); make verify reports what is announced" >&2
fi
