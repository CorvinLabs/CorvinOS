"""SRF-6: a compute stage artifact SVG must not run script in the console origin.

It stays inline (the panel shows it in an <img>) but carries
``Content-Security-Policy: sandbox`` + ``nosniff`` - the rule chat attachments use
(``attachments_common.serve_headers``). Driven over HTTP through the real route.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.cookie)</script></svg>'


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from corvin_console import audit as console_audit
    from corvin_console import deps
    from corvin_console.routes import compute

    monkeypatch.setattr(console_audit, "action_performed", lambda **kw: None)
    rec = SimpleNamespace(tenant_id="_default", sid_fingerprint="fp", tier="owner")
    app = FastAPI()
    app.include_router(compute.router, prefix="/v1/console")
    app.dependency_overrides[deps.require_session] = lambda: rec
    d = compute._pipelines_dir("_default") / "p1" / "stages" / "s1" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "chart.svg").write_text(SVG, encoding="utf-8")
    (d / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return TestClient(app)


def test_stage_svg_is_served_sandboxed(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/console/compute/pipelines/p1/stages/s1/artifact-image/chart.svg")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_stage_png_stays_inline_with_nosniff(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/console/compute/pipelines/p1/stages/s1/artifact-image/plot.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["x-content-type-options"] == "nosniff"
