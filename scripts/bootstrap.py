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
5. The CMS studio's gateway key, handed over live: PUT to cms-studio's /internal/v1/ai-gateway
   with a pacod-id service token of its own (CMS_ADMIN_TOKEN, an account holding
   cms.platform_admin). The studio stores the pair and uses it from the next call. PACOD_GW_API_KEY
   in cms.env stays, as the fallback the stored pair overrides. Then, the same way, the models
   (/internal/v1/ai-models): CMS_AI_MODEL as the default and CMS_AI_MODEL_<FEATURE> per feature,
   a blank one clearing that feature's choice, so demo.env decides; PACOD_AI_MODEL stays too.

Every step keeps what still works, so a second run changes nothing. All results go to
/state/bootstrap.env; nothing secret is printed. scripts/render-env.sh then hands them to the
services, and `make up` recreates the ones whose settings changed.
"""

from __future__ import annotations

import base64
import hashlib
import http.cookies
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
# The studio's internal API, on pacod-net (Caddy does not route /internal/).
CMS_STUDIO = "http://cms-studio:7443"
CMS_ADMIN_NAME = "pacod-demo cms admin"
# The studio's AI features (pacod-cms's AiFeature), each set by CMS_AI_MODEL_<NAME>.
CMS_AI_FEATURES = ("translate", "pdf", "assist", "vision", "design")


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


def cms_call(method: str, path: str, token: str, body=None):
    """(status, parsed body) of the studio's internal API; status 0 when it cannot be reached --
    gw.request would end the bootstrap, and a studio that is not up must cost this step only."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{CMS_STUDIO}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", "replace")


def cms_admin_token() -> str | None:
    """A pacod-id service token holding cms.platform_admin, which /internal/v1/ai-gateway needs
    (the studio's provisioner holds cms.provisioner only). Kept while the studio takes it; else
    the account is made or found, bound, and given one new token, every older one revoked."""
    have = gw.read_env(OUT).get("CMS_ADMIN_TOKEN")
    if have:
        status, _ = cms_call("GET", "/internal/v1/ai-gateway", have)
        if status in (200, 404, 503, 0):
            # 404: a studio older than the API; 503/0: down or catching up. Not the token's fault.
            return have
        say(f"cms: CMS_ADMIN_TOKEN is refused ({status}); making a new one")
    ident = gw.read_env(gw.ID_ENV)
    session = IdSession(ident["PID_BOOTSTRAP_ADMIN_EMAIL"], ident["PID_BOOTSTRAP_ADMIN_PASSWORD"])
    status, listed = session.call("GET", "/ui-api/v1/admin/service-accounts")
    if status != 200:
        gw.fail(f"pacod-id did not list its service accounts ({status}): {gw.short(listed)}")
    account = next((a for a in listed.get("service_accounts", [])
                    if a.get("name") == CMS_ADMIN_NAME and a.get("status") != "disabled"), None)
    if account is None:
        status, account = session.call("POST", "/ui-api/v1/admin/service-accounts", {"name": CMS_ADMIN_NAME})
        if status != 201:
            gw.fail(f"pacod-id did not make the CMS admin account ({status}): {gw.short(account)}")
    # The role before the token: a token whose binding then failed would be a live credential
    # nobody recorded. cms.* roles exist once the studio has pushed its catalogue.
    for _ in range(30):
        binding = {"principal": account["id"], "role": "cms.platform_admin", "scope": "platform"}
        status, answer = session.call("POST", "/ui-api/v1/admin/bindings", binding)
        code = (answer.get("error", {}).get("details", {}).get("code") if isinstance(answer, dict) else None)
        if status == 201 or code == "binding_exists":
            break
        if code != "unknown_role":
            gw.fail(f"pacod-id refused the CMS admin cms.platform_admin ({status}): {gw.short(answer)}")
        time.sleep(2)
    else:
        say("cms: cms.platform_admin waits for the studio's catalogue at pacod-id; make bootstrap sets the key")
        return None
    status, made = session.call("POST", f"/ui-api/v1/admin/service-accounts/{account['id']}/tokens", {})
    if status != 201 or not isinstance(made, dict) or not made.get("token"):
        gw.fail(f"pacod-id did not give the CMS admin a token ({status})")
    status, tokens = session.call("GET", f"/ui-api/v1/admin/service-accounts/{account['id']}/tokens")
    for t in (tokens or {}).get("tokens", []) if status == 200 else []:
        if t.get("id") != made.get("id") and t.get("status") != "revoked":
            session.call("DELETE", f"/ui-api/v1/admin/service-tokens/{t['id']}")
    gw.write_env(OUT, {"CMS_ADMIN_TOKEN": made["token"], "CMS_ADMIN_ACCOUNT": account["id"]})
    say("cms: made CMS_ADMIN_TOKEN (a service account holding cms.platform_admin)")
    return made["token"]


