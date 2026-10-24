#!/bin/sh
# The nightly reset: `make reset` every day at RESET_AT (HH:MM) in the time zone TZ, which the
# Makefile's scheduler target sets from demo.env's DEMO_RESET_TIME and DEMO_RESET_TIMEZONE. Runs in
# the container pacod-demo-reset, outside the compose project (a reset stops the project, and would
# stop itself), as the user who runs make, with the demo's checkout at its own path: make, compose
# and every bind mount they hand to Docker then mean what they mean on the host.
#
# Read its log with `docker logs pacod-demo-reset`.
set -eu

say() { echo "$(date '+%Y-%m-%d %H:%M %Z') reset: $*"; }

[ -f "/usr/share/zoneinfo/$TZ" ] || { say "unknown time zone $TZ (DEMO_RESET_TIMEZONE)"; exit 1; }

# The next RESET_AT, today's if it is still ahead. A time the clock skips (a daylight-saving change)
# is taken an hour later.
next() {
	for day in today tomorrow; do
		t=$(date -d "$day $RESET_AT" +%s 2>/dev/null || date -d "$day $RESET_AT + 1 hour" +%s)
		if [ "$t" -gt "$(date +%s)" ]; then echo "$t"; return; fi
	done
}

say "every day at $RESET_AT $TZ"
while :; do
	at=$(next)
	say "next reset at $(date -d "@$at" '+%Y-%m-%d %H:%M %Z')"
	# In steps, so that a machine that slept through the time notices when it wakes.
	while now=$(date +%s); [ "$now" -lt "$at" ]; do
		left=$((at - now))
		sleep $((left < 300 ? left : 300))
	done
	# A laptop that wakes long after the time: no reset in the middle of someone's work.
	if [ $(($(date +%s) - at)) -gt 1800 ]; then
		say "skipped: this machine was asleep at $RESET_AT"
	elif [ "$(docker inspect -f '{{.State.Running}}' pacod-postgres 2>/dev/null)" != true ]; then
		say "skipped: the stack is not running"
	elif make --no-print-directory reset; then
		say "done: the demo is back to its baseline"
	else
		say "FAILED (exit $?): make reset by hand shows why"
	fi
done
