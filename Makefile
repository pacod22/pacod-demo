# The pacod full stack on one machine. Edit demo.env (and demo.local.env for the password and
# provider keys, never committed), then:
#
#   make up        build what is missing, start everything, bootstrap, print the addresses
#   make verify    check that the stack works as one platform
#   make reset     undo every change: back to the baseline (also every night, DEMO_RESET_TIME)
#   make down      stop; the data stays
#   make start     start it again quickly (no build, bootstrap or seed); redeploy S=… one service
#   make clean     stop and delete everything the demo made (data, state; IMAGES=1: images too;
#                  the downloaded models only with MODELS=1)
#
# Needs Docker (with Compose 2.24 or newer) and make; everything else runs in containers.

SHELL := /bin/sh
.DEFAULT_GOAL := help

TOOLS := pacod-demo-tools:dev
# Playwright with its browsers, for the home page's screenshots (make shots).
SHOTS := pacod-demo-shots:dev
# Docker's CLI, make and tar: the nightly reset's scheduler and the baseline's archiver.
RESET := pacod-demo-reset:dev
# The compose project: the six repositories' own compose files plus the shared layer.
COMPOSE = COMPOSE_PROFILES=$$(cat .state/profiles 2>/dev/null || echo ui) docker compose \
	--project-directory . -f compose.yml
# The tools image on pacod-net, and on pacod-admin-net for the gateway's management API, with
# the demo's state and the repositories it reuses.
TOOLS_RUN = docker run --rm --network pacod-net --network pacod-admin-net --user $$(id -u):$$(id -g) \
	-v "$(CURDIR):/work" -v "$(CURDIR)/.state:/state" \
	-v "$(CURDIR)/../pacod-ap:/src/pacod-ap:ro" -v "$(CURDIR)/../pacod-cms:/src/pacod-cms:ro" \
	--env-file .state/env/stack.env $(TOOLS)
# The app's services that read the bootstrap's credentials (and the products' that read theirs).
RECREATE := ap-api ap-admin-api ap-improvement-loop-worker ap-newsletter-worker cms-studio kb-pipeline \
	bench-api
# A one-off CMS studio container running one of its commands (pacod-cms's STUDIO_CLI): no web
# server, bootstrap, pacod-id or jobs.
CMS_CLI = $(COMPOSE) run --rm --no-deps -T cms-studio --spring.main.web-application-type=none \
	--pacod.bootstrap.enabled=false --pacod.id.enabled=false --pacod.jobs.runner.enabled=false \
	--logging.level.root=WARN --logging.level.io.pacod.cms.studio.site=INFO --app.log.dir=/tmp/logs
WAIT := --wait --wait-timeout 2400
# The models of pacod-embeddings, an external volume shared with pacod-ap's own stacks.
HF_CACHE := pacod-hf-cache
HF_CACHE_OLD := pacod-demo_pacod-hf-cache

.PHONY: help up run start redeploy restart verify down stop clean remove build status ps logs urls bootstrap \
	preflight network tools init render seed hosts config shots cms-space cms-seed \
	baseline first-baseline reset scheduler reset-image google

