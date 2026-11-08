"""The full stack's checks on pacod-net (make verify, inside the pacod-demo-tools image).

Each check prints PASS, WARN or FAIL with one line of detail; the exit status is the number of
failures (capped at 1). Nothing secret is printed. The checks follow the products' own smoke
tests (pacod-ap's scripts/smoke_local.sh and smoke_gateway_turn.sh, pacod-cms's
scripts/smoke.sh, pacod-gw's mock provider), pointed at the demo's addresses.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, "/src/pacod-ap/scripts")
import gw_bootstrap as gw  # noqa: E402

E = os.environ
ISSUER = E["PACOD_ID_ISSUER"]
# A demo published beyond this machine (render-env.sh): private sites, no dev tokens.
PUBLIC = E.get("DEMO_PUBLIC") == "true"
# The products that run (demo.env's DEMO_PRODUCTS, resolved by render-env.sh), by their overlays'
# names; every one in an env file rendered before it. Only their checks run.
ALL_PRODUCTS = ("gw", "ap", "cms", "kb", "bench", "broker", "agg")
PRODUCTS = set(E.get("DEMO_PRODUCTS", ",".join(("id", *ALL_PRODUCTS))).split(","))
fails = 0


def product_runs(product: str | None) -> bool:
    return product is None or product in PRODUCTS


def report(ok: bool | None, name: str, detail: str = "") -> None:
    global fails
    word = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
    if ok is False:
        fails += 1
    print(f"  {word}  {name}{'  ' + detail if detail else ''}", flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: ANN002, ANN003
        return None


OPEN = urllib.request.build_opener(NoRedirect)


def http(method: str, url: str, *, body=None, headers=None, timeout: float = 30, raw: bool = False):
    """(status, parsed JSON or text, headers); never raises for HTTP status codes."""
    data = None
    h = dict(headers or {})
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        r = OPEN.open(req, timeout=timeout)
        status, text, hdrs = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, text, hdrs = e.code, e.read(), e.headers
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}", {}
    if raw:
        return status, text, hdrs
    try:
        return status, json.loads(text), hdrs
    except ValueError:
        return status, text.decode(errors="replace"), hdrs


def check(name: str, url: str, want=(200,), contains: str | None = None, **kw) -> tuple[int, object]:
    status, body, hdrs = http("GET", url, **kw)
    ok = status in want and (contains is None or contains in (body if isinstance(body, str) else json.dumps(body)))
    report(ok, name, f"{status}" + ("" if ok else f" {str(body)[:120]}"))
    return status, body


def mock_script(*replies: str) -> None:
    """The gateway's mock provider answers the next calls with these texts."""
    b64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")  # noqa: E731
    http("DELETE", "http://gw-mock:9000/__mock/script")
    http("POST", "http://gw-mock:9000/__mock/script", body=[f"reply={b64(r)}" for r in replies])


def section(title: str) -> None:
    print(f"\n{title}", flush=True)


# The agent a product's call names. pacod-ap's keys speak for several registered agents (its
# profiles and pacod-ap, its calls outside a profile), so each call names one; the other products'
# keys are bound to one agent each and need not (scripts/bootstrap.py).
AGENT_HEADERS = {"pacod-ap": {"x-gw-agent-id": "pacod-ap"}}
# The gateway's names for ap-api's tools: the tool server pacod-ap's (scripts/bootstrap.py).
TOOL_PREFIX = "pacod-ap__"


# ---- 1. The edge: every public address answers through pacod-caddy -------------------------------
def edge() -> None:
    section("edge (pacod-caddy, through the public names)")
    status, disc = check("pacod-id discovery", f"{ISSUER}/.well-known/openid-configuration")
    if status == 200:
        report(disc.get("issuer") == ISSUER, "issuer is the configured one", str(disc.get("issuer")))
    for product, name, url, kw in [
        ("ap", "app", E["AP_ORIGIN"] + "/", {"want": (200, 302, 303)}),
        ("gw", "gateway console", E["GW_CONSOLE_ORIGIN"] + "/", {"want": (200, 302, 303)}),
        ("gw", "gateway data plane", E["GW_API_ORIGIN"] + "/v1/models", {"want": (401,)}),
        ("cms", "cms studio", E["CMS_STUDIO_ORIGIN"] + "/mgmt/login", {"want": (200, 302)}),
        ("cms", "cms demo website", E["CMS_DEMO_ORIGIN"] + "/", {"want": (200,)}),
        ("kb", "knowledge base", E["KB_ORIGIN"] + "/kb/healthz", {"contains": '"identity"'}),
        ("bench", "benchmark", E["BENCH_ORIGIN"] + "/", {"want": (302, 303)}),
        ("broker", "broker console", E["BROKER_ORIGIN"] + "/", {"want": (302, 303)}),
        ("broker", "broker API", E["BROKER_API_ORIGIN"] + "/healthz", {}),
        ("agg", "aggregator console", E["AGG_CONSOLE_URL"] + "/", {"want": (302, 303)}),
        ("agg", "aggregator API", E["AGG_API_ORIGIN"] + "/healthz", {}),
        ("agg", "aggregator's admin API is not at the edge", E["AGG_API_ORIGIN"] + "/api/v1/status", {"want": (404,)}),
    ]:
        if product_runs(product):
            check(name, url, **kw)
    if not PUBLIC:
        check("grafana", E["GRAFANA_ORIGIN"] + "/api/health")
    if PUBLIC:
        # Published beyond this machine: the mail viewer, Prometheus and Grafana ask for the
        # admin's sign-in.
        admin = base64.b64encode(f"{E['DEMO_ADMIN_EMAIL']}:{E['DEMO_ADMIN_PASSWORD']}".encode()).decode()
        for name, url in [("mail", E["MAIL_ORIGIN"] + "/"), ("prometheus", E["PROMETHEUS_ORIGIN"] + "/"),
                          ("grafana", E["GRAFANA_ORIGIN"] + "/api/health")]:
            check(f"{name} asks for a sign-in", url, want=(401,))
            check(f"{name} opens with the admin's", url, want=(200, 302),
                  headers={"Authorization": f"Basic {admin}"})
    else:
        check("mail", E["MAIL_ORIGIN"] + "/", want=(200,))
    # The home page answers at the bare domain. On pacod-net that name is the edge's (an alias),
    # except `localhost`, which is every container's own: there, ask the edge with its Host.
    if E["DEMO_DOMAIN"] == "localhost":
        port = E["DEMO_HTTP_PORT"]
        home, hdrs = f"http://pacod-caddy:{port}/", {"Host": f"localhost:{port}"}
    else:
        home, hdrs = E["DEMO_ORIGIN"] + "/", {}
    # A rendered template names the studio (with the CMS, from the CMS's hosts) or pacod-id's
    # administration, which it always shows.
    rendered = E["CMS_STUDIO_ORIGIN"] + "/" if product_runs("cms") else ISSUER + "/admin"
    check("home page", home, headers=hdrs, contains=rendered)
    # Its files for crawlers and language models, templates like the page: the origin in them shows
    # they were rendered.
    check("home robots.txt", home + "robots.txt", headers=hdrs, contains=f"Sitemap: {E['DEMO_ORIGIN']}/sitemap.xml")
    check("home sitemap.xml", home + "sitemap.xml", headers=hdrs, contains=f"<loc>{E['DEMO_ORIGIN']}/</loc>")
    check("home llms.txt", home + "llms.txt", headers=hdrs, contains=rendered)


