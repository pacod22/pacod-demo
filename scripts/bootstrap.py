"""The full stack's bootstrap: the credentials the products need from each other.

    make bootstrap        (also the last step of make up)
    bootstrap.py tools    the tools phase alone (step 9; make bootstrap runs it after the rest)

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

6. pacod-broker for the gateway (pacod-gw's gw-broker and core, docs/pacod-gw.md of
   pacod-broker): a gateway token with broker_bridge (GWB_GW_TOKEN); pacod-id service accounts
   holding broker.consumer and broker.witness for the bridge (GWB_CONSUMER_TOKEN) and
   broker.consumer for the core (PGW_BROKER_TOKEN); and one account per gateway organisation
   holding broker.provider there, whose token lets the bridge announce that organisation's
   offerings. The bridge's file (/state/broker/gw-broker.yaml) lists those organisations; a
   template is offered once its editor switches "Offer on pacod-broker" on.
7. pacod-broker for the app and the CMS: an account holding broker.consumer and broker.provider
   at the platform for pacod-ap (BROKER_AP_TOKEN: its agents' search tool, the console's Broker
   page, the agents and the MCP server it announces); one holding broker.consumer for the CMS
   studio (CMS_BROKER_TOKEN: reputation and the broker's models on the installation screen);
   one holding broker.consumer for pacod-kb (KB_BROKER_TOKEN: its read-only Broker page, which
   announces nothing);
   and broker.provisioner at the platform for the studio's provisioner account, with which the
   studio makes each organisation's own provider account when it first offers a website.

8. The gateway's agents (pacod-gw runbook section 21.3): pacod-ap's fallback agent pacod-ap, to
   which its four keys are bound (its admin API then registers each profile and binds the keys
   to them, make bootstrap's next step), and one agent per product, each product's key bound to
   its own: pacod-cms, pacod-kb, pacod-aggregator. render-env.sh names the same agent to each
   product (PACOD_GW_AGENT_ID, AGG_GW_AGENT).
9. The tools phase (`bootstrap.py tools`, once the services are recreated, since pacod-gw must
   allow ap-api for tool servers and ap-api must be up): ap-api's /mcp becomes the gateway's tool
   server pacod-ap (runbook section 21.1), called with a role-less pacod-id service token of its
   own (GW_AP_MCP_TOKEN); its tools are approved as they are (it is the stack's own server), put
   in the workspace's bundle pacod-ap-tools, and granted to the key pacod-tools (TOOLS_GW_KEY), the
   one an MCP client of the gateway's /mcp uses. No family service speaks A2A yet, so the
   gateway's A2A proxy has no remote agent here.

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
import re
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
    ("pacod-aggregator", "AGG_GW_KEY", 5.0),
]

# The products' gateway agents: each product's key is bound to its own, so that its calls are that
# agent's whether they name it or not (render-env.sh names it all the same), with a kill switch
# and limits of its own. A pacod-ap checkout from before registered agents has no KEY_AGENTS; the
# keys then stay unbound, and the names the products send stay labels.
#
# pacod-ap's own four keys are bound (to its fallback agent, then its profiles) only while its
# running images name each call's agent in x-gw-agent-id: make bootstrap checks them
# (scripts/ap-names-agents.sh, AP_GW_AGENTS=1). An older image sends the profile in the body
# alone, which a key bound to an agent refuses (403), so its keys are then left as they are until
# its images are rebuilt. The products' keys bind either way: a key bound to one agent needs no
# header, and older product images send none.
AP_NAMES_AGENTS = os.environ.get("AP_GW_AGENTS") == "1"
PRODUCT_AGENTS = [
    ("pacod-cms", "pacod-cms", "The CMS studio's AI features (pacod-demo)"),
    ("pacod-kb", "pacod-kb", "The knowledge base's converter and content security (pacod-demo)"),
    ("pacod-aggregator", "pacod-aggregator", "The aggregator's reading of vendors' pages (pacod-demo)"),
]
if hasattr(gw, "KEY_AGENTS"):
    if not AP_NAMES_AGENTS:
        # Nor is its fallback agent registered: an unbound key may not name a registered agent.
        gw.KEY_AGENTS.clear()
        gw.AGENTS = [a for a in gw.AGENTS if a[0] != gw.FALLBACK_AGENT]
    gw.AGENTS = [*gw.AGENTS, *PRODUCT_AGENTS]
    gw.KEY_AGENTS.update({name: [name] for name, _, _ in PRODUCT_AGENTS})

ISSUER = os.environ["PACOD_ID_ISSUER"]
# The management API listens on pacod-admin-net alone, under this alias.
ADMIN_URL = "http://pacod-gw-admin:8081"
DATA_URL = "http://pacod-gw:8080"
# The studio's internal API, on pacod-net (Caddy does not route /internal/).
CMS_STUDIO = "http://cms-studio:7443"
CMS_ADMIN_NAME = "pacod-demo cms admin"
# pacod-aggregator's public API, on pacod-net.
AGG_URL = "http://pacod-aggregator:8150"
# The mock providers whose models carry real names, with their vendors in pacod-aggregator's
# catalogue. A provider finds its vendor by its address, and a mock's address matches no preset.
MOCK_VENDORS = {"mock-deepseek": "deepseek", "mock-gemini": "google", "mock-glm": "zai"}
# pacod-broker's public API, on pacod-net; its accounts at pacod-id carry this prefix.
BROKER_URL = "http://pacod-broker:8060"
BROKER_ACCOUNT = "pacod-demo gw-broker"
# The studio's AI features (pacod-cms's AiFeature), each set by CMS_AI_MODEL_<NAME>.
CMS_AI_FEATURES = ("translate", "pdf", "assist", "vision", "design")
# The tools phase: ap-api's MCP endpoint on pacod-net, the gateway's names for it, and the key an
# MCP client of the gateway uses (no daily cap: tool calls cost nothing).
AP_MCP = "http://ap-api:8000/mcp"
TOOL_SERVER = "pacod-ap"
TOOL_BUNDLE = "pacod-ap-tools"
TOOLS_KEY = ("pacod-tools", "TOOLS_GW_KEY", None)
TOOLS_ACCOUNT = "pacod-demo gw tools"


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
    if hasattr(gw, "ensure_agents"):
        if hasattr(gw, "KEY_AGENTS") and not AP_NAMES_AGENTS:
            say("gateway: the running pacod-ap names no agents (an older image): its keys stay unbound")
        # The agents first: a key may name only agents the workspace has.
        gw.ensure_keys(api, DATA_URL, gw.ensure_agents(api))
    else:
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


def service_token(session: IdSession, name: str, bindings: list[tuple[str, str]]) -> str | None:
    """A new token of the pacod-id service account `name` (made when missing), after binding it
    the roles given as (role, scope). Every older token of the account is revoked. None while a
    role waits for its product's catalogue: a token is never made before its roles are bound."""
    status, listed = session.call("GET", "/ui-api/v1/admin/service-accounts")
    if status != 200:
        gw.fail(f"pacod-id did not list its service accounts ({status}): {gw.short(listed)}")
    account = next((a for a in listed.get("service_accounts", [])
                    if a.get("name") == name and a.get("status") != "disabled"), None)
    if account is None:
        status, account = session.call("POST", "/ui-api/v1/admin/service-accounts", {"name": name})
        if status != 201:
            gw.fail(f"pacod-id did not make the account {name!r} ({status}): {gw.short(account)}")
    for role, scope in bindings:
        for _ in range(30):
            binding = {"principal": account["id"], "role": role, "scope": scope}
            status, answer = session.call("POST", "/ui-api/v1/admin/bindings", binding)
            code = (answer.get("error", {}).get("details", {}).get("code") if isinstance(answer, dict) else None)
            if status == 201 or code == "binding_exists":
                break
            if code not in ("unknown_role", "scope_unknown"):
                gw.fail(f"pacod-id refused {name!r} {role} at {scope} ({status}): {gw.short(answer)}")
            time.sleep(2)
        else:
            say(f"{role} at {scope} is not there yet at pacod-id; make bootstrap adds it")
            return None
    status, made = session.call("POST", f"/ui-api/v1/admin/service-accounts/{account['id']}/tokens", {})
    if status != 201 or not isinstance(made, dict) or not made.get("token"):
        gw.fail(f"pacod-id did not give {name!r} a token ({status})")
    status, tokens = session.call("GET", f"/ui-api/v1/admin/service-accounts/{account['id']}/tokens")
    for t in (tokens or {}).get("tokens", []) if status == 200 else []:
        if t.get("id") != made.get("id") and t.get("status") != "revoked":
            session.call("DELETE", f"/ui-api/v1/admin/service-tokens/{t['id']}")
    return made["token"]