help:
	@echo "pacod full-stack demo (configuration: demo.env)"
	@echo ""
	@echo "  make up        start the whole stack (the first run builds every image: 30+ minutes)"
	@echo "  make verify    check the running stack end to end"
	@echo "  make reset     undo every change: back to the baseline (also every night at DEMO_RESET_TIME)"
	@echo "  make down      stop it; data is kept"
	@echo "  make clean     remove it: containers, volumes, generated state (IMAGES=1: images too;"
	@echo "                 the downloaded models are kept, MODELS=1: those too)"
	@echo ""
	@echo "  make start     start a stack that has run before: no build, bootstrap or seed"
	@echo "  make redeploy S=\"<service>...\"   rebuild and recreate only those services"
	@echo "  make restart S=\"<service>...\"    restart only those services"
	@echo ""
	@echo "  make urls      the addresses and the admin's sign-in"
	@echo "  make status    the containers and their health"
	@echo "  make logs S=<service>   follow one service's log (all without S)"
	@echo "  make build     rebuild the images from the six checkouts"
	@echo "  make bootstrap redo the cross-product credentials (after adding a provider key)"
	@echo "  make baseline  keep the data as it is now as the state every reset returns to"
	@echo "  make cms-space create the CMS's Starward website (magazine design) on a stack without it"
	@echo "  make cms-seed SITE=<id> [DEMO=space]   the demo content into a CMS website without articles"
	@echo "  make google    write Starward's Google settings (demo.env) into the CMS again"
	@echo "  make hosts     /etc/hosts lines (Safari and *.localhost, or a domain before its DNS)"
	@echo "  make shots     retake the home page's screenshots from the running stack"

# ---- run ------------------------------------------------------------------------------------------

up: preflight network tools init render
	@echo "up: starting the stack (the first start builds the images and downloads models)"
	$(COMPOSE) up -d --remove-orphans $(WAIT)
	@$(MAKE) --no-print-directory bootstrap
	@$(MAKE) --no-print-directory seed
	@$(MAKE) --no-print-directory first-baseline
	@$(MAKE) --no-print-directory google
	@$(MAKE) --no-print-directory scheduler
	@$(MAKE) --no-print-directory urls

run: up

# ---- re-deploy ------------------------------------------------------------------------------------
# Faster than `make up` on a stack that has run before. start: every service from the images there
# are (nothing built, no bootstrap, seed or baseline). redeploy S="<services>": rebuild those images
# and recreate only those containers (--no-deps: compose would otherwise rebuild and recreate what
# they depend on as well, pacod-embeddings among them, which then loads its models again).
# restart S="<services>": restart them as they are.
start: network render
	@test -f .state/secrets.env || { echo "start: no stack yet; run make up" >&2; exit 1; }
	$(COMPOSE) up -d --remove-orphans --no-build $(WAIT)
	@$(MAKE) --no-print-directory urls

redeploy: network render
	@test -n "$(S)" || { echo 'usage: make redeploy S="<service> [<service>...]"' >&2; exit 1; }
	$(COMPOSE) up -d --no-deps --build $(WAIT) $(S)

restart: render
	@test -n "$(S)" || { echo 'usage: make restart S="<service> [<service>...]"' >&2; exit 1; }
	$(COMPOSE) restart $(S)

# The machine's configuration: git ignores it; the first run copies the committed template.
demo.env:
	@cp demo.env.example $@
	@echo "demo.env: copied from demo.env.example; edit it, then run make again" >&2; exit 1

preflight: demo.env
	@set -a; . ./demo.env; [ ! -f demo.local.env ] || . ./demo.local.env; set +a; sh scripts/preflight.sh

# pacod-admin-net carries the listeners only servers reach (the gateway's management API,
# pacod-id's internal one) and must be internal; compose never checks that, so this does.
network:
	@docker network inspect pacod-net >/dev/null 2>&1 || docker network create pacod-net >/dev/null
	@docker network inspect pacod-admin-net >/dev/null 2>&1 || docker network create --internal pacod-admin-net >/dev/null
	@test "$$(docker network inspect -f '{{.Internal}}' pacod-admin-net)" = true || \
		{ echo "pacod-admin-net is not internal: remove it (docker network rm pacod-admin-net) and run make network" >&2; exit 1; }
	@# The models (platform.yml): external, so no teardown of the project deletes them. The first
	@# time, the models of the project's own volume it replaces are copied over.
	@docker volume inspect $(HF_CACHE) >/dev/null 2>&1 || { \
		docker volume create $(HF_CACHE) >/dev/null; \
		if docker volume inspect $(HF_CACHE_OLD) >/dev/null 2>&1; then \
			echo "network: copying the models from $(HF_CACHE_OLD) (make clean removes it)"; \
			docker run --rm -v $(HF_CACHE_OLD):/from:ro -v $(HF_CACHE):/to alpine:3.20 cp -a /from/. /to/ || \
				{ docker volume rm $(HF_CACHE) >/dev/null; echo "network: the copy failed; run it again" >&2; exit 1; }; \
		fi; }