# ---- 2. Health of every service ------------------------------------------------------------------
def health() -> None:
    section("health (pacod-net)")
    for product, name, url in [
        (None, "pacod-id ready", "http://pacod-id:9170/readyz"),
        ("gw", "pacod-gw data plane ready", "http://pacod-gw:8080/readyz"),
        ("gw", "pacod-gw management ready (pacod-admin-net)", "http://pacod-gw-admin:8081/readyz"),
        ("gw", "gw-analytics ready", "http://gw-analytics:8090/readyz"),
        ("gw", "gw-tester", "http://gw-tester:8095/healthz"),
        ("ap", "ap-api", "http://ap-api:8000/health"),
        ("ap", "ap-admin-api", "http://ap-admin-api:8000/health"),
        ("ap", "ap-mcp", "http://ap-mcp:8000/health"),
        ("ap", "pacod-embeddings", "http://pacod-embeddings:8080/health"),
        ("cms", "cms-site", "http://cms-site:8443/web/v1/health"),
        ("cms", "cms-studio", "http://cms-studio:7443/mgmt-health"),
        ("kb", "kb-pipeline", "http://kb-pipeline:8000/health"),
        # Ready only with a current copy of its roles from pacod-id.
        ("bench", "bench-api ready (pacod-admin-net)", "http://bench-api-internal:8190/api/v1/readyz"),
        ("broker", "pacod-broker ready", "http://pacod-broker:8060/readyz"),
        ("broker", "pacod-broker admin (pacod-admin-net)", "http://pacod-broker-admin:8061/healthz"),
        # Ready only with a current copy of its roles from pacod-id.
        ("agg", "pacod-aggregator ready", "http://pacod-aggregator:8150/readyz"),
        ("agg", "pacod-aggregator admin (pacod-admin-net)", "http://pacod-aggregator-admin:8151/healthz"),
        ("agg", "agg-scraper ready", "http://agg-scraper:8152/readyz"),
        (None, "pacod-qdrant", "http://pacod-qdrant:6333/readyz"),
        (None, "pacod-prometheus", "http://pacod-prometheus:9090/-/ready"),
        (None, "pacod-alertmanager", "http://pacod-alertmanager:9093/-/ready"),
        (None, "pacod-grafana", "http://pacod-grafana:3000/api/health"),
    ]:
        if product_runs(product):
            check(name, url)
    if product_runs("kb"):
        status, body, _ = http("GET", "http://kb-ui:8080/kb/healthz")
        ident = body.get("identity") if isinstance(body, dict) else None
        report(status == 200 and ident == "ready", "kb-ui signed in to pacod-id", f"identity={ident}")

    # Prometheus: every target up (the app's ingestion only when it runs), but those of the products
    # that do not run: every product's scrape jobs stay (platform.yml), by the products' own names.
    status, body, _ = http("GET", "http://pacod-prometheus:9090/api/v1/targets?state=active")
    if status != 200:
        report(False, "prometheus targets", str(status))
        return
    targets = body["data"]["activeTargets"]
    ingestion = "ingestion" in E.get("DEMO_PROFILES", "")
    down = [t for t in targets if t["health"] != "up"]
    bridge = "broker" in Path("/state/profiles").read_text() if Path("/state/profiles").exists() else False
    job_products = {"pacod-gw-core": "gw", "api": "ap", "admin-api": "ap", "mcp-server": "ap", "embeddings": "ap",
                    "ingestion": "ap", "frontend": "ap", "pgbouncer": "ap", "redis": "ap", "pacod-bench": "bench",
                    "pacod-broker": "broker", "pacod-aggregator": "agg"}
    hard = [t for t in down if not (t["labels"]["job"] == "ingestion" and not ingestion)
            and not (t["labels"]["job"] == "pacod-gw-broker" and not bridge)
            and product_runs(job_products.get(t["labels"]["job"]))]
    jobs = sorted({t["labels"]["job"] for t in targets})
    left_out = [p for p in ALL_PRODUCTS if not product_runs(p)]
    report(not hard, f"prometheus: {len(targets) - len(down)}/{len(targets)} targets up ({len(jobs)} jobs)",
           ", ".join(f"{t['labels']['job']}={t['lastError'][:60]}" for t in hard)
           + ("" if ingestion else "  (the app's ingestion is off: DEMO_PROFILES)")
           + (f"  (not running: {', '.join(left_out)}, DEMO_PRODUCTS)" if left_out else ""))

    # One monitoring stack for every product: the gateway's, the app's and the broker's rules and
    # dashboards.
    status, body, _ = http("GET", "http://pacod-prometheus:9090/api/v1/rules")
    files = {g["file"].split("/rules/")[-1].split("/")[0] for g in body["data"]["groups"]} if status == 200 else set()
    groups = len(body["data"]["groups"]) if status == 200 else 0
    report({"gw", "ap", "broker", "agg"} <= files, "prometheus: rules of the gateway, the app, the broker and the aggregator loaded", f"{groups} groups from {sorted(files)}")
    status, body, _ = http("GET", "http://pacod-grafana:3000/api/search?type=dash-db")
    folders = {d.get("folderUid") for d in body} if status == 200 and isinstance(body, list) else set()
    report({"pacod-gw", "pacod", "pacod-broker", "pacod-aggregator"} <= folders,
           "grafana: dashboards of the gateway, the app, the broker and the aggregator",
           f"{len(body) if isinstance(body, list) else 0} dashboards in {sorted(f for f in folders if f)}")