def cms_put(path: str, token: str, body: dict):
    """A PUT to the studio, retried while pacod-id's feed has not yet told it about a new binding
    (403) or it is catching up (503, unreachable)."""
    for _ in range(20):
        status, answer = cms_call("PUT", path, token, body)
        if status not in (0, 403, 503):
            return status, answer
        time.sleep(2)
    return status, answer


def cms_models(token: str) -> None:
    """demo.env's models into the running studio: the default and every feature, blank clearing
    that feature's choice. The studio checks each against what the gateway offers this key and
    what the feature needs; a refusal is said, PACOD_AI_MODEL then applies."""
    default = os.environ.get("CMS_AI_MODEL", "").strip()
    features = {f: os.environ.get(f"CMS_AI_MODEL_{f.upper()}", "").strip() for f in CMS_AI_FEATURES}
    status, answer = cms_put("/internal/v1/ai-models", token, {"default": default, "features": features})
    chosen = ", ".join(f"{f} {m}" for f, m in features.items() if m) or "none per feature"
    if status == 200:
        say(f"cms: the studio uses {default or 'PACOD_AI_MODEL'} by default ({chosen})")
    elif status == 422 and isinstance(answer, dict) and answer.get("code") == "unchanged":
        say(f"cms: the studio already uses {default or 'PACOD_AI_MODEL'} by default ({chosen})")
    elif status == 422 and isinstance(answer, dict):
        say(f"cms: the studio refused the models ({answer.get('code')} for {answer.get('field')}: "
            f"{answer.get('error')}); fix CMS_AI_MODEL* in demo.env and make bootstrap")
    elif status == 404:
        say("cms: the studio predates /internal/v1/ai-models (rebuild pacod-cms); PACOD_AI_MODEL applies")
    else:
        say(f"cms: the studio did not take the models ({status}); PACOD_AI_MODEL applies")


def cms_gateway_key() -> None:
    """CMS_GW_KEY into the running studio, with the gateway's address: one PUT, then one call
    through it. A studio that is down or predates the API keeps PACOD_GW_API_KEY from cms.env."""
    key = gw.read_env(OUT).get("CMS_GW_KEY")
    if not key:
        say("cms: no CMS_GW_KEY to hand over")
        return
    token = cms_admin_token()
    if not token:
        return
    status, answer = cms_put("/internal/v1/ai-gateway?test=true", token, {"url": DATA_URL, "apiKey": key})
    if status == 200:
        test = (answer or {}).get("test", {})
        how = f"tested, {test.get('millis')} ms" if test.get("ok") else f"test failed: {test.get('error')}"
        say(f"cms: the studio took CMS_GW_KEY live ({how})")
        # The models are checked against the list the gateway gives the key just set.
        cms_models(token)
    elif status == 404:
        say("cms: the studio predates /internal/v1/ai-gateway (rebuild pacod-cms); PACOD_GW_API_KEY applies")
    elif status in (0, 503):
        say(f"cms: the studio did not answer ({status}); PACOD_GW_API_KEY applies, make bootstrap retries")
    else:
        # The last step, and PACOD_GW_API_KEY still applies: said, never the end of make up.
        say(f"cms: the studio refused the gateway key ({status}): {gw.short(answer)}; PACOD_GW_API_KEY applies")


def main() -> None:
    OUT.touch(mode=0o600, exist_ok=True)
    api = gateway()
    bench(api)
    cms_provisioner()
    admin_roles()
    cms_gateway_key()
    say(f"done; credentials in .state/{OUT.name}")


if __name__ == "__main__":
    main()