def broker_takes(token: str | None) -> bool:
    """Whether pacod-broker still takes a stored token: 401 means it does not. The broker being
    down is not the token's fault, so that keeps it too."""
    if not token:
        return False
    status, _, _ = gw.request("GET", f"{BROKER_URL}/v1/reputation/me", token=token)
    return status != 401


def broker(api: gw.Api) -> None:
    have = gw.read_env(OUT)
    # The bridge reads the platform as broker_bridge (calls with tickets included), no more.
    token = have.get("GWB_GW_TOKEN")
    if not token or gw.refused(gw.probe("GET", f"{api.base}/orgs", token)):
        token = gw.make_admin_token(api, "pacod-gw-broker", "broker_bridge", {"type": "platform"})
        gw.write_env(OUT, {"GWB_GW_TOKEN": token})
        say("broker: made GWB_GW_TOKEN (broker_bridge)")

    ident = gw.read_env(gw.ID_ENV)
    session = None

    def mint(var: str, name: str, bindings: list[tuple[str, str]]) -> bool:
        nonlocal session
        if broker_takes(gw.read_env(OUT).get(var)):
            return True
        session = session or IdSession(ident["PID_BOOTSTRAP_ADMIN_EMAIL"], ident["PID_BOOTSTRAP_ADMIN_PASSWORD"])
        made = service_token(session, name, bindings)
        if made is None:
            return False
        gw.write_env(OUT, {var: made})
        say(f"broker: made {var} ({', '.join(r for r, _ in bindings)})")
        return True

    mint("GWB_CONSUMER_TOKEN", f"{BROKER_ACCOUNT} bridge",
         [("broker.consumer", "platform"), ("broker.witness", "platform")])
    mint("PGW_BROKER_TOKEN", f"{BROKER_ACCOUNT} core", [("broker.consumer", "platform")])

    # One provider account per organisation of the gateway, so that each announces as itself
    # and its own members' calls never count towards its reputation (docs/reputation.md).
    orgs = []
    for org in api.items("/orgs"):
        var = "GWB_ORG_TOKEN_" + "".join(c if c.isalnum() else "_" for c in org["name"]).upper()
        if mint(var, f"{BROKER_ACCOUNT} {org['name']}", [("broker.provider", f"organisation:{org['id']}")]):
            orgs.append((org["name"], var))
    write_bridge_file(orgs)

    # pacod-ap is one installation: one account announces its agents and searches the broker.
    mint("BROKER_AP_TOKEN", "pacod-demo ap broker",
         [("broker.consumer", "platform"), ("broker.provider", "platform")])
    # The CMS studio reads with this one; each organisation announces with its own, which the
    # studio's provisioner makes (pacod-cms's BrokerProviders), hence broker.provisioner.
    mint("CMS_BROKER_TOKEN", "pacod-demo cms broker", [("broker.consumer", "platform")])
    # pacod-kb only reads the broker (its Broker page), so its own account holds broker.consumer.
    mint("KB_BROKER_TOKEN", "pacod-demo kb broker", [("broker.consumer", "platform")])
    provisioner = gw.read_env(OUT).get("PACOD_ID_PROVISIONER_ACCOUNT")
    if provisioner:
        session = session or IdSession(ident["PID_BOOTSTRAP_ADMIN_EMAIL"], ident["PID_BOOTSTRAP_ADMIN_PASSWORD"])
        bind_platform_role(session, provisioner, "broker.provisioner")


