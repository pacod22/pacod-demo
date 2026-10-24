"""The home page's screenshots (make shots, inside the pacod-demo-shots image, on pacod-net).

Signs in once through pacod-id as the demo's administrator, then visits each product, whose own
sign-in completes against that session, and writes home/screenshots/<name>.png: the viewport only,
light mode. Pages that show secrets (gateway keys, provider settings, mails with their links) are
left out on purpose: the screenshots are committed. The password is never printed.

Chromium resolves every *.localhost name to its own loopback, whatever Docker's DNS says, and a
public domain to wherever its DNS points: the resolver rule sends the demo's names to the edge
(pacod-caddy) on pacod-net, and the pages keep their real host names, the ones the products'
sign-in redirects are registered for.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

E = os.environ
OUT = Path("/work/home/screenshots")
DOMAIN = E["DEMO_DOMAIN"]
SITE_DOMAIN = E["DEMO_SITE_DOMAIN"]

# (file name, address): what the home page shows for each product.
SHOTS = [
    ("id-admin", E["PACOD_ID_ISSUER"] + "/admin"),
    ("gw-console", E["GW_CONSOLE_ORIGIN"] + "/"),
    ("ap-dashboard", E["AP_ORIGIN"] + "/dashboard"),
    ("ap-site", E["AP_ORIGIN"] + "/"),
    ("cms-studio", E["CMS_STUDIO_ORIGIN"] + "/mgmt/sites"),
    ("cms-site", E["CMS_DEMO_ORIGIN"] + "/"),
    ("cms-space", E["CMS_SPACE_ORIGIN"] + "/"),
    ("kb", E["KB_ORIGIN"] + "/kb"),
]


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle", timeout=30_000)


def sign_in(page: Page) -> None:
    password = E.get("DEMO_ADMIN_PASSWORD") or E["DEMO_ADMIN_PASSWORD_GENERATED"]
    page.goto(E["PACOD_ID_ISSUER"] + "/admin")
    settle(page)
    page.fill("input[name=email]", E["DEMO_ADMIN_EMAIL"])
    page.fill("input[name=password]", password)
    page.get_by_role("button", name="Sign in").click()
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=30_000)
    except Exception:  # noqa: BLE001
        sys.exit("shots: the sign-in at pacod-id failed")
    settle(page)


def visit(page: Page, url: str) -> None:
    page.goto(url)
    settle(page)
    # The studio's sign-in page asks for one click; pacod-id's session does the rest.
    if "/mgmt/login" in page.url:
        page.get_by_role("link", name="Sign in").or_(page.get_by_role("button", name="Sign in")).first.click()
        settle(page)
        page.goto(url)
        settle(page)
    # The app's cookie banner would cover the page.
    necessary = page.get_by_role("button", name="Necessary only")
    if necessary.count() and necessary.first.is_visible():
        necessary.first.click()
        page.wait_for_timeout(300)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[f"--host-resolver-rules=MAP *.{DOMAIN} pacod-caddy, MAP *.{SITE_DOMAIN} pacod-caddy"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800}, color_scheme="light", locale="en-GB"
        )
        page = context.new_page()
        sign_in(page)
        for name, url in SHOTS:
            visit(page, url)
            if "/login" in page.url:
                sys.exit(f"shots: {name} still asks for a sign-in ({page.url})")
            page.screenshot(path=str(OUT / f"{name}.png"))
            print(f"  {name:14} {page.url}", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
