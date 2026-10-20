"""The full stack's bootstrap: the credentials the products need from each other.

    make bootstrap        (also the last step of make up)

Runs in the pacod-demo-tools image on pacod-net, with the repositories mounted read-only under
/src and the demo's state under /state. Nothing here is new logic: it drives the scripts the
products already ship, pointed at the demo's files instead of a checkout's.

1. The gateway (pacod-ap's scripts/gw_bootstrap.py, imported): an admin token through the
   bootstrap admin's sign-in, the platform configuration (mock providers, or the real ones when
   demo.env has a provider key), provider credentials, one gateway key per calling service
   (the app's four plus pacod-cms and pacod-kb), and the app's report and admin tokens.
2. The CMS studio's provisioner account and token (pacod-cms's scripts/id-provisioner.mjs).

Every step keeps what still works, so a second run changes nothing. All results go to
/state/bootstrap.env; nothing secret is printed. scripts/render-env.sh then hands them to the
services, and `make up` recreates the ones whose settings changed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

STATE = Path("/state")
AP = Path("/src/pacod-ap")
CMS = Path("/src/pacod-cms")
OUT = STATE / "bootstrap.env"

sys.path.insert(0, str(AP / "scripts"))
import gw_bootstrap as gw  # noqa: E402

# The scripts' own files, redirected to the demo's state. ROOT is "/" so that their messages,
# which print paths relative to it, work for paths outside a pacod-ap checkout.
gw.ROOT = Path("/")
gw.GATEWAY_ENV = OUT
gw.ID_ENV = STATE / "env" / "bootstrap-id.env"
gw.PROVIDERS_ENV = STATE / "env" / "providers.env"
gw.CONFIG = AP / "deploy" / "pacod-gw" / "platform-config.{env}.json"

# The other products that call the gateway, beside the app's four: each its own key, with a
# daily spend cap in USD (it only matters with real providers).
gw.SERVICES = [
    *gw.SERVICES,
    ("pacod-cms", "CMS_GW_KEY", 5.0),
    ("pacod-kb", "KB_GW_KEY", 5.0),
]

ISSUER = os.environ["PACOD_ID_ISSUER"]
ADMIN_URL = "http://pacod-gw:8081"
DATA_URL = "http://pacod-gw:8080"


def say(msg: str) -> None:
    print(f"bootstrap: {msg}", flush=True)


def gateway() -> None:
    providers = gw.read_env(gw.PROVIDERS_ENV)
    live = any(providers.get(v) for v in gw.PROVIDER_KEYS.values())
    env = "live" if live else "dev"
    say(f"gateway: {'real providers (a key is set in demo.env)' if live else 'the mock provider'}")
    # The admin token always comes the development way, through the bootstrap admin's sign-in at
    # pacod-id (the live way expects a token made by hand in the console).
    api = gw.admin_api("dev", ADMIN_URL, ISSUER)
    gw.import_config(api, Path(str(gw.CONFIG).format(env=env)))
    gw.ensure_credentials(api, rotate=False)
    gw.ensure_keys(api, DATA_URL)
    gw.ensure_admin_api_tokens(api)


def cms_provisioner() -> None:
    # id-provisioner.mjs reads the admin's sign-in from <dir>/.env and writes the account and the
    # token back there; the demo gives it a directory of its own.
    work = STATE / "cms"
    work.mkdir(parents=True, exist_ok=True)
    env_file = work / ".env"
    have = gw.read_env(env_file)
    gw.write_env(
        env_file,
        {
            "ID_ADMIN_EMAIL": os.environ["DEMO_ADMIN_EMAIL"],
            "ID_ADMIN_PASSWORD": os.environ["DEMO_ADMIN_PASSWORD"],
            **{k: v for k, v in have.items() if k.startswith("PACOD_ID_PROVISIONER_")},
        },
    )
    subprocess.run(
        ["node", str(CMS / "scripts" / "id-provisioner.mjs"), str(work), ISSUER,
         os.environ["CMS_STUDIO_ORIGIN"]],
        check=True,
    )
    made = gw.read_env(env_file)
    gw.write_env(OUT, {k: v for k, v in made.items() if k.startswith("PACOD_ID_PROVISIONER_")})
    say("cms: the studio's provisioner is in place")


def main() -> None:
    OUT.touch(mode=0o600, exist_ok=True)
    gateway()
    cms_provisioner()
    say(f"done; credentials in .state/{OUT.name}")


if __name__ == "__main__":
    main()