# ---- 3. Single sign-on --------------------------------------------------------------------------
class Jar:
    """A browser's cookies for one site, enough to walk a sign-in by hand."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def absorb(self, hdrs) -> None:
        for value in (hdrs.get_all("Set-Cookie") or []) if hdrs else []:
            pair = value.split(";", 1)[0]
            name, _, val = pair.partition("=")
            self.cookies[name.strip()] = val.strip()

    def header(self) -> dict[str, str]:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())} if self.cookies else {}


def follow(method: str, url: str, jar: Jar, **kw):
    status, body, hdrs = http(method, url, headers={**jar.header(), **kw.pop("headers", {})}, **kw)
    jar.absorb(hdrs)
    return status, body, (hdrs.get("Location", "") if hdrs else "")


def sso() -> None:
    """One sign-in at pacod-id, then every product: its login sends the browser to pacod-id, which
    answers straight back to the product's own callback with a code (no second sign-in), and the
    callback ends in the product's own session. The first product signs in with the password."""
    section("single sign-on (pacod-id, realm pacod: one sign-in, every product)")
    from urllib.parse import parse_qs, urljoin, urlparse

    idjar = Jar()
    products = [
        ("ap", "app (pacod-ap)", E["AP_ORIGIN"], "/auth/login", "/auth/callback"),
        ("gw", "gateway console (pacod-gw)", E["GW_CONSOLE_ORIGIN"], "/auth/login", "/auth/callback"),
        ("cms", "cms studio (pacod-cms)", E["CMS_STUDIO_ORIGIN"], "/mgmt/oauth2/authorization/pacod-id",
         "/mgmt/oauth2/code/pacod-id"),
        ("kb", "knowledge base (pacod-kb)", E["KB_ORIGIN"], "/kb/login", "/kb/auth/callback"),
        ("bench", "benchmark (pacod-benchmark)", E["BENCH_ORIGIN"], "/auth/login", "/auth/callback"),
        ("broker", "broker console (pacod-broker)", E["BROKER_ORIGIN"], "/auth/login", "/auth/callback"),
        ("agg", "aggregator console (pacod-aggregator)", E["AGG_CONSOLE_URL"], "/auth/login", "/auth/callback"),
    ]
    for product, name, origin, login, callback in products:
        if not product_runs(product):
            continue
        jar = Jar()
        status, _, to = follow("GET", origin + login, jar)
        if not to.startswith(ISSUER + "/oidc/authorize"):
            report(False, f"{name}: sends people to pacod-id", f"{status} {to[:70]}")
            continue
        status, _, to = follow("GET", to, idjar)
        to = urljoin(ISSUER, to)
        signed_in_here = False
        if to.startswith(ISSUER) and "flow=" in to:
            # No session at pacod-id yet: the password step of the hosted pages' API.
            flow = parse_qs(urlparse(to).query)["flow"][0]
            st, step, _ = follow("POST", f"{ISSUER}/ui-api/v1/flows/{flow}/password", idjar,
                                 headers={"Origin": ISSUER},
                                 body={"email": E["DEMO_ADMIN_EMAIL"], "password": E["DEMO_ADMIN_PASSWORD"]})
            to = urljoin(ISSUER, step.get("to", "")) if isinstance(step, dict) else ""
            signed_in_here = True
            report(st == 200 and bool(to), f"{name}: the admin signs in at pacod-id", str(st))
        back = urlparse(to)
        has_code = "code" in parse_qs(back.query)
        on_callback = to.startswith(origin + callback)
        report(on_callback and has_code,
               f"{name}: pacod-id answers {'after the sign-in' if signed_in_here else 'without a new sign-in'}",
               f"back to {back.path or to[:60]}")
        if not (on_callback and has_code):
            continue
        status, _, landing = follow("GET", to, jar)
        ok = status in (302, 303, 307) and bool(jar.cookies) and "error" not in landing.lower() \
            and not landing.startswith(ISSUER)
        report(ok, f"{name}: its callback opens a session", f"{status} -> {landing[:50] or '(no redirect)'}")

    # pacod-id gives the admin PID_BOOTSTRAP_ADMIN_ROLES only when it creates the account; the
    # bootstrap gives an older account what it lacks (admin_roles in scripts/bootstrap.py), of the
    # products that run (DEMO_ADMIN_ROLES).
    roles = E.get("DEMO_ADMIN_ROLES")
    if roles is None:
        roles = gw.read_env(Path("/state/env/id.env")).get("PID_BOOTSTRAP_ADMIN_ROLES", "")
    if not idjar.cookies:
        if any(product_runs(p) for p, *_ in products):
            report(False, "the admin's roles", "no sign-in at pacod-id to read them with (above)")
        else:
            report(None, "the admin's roles", "not tested: no product runs to sign in to (DEMO_PRODUCTS)")
        return
    status, body, _ = follow("GET", f"{ISSUER}/ui-api/v1/admin/bindings", idjar)
    bindings = body.get("bindings", []) if isinstance(body, dict) else []
    held = {b["role"] for b in bindings if b.get("scope") == "platform"}
    missing = [r for r in roles.split(",") if r and r not in held]
    report(status == 200 and not missing, "the admin holds every role of the products that run",
           f"{status}, lacks {', '.join(missing)}: make bootstrap" if missing else f"{len(held)} roles")


