#!/bin/sh
# The demo's original state, and the way back to it: `make baseline`, `make reset` and the nightly
# reset (scripts/reset-scheduler.sh).
#
#   sh scripts/baseline.sh save       the stack's data as it is now becomes the baseline
#   sh scripts/baseline.sh restore    the stack's data goes back to the baseline
#   sh scripts/baseline.sh wait-cms   wait until the CMS has written its three demo websites
#
# The baseline is a copy of every data volume of the stack, of the CMS's files (.state/cms/storage)
# and of the bootstrap's credentials (.state/bootstrap.env, which belong to the copied databases),
# kept in the Docker volume pacod-demo-baseline: labelled with the project, so `make clean` deletes
# it with the rest. .state/baseline, its date, says that a save finished: render-env.sh turns the
# nightly reset on only with it. Saving and restoring stop every service but the edge (the home page stays up; the
# products answer 502 meanwhile), copy while nothing writes the databases' files, and start them
# again.
#
# Not copied, because they are caches or records rather than the demo's state: the models
# (*-hf-cache, *-model-cache), the edge's own data, Prometheus's metrics and the old log volumes.
# A volume made after the baseline (an optional component switched on later) is left as it is.
set -eu
cd "$(dirname "$0")/.."
state=$(pwd)/.state

PROJECT=pacod-demo
BASELINE=pacod-demo-baseline
IMAGE=pacod-demo-reset:dev
# The edge stays up through a save or restore: it holds nothing that is copied.
EDGE=pacod-caddy

say() { echo "baseline: $*"; }
die() { echo "baseline: $*" >&2; exit 1; }

# The Makefile's COMPOSE.
compose() {
	COMPOSE_PROFILES=$(cat "$state/profiles" 2>/dev/null || echo ui) \
		docker compose --project-directory . -f compose.yml "$@"
}

copied() {
	case $1 in
		"$BASELINE" | *-hf-cache | *-model-cache | "${PROJECT}_pacod-caddy-"* | "${PROJECT}_pacod-prometheus-data" | *-logs) return 1 ;;
	esac
}

running() { [ "$(docker inspect -f '{{.State.Running}}' pacod-postgres 2>/dev/null)" = true ]; }

# One save or restore at a time: the nightly one and a `make reset` by hand must not meet.
lock() {
	mkdir "$state/baseline.lock" 2>/dev/null ||
		die "another save or restore is running (if none is, remove .state/baseline.lock)"
	trap 'finish $?' EXIT
	trap 'exit 130' INT TERM
}

stopped=false
stop() {
	say "stopping the stack (the home page stays up)"
	# shellcheck disable=SC2046
	compose stop $(compose config --services | grep -vx "$EDGE")
	stopped=true
}

start() {
	# The env files again: a restore may have brought back other bootstrap credentials.
	sh scripts/render-env.sh >/dev/null
	say "starting the stack"
	compose up -d --wait --wait-timeout 2400
	stopped=false
}

# Whatever failed, a stopped stack is started again: a demo that is down is worse than one whose
# reset failed. The failure is still the exit status.
finish() {
	if [ "$stopped" = true ]; then
		say "failed; starting the stack again" >&2
		start || true
	fi
	rmdir "$state/baseline.lock" 2>/dev/null || true
	exit "$1"
}

