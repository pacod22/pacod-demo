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
fails = 0


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


# ---- 1. The edge: every public address answers through pacod-caddy -------------------------------
def edge() -> None:
    section("edge (pacod-caddy, through the public names)")
    status, disc = check("pacod-id discovery", f"{ISSUER}/.well-known/openid-configuration")
    if status == 200:
        report(disc.get("issuer") == ISSUER, "issuer is the configured one", str(disc.get("issuer")))
    check("app", E["AP_ORIGIN"] + "/", want=(200, 302, 303))
    check("gateway console", E["GW_CONSOLE_ORIGIN"] + "/", want=(200, 302, 303))
    check("gateway data plane", E["GW_API_ORIGIN"] + "/v1/models", want=(401,))
    check("cms studio", E["CMS_STUDIO_ORIGIN"] + "/mgmt/login", want=(200, 302))
    check("cms demo website", E["CMS_DEMO_ORIGIN"] + "/", want=(200,))
    check("knowledge base", E["KB_ORIGIN"] + "/kb/healthz", contains='"identity"')
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
    check("home page", home, headers=hdrs, contains=E["CMS_STUDIO_ORIGIN"] + "/")


# ---- 2. Health of every service ------------------------------------------------------------------
def health() -> None:
    section("health (pacod-net)")
    for name, url in [
        ("pacod-id ready", "http://pacod-id:9170/readyz"),
        ("pacod-gw data plane ready", "http://pacod-gw:8080/readyz"),
        ("pacod-gw management ready (pacod-admin-net)", "http://pacod-gw-admin:8081/readyz"),
        ("gw-analytics ready", "http://gw-analytics:8090/readyz"),
        ("gw-tester", "http://gw-tester:8095/healthz"),
        ("ap-api", "http://ap-api:8000/health"),
        ("ap-admin-api", "http://ap-admin-api:8000/health"),
        ("ap-mcp", "http://ap-mcp:8000/health"),
        ("pacod-embeddings", "http://pacod-embeddings:8080/health"),
        ("cms-site", "http://cms-site:8443/web/v1/health"),
        ("cms-studio", "http://cms-studio:7443/mgmt-health"),
        ("kb-pipeline", "http://kb-pipeline:8000/health"),
        ("pacod-qdrant", "http://pacod-qdrant:6333/readyz"),
        ("pacod-prometheus", "http://pacod-prometheus:9090/-/ready"),
        ("pacod-alertmanager", "http://pacod-alertmanager:9093/-/ready"),
        ("pacod-grafana", "http://pacod-grafana:3000/api/health"),
    ]:
        check(name, url)
    status, body, _ = http("GET", "http://kb-ui:8080/kb/healthz")
    ident = body.get("identity") if isinstance(body, dict) else None
    report(status == 200 and ident == "ready", "kb-ui signed in to pacod-id", f"identity={ident}")

    # Prometheus: every target up (the app's ingestion only when it runs).
    status, body, _ = http("GET", "http://pacod-prometheus:9090/api/v1/targets?state=active")
    if status != 200:
        report(False, "prometheus targets", str(status))
        return
    targets = body["data"]["activeTargets"]
    ingestion = "ingestion" in E.get("DEMO_PROFILES", "")
    down = [t for t in targets if t["health"] != "up"]
    hard = [t for t in down if not (t["labels"]["job"] == "ingestion" and not ingestion)]
    jobs = sorted({t["labels"]["job"] for t in targets})
    report(not hard, f"prometheus: {len(targets) - len(down)}/{len(targets)} targets up ({len(jobs)} jobs)",
           ", ".join(f"{t['labels']['job']}={t['lastError'][:60]}" for t in hard)
           + ("" if ingestion else "  (the app's ingestion is off: DEMO_PROFILES)"))

    # One monitoring stack for every product: the gateway's and the app's rules and dashboards.
    status, body, _ = http("GET", "http://pacod-prometheus:9090/api/v1/rules")
    files = {g["file"].split("/rules/")[-1].split("/")[0] for g in body["data"]["groups"]} if status == 200 else set()
    groups = len(body["data"]["groups"]) if status == 200 else 0
    report({"gw", "ap"} <= files, "prometheus: rules of the gateway and the app loaded", f"{groups} groups from {sorted(files)}")
    status, body, _ = http("GET", "http://pacod-grafana:3000/api/search?type=dash-db")
    folders = {d.get("folderUid") for d in body} if status == 200 and isinstance(body, list) else set()
    report({"pacod-gw", "pacod"} <= folders, "grafana: dashboards of the gateway and the app",
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
        ("app (pacod-ap)", E["AP_ORIGIN"], "/auth/login", "/auth/callback"),
        ("gateway console (pacod-gw)", E["GW_CONSOLE_ORIGIN"], "/auth/login", "/auth/callback"),
        ("cms studio (pacod-cms)", E["CMS_STUDIO_ORIGIN"], "/mgmt/oauth2/authorization/pacod-id",
         "/mgmt/oauth2/code/pacod-id"),
        ("knowledge base (pacod-kb)", E["KB_ORIGIN"], "/kb/login", "/kb/auth/callback"),
    ]
    for name, origin, login, callback in products:
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


# ---- 4. The gateway: every product's key works ------------------------------------------------
def gateway() -> None:
    section("gateway (pacod-gw, one key per product)")
    creds = gw.read_env(Path("/state/bootstrap.env"))
    model = E.get("CMS_AI_MODEL", "anthropic/claude-haiku-4-5")
    for product, var in [("pacod-ap", "GATEWAY_KEY_API"), ("pacod-cms", "CMS_GW_KEY"), ("pacod-kb", "KB_GW_KEY")]:
        key = creds.get(var)
        if not key:
            report(False, f"{product}: key", f"{var} is not in .state/bootstrap.env (make bootstrap)")
            continue
        auth = {"Authorization": f"Bearer {key}"}
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
    response_cache(creds.get("GATEWAY_KEY_API"), model)


def response_cache(key: str | None, model: str) -> None:
    """The response cache (pacod-gw's docs/plugin-concept.md, section 15), off in a gateway by
    default but on in the demo (overlays/gw.yml): the same call at temperature 0 twice; the second
    is answered from gw-valkey, without the provider. Switched off in the console, the call reaches
    the provider and is not kept."""
    if not key:
        return
    auth = {"Authorization": f"Bearer {key}"}
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


def main() -> None:
    only = sys.argv[1:] or ["edge", "health", "sso", "gateway", "app", "kb", "cms"]
    for name in only:
        globals()[name]()
    print()
    print(f"verify: {'all checks passed' if not fails else f'{fails} check(s) failed'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