def aggregator() -> None:
    """The gateway's core reads the catalogue with a service account holding agg.consumer. A
    stored token is kept unless the aggregator refuses it (401): one that is down keeps it too."""
    var = "PGW_AGGREGATOR_TOKEN"
    have = gw.read_env(OUT).get(var)
    if have and gw.request("GET", f"{AGG_URL}/v1/vendors", token=have)[0] != 401:
        return
    ident = gw.read_env(gw.ID_ENV)
    session = IdSession(ident["PID_BOOTSTRAP_ADMIN_EMAIL"], ident["PID_BOOTSTRAP_ADMIN_PASSWORD"])
    made = service_token(session, "pacod-demo gw aggregator", [("agg.consumer", "platform")])
    if made is not None:
        gw.write_env(OUT, {var: made})
        say(f"aggregator: made {var} (agg.consumer)")


def model_data(api: gw.Api) -> None:
    """Gives the mock providers their vendors: without one a provider's models stay out of the
    gateway's sync, and the catalogue would change nothing here. Each bootstrap writes the
    providers again without the field. With a real key there are no mock providers, and the real
    ones find their vendors by address."""
    for provider in api.items("/providers"):
        vendor = MOCK_VENDORS.get(provider["name"])
        if vendor is None or provider.get("catalog_vendor") == vendor:
            continue
        body = {k: v for k, v in provider.items() if k not in ("id", "created_at", "updated_at")}
        api.call("PUT", f"/providers/{provider['id']}", {**body, "catalog_vendor": vendor})
        say(f"model data: {provider['name']} takes {vendor}'s entries")