tools:
	@docker image inspect $(TOOLS) >/dev/null 2>&1 || docker build -q -t $(TOOLS) tools/ >/dev/null

# The secrets, once. Without .state/secrets.env but with the demo's volumes, the data depends
# on secrets that are gone: refuse rather than make new ones.
init: tools
	@if [ ! -f .state/secrets.env ] && [ -n "$$(docker volume ls -q --filter label=com.docker.compose.project=pacod-demo)" ]; then \
		echo "init: .state/secrets.env is missing but the demo's volumes exist; run make clean first" >&2; exit 1; fi
	@mkdir -p .state
	@# A stack with no data yet: the first make up keeps what it makes as the baseline (first-baseline).
	@if [ -z "$$(docker volume ls -q --filter label=com.docker.compose.project=pacod-demo)" ]; then \
		touch .state/baseline-pending; fi
	@docker run --rm --user $$(id -u):$$(id -g) -v "$(CURDIR):/work" $(TOOLS) sh scripts/init-secrets.sh

render: demo.env
	@sh scripts/render-env.sh

# The cross-product credentials (gateway keys, service tokens), then the services that read them
# are recreated with the new values (compose recreates only those whose settings changed).
bootstrap: network tools render
	@$(TOOLS_RUN) python3 -I /work/scripts/bootstrap.py
	@sh scripts/render-env.sh
	$(COMPOSE) up -d --no-deps $(WAIT) $(RECREATE)

# The app's own seed (pacod-ap's bootstrap-local): tables, the starter agent's facts, dev users,
# and its corpus when its ingestion runs. Each step is idempotent.
seed:
	@echo "seed: the app's database and starter agent"
	@docker exec ap-api python -m scripts.init_db >/dev/null
	@docker exec ap-api python -m scripts.seed_reference_facts --profile starter >/dev/null
	@docker exec ap-api python -m scripts.seed_dev_users >/dev/null
	@if docker ps --format '{{.Names}}' | grep -qx ap-ingestion-cpu; then \
		docker exec ap-ingestion-cpu python -m scripts.ingest_corpus --profile starter >/dev/null; fi

verify:
	@sh scripts/verify.sh

# ---- the nightly reset ------------------------------------------------------------------------------
#
# The baseline is the stack's data at one moment (scripts/baseline.sh): the first make up of a fresh
# stack keeps what it made, `make baseline` what there is now. `make reset` returns every product to
# it, then runs the bootstrap again, which applies what demo.local.env has gained since (a provider
# key). The scheduler does `make reset` every day at DEMO_RESET_TIME in DEMO_RESET_TIMEZONE
# (demo.env); the home page says when. Both start the stack again, so both make the secrets first,
# as make up does: a repository pulled since the last make up may need one the demo has not made
# yet (pacod-gw's PGW_VALKEY_PASSWORD), and its preflight would stop the start without it.

reset-image:
	@docker image inspect $(RESET) >/dev/null 2>&1 || docker build -q -t $(RESET) tools/reset/ >/dev/null

baseline: init render reset-image
	@sh scripts/baseline.sh save
	@$(MAKE) --no-print-directory scheduler

# Once, on a stack whose data this make up made, after the CMS has written its demo websites.
first-baseline:
	@if [ -f .state/baseline-pending ] && sh scripts/baseline.sh wait-cms; then \
		$(MAKE) --no-print-directory baseline && rm -f .state/baseline-pending; fi

reset: init render reset-image
	@sh scripts/baseline.sh restore
	@$(MAKE) --no-print-directory bootstrap
	@$(MAKE) --no-print-directory google