# ---- 4. The gateway: every product's key works ------------------------------------------------
def gateway() -> None:
    section("gateway (pacod-gw, one key per product)")
    creds = gw.read_env(Path("/state/bootstrap.env"))
    model = E.get("CMS_AI_MODEL", "anthropic/claude-haiku-4-5")
    # The keys of the products that run (the bootstrap makes every product's); the aggregator's when
    # it is the only one that calls the gateway.
    keys = [(product, var) for p, product, var in [("ap", "pacod-ap", "GATEWAY_KEY_API"),
                                                   ("cms", "pacod-cms", "CMS_GW_KEY"),
                                                   ("kb", "pacod-kb", "KB_GW_KEY")] if product_runs(p)]
    if not keys and product_runs("agg"):
        keys = [("pacod-aggregator", "AGG_GW_KEY")]
    if not keys:
        report(None, "the products' keys", "not tested: no product that calls the gateway runs (DEMO_PRODUCTS)")
    for product, var in keys:
        key = creds.get(var)
        if not key:
            report(False, f"{product}: key", f"{var} is not in .state/bootstrap.env (make bootstrap)")
            continue
        auth = {"Authorization": f"Bearer {key}", **AGENT_HEADERS.get(product, {})}
        status, body, _ = http("GET", "http://pacod-gw:8080/v1/models", headers=auth)
        n = len(body.get("data", [])) if isinstance(body, dict) else 0
        report(status == 200 and n > 0, f"{product}: lists models", f"{status}, {n} models")
        mock_script(f"pong from {product}")
        # Past the response cache: every product's call reaches the provider.
        status, body, _ = http("POST", "http://pacod-gw:8080/v1/chat/completions", timeout=60,
                               headers={**auth, "x-gw-cache": "off"},
                               body={"model": model, "messages": [{"role": "user", "content": "ping"}]})
        text = body["choices"][0]["message"]["content"] if status == 200 else str(body)[:120]
        report(status == 200, f"{product}: a chat completion", f"{status} {text[:50]!r}")
    if product_runs("cms"):
        cms_stored_key(creds)
    if keys:
        response_cache(keys[0][0], creds.get(keys[0][1]), model)


def cms_stored_key(creds: dict[str, str]) -> None:
    """The bootstrap hands CMS_GW_KEY to the running studio (/internal/v1/ai-gateway): the studio
    says it uses a stored pair whose key ends as CMS_GW_KEY does. Only the last four characters
    are ever answered."""
    token, key = creds.get("CMS_ADMIN_TOKEN"), creds.get("CMS_GW_KEY")
    if not token or not key:
        report(False, "pacod-cms: the studio holds the key", "CMS_ADMIN_TOKEN or CMS_GW_KEY missing (make bootstrap)")
        return
    status, body, _ = http("GET", "http://cms-studio:7443/internal/v1/ai-gateway",
                           headers={"Authorization": f"Bearer {token}"})
    ok = (status == 200 and isinstance(body, dict) and body.get("source") == "stored"
          and body.get("keyHint") == "…" + key[-4:])
    detail = f"{status}, {body.get('source')} {body.get('keyHint')}" if isinstance(body, dict) else f"{status}"
    report(ok, "pacod-cms: the studio holds the key (stored, live)", detail if ok else f"{detail}: make bootstrap")
    status, body, _ = http("GET", "http://cms-studio:7443/internal/v1/ai-models",
                           headers={"Authorization": f"Bearer {token}"})
    wanted = {f: E.get(f"CMS_AI_MODEL_{f.upper()}", "") or E.get("CMS_AI_MODEL", "")
              for f in ("translate", "pdf", "assist", "vision", "design")}
    held = ({f: v.get("model") for f, v in body.get("features", {}).items()}
            if status == 200 and isinstance(body, dict) else {})
    differ = [f"{f} {held.get(f)} not {m}" for f, m in wanted.items() if m and held.get(f) != m]
    report(status == 200 and not differ, "pacod-cms: the studio uses demo.env's models",
           f"{status}, " + ("; ".join(differ) + ": make bootstrap" if differ else
                            ", ".join(f"{f} {m}" for f, m in held.items())))


def response_cache(product: str, key: str | None, model: str) -> None:
    """The response cache (pacod-gw's docs/plugin-concept.md, section 15), off in a gateway by
    default but on in the demo (overlays/gw.yml): the same call at temperature 0 twice, with the
    key of `product`; the second is answered from gw-valkey, without the provider. Switched off in
    the console, the call reaches the provider and is not kept."""
    if not key:
        return
    auth = {"Authorization": f"Bearer {key}", **AGENT_HEADERS.get(product, {})}
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "user", "content": f"Remember this answer (verify {uuid.uuid4().hex[:8]})."}]}
    # One reply for each call that may reach the mock; what is left is removed below, so that no
    # visitor's call gets it.
    mock_script(*["kept by the response cache"] * 11)
    try:
        status, first, hdrs = http("POST", "http://pacod-gw:8080/v1/chat/completions", headers=auth,
                                   body=body, timeout=60)
        result = hdrs.get("x-gw-cache") if hdrs else None
        if status == 200 and not result:
            attempts = hdrs.get("x-gw-attempts") if hdrs else None
            report(None if attempts != "0" else False,
                   "response cache: off (the demo starts it on: switched off in the console?)",
                   f"{status}, no x-gw-cache, {attempts} attempts")
            return
        if status != 200 or result != "miss":
            report(False, "response cache: a new call is a miss", f"{status}, x-gw-cache {result}")
            return
        # The answer is kept once the call has settled, off the request path.
        for _ in range(10):
            time.sleep(0.5)
            status, again, hdrs = http("POST", "http://pacod-gw:8080/v1/chat/completions", headers=auth,
                                       body=body, timeout=60)
            result = hdrs.get("x-gw-cache") if hdrs else None
            if status != 200 or result == "hit":
                break
    finally:
        http("DELETE", "http://gw-mock:9000/__mock/script")
    content = lambda r: r["choices"][0]["message"]["content"]  # noqa: E731
    same = status == 200 and content(again) == content(first)
    attempts = hdrs.get("x-gw-attempts") if hdrs else None
    report(result == "hit" and attempts == "0" and same, "response cache: the repeated call is a hit (gw-valkey)",
           f"{status}, x-gw-cache {result}, {attempts} attempts, age {hdrs.get('Age') if hdrs else None} s"
           + ("" if same or status != 200 else ", another answer"))