def bind_platform_role(session: IdSession, principal: str, role: str) -> None:
    """`role` at the platform for `principal`, kept when it is there; waits a moment for the
    role's product to push its catalogue, as a stack that has just started may not have."""
    for _ in range(30):
        binding = {"principal": principal, "role": role, "scope": "platform"}
        status, answer = session.call("POST", "/ui-api/v1/admin/bindings", binding)
        code = (answer.get("error", {}).get("details", {}).get("code") if isinstance(answer, dict) else None)
        if status == 201:
            say(f"broker: gave the studio's provisioner {role}")
            return
        if code == "binding_exists":
            return
        if code != "unknown_role":
            gw.fail(f"pacod-id refused the studio's provisioner {role} ({status}): {gw.short(answer)}")
        time.sleep(2)
    say(f"broker: {role} is not there yet at pacod-id; make bootstrap adds it")


def write_bridge_file(orgs: list[tuple[str, str]]) -> None:
    """The bridge's file and the organisations' token files, mounted into gw-broker
    (overlays/gw.yml), which runs as the user who runs make and so reads them at 0600."""
    folder = STATE / "broker"
    folder.mkdir(mode=0o700, exist_ok=True)
    tokens = gw.read_env(OUT)
    lines = [
        "# Written by scripts/bootstrap.py at every run: edit the offerings in the console",
        "# (a template's \"Offer on pacod-broker\"), not here.",
        "organisations:",
    ]
    for name, var in orgs:
        path = folder / f"{var.lower()}.token"
        path.write_text(tokens[var] + "\n", encoding="utf-8")
        path.chmod(0o600)
        lines += [f"  - organisation: {json.dumps(name)}", f"    token_file: /etc/gw-broker/{path.name}"]
    if not orgs:
        lines[-1] = "organisations: []"
    target = folder / "gw-broker.yaml"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    target.chmod(0o600)
    say(f"broker: the bridge offers for {len(orgs)} organisation(s)")


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


