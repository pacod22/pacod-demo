#!/bin/sh
# Builds the stack's images, one service at a time. Compose builds all it is given at once, and
# COMPOSE_PARALLEL_LIMIT does not change that (measured with Compose 5.6: three builds at limit 1
# took as long as one). On a machine that also runs the stack, every image at once runs it out of
# memory, and the kernel kills compose: make then stops with Error 255.
#
#   sh scripts/build.sh                  every service: make build
#   sh scripts/build.sh <service>...     those services: make redeploy
#   sh scripts/build.sh --missing        only images that are not here: make up, which never
#                                        rebuilds an image it finds
set -eu
cd "$(dirname "$0")/.."

COMPOSE_PROFILES=$(cat .state/profiles 2>/dev/null || echo ui)
export COMPOSE_PROFILES
compose() { docker compose --project-directory . -f compose.yml "$@"; }

missing=
if [ "${1:-}" = --missing ]; then
	missing=1
	shift
fi

# Images already looked at. config --images names a service's dependencies too, and config
# --services lists dependencies first, so what is new here is the service's own image.
seen=
for service in ${*:-$(compose config --services)}; do
	if [ -n "$missing" ]; then
		absent=
		for image in $(compose config --images "$service"); do
			case " $seen " in
			*" $image "*) ;;
			*)
				seen="$seen $image"
				docker image inspect "$image" >/dev/null 2>&1 || absent=1
				;;
			esac
		done
		[ -n "$absent" ] || continue
	fi
	compose build "$service"
done