# ---- 4b. Tools and agents: the gateway's /mcp, its tool server pacod-ap, registered agents --------
MCP = "http://pacod-gw:8080/mcp"
GW_ADMIN = "http://pacod-gw-admin:8081/api/v1"
MODERN = "2026-07-28"


def mcp(key: str, method: str, params: dict | None = None, *, modern: bool = False, name: str | None = None,
        headers: dict | None = None):
    """One JSON-RPC request to the gateway's /mcp: the 2025 revisions' way, or 2026-07-28's, whose
    headers must say what the body says (pacod-gw runbook section 21.2)."""
    params = dict(params or {})
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json", **(headers or {})}
    if modern:
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": MODERN}
        h |= {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
        if name:
            h["Mcp-Name"] = name
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        body["params"] = params
    return http("POST", MCP, headers=h, body=body)


def result_of(body) -> dict:
    return body.get("result", {}) if isinstance(body, dict) and isinstance(body.get("result"), dict) else {}


def tools() -> None:
    section("tools and agents (pacod-gw: /mcp, the tool server pacod-ap, registered agents)")
    creds = gw.read_env(Path("/state/bootstrap.env"))
    key = creds.get("TOOLS_GW_KEY")
    trace = f"verify-{uuid.uuid4().hex[:12]}"
    if not key:
        report(False, "the tools key", "TOOLS_GW_KEY is not in .state/bootstrap.env (make bootstrap)")
    else:
        status, body, hdrs = mcp(key, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                     "clientInfo": {"name": "pacod-demo verify", "version": "1"}})
        server = result_of(body).get("serverInfo", {}).get("name")
        session = hdrs.get("Mcp-Session-Id") if hdrs else None
        report(status == 200 and server == "pacod-gw" and not session,
               "/mcp: the gateway answers initialize itself, keeping no session", f"{status}, {server}")
        status, body, _ = mcp(key, "tools/list")
        names = sorted(t.get("name", "") for t in result_of(body).get("tools", []))
        want = {f"{TOOL_PREFIX}{t}" for t in ("list_agents", "list_facts", "get_fact")}
        report(want <= set(names), "/mcp: the key's bundle grants pacod-ap's approved tools",
               f"{status}, {', '.join(names) or 'none'}" + ("" if want <= set(names) else ": make bootstrap"))
        status, body, _ = mcp(key, "tools/call", {"name": f"{TOOL_PREFIX}list_agents", "arguments": {}},
                              headers={"x-gw-trace": trace})
        res = result_of(body)
        ok = status == 200 and isinstance(res.get("content"), list) and not res.get("isError")
        report(ok, "/mcp: pacod-ap__list_agents is called at ap-api, with the gateway's credential",
               f"{status}" + ("" if ok else f" {str(body)[:120]}"))
        status, body, _ = mcp(key, "tools/list", modern=True)
        kind = result_of(body).get("resultType")
        report(status == 200 and kind == "complete", f"/mcp: the {MODERN} revision", f"{status}, resultType {kind}")
        status, _, _ = http("GET", MCP, headers={"Authorization": f"Bearer {key}"})
        report(status == 405, "/mcp: GET is refused (no streams, no sessions)", str(status))
        status, _, _ = http("POST", "http://pacod-gw:8080/a2a/nobody",
                            headers={"Authorization": f"Bearer {key}", "A2A-Version": "1.0"},
                            body={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}})
        report(status == 404, "/a2a: no remote agent is granted (none in the stack speaks A2A)", str(status))
    kb_key = creds.get("KB_GW_KEY")
    if kb_key:
        status, body, _ = mcp(kb_key, "tools/list")
        names = [t.get("name", "") for t in result_of(body).get("tools", [])]
        theirs = [n for n in names if n.startswith(TOOL_PREFIX)]
        report(status == 200 and not theirs, "/mcp: a key without the bundle sees none of pacod-ap's tools",
               f"{status}, {len(names)} tools ({', '.join(names) or 'none'})")

    admin = creds.get("GW_ADMIN_TOKEN")
    if not admin:
        report(False, "agents: the admin token", "GW_ADMIN_TOKEN is not in .state/bootstrap.env (make bootstrap)")
        return
    auth = {"Authorization": f"Bearer {admin}"}

    def items(path: str, field: str = "items") -> tuple[int, list]:
        status, body, _ = http("GET", GW_ADMIN + path, headers=auth)
        return status, (body.get(field, []) if isinstance(body, dict) else [])

    _, orgs = items("/orgs")
    org = next((o["id"] for o in orgs if o.get("name") == "pacod"), None)
    _, spaces = items(f"/orgs/{org}/workspaces") if org else (0, [])
    ws = next((w["id"] for w in spaces if w.get("name") == "pacod"), None)
    if not ws:
        report(False, "agents: the workspace pacod/pacod", "not found (make bootstrap)")
        return
    status, agents = items(f"/orgs/{org}/workspaces/{ws}/agents")
    names = {a.get("name") for a in agents}
    want = {"pacod-ap", "starter", "pacod-kb", "pacod-cms", "pacod-aggregator"}
    report(status == 200 and want <= names, "agents: pacod-ap, its starter profile and the products are registered",
           f"{status}, {', '.join(sorted(n for n in names if n)) or 'none'}"
           + ("" if want <= names else f"; missing {', '.join(sorted(want - names))}: make bootstrap"))
    _, keys = items(f"/orgs/{org}/workspaces/{ws}/keys")
    ap_key = next((k for k in keys if k.get("name") == "pacod-api"), {})
    bound = ap_key.get("agents", [])
    report("starter" in bound and "pacod-ap" in bound, "agents: pacod-ap's key speaks for them",
           ", ".join(bound) or "no agents")
    secret = creds.get("GATEWAY_KEY_API")
    if secret and len(bound) > 1:
        bare, _, _ = http("GET", "http://pacod-gw:8080/v1/models", headers={"Authorization": f"Bearer {secret}"})
        named, _, _ = http("GET", "http://pacod-gw:8080/v1/models",
                           headers={"Authorization": f"Bearer {secret}", "x-gw-agent-id": "starter"})
        report(bare == 403 and named == 200, "agents: a call of that key names its agent",
               f"without x-gw-agent-id {bare}, as starter {named}")

    if key:
        # The record of the call above reaches the analytics service through the core's spool.
        found = None
        for _ in range(15):
            _, calls = items(f"/tool-calls?workspace_id={ws}&trace_id={trace}&limit=5", "tool_calls")
            found = next((c for c in calls if c.get("exposed_name") == f"{TOOL_PREFIX}list_agents"), None)
            if found:
                break
            time.sleep(2)
        report(bool(found) and found.get("verdict") == "allowed" and found.get("target") == "pacod-ap",
               "tool calls: the call is recorded (Insight, Tool calls)",
               f"{found.get('verdict')}, {found.get('status')}, {found.get('total_ms', '?')} ms" if found
               else "no record within 30 s")