def ap_mcp_answers(token: str | None) -> int:
    """The status ap-api's /mcp gives a ping with `token`: 200 when it takes the token."""
    if not token:
        return 401
    status, _, _ = gw.request("POST", AP_MCP, token=token, body={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    return status


def tools() -> None:
    """ap-api's /mcp as the gateway's tool server pacod-ap (step 9 of the module's docstring)."""
    if not hasattr(gw, "KEY_BUNDLES"):
        say("tools: this pacod-ap checkout grants no tool bundles to keys; no tool server")
        return
    api = gw.admin_api("dev", ADMIN_URL, ISSUER)
    org, ws = gw.platform_ids(api)

    # The credential the gateway calls ap-api with: a pacod-id service token, no role (ap-api's
    # /mcp asks for an active account only). A token ap-api still takes is kept.
    token = gw.read_env(OUT).get("GW_AP_MCP_TOKEN")
    rotated = False
    status = ap_mcp_answers(token)
    if status == 401:
        ident = gw.read_env(gw.ID_ENV)
        session = IdSession(ident["PID_BOOTSTRAP_ADMIN_EMAIL"], ident["PID_BOOTSTRAP_ADMIN_PASSWORD"])
        token = service_token(session, TOOLS_ACCOUNT, [])
        gw.write_env(OUT, {"GW_AP_MCP_TOKEN": token})
        rotated = True
        say("tools: made GW_AP_MCP_TOKEN (ap-api's /mcp, no role)")
        status = ap_mcp_answers(token)
    if status != 200:
        say(f"tools: ap-api's /mcp answers {status}; no tool server this time (make bootstrap again)")
        return

    servers = f"/orgs/{org}/tool-servers"
    wanted = {
        "name": TOOL_SERVER,
        "description": "pacod-ap's offered agents and their reference facts (ap-api's /mcp)",
        "url": AP_MCP,
        "auth": {"header": "authorization", "prefix": "Bearer "},
        "workspaces": [ws],
    }
    server = api.find(servers, TOOL_SERVER)
    if server is None:
        status, server = api.try_call("POST", servers, wanted)
        if status == 422 and "url" in gw.short(server):
            say("tools: pacod-gw does not allow ap-api for tool servers (PGW_TOOL_EGRESS_ALLOW_PRIVATE, "
                "overlays/gw.yml): make redeploy S=pacod-gw, then make bootstrap")
            return
        if status != 201:
            gw.fail(f"POST {servers} answered {status}: {gw.short(server)}")
        say(f"tools: registered the tool server {TOOL_SERVER} ({AP_MCP})")
    elif server.get("url") != AP_MCP or server.get("workspaces") != [ws]:
        server = api.call("PUT", f"{servers}/{server['id']}", {**server, **wanted})
        say(f"tools: the tool server {TOOL_SERVER} points at {AP_MCP} again")
    sid = server["id"]

    creds = api.items(f"{servers}/{sid}/credentials")
    current = [c for c in creds if c.get("state") == "current" and c.get("scope") != "workspace"]
    if rotated or not current:
        api.call("POST", f"{servers}/{sid}/credentials",
                 {"label": "pacod-demo", "secret": token, "make_current": True})
        say("tools: the tool server's credential is GW_AP_MCP_TOKEN")

    # Approval: the stack's own server, so its tools are approved as the probe sees them. A tool an
    # operator disabled stays so, and one the server no longer lists is left alone.
    view = api.call("POST", f"{servers}/{sid}/probe")
    probe = view.get("probe") or {}
    if not probe.get("ok"):
        say(f"tools: probing ap-api's /mcp failed: {probe.get('problem')}")
        return
    for attempt in range(2):
        retry = False
        for tool in view.get("tools", []):
            if tool.get("status") not in ("new", "changed"):
                continue
            name, seen = tool["tool"], tool.get("seen") or {}
            path = f"{servers}/{sid}/tools/{urllib.parse.quote(name, safe='')}/approve"
            body = {"hash": seen.get("hash")}
            status, answer = api.try_call("POST", path, body)
            if status == 422:
                # A name providers do not take as a function name (a dot, too long) needs an alias
                # in the server's namespace; the gateway suggests none for such a tool.
                alias = (f"{TOOL_SERVER}__" + re.sub(r"[^A-Za-z0-9_-]", "_", name))[:64]
                status, answer = api.try_call("POST", path, {**body, "exposed_name": alias})
            if status == 409 and attempt == 0:
                retry = True
                continue
            if status not in (200, 201):
                say(f"tools: {name} is not approved ({status}: {gw.short(answer)}); approve it in the console")
                continue
            say(f"tools: approved {name} ({'its definition changed' if tool['status'] == 'changed' else 'new'})")
        if not retry:
            break
        view = api.call("POST", f"{servers}/{sid}/probe")

    bundles = f"/orgs/{org}/workspaces/{ws}/tool-bundles"
    if api.find(bundles, TOOL_BUNDLE) is None:
        api.call("POST", bundles, {"name": TOOL_BUNDLE, "description": "pacod-ap's tools (pacod-demo)",
                                   "allow": [f"mcp:{TOOL_SERVER}/*"]})
        say(f"tools: made the bundle {TOOL_BUNDLE}")
    # The key comes after the bundle: a key may name only bundles the workspace has.
    gw.KEY_BUNDLES[TOOLS_KEY[0]] = [TOOL_BUNDLE]
    gw.ensure_keys(api, DATA_URL, services=[TOOLS_KEY])


def main() -> None:
    OUT.touch(mode=0o600, exist_ok=True)
    if sys.argv[1:] == ["tools"]:
        tools()
        return
    api = gateway()
    bench(api)
    cms_provisioner()
    admin_roles()
    cms_gateway_key()
    broker(api)
    aggregator()
    model_data(api)
    say(f"done; credentials in .state/{OUT.name}")


if __name__ == "__main__":
    main()
