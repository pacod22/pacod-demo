"""The full stack's bootstrap: the credentials the products need from each other.

    make bootstrap        (also the last step of make up)

Runs in the pacod-demo-tools image on pacod-net, with the repositories mounted read-only under
/src and the demo's state under /state. Nothing here is new logic: it drives the scripts the
products already ship, pointed at the demo's files instead of a checkout's.

1. The gateway (pacod-ap's scripts/gw_bootstrap.py, imported): an admin token through the
   bootstrap admin's sign-in, the platform configuration (mock providers, or the real ones when
   demo.env has a provider key), provider credentials, one gateway key per calling service
   (the app's four plus pacod-cms and pacod-kb), and the app's report and admin tokens.
2. pacod-benchmark's admin token (BENCH_GW_ADMIN_TOKEN): a platform admin of the gateway, as the
   benchmark sets up and removes its own organisation, workspaces and keys for every run.
3. The CMS studio's provisioner account and token (pacod-cms's scripts/id-provisioner.mjs).
4. The admin's roles: those of PID_BOOTSTRAP_ADMIN_ROLES its account lacks. pacod-id gives them
   only when it creates the account, so an account older than a product (one a reset brings back
   from a baseline made before pacod-benchmark joined, say) never got that product's role.

Every step keeps what still works, so a second run changes nothing. All results go to
/state/bootstrap.env; nothing secret is printed. scripts/render-env.sh then hands them to the
services, and `make up` recreates the ones whose settings changed.
"""

from __future__ import annotations

import base64
import hashlib
import http.cookies
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
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
# The management API listens on pacod-admin-net alone, under this alias.
ADMIN_URL = "http://pacod-gw-admin:8081"
DATA_URL = "http://pacod-gw:8080"


def say(msg: str) -> None:
    print(f"bootstrap: {msg}", flush=True)


def gateway() -> gw.Api:
    providers = gw.read_env(gw.PROVIDERS_ENV)
    live = any(providers.get(v) for v in gw.PROVIDER_KEYS.values())
    env = "live" if live else "dev"
    say(f"gateway: {'real providers (a key is set in demo.local.env)' if live else 'the mock provider'}")
    # The admin token always comes the development way, through the bootstrap admin's sign-in at
    # pacod-id (the live way expects a token made by hand in the console).
    api = gw.admin_api("dev", ADMIN_URL, ISSUER)
    gw.import_config(api, Path(str(gw.CONFIG).format(env=env)))
    gw.ensure_credentials(api, rotate=False)
    gw.ensure_keys(api, DATA_URL)
    gw.ensure_admin_api_tokens(api)
    return api


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


def bench(api: gw.Api) -> None:
    # A platform admin, as only one may read the system's status: a token that answers it is kept.
    token = gw.read_env(OUT).get("BENCH_GW_ADMIN_TOKEN")
    if token:
        status = gw.probe("GET", f"{api.base}/system/status", token)
        if not gw.refused(status):
            say("bench: BENCH_GW_ADMIN_TOKEN works")
            return
        say(f"bench: BENCH_GW_ADMIN_TOKEN is refused ({status}); making a new one")
    token = gw.make_admin_token(api, "pacod-bench", "platform_admin", {"type": "platform"})
    gw.write_env(OUT, {"BENCH_GW_ADMIN_TOKEN": token})
    say("bench: made BENCH_GW_ADMIN_TOKEN (platform admin)")


class IdSession:
    """pacod-id's hosted pages' API as the bootstrap admin, as pacod-cms's id-provisioner.mjs uses
    it: an authorize request opens a flow, its password step sets the realm's session cookie, and
    /ui-api/v1/admin is then the admin API as that person. Writes there need the issuer's Origin."""

    def __init__(self, email: str, password: str):
        self.jar = http.cookies.SimpleCookie()
        verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": gw.SMOKE_CLIENT,
                "redirect_uri": gw.SMOKE_REDIRECT,
                "scope": "openid",
                "state": secrets.token_hex(16),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        status, _, headers = gw.request("GET", f"{ISSUER}/oidc/authorize?{query}")
        self.absorb(headers)
        to = urllib.parse.urljoin(ISSUER, headers.get("Location", ""))
        flow = urllib.parse.parse_qs(urllib.parse.urlparse(to).query).get("flow", [None])[0]
        if not flow:
            gw.fail(f"pacod-id opened no sign-in flow ({status})")
        status, step = self.call(
            "POST", f"/ui-api/v1/flows/{flow}/password", {"email": email, "password": password}
        )
        if not isinstance(step, dict) or step.get("next") != "redirect":
            gw.fail(f"signing in to pacod-id as the bootstrap admin failed ({status}): {gw.short(step)}")

    def absorb(self, headers) -> None:
        for value in headers.get_all("Set-Cookie") or []:
            self.jar.load(value)

    def call(self, method: str, path: str, body=None):
        cookie = "; ".join(f"{k}={m.value}" for k, m in self.jar.items())
        status, data, headers = gw.request(
            method, f"{ISSUER}{path}", body=body, headers={"Cookie": cookie, "Origin": ISSUER}
        )
        self.absorb(headers)
        return status, data


def admin_roles() -> None:
    # The roles pacod-id was told to give, from the file it reads them from. Added as the admin
    # itself, whose id.admin may give any role; none is ever taken away.
    roles = gw.read_env(STATE / "env" / "id.env").get("PID_BOOTSTRAP_ADMIN_ROLES", "")
    wanted = [r for r in roles.split(",") if r]
    ident = gw.read_env(gw.ID_ENV)
    session = IdSession(ident["PID_BOOTSTRAP_ADMIN_EMAIL"], ident["PID_BOOTSTRAP_ADMIN_PASSWORD"])
    status, me = session.call("GET", "/ui-api/v1/account")
    if status != 200:
        gw.fail(f"pacod-id did not say who the bootstrap admin is ({status}): {gw.short(me)}")
    account = me["account"]["id"]
    status, listed = session.call("GET", "/ui-api/v1/admin/bindings")
    if status != 200:
        gw.fail(f"pacod-id did not list the bootstrap admin's roles ({status}): {gw.short(listed)}")
    held = {b["role"] for b in listed["bindings"] if b["scope"] == "platform"}
    missing = [r for r in wanted if r not in held]
    if not missing:
        say("admin: holds every role of PID_BOOTSTRAP_ADMIN_ROLES")
        return
    # A product's roles exist once its service has pushed its catalogue, which on a stack that
    # has just started may be a moment away.
    for _ in range(30):
        waiting = []
        for role in missing:
            binding = {"principal": account, "role": role, "scope": "platform"}
            status, answer = session.call("POST", "/ui-api/v1/admin/bindings", binding)
            error = answer.get("error", {}) if isinstance(answer, dict) else {}
            code = error.get("details", {}).get("code")
            if status == 201:
                say(f"admin: gave the account {role}, which it lacked")
            elif code == "unknown_role":
                waiting.append(role)
            elif code != "binding_exists":
                gw.fail(f"pacod-id refused the bootstrap admin {role} ({status}): {gw.short(answer)}")
        missing = waiting
        if not missing:
            return
        time.sleep(2)
    for role in missing:
        say(f"admin: {role} waits for its product's catalogue at pacod-id; make bootstrap adds it")


def main() -> None:
    OUT.touch(mode=0o600, exist_ok=True)
    api = gateway()
    bench(api)
    cms_provisioner()
    admin_roles()
    say(f"done; credentials in .state/{OUT.name}")


if __name__ == "__main__":
    main()