# The scheduler's container, made again on every make up (the time may have changed): outside the
# compose project, since a reset stops the project; as the user who runs make, with the socket's
# group; and with the checkout (and the log directory, which may lie elsewhere) at their own paths,
# so that the make, compose and docker run it starts hand Docker the host's paths. Off with no
# DEMO_RESET_TIME, and until there is a baseline (render-env.sh's DEMO_RESET).
scheduler: render reset-image
	@# Never in the middle of a reset: killed there, it would leave the stack stopped.
	@if [ -d .state/baseline.lock ]; then \
		echo "scheduler: a reset or baseline is running (docker logs pacod-demo-reset); try again when it is done" >&2; exit 1; fi
	@docker rm -f pacod-demo-reset >/dev/null 2>&1 || true
	@set -a; . .state/env/stack.env; set +a; \
	if [ "$$DEMO_RESET" != true ]; then \
		if [ -n "$$DEMO_RESET_TIME" ]; then echo "scheduler: no nightly reset until there is a baseline: make baseline"; fi; \
		exit 0; fi; \
	sock=$$(docker run --rm --network none -v /var/run/docker.sock:/docker.sock $(RESET) stat -c %g /docker.sock); \
	docker run -d --name pacod-demo-reset --restart unless-stopped --network none \
		--log-opt max-size=10m --log-opt max-file=5 \
		--user $$(id -u):$$(id -g) --group-add "$$sock" \
		-e HOME=/tmp -e TZ="$$DEMO_RESET_TIMEZONE" -e RESET_AT="$$DEMO_RESET_TIME" \
		-v /var/run/docker.sock:/var/run/docker.sock \
		-v "$(abspath $(CURDIR)/..):$(abspath $(CURDIR)/..)" -v "$$DEMO_LOG_DIR:$$DEMO_LOG_DIR" \
		-w "$(CURDIR)" $(RESET) sh scripts/reset-scheduler.sh >/dev/null; \
	echo "scheduler: the demo resets every day at $$DEMO_RESET_TIME $$DEMO_RESET_TIMEZONE (docker logs pacod-demo-reset)"

# Starward's Google Tag Manager container and Search Console verification (demo.env), into its
# settings in the CMS. After first-baseline in make up (a fresh stack's CMS writes Starward's
# settings until wait-cms is done) and after every reset (the baseline may hold others). The home
# page's are the edge's: make up.
google: render
	@sh scripts/google.sh

# The CMS's third demo website, "Starward" (the space travels), on the magazine template set, for a
# stack whose CMS data predates it or holds its earlier version: the studio creates its demo
# websites only on an empty platform. A one-off studio container runs pacod-cms's --create-site
# (its `make site-create ... DEMO=space`) at CMS_SPACE_HOST, in the demo website's organisation
# (ORG=org_... another; none found: an organisation of its own). Refused, and nothing changed,
# while a website `space` exists or was deleted less than three minutes ago (README.md: The CMS's
# demo websites). Needs a studio image built from pacod-cms with the magazine set (make build): an
# older one refuses the set before it creates anything. The baseline still holds the stack as it
# was, so the nightly reset would take Starward back: make baseline afterwards.
cms-space: render
	@host=$$(sed -n 's/^CMS_SPACE_HOST=//p' .state/env/stack.env); \
	org="$(ORG)"; \
	if [ -z "$$org" ]; then \
		org=$$(docker exec cms-mongo sh -c 'mongosh --quiet -u "$$MONGO_INITDB_ROOT_USERNAME" \
			-p "$$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval \
			"print(db.getSiblingDB(\"pacod_cms\").website.findOne({_id: \"demo\"})?.organisationId ?? \"\")"' \
			2>/dev/null) || org=; fi; \
	$(CMS_CLI) --create-site --site-id=space --site-name=Starward --hosts="$$host" --language=en \
		--template-set=magazine --demo=space $${org:+--org-id="$$org"} || exit 1; \
	sh scripts/google.sh; \
	if [ -f .state/baseline ]; then \
		echo "cms-space: the nightly reset still returns to the baseline of $$(cat .state/baseline)," \
			"without this Starward: make baseline to keep it"; fi