# ---- 5. The app: one streamed chat turn -----------------------------------------------------------
def app() -> None:
    section("app (pacod-ap: a chat turn through the gateway)")
    if PUBLIC:
        report(None, "skipped: the turn signs in with a dev token, and a public demo has none")
        return
    classifier = json.dumps({"is_relevant": True, "confidence": 0.95, "detected_language": "en",
                             "reason": "a pricing question", "needs_clarification": False, "follow_ups": []})
    planner = json.dumps({"is_complex": False, "tool_calls": [
        {"name": "get_fact", "args": {"fact_key": "plan_price_monthly", "variant": "plus"}}]})
    if not any(gw.read_env(Path("/state/env/providers.env")).values()):
        mock_script(classifier, planner)
    stamp = uuid.uuid4().hex[:8]
    status, text, _ = http("POST", "http://ap-api:8000/api/v1/chat", timeout=180, raw=True,
                           headers={"Authorization": "Bearer dev-token-test_user"},
                           body={"message": f"What does the Plus plan cost each month? (verify {stamp})",
                                 "session_id": f"verify-{stamp}"})
    sse = text.decode(errors="replace") if isinstance(text, bytes) else str(text)
    done = "event: done" in sse
    tokens = sse.count("event: token")
    report(status == 200 and done, "the stream reaches 'done'", f"{status}, {tokens} token events"
           + ("" if done else f": {sse[:120]!r}"))


