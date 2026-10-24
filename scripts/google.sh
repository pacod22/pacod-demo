#!/bin/sh
# Starward's Google Tag Manager container and Search Console verification (demo.env's
# DEMO_STARWARD_GTM_ID and DEMO_STARWARD_GOOGLE_SITE_VERIFICATION), written into its settings: the
# CMS's siteSettings document of the website `space`, whose gtmId and googleSiteVerification every
# template set renders (pacod-cms README.md: Configuring a website). demo.env owns these two fields:
# an empty value removes the field. The home page's are the edge's (config/Caddyfile).
#
#   sh scripts/google.sh      run by make up, make reset and make cms-space, after scripts/render-env.sh
#
# Only an existing document is updated, never one made: the CMS seeds a website's settings only
# while they are absent, so a document holding just these two would leave Starward without its
# name, menus and share picture. Without the website (or the CMS) it says so and changes nothing.
# The CMS shows the change within its 60-second cache.
set -eu
cd "$(dirname "$0")/.."

gtm=$(sed -n 's/^DEMO_STARWARD_GTM_ID=//p' .state/env/stack.env)
verification=$(sed -n 's/^DEMO_STARWARD_GOOGLE_SITE_VERIFICATION=//p' .state/env/stack.env)

if ! docker ps --format '{{.Names}}' | grep -qx cms-mongo; then
	echo "google: the CMS's database is not running; Starward's Google settings are left as they are"
	exit 0
fi

# The values go in through the environment, never into the script's text.
docker exec -e GTM="$gtm" -e VERIFICATION="$verification" cms-mongo sh -c 'mongosh --quiet \
	-u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin \
	--eval "$0"' '
	const set = {}, unset = {};
	for (const [field, value] of [["gtmId", process.env.GTM], ["googleSiteVerification", process.env.VERIFICATION]]) {
		if (value) set[field] = value; else unset[field] = "";
	}
	const update = {};
	if (Object.keys(set).length) update.$set = set;
	if (Object.keys(unset).length) update.$unset = unset;
	const r = db.getSiblingDB("site_space").siteSettings.updateOne({_id: "site"}, update);
	if (r.matchedCount === 0) {
		print("google: Starward has no settings yet (no website space): nothing written; make up or make cms-space again once it exists");
	} else {
		print("google: Starward: " + (set.gtmId ? "Tag Manager " + set.gtmId : "no Tag Manager") + ", " +
			(set.googleSiteVerification ? "Search Console verification set" : "no Search Console verification"));
	}
'