# The demo content into an existing CMS website (pacod-cms's `make seed`): the journal, or with
# DEMO=space the space travels. Refused, and nothing written, if the website has articles; a run
# that stopped halfway (a demo picture that could not be stored) is finished by running it again.
# It keeps the website's template set: Starward's layouts only on magazine (else cms-space).
cms-seed: render
	@test -n "$(SITE)" || { echo 'usage: make cms-seed SITE=<website id: demo, minimal, space> [DEMO=space]'; exit 2; }
	$(CMS_CLI) --seed-demo --site-id="$(SITE)" $(if $(DEMO),--demo="$(DEMO)")

# ---- stop, remove ---------------------------------------------------------------------------------

# Every target that runs compose renders first: the env files of an older checkout can lack
# variables the compose files now read (compose then fails on, e.g., an empty port).
down: render
	@docker rm -f pacod-demo-reset >/dev/null 2>&1 || true
	$(COMPOSE) down --remove-orphans

stop: down

clean:
	@if [ "$(YES)" != 1 ]; then \
		printf 'This deletes every container, volume and secret of the demo%s. Continue? [y/N] ' \
			"$$( [ '$(IMAGES)' = 1 ] && echo ', and its images' )"; \
		read ans; [ "$$ans" = y ] || [ "$$ans" = Y ] || { echo "clean: nothing removed"; exit 1; }; fi
	@# The scheduler is not one of the project's containers (scheduler).
	@docker rm -f pacod-demo-reset >/dev/null 2>&1 || true
	@# With its secrets the project is taken down by compose, from freshly rendered env files
	@# (compose also knows its images); without them (a run that failed half-way) by its label.
	@if [ -f .state/secrets.env ] && sh scripts/render-env.sh; then \
		$(COMPOSE) down --volumes --remove-orphans $(if $(filter 1,$(IMAGES)),--rmi all); \
	else \
		ids=$$(docker ps -aq --filter label=com.docker.compose.project=pacod-demo); \
		if [ -n "$$ids" ]; then docker rm -f $$ids >/dev/null; fi; \
	fi
	@# Volumes of services the compose files no longer define (an optional profile that was on,
	@# a component removed since) are not removed by down: every volume of the project is.
	@vols=$$(docker volume ls -q --filter label=com.docker.compose.project=pacod-demo); \
		if [ -n "$$vols" ]; then docker volume rm $$vols >/dev/null; fi
	@if [ "$(IMAGES)" = 1 ]; then docker image rm $(TOOLS) $(SHOTS) $(RESET) >/dev/null 2>&1 || true; fi
	@rm -rf .state
	@for net in pacod-admin-net pacod-net; do \
		if [ -z "$$(docker ps -aq --filter network=$$net)" ]; then \
			docker network rm $$net >/dev/null 2>&1 || true; fi; done
	@# The models are not the project's (external): kept unless MODELS=1, and not while another
	@# stack (pacod-ap's own) still mounts them.
	@if [ "$(MODELS)" = 1 ]; then \
		if [ -n "$$(docker ps -aq --filter volume=$(HF_CACHE))" ]; then \
			echo "clean: $(HF_CACHE) is still mounted by another stack; kept" >&2; \
		else docker volume rm $(HF_CACHE) >/dev/null 2>&1 || true; echo "clean: the models removed"; fi; \
	else echo "clean: the models ($(HF_CACHE)) are kept; MODELS=1 deletes them"; fi
	@echo "clean: done"

remove: clean

# ---- look -----------------------------------------------------------------------------------------

build: network tools init render
	COMPOSE_PARALLEL_LIMIT=2 $(COMPOSE) build

status ps: render
	@$(COMPOSE) ps --format 'table {{.Name}}\t{{.Status}}'

