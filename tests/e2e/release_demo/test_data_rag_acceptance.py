"""Data chapter acceptance against a RUNNING sandbox console (opt-in).

Everything goes through the real interfaces of a console started from this checkout:
Playwright drives the Databases Connect form (with credentials), Add Provider, the HTTP
bridge form and the Knowledge Hub dialogs; the chat turn is a WebSocket opened from inside
the logged-in page (the transport the SPA uses) and answered by the real ``claude`` engine.

Opt-in - it needs live sidecars and spends a real LLM turn:

    CORVIN_ACCEPT_CONSOLE=http://127.0.0.1:8799     # never the live :8765
    CORVIN_ACCEPT_TRUTH=/tmp/demo-data/truth.json    # generator output
    CORVIN_ACCEPT_PG=127.0.0.1:5433:synthetic_charts:corvin_ro:<password>
    CORVIN_ACCEPT_BM25=http://127.0.0.1:8790/search
    CORVIN_ACCEPT_CHAT=1                              # also run the real chat turn

Tests run in file order and build on each other (connection -> provider -> hub -> chat).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

BASE = os.environ.get("CORVIN_ACCEPT_CONSOLE", "")
pytestmark = pytest.mark.skipif(not BASE, reason="CORVIN_ACCEPT_CONSOLE not set (opt-in acceptance)")

if BASE and re.search(r":(8765|8000)\b", BASE):  # pragma: no cover - refuse the live console
    raise RuntimeError(f"refusing to run acceptance against a live console port: {BASE}")

OUT = Path(os.environ.get("CORVIN_ACCEPT_OUT", "/tmp/corvin-accept"))


@pytest.fixture(scope="module")
def browser_page():
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        page.goto(f"{BASE}/v1/console/auth/local-login?next=/console/app/data-sources")
        page.wait_for_load_state("networkidle")
        yield page
        ctx.close()
        browser.close()


def _api(page, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """A same-origin fetch from the logged-in page (cookie + CSRF like the SPA)."""
    res = page.evaluate(
        """async ([method, path, body]) => {
            const who = await (await fetch('/v1/console/auth/whoami', {credentials: 'include'})).json();
            const r = await fetch(path, {method, credentials: 'include',
              headers: {'Content-Type': 'application/json', 'X-CSRF-Token': who.csrf_token},
              body: body === null ? undefined : JSON.stringify(body)});
            let j = null; try { j = await r.json(); } catch (e) {}
            return [r.status, j];
        }""",
        [method, path, body],
    )
    return res[0], res[1]


# ── 1. Databases: register PostgreSQL through the Connect form ──────────────────────────

def test_register_postgres_through_the_connect_form(browser_page):
    host, port, db, user, password = os.environ["CORVIN_ACCEPT_PG"].split(":", 4)
    page = browser_page
    page.goto(f"{BASE}/console/app/data-sources")
    page.get_by_test_id("connect-database-btn").click()
    page.get_by_test_id("db-type-postgresql").click()
    page.get_by_test_id("conn-name-input").fill("charts-pg")
    page.get_by_test_id("config-host").fill(host)
    page.get_by_test_id("config-port").fill(port)
    page.get_by_test_id("config-database").fill(db)
    page.get_by_test_id("config-ssl_mode").select_option("disable")
    page.get_by_test_id("cred-PGUSER").fill(user)
    page.get_by_test_id("cred-PGPASSWORD").fill(password)
    submit = page.get_by_test_id("conn-submit-btn")
    if submit.is_disabled():
        raise AssertionError("Connect button is disabled: " + str(page.evaluate(
            "() => [...document.querySelectorAll('input,select')].filter(e => !e.checkValidity())"
            ".map(e => e.getAttribute('data-testid') || e.id || e.name)")))
    with page.expect_response(lambda r: "/data-sources/_secrets/PGPASSWORD" in r.url) as sec:
        with page.expect_response(lambda r: r.url.endswith("/v1/console/data-sources")
                                  and r.request.method == "POST") as reg:
            submit.click()
    assert sec.value.status == 200, sec.value.text()
    assert reg.value.status == 201, reg.value.text()
    page.get_by_test_id("conn-row-charts-pg").wait_for(timeout=15000)
    page.screenshot(path=str(OUT / "01-databases-registered.png"), full_page=True)
    # the manifest holds names only; the names endpoint lists them
    status, names = _api(page, "GET", "/v1/console/data-sources/_secrets")
    assert status == 200 and {"PGUSER", "PGPASSWORD"} <= set(names["names"])
    status, conn = _api(page, "GET", "/v1/console/data-sources/charts-pg")
    assert status == 200 and conn["secrets"] == ["PGUSER", "PGPASSWORD"]
    assert password not in json.dumps(conn)
    status, ping = _api(page, "POST", "/v1/console/data-sources/charts-pg/test")
    assert status == 200 and ping["ok"] is True, ping


# ── 2. Knowledge: register the BM25 provider through Add Provider ───────────────────────

def test_register_bm25_through_add_provider(browser_page):
    endpoint = os.environ["CORVIN_ACCEPT_BM25"]
    page = browser_page
    page.goto(f"{BASE}/console/app/custom-provider")
    page.locator("#provider_id").fill("bm25-demo")
    page.locator("#name").fill("Synthetic charts catalogue")
    page.locator("#description").fill("BM25 over the synthetic songs, artists and albums")
    page.locator("#author").fill("release-demo")
    page.get_by_role("button", name="Next").click()
    page.locator("#endpoint").fill(endpoint)
    page.locator("#auth_type").select_option("none")
    page.get_by_role("button", name="Test API Connectivity").click()
    page.get_by_text("Connected!").wait_for(timeout=15000)
    page.get_by_role("button", name="Next").click()
    page.locator("#content_path").fill("results[].content")
    page.locator("#score_path").fill("results[].score")
    page.locator("#metadata_path").fill("results[]")
    page.get_by_role("button", name="Next").click()
    page.get_by_text("keyword-search").first.click()
    page.locator("#classification").select_option("INTERNAL")
    with page.expect_response(lambda r: "/custom-provider/create" in r.url) as created:
        page.get_by_role("button", name="Create Provider").click()
    assert created.value.json()["status"] == "created", created.value.text()
    page.get_by_test_id("provider-created").wait_for()

    page.goto(f"{BASE}/console/app/rag")
    badge = page.get_by_test_id("rag-health-badge").first
    badge.wait_for(timeout=20000)
    page.wait_for_function(
        "() => document.querySelector('[data-testid=rag-health-badge]')?.textContent.includes('Healthy')",
        timeout=40000)
    page.screenshot(path=str(OUT / "02-knowledge-provider-healthy.png"), full_page=True)

    page.get_by_role("tab", name=re.compile("Query Tester")).click()
    page.locator("#query").fill("Glasswind Cobbleway")
    page.get_by_role("button", name="Execute").click()
    page.get_by_test_id("rag-query-result").wait_for(timeout=20000)
    assert "Parallaxo Records" in page.get_by_test_id("rag-query-result").inner_text()
    page.screenshot(path=str(OUT / "03-knowledge-query.png"), full_page=True)


# ── 3a. HTTP bridge form: network access field, valid placeholder, readable errors ──────

def test_http_bridge_form(browser_page):
    page = browser_page
    page.goto(f"{BASE}/console/app/data-sources")
    page.get_by_test_id("add-http-adapter-btn").click()
    page.get_by_test_id("adapter-id-input").fill("demo-bridge")
    page.get_by_test_id("adapter-name-input").fill("Demo bridge")
    page.get_by_test_id("adapter-base-url-input").fill("https://example.com")
    assert page.get_by_test_id("adapter-egress-select").input_value() == "restricted"
    page.get_by_test_id("adapter-auth-type-select").select_option("bearer")
    env_input = page.get_by_test_id("adapter-auth-env-var-input")
    assert env_input.get_attribute("placeholder").startswith("e.g. CORVIN_DS_")
    env_input.fill("CORVIN_DS_DEMO_KEY")
    with page.expect_response(lambda r: "/data-sources/adapters/http/demo-bridge" in r.url) as put:
        page.get_by_test_id("adapter-submit-btn").click()
    assert put.value.status == 200, put.value.text()
    assert put.value.request.post_data_json["network_egress"] == "restricted"
    page.get_by_test_id("ping-http-adapter-demo-bridge").click()
    # a public host without the bridge protocol answers non-2xx: a sentence, not a bare "Error"
    page.get_by_text(re.compile(r"The server (answered with HTTP \d+|did not answer)|"
                                r"The ping did not complete")).wait_for(timeout=20000)
    page.screenshot(path=str(OUT / "04-http-bridge-ping.png"), full_page=True)
    page.once("dialog", lambda d: d.accept())
    page.get_by_test_id("remove-http-adapter-demo-bridge").click()
    page.get_by_test_id("ping-http-adapter-demo-bridge").wait_for(state="detached", timeout=10000)


# ── 3b. Knowledge Hub dialogs: publish, review, import, export ───────────────────────────

def test_hub_publish_review_import_export(browser_page):
    page = browser_page
    page.goto(f"{BASE}/console/app/rag-hub")
    page.get_by_test_id("hub-publish-select").select_option("bm25-demo")
    page.get_by_test_id("hub-publish-btn").click()
    page.get_by_test_id("hub-notice").filter(has_text="Published").wait_for(timeout=10000)
    page.get_by_test_id("hub-card-bm25-demo").wait_for()
    assert "community" not in page.content().lower()

    page.get_by_test_id("hub-review-btn-bm25-demo").click()
    page.get_by_test_id("hub-review-rating").select_option("4")
    page.get_by_test_id("hub-review-text").fill("Finds every synthetic track by name.")
    page.get_by_test_id("hub-review-confirm").click()
    page.get_by_test_id("hub-notice").filter(has_text="Review added").wait_for(timeout=10000)
    page.wait_for_function(
        "() => document.querySelector('[data-testid=hub-rating-bm25-demo]')?.textContent === '4'",
        timeout=40000)

    page.get_by_test_id("hub-import-btn-bm25-demo").click()
    page.get_by_test_id("hub-import-id").fill("bm25-demo-copy")
    page.get_by_test_id("hub-import-confirm").click()
    page.get_by_test_id("hub-notice").filter(has_text="imported").wait_for(timeout=10000)
    page.screenshot(path=str(OUT / "05-hub-imported.png"), full_page=True)
    status, listed = _api(page, "GET", "/v1/console/rag/providers")
    assert {"bm25-demo", "bm25-demo-copy"} <= {p["id"] for p in listed["providers"]}

    with page.expect_download() as dl:
        page.get_by_test_id("hub-export-btn-bm25-demo").click()
    exported = Path(dl.value.path()).read_text()
    assert "kind: RAGProvider" in exported and "bm25-demo" in exported

    status, summary = _api(page, "GET", "/v1/console/hub/analytics/summary")
    assert status == 200 and summary["total_providers"] == 1 and summary["total_reviews"] == 1


# ── 4. A real chat turn, real claude engine: one answer from the DB, one from RAG ────────

@pytest.mark.skipif(not os.environ.get("CORVIN_ACCEPT_CHAT"), reason="CORVIN_ACCEPT_CHAT not set")
def test_real_chat_turn_answers_from_db_and_rag(browser_page):
    truth = json.loads(Path(os.environ["CORVIN_ACCEPT_TRUTH"]).read_text())
    de = next(f for f in truth["facts"] if f["id"] == "fact_01")
    track = de["answer"]["track_name"]
    artist = de["answer"]["artist_names"]
    page = browser_page
    page.goto(f"{BASE}/console/app/chat")
    status, created = _api(page, "POST", "/v1/console/chat/sessions", {"title": "acceptance"})
    assert status == 200, created
    sid = created["session"]["sid"]
    prompt = (
        "Use only the tools, do not guess. "
        "1) With datasource_query on the connection charts-pg, find the track_uri with the most "
        "streams in country DE (table charts_songs_daily, SUM(streams)) and its track_name from "
        "the songs table. "
        "2) The database answer has no artist: with rag_query, look up that track name in the "
        "knowledge catalogue and report its artist and record label. "
        "Answer in one line: TRACK=<name>; STREAMS=<number>; ARTIST=<artist>; LABEL=<label>."
    )
    events = page.evaluate(
        """async ([sid, prompt]) => {
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const ws = new WebSocket(`${proto}://${location.host}/v1/console/chat/sessions/${sid}/stream`);
            const out = [];
            return await new Promise((resolve) => {
              const timer = setTimeout(() => { out.push({type: 'timeout'}); ws.close(); resolve(out); }, 420000);
              ws.onmessage = (m) => {
                const ev = JSON.parse(m.data); out.push(ev);
                if (ev.type === 'ready') ws.send(JSON.stringify({type: 'user', text: prompt}));
                if (ev.type === 'done' && out.some(e => e.type === 'turn_started' || e.type === 'delta')) {
                  clearTimeout(timer); ws.close(); resolve(out);
                }
              };
              ws.onerror = () => { out.push({type: 'ws_error'}); };
            });
        }""",
        [sid, prompt],
    )
    (OUT / "chat-events.json").write_text(json.dumps(events, indent=1, default=str))
    text = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    final = next((e.get("text", "") for e in reversed(events) if e.get("type") == "result"), "") or text
    (OUT / "chat-answer.txt").write_text(final)
    tools = json.dumps([e for e in events if "tool" in str(e.get("type", ""))])
    assert "datasource_query" in tools, f"no datasource_query call; events: {[e.get('type') for e in events]}"
    assert "rag_query" in tools, "no rag_query call"
    assert track in final, final
    assert str(de["value"]) in final.replace(",", "").replace(".", ""), final
    # from RAG: the artist (truth.json fact_01.answer.artist_names) and the catalogue label
    assert artist in final, final
    assert "Parallaxo Records" in final, final
