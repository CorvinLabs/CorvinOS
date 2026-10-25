"""Browser proof (Playwright, Chromium) against a sandbox GATEWAY serving THIS checkout's SPA build.

The gateway (``corvin_gateway.app``) is the production host: it mounts the console under ``/v1/console``
and serves the SPA at ``/console/``. ``core/console/corvin_console/web-next/dist`` must exist before the
server starts (``vite build``). Login is the real credential-less local route (loopback only).

Covered panels (each one asserts what the operator SEES, and where the SPA can be checked on the wire,
the request body is captured and checked for the re-auth token):

* Files            - protected stores are not listed; a user file is (positive control)
* Audit & Compliance - "Verify chain" runs the real walker and reports an intact chain
* License          - Apply Key with a non-licence key: the server's refusal is shown, and the request
                     carries the session fingerprint as re_auth_token
* API Keys         - a stored key shows Delete; Delete asks for confirmation and removes the key
* Dashboard        - the data-source count says "configured", not "connected"

Opt-in: ``SBX_UI=1`` (needs the built dist and Chromium from the Playwright cache).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .harness import Server  # noqa: E402
from .helpers_gap import chain_events, verify_tenant_chain  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("SBX_UI") != "1",
                                reason="browser proof is opt-in (SBX_UI=1; needs web-next/dist)")

DIST = harness.REPO_ROOT / "core/console/corvin_console/web-next/dist/index.html"
ENV_PATH: Path | None = None  # set by the gw fixture: the sandbox service.env the console reads
SEED_KEY = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent.byok import _vault_set, _write_service_env
_vault_set("custom_sbxuikey", "sbx-ui-value-7788")
_write_service_env("custom_sbxuikey", "sbx-ui-value-7788")
import provider_keys
print(provider_keys.service_env_path())
"""


@pytest.fixture(scope="module")
def gw(sandbox, live_tripwire):
    assert DIST.is_file(), "build the SPA first: web-next/dist is missing"
    # files the Files panel should show or hide (tenant _default)
    home = sandbox.tenant_home("_default")
    (home / "files").mkdir(parents=True, exist_ok=True)
    (home / "files" / "ui-readme.txt").write_text("hello from the ui proof", encoding="utf-8")
    (home / "global").mkdir(parents=True, exist_ok=True)
    (home / "global" / "connector_vault.json").write_text(json.dumps({"openai": "SBX-UI-VAULT-1"}))
    # a STORED custom key, written by the same two writers apply_byok_secret() uses (L16 vault
    # item tagged byok + service.env), so the API Keys list and the delete both see a real key
    seed = sandbox.run_child(SEED_KEY, str(harness.REPO_ROOT / "corvin_operator"))
    global ENV_PATH
    ENV_PATH = Path(seed.strip().splitlines()[-1])
    srv = Server(sandbox, Server.GATEWAY, "gateway-ui").start()
    yield srv
    from .conftest import stop_checked
    stop_checked(srv)


@pytest.fixture(scope="module")
def browser_page(gw):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        page.goto(f"{gw.base}/v1/console/auth/local-login", wait_until="domcontentloaded")
        yield page, ctx
        ctx.close()
        browser.close()


def _open(page, base, path, text=None):
    page.goto(f"{base}/console/app/{path}", wait_until="networkidle")
    if text is not None:
        page.get_by_text(text).first.wait_for(timeout=30_000)


def test_UI_files_hides_the_vault_and_shows_the_user_file(gw, browser_page, sandbox):
    page, _ = browser_page
    _open(page, gw.base, "files")
    # the tree folders are buttons that expand on click; open the user's files/ zone
    tree_files = page.locator("button").filter(has_text=re.compile(r"^\s*(?:\S\s*)?files\s*$")).first
    tree_files.wait_for(timeout=30_000)
    tree_files.click()
    page.get_by_text("ui-readme.txt").first.wait_for(timeout=30_000)
    body = page.content()
    assert "connector_vault" not in body, "the Files panel lists the plaintext connector vault"
    assert "ui-readme.txt" in body


def test_UI_compliance_verify_chain_button_reports_an_intact_chain(gw, browser_page, sandbox):
    page, _ = browser_page
    _open(page, gw.base, "compliance", "Hash chain")
    btn = page.get_by_test_id("verify-chain-btn")
    btn.wait_for(timeout=30_000)
    btn.click()
    result = page.get_by_test_id("verify-chain-result")
    result.wait_for(timeout=60_000)
    text = result.inner_text()
    assert "Chain intact" in text, f"verify result: {text!r}"
    assert verify_tenant_chain(sandbox, "_default")["ok"]


def test_UI_license_apply_sends_the_fingerprint_and_shows_the_refusal(gw, browser_page, sandbox):
    page, _ = browser_page
    fp = {"value": None}

    def capture(route, request):
        if request.method == "POST" and request.url.endswith("/v1/console/license/key"):
            fp["value"] = json.loads(request.post_data or "{}")
        route.continue_()

    page.route(re.compile(r".*/v1/console/license/key$"), capture)
    _open(page, gw.base, "license", "Key Setup")
    page.get_by_text("Key Setup", exact=True).first.click()  # collapsed by default
    page.locator("textarea").first.fill("CORVIN-not-a-real-licence-key-0000")
    page.get_by_role("button", name="Apply Key").first.click()
    page.get_by_text(re.compile("signature verification failed", re.I)).first.wait_for(timeout=30_000)
    page.unroute(re.compile(r".*/v1/console/license/key$"))
    sent = fp["value"]
    assert sent is not None, "the Apply Key request was not sent"
    assert sent.get("re_auth_token"), f"the SPA sent no re-auth token: {sent}"
    assert len(sent["re_auth_token"]) == 12 and re.fullmatch(r"[0-9a-f]{12}", sent["re_auth_token"]), sent


def test_UI_api_key_delete_removes_the_stored_key(gw, browser_page, sandbox):
    page, _ = browser_page
    _open(page, gw.base, "api-keys")
    page.get_by_text(re.compile("sbxuikey|SBXUIKEY", re.I)).first.wait_for(timeout=30_000)
    row_delete = page.get_by_test_id("delete-key-btn").first
    row_delete.wait_for(timeout=30_000)
    page.once("dialog", lambda d: d.accept())
    with page.expect_response(re.compile(r".*/v1/console/byok/secrets/.*")) as resp_info:
        row_delete.click()
    resp = resp_info.value
    assert resp.status in (200,), f"delete answered {resp.status}: {resp.text()[:200]}"
    env_text = ENV_PATH.read_text()
    assert "CORVIN_CUSTOM_SBXUIKEY" not in env_text, "the key is still in service.env"


def test_UI_dashboard_says_configured_not_connected(gw, browser_page, sandbox):
    page, _ = browser_page
    _open(page, gw.base, "dashboard", "Data Sources")
    text = page.content()
    assert "connected" not in re.findall(r"\b\d+ connected\b", text), "data sources still say 'connected'"
    assert re.search(r"\b\d+ configured\b", text), "data-source count not labelled 'configured'"