logs: render
	$(COMPOSE) logs -f --tail=200 $(S)

config: render
	@$(COMPOSE) config -q && echo "config: the stack's compose files are valid"

urls: render
	@set -a; . .state/env/stack.env; set +a; \
	echo ""; \
	echo "pacod demo: $$DEMO_URL_SCHEME://<name>.$$DEMO_DOMAIN$${DEMO_URL_PORT:+:$$DEMO_URL_PORT}"; \
	echo ""; \
	echo "  Home (every product)        $$DEMO_ORIGIN"; \
	echo "  App (pacod-ap)              $$AP_ORIGIN"; \
	echo "  Gateway console (pacod-gw)  $$GW_CONSOLE_ORIGIN"; \
	echo "  Gateway API (OpenAI)        $$GW_API_ORIGIN/v1"; \
	echo "  CMS studio (pacod-cms)      $$CMS_STUDIO_ORIGIN"; \
	echo "  CMS demo website            $$CMS_DEMO_ORIGIN"; \
	echo "  CMS minimal website         $$CMS_MINIMAL_ORIGIN"; \
	echo "  CMS space website           $$CMS_SPACE_ORIGIN"; \
	echo "  Knowledge base (pacod-kb)   $$KB_ORIGIN"; \
	echo "  Benchmark (pacod-benchmark) $$BENCH_ORIGIN"; \
	echo "  Identity (pacod-id)         $$PACOD_ID_ISSUER/admin"; \
	echo "  Grafana                     $$GRAFANA_ORIGIN"; \
	echo "  Mail (every mail sent)      $$MAIL_ORIGIN"; \
	echo ""; \
	echo "  Sign in everywhere as       $$DEMO_ADMIN_EMAIL"; \
	echo "  Password                    $$DEMO_ADMIN_PASSWORD"; \
	echo ""; \
	if [ "$$DEMO_RESET" = true ]; then \
		echo "  Every change is undone every day at $$DEMO_RESET_TIME $$DEMO_RESET_TIMEZONE (make reset: now)"; \
		echo "  back to the baseline of $$(cat .state/baseline)"; \
	elif [ -n "$$DEMO_RESET_TIME" ]; then \
		echo "  No nightly reset yet: make baseline keeps the data as it is now as the state to return to"; \
	else \
		echo "  No nightly reset (DEMO_RESET_TIME is empty)"; fi; \
	echo ""

# The home page's screenshots (home/screenshots/), taken from the running stack as its admin.
shots: network render
	@docker image inspect $(SHOTS) >/dev/null 2>&1 || docker build -q -t $(SHOTS) tools/shots/ >/dev/null
	@echo "shots: home/screenshots/"
	@docker run --rm --network pacod-net --user $$(id -u):$$(id -g) -e HOME=/tmp \
		-v "$(CURDIR):/work" --env-file .state/env/stack.env $(SHOTS) python3 -I /work/scripts/shots.py

hosts:
	@set -a; . ./demo.env; [ ! -f demo.local.env ] || . ./demo.local.env; set +a; \
	sd=$${DEMO_SITE_DOMAIN:-$$DEMO_DOMAIN}; \
	echo "# pacod demo: add to /etc/hosts for browsers that do not resolve *.$$DEMO_DOMAIN and *.$$sd"; \
	if [ "$$DEMO_DOMAIN" != localhost ]; then echo "127.0.0.1 $$DEMO_DOMAIN"; fi; \
	for h in id app chat console gw $${DEMO_STUDIO_SUBDOMAIN:-studio} kb bench grafana mail prometheus; do \
		echo "127.0.0.1 $$h.$$DEMO_DOMAIN"; done; \
	for h in $${DEMO_CMS_SUBDOMAIN:-cms} $${DEMO_CMS_MINIMAL_SUBDOMAIN:-minimal} $${DEMO_CMS_SPACE_SUBDOMAIN:-space}; do \
		echo "127.0.0.1 $$h.$$sd"; done