save() {
	running || die "the stack is not running: make up first"
	docker image inspect "$IMAGE" >/dev/null 2>&1 || die "no $IMAGE image (make baseline builds it)"
	lock
	vols=$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" | while read -r v; do
		if copied "$v"; then echo "$v"; fi
	done)
	[ -n "$vols" ] || die "the stack has no data volumes"
	docker volume inspect "$BASELINE" >/dev/null 2>&1 ||
		docker volume create --label "com.docker.compose.project=$PROJECT" "$BASELINE" >/dev/null

	taken=$(date -u '+%Y-%m-%d %H:%M UTC')
	stop
	set --
	for v in $vols; do set -- "$@" -v "$v:/volumes/$v:ro"; done
	say "copying $(echo "$vols" | wc -l | tr -d ' ') volumes and the CMS's files"
	docker run --rm --network none "$@" -v "$state/cms/storage:/volumes/cms-storage:ro" \
		-v "$state:/state:ro" -v "$BASELINE:/baseline" -e TAKEN="$taken" "$IMAGE" sh -euc '
			rm -rf /baseline/new /baseline/old
			mkdir /baseline/new
			for d in /volumes/*; do
				tar --numeric-owner -I "zstd -T0 -q" -C "$d" -cf "/baseline/new/${d##*/}.tar.zst" .
			done
			ls /volumes > /baseline/new/volumes
			cp /state/bootstrap.env /baseline/new/bootstrap.env
			echo "$TAKEN" > /baseline/new/taken
			if [ -d /baseline/current ]; then mv /baseline/current /baseline/old; fi
			mv /baseline/new /baseline/current
			rm -rf /baseline/old
			echo "baseline: $(du -sh /baseline/current | cut -f1) kept in the volume pacod-demo-baseline"'
	echo "$taken" > "$state/baseline"
	start
	say "saved: make reset, and every nightly reset, returns the demo to this state"
}

restore() {
	[ -f "$state/baseline" ] && docker volume inspect "$BASELINE" >/dev/null 2>&1 ||
		die "there is no baseline: make baseline"
	running || die "the stack is not running: make up first"
	docker image inspect "$IMAGE" >/dev/null 2>&1 || die "no $IMAGE image (make reset builds it)"
	saved=$(docker run --rm --network none -v "$BASELINE:/baseline:ro" "$IMAGE" \
		sh -c 'cat /baseline/current/taken /baseline/current/volumes') ||
		die "the baseline is incomplete: make baseline"
	taken=$(echo "$saved" | sed -n 1p)
	lock

	stop
	set --
	for v in $(echo "$saved" | sed 1d); do
		if [ "$v" = cms-storage ]; then
			set -- "$@" -v "$state/cms/storage:/volumes/cms-storage"
		elif docker volume inspect "$v" >/dev/null 2>&1; then
			set -- "$@" -v "$v:/volumes/$v"
		fi
	done
	say "restoring the baseline of $taken"
	# As root, so every file gets back its owner (PostgreSQL's, MongoDB's, ...); the volume is
	# emptied first, so nothing made since the baseline is left over.
	docker run --rm --network none "$@" -v "$state:/state" -v "$BASELINE:/baseline:ro" \
		-e OWNER="$(id -u):$(id -g)" "$IMAGE" sh -euc '
			for d in /volumes/*; do
				find "$d" -mindepth 1 -delete
				tar --numeric-owner -I zstd -C "$d" -xpf "/baseline/current/${d##*/}.tar.zst"
			done
			cp /baseline/current/bootstrap.env /state/bootstrap.env
			chown "$OWNER" /state/bootstrap.env
			chmod 600 /state/bootstrap.env'
	start
	say "restored the baseline of $taken"
}

# The CMS writes its demo websites after its health check passes, and a copy taken meanwhile would
# keep a half-written one for good: wait until each has its articles (they are written last).
wait_cms() {
	say "waiting for the CMS's demo websites"
	i=0
	while :; do
		n=$(docker exec cms-mongo sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" \
			-p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "
				[\"demo\", \"minimal\", \"space\"].filter(s =>
					db.getSiblingDB(\"site_\" + s).article.estimatedDocumentCount() > 0).length"' \
			2>/dev/null || echo 0)
		[ "$n" = 3 ] && break
		i=$((i + 1))
		if [ "$i" -ge 120 ]; then
			echo "baseline: after 10 minutes the CMS has $n of its 3 demo websites with articles; no baseline" \
				"taken. Fill them (README: The CMS's demo websites), then make baseline" >&2
			exit 1
		fi
		sleep 5
	done
	# The demo's bookings follow the articles.
	sleep 15
}

case ${1:-} in
	save) save ;;
	restore) restore ;;
	wait-cms) wait_cms ;;
	*) echo "usage: sh scripts/baseline.sh save|restore|wait-cms" >&2; exit 2 ;;
esac