# ---- 6. The knowledge base: ingest, find, delete ---------------------------------------------------
def kb() -> None:
    section("knowledge base (pacod-kb: shared Qdrant, embeddings and PostgreSQL)")
    key = {"X-API-Key": E["PIPELINE_API_SECRET"]}
    marker = f"zebrafinch{uuid.uuid4().hex[:6]}"
    doc = (f"# Demo note {marker}\n\nThe pacod demo stores this note to check that ingestion, the shared "
           f"embeddings service and the shared vector store work together. Codeword: {marker}.\n")
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{marker}.md\"\r\n"
            f"Content-Type: text/markdown\r\n\r\n{doc}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"trusted\"\r\n\r\ntrue\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"validate_language\"\r\n\r\nfalse\r\n"
            f"--{boundary}--\r\n").encode()
    status, answer, _ = http("POST", "http://kb-pipeline:8000/ingest/file", body=body,
                             headers={**key, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    job = answer.get("job_id") if isinstance(answer, dict) else None
    if not job:
        report(False, "ingest a document", f"{status} {str(answer)[:120]}")
        return
    state = "?"
    for _ in range(120):
        _, j, _ = http("GET", f"http://kb-pipeline:8000/jobs/{job}", headers=key)
        state = j.get("status", "?") if isinstance(j, dict) else "?"
        if state not in ("pending", "running", "new", "?"):
            break
        time.sleep(1)
    report(state not in ("error", "pending", "running", "new"), "ingest a document", f"job {state}")
    status, found, _ = http("POST", "http://kb-pipeline:8000/query", headers=key, timeout=60,
                            body={"query": f"codeword {marker}", "top_k": 5, "wrap_untrusted": False})
    hits = found.get("results", []) if isinstance(found, dict) else []
    mine = [h for h in hits if marker in h.get("text", "")]
    report(bool(mine), "find it again by search", f"{status}, {len(hits)} results")
    for doc_id in {h["source_id"] for h in mine}:
        status, _, _ = http("DELETE", f"http://kb-pipeline:8000/documents/{doc_id}", headers=key)
        report(status == 200, "delete it", str(status))


# ---- 7. The CMS --------------------------------------------------------------------------------
def cms() -> None:
    section("cms (pacod-cms)")
    status, page, _ = http("GET", E["CMS_DEMO_ORIGIN"] + "/")
    report(status == 200 and "<html" in str(page).lower(), "the demo website renders", str(status))
    # Starward on the magazine template set; an earlier one is replaced as README.md says (The CMS's
    # demo websites).
    status, page, _ = http("GET", E["CMS_SPACE_ORIGIN"] + "/")
    ok = status == 200 and "/site-assets/magazine.css" in str(page)
    report(ok, "Starward renders on the magazine set",
           "" if ok else str(status) if status != 200 else "another set: README.md, The CMS's demo websites")
    check("the studio's login page", E["CMS_STUDIO_ORIGIN"] + "/mgmt/login", want=(200, 302))


# ---- 8. The benchmark: the gateway it manages -----------------------------------------------------
def bench() -> None:
    section("benchmark (pacod-benchmark: the gateway it manages)")
    token = gw.read_env(Path("/state/bootstrap.env")).get("BENCH_GW_ADMIN_TOKEN")
    if not token:
        report(False, "its token for the gateway", "BENCH_GW_ADMIN_TOKEN is not in .state/bootstrap.env (make bootstrap)")
        return
    auth = {"Authorization": f"Bearer {token}"}
    # Only a platform admin may read the system's status, and the benchmark needs one.
    status, _, _ = http("GET", "http://pacod-gw-admin:8081/api/v1/system/status", headers=auth)
    report(status == 200, "its token is a platform admin of the gateway", str(status))
    status, body, _ = http("GET", "http://pacod-gw-admin:8081/api/v1/benchmark-reports", headers=auth)
    kept = len(body.get("items", [])) if isinstance(body, dict) else 0
    report(status == 200, "the gateway takes its reports", f"{status}, {kept} kept")
    # render-env.sh names the gateway once the token exists; make up then recreates bench-api.
    named = gw.read_env(Path("/state/env/bench.env")).get("BENCH_GW_ADMIN_URL")
    report(bool(named), "bench-api is given the gateway to manage", named or "BENCH_GW_ADMIN_URL is empty")


# ---- 9. The broker: pacod-gw as provider, source and witness -------------------------------------
def broker() -> None:
    section("broker (pacod-broker: pacod-gw's bridge, pacod-ap and the CMS)")
    made = gw.read_env(Path("/state/bootstrap.env"))
    if product_runs("gw"):
        broker_gateway(made)
    # pacod-ap, the CMS studio and pacod-kb search it with their own tokens.
    for product, var in (("ap", "BROKER_AP_TOKEN"), ("cms", "CMS_BROKER_TOKEN"), ("kb", "KB_BROKER_TOKEN")):
        if not product_runs(product):
            continue
        if not made.get(var):
            report(False, f"{var} searches the broker", "missing (make bootstrap)")
            continue
        auth = {"Authorization": f"Bearer {made[var]}"}
        status, _, _ = http("POST", "http://pacod-broker:8060/v1/discover", body={"limit": 1}, headers=auth)
        report(status == 200, f"{var} searches the broker", str(status))
    if product_runs("ap"):
        broker_ap(made)


def broker_gateway(made: dict[str, str]) -> None:
    """pacod-gw on the broker: its bridge (gw-broker) and its core."""
    missing = [v for v in ("GWB_GW_TOKEN", "GWB_CONSUMER_TOKEN", "PGW_BROKER_TOKEN") if not made.get(v)]
    if missing:
        report(False, "the bridge's and the core's tokens", f"{', '.join(missing)} missing (make bootstrap)")
        return
    # The bridge reads the platform as broker_bridge, which also reads the calls with tickets.
    auth = {"Authorization": f"Bearer {made['GWB_GW_TOKEN']}"}
    status, _, _ = http("GET", "http://pacod-gw-admin:8081/api/v1/broker/calls", headers=auth)
    report(status == 200, "GWB_GW_TOKEN reads the calls with tickets (broker_bridge)", str(status))
    # The bridge's and the core's pacod-id tokens search the broker.
    for var in ("GWB_CONSUMER_TOKEN", "PGW_BROKER_TOKEN"):
        auth = {"Authorization": f"Bearer {made[var]}"}
        status, _, _ = http("POST", "http://pacod-broker:8060/v1/discover", body={"limit": 1}, headers=auth)
        report(status == 200, f"{var} searches the broker", str(status))
    status, _, _ = http("GET", "http://gw-broker:8097/readyz")
    report(status == 200, "gw-broker ready", str(status))
    # The core sees the broker (its Broker page in the console reads the same).
    admin = made.get("BENCH_GW_ADMIN_TOKEN")
    if admin:
        status, body, _ = http("GET", "http://pacod-gw-admin:8081/api/v1/broker/status",
                               headers={"Authorization": f"Bearer {admin}"})
        reachable = isinstance(body, dict) and body.get("reachable") is True
        report(status == 200 and reachable, "the gateway's core reaches the broker",
               f"{status} {gw.short(body) if not reachable else ''}")


def broker_ap(made: dict[str, str]) -> None:
    """pacod-ap on the broker: its connection, its starter agent and the agents' site."""
    # pacod-ap's own view of the connection the bootstrap provisioned: its Broker page's "Test
    # connection" (the broker answers, and takes pacod-ap's token).
    if PUBLIC:
        report(None, "pacod-ap's connection to the broker",
               "skipped: the test signs in with a dev token, and a public demo has none")
    else:
        status, body, _ = http("POST", "http://ap-admin-api:8000/api/v1/broker/config/test",
                               headers={"Authorization": "Bearer dev-token-test_user"})
        ok = status == 200 and isinstance(body, dict) and body.get("reachable") and body.get("accepted")
        report(bool(ok), "pacod-ap reaches the broker, which takes its token",
               f"as {body.get('client')}, token from the {body.get('token')}" if ok else f"{status} {gw.short(body)}")
    # The starter agent, offered as demo.env's AP_BROKER_OFFER says (scripts/broker-offer.sh), is
    # on the broker: ap-admin-api announces it within a minute of the offer.
    offer, token = E.get("AP_BROKER_OFFER", ""), made.get("BROKER_AP_TOKEN")
    if not offer:
        report(None, "pacod-ap's starter agent on the broker", "not offered (AP_BROKER_OFFER is empty)")
    elif token:
        found, deadline = None, time.monotonic() + 90
        while found is None:
            status, body, _ = http("POST", "http://pacod-broker:8060/v1/discover",
                                   body={"kind": "agent", "limit": 50},
                                   headers={"Authorization": f"Bearer {token}"})
            hits = body.get("hits", []) if isinstance(body, dict) else []
            found = next((h for h in hits if (h.get("provider") or {}).get("name") == "pacod-ap"
                          and (h.get("resource") or {}).get("key") == "agent.starter"), None)
            if found is None:
                if time.monotonic() > deadline:
                    break
                time.sleep(5)
        resource = (found or {}).get("resource") or {}
        urls = [i.get("url") for i in resource.get("interfaces") or []]
        report(found is not None and E["AP_AGENTS_ORIGIN"] + "/v1" in urls,
               "the broker lists pacod-ap's starter agent at the agents' site",
               f"{resource.get('id')} at {', '.join(map(str, urls))}" if found else
               f"{status}: not announced within 90 s (ap-admin-api's broker_announce_failed, make bootstrap)")
        if found:
            seen = (resource.get("access") or {}).get("visibility")
            report(seen == offer, "offered as demo.env says", f"{seen} (AP_BROKER_OFFER={offer})")
    # The agents' site answers, and only with a token.
    status, _, _ = http("POST", E["AP_AGENTS_ORIGIN"] + "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    report(status == 401, "the agents' MCP (agents.) refuses a call without a token", str(status))


# ---- 10. The aggregator: pacod-gw's model data --------------------------------------------------
def aggregator() -> None:
    section("aggregator (pacod-aggregator: pacod-gw's model data)")
    have = gw.read_env(Path("/state/bootstrap.env"))
    status, _, _ = http("GET", "http://pacod-aggregator:8150/v1/catalog")
    report(status == 401, "the catalogue refuses a call without a token", str(status))
    # The rest is the gateway's: its token for the catalogue, and its sync.
    if not product_runs("gw"):
        return
    token = have.get("PGW_AGGREGATOR_TOKEN")
    if not token:
        report(False, "PGW_AGGREGATOR_TOKEN reads the catalogue", "missing (make bootstrap)")
        return
    auth = {"Authorization": f"Bearer {token}"}
    status, body, _ = http("GET", "http://pacod-aggregator:8150/v1/catalog", headers=auth)
    models = len(body.get("models", [])) if isinstance(body, dict) else 0
    report(status == 200, "PGW_AGGREGATOR_TOKEN reads the catalogue", f"{status}, {models} models")
    admin = have.get("BENCH_GW_ADMIN_TOKEN")
    if admin:
        status, job, _ = http("GET", "http://pacod-gw-admin:8081/api/v1/price-job",
                              headers={"Authorization": f"Bearer {admin}"})
        link = job.get("sync", {}).get("aggregator", {}) if isinstance(job, dict) else {}
        report(status == 200 and link.get("configured") and link.get("token"),
               "the gateway's core has the aggregator's address and token", json.dumps(link))
        report(status == 200 and job.get("source") == "pacod_aggregator" and not job.get("problems"),
               "the gateway's sync of model data has no problem", str(job.get("problems")))
        # The only check that takes the three together: the core's fetch, the aggregator, and a
        # service token of pacod-id's. The settings above would pass with any of them broken.
        headers = {"Authorization": f"Bearer {admin}"}
        runs_url = "http://pacod-gw-admin:8081/api/v1/price-job/runs"
        _, before, _ = http("GET", f"{runs_url}?limit=200", headers=headers)
        known = {r["id"] for r in before} if isinstance(before, list) else set()
        status, _, _ = http("POST", "http://pacod-gw-admin:8081/api/v1/price-job/run", headers=headers)
        report(status == 202, "the gateway takes the order to sync", str(status))
        run = None
        for _ in range(60):
            _, runs, _ = http("GET", f"{runs_url}?job=sync&limit=10", headers=headers)
            run = next((r for r in runs if isinstance(runs, list) and r.get("job") == "catalog_sync"
                        and r.get("finished_at") and r.get("id") not in known), None)
            if run:
                break
            time.sleep(2)
        read = bool(run) and run.get("ok") is True and "skipped" not in (run.get("result") or {})
        report(read, "the gateway's sync reads the aggregator's catalogue",
               str(run.get("message")) if run else "no run finished in two minutes")
        # A sync that matches no model reads a full catalogue just the same, so reading proves
        # nothing about the data. The catalogue's lists come from the internet and the demo runs
        # offline: an empty one is a warning. With models in it, a sync that took none is a failure.
        counts = ((run or {}).get("result") or {}).get("counts") or {}
        name = "the gateway's models take data from the catalogue"
        if models:
            report(counts.get("applied", 0) + counts.get("unchanged", 0) > 0, name, json.dumps(counts))
        else:
            report(None, name, "not tested: the catalogue is empty, and its lists need the internet")


# Each section and the product it checks (None: always); one named on the command line runs anyway.
SECTIONS = {"edge": None, "health": None, "sso": None, "gateway": "gw", "tools": "ap", "app": "ap", "kb": "kb",
            "cms": "cms", "bench": "bench", "broker": "broker", "aggregator": "agg"}


def main() -> None:
    only = sys.argv[1:] or [name for name, product in SECTIONS.items() if product_runs(product)]
    left_out = [p for p in ALL_PRODUCTS if not product_runs(p)]
    if left_out:
        print(f"\nthe products that run: {', '.join(p for p in ('id', *ALL_PRODUCTS) if product_runs(p))} (DEMO_PRODUCTS);"
              f" not checked: {', '.join(left_out)}", flush=True)
    for name in only:
        globals()[name]()
    print()
    print(f"verify: {'all checks passed' if not fails else f'{fails} check(s) failed'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
