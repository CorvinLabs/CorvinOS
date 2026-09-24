"""corvinOS.shared.voice_models — the offline-voice SSOT.

Downloads run against a real local HTTP server (no mocks of the transfer
itself), including a server that lies about Content-Length and closes early —
the truncated-model case that used to be accepted as "present" forever.
"""
from __future__ import annotations

import http.server
import json
import re
import threading
from pathlib import Path

import pytest

from corvinOS.shared import voice_models as vm

REPO = Path(__file__).resolve().parents[1]
MODEL_BYTES = b"\x08" * 1_200_000
MODEL_JSON = json.dumps({"audio": {"sample_rate": 22050}}).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    truncate = False
    hits: list[str] = []

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        body = MODEL_JSON if self.path.endswith(".json") else MODEL_BYTES
        if self.path.startswith("/missing/"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if type(self).truncate and not self.path.endswith(".json"):
            self.wfile.write(body[: len(body) // 3])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)


@pytest.fixture()
def server():
    _Handler.truncate = False
    _Handler.hits = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture()
def voice_dir(tmp_path, monkeypatch):
    d = tmp_path / "corvin-voice"
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(d))
    monkeypatch.delenv("CORVIN_PIPER_MODEL_DIR", raising=False)
    monkeypatch.setattr(vm, "piper_engine_available", lambda: True)
    vm._JOBS.clear()
    return d


@pytest.mark.parametrize("code,expected", [
    ("de-DE", "de"), ("zh-Hans", "zh"), ("nb", "no"), ("pt_BR", "pt"),
    ("EN", "en"), ("", ""), (None, ""),
])
def test_normalise_lang(code, expected):
    assert vm.normalise_lang(code) == expected


def test_every_console_language_is_offline_or_declared_online_only():
    src = (REPO / "core/console/corvin_console/web-next/src/pages/voice.tsx").read_text()
    block = src[src.index("LANG_OPTIONS"):]
    block = block[: block.index("];")]
    codes = {vm.normalise_lang(c) for c in re.findall(r'value:\s*"([^"]+)"', block)}
    assert codes, "could not read LANG_OPTIONS"
    unknown = codes - set(vm.PIPER_VOICES) - vm.NO_OFFLINE_VOICE
    assert not unknown, f"console offers languages with no voice decision: {sorted(unknown)}"


def _stems_in(path: Path) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    start = src.index("_PIPER_MODELS: dict[str, str] = {")
    block = src[start: src.index("}", start)]
    return dict(re.findall(r'"([a-z]{2})":\s*"([^"]+)"', block))


@pytest.mark.parametrize("path", [
    "corvin_operator/voice/scripts/say.py",
    "corvin_operator/bridges/shared/adapter.py",
])
def test_runtime_stem_tables_match_the_ssot(path):
    ssot = {lang: rel.rsplit("/", 1)[-1] for lang, rel in vm.PIPER_VOICES.items()}
    assert _stems_in(REPO / path) == ssot


def test_installer_uses_the_same_table():
    from corvinOS.installer.steps import piper
    assert {k: v[1] for k, v in piper._MODELS.items()} == vm.PIPER_VOICES


def test_fetch_rejects_a_truncated_transfer_and_leaves_nothing(server, tmp_path):
    _Handler.truncate = True
    dest = tmp_path / "m.onnx"
    assert vm.fetch(f"{server}/x.onnx", dest, timeout=5, attempts=1) is False
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_fetch_404_is_a_failure_not_an_error_page(server, tmp_path):
    dest = tmp_path / "m.onnx"
    assert vm.fetch(f"{server}/missing/x.onnx", dest, timeout=5, attempts=1) is False
    assert not dest.exists()


def test_download_records_config_and_heals_a_corrupt_model(server, voice_dir, monkeypatch):
    monkeypatch.setattr(vm, "HF_BASE", server)
    stem = vm.stem_for("sv")
    bad = voice_dir / "piper-models" / f"{stem}.onnx"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"<html>truncated</html>")  # non-empty, but not a model
    assert vm.model_path("sv") is None
    onnx = vm.download_piper_model("sv-SE")
    assert onnx == bad and onnx.stat().st_size == len(MODEL_BYTES)
    assert vm.model_valid(onnx)
    cfg = json.loads((voice_dir / "config.json").read_text())
    assert cfg["piper_model_sv"] == str(onnx)
    assert vm.model_path("sv") == onnx
    assert vm.language_status("sv")["state"] == "ready"


def test_status_never_claims_another_languages_model(server, voice_dir, monkeypatch):
    monkeypatch.setattr(vm, "HF_BASE", server)
    vm.download_piper_model("de")
    assert vm.language_status("de")["state"] == "ready"
    assert vm.language_status("fr")["state"] == "missing"
    assert vm.language_status("ja")["state"] == "online_only"


def test_request_downloads_in_the_background_once(server, voice_dir, monkeypatch):
    monkeypatch.setattr(vm, "HF_BASE", server)
    done: list[dict] = []
    first = vm.request("fi", on_done=done.append)
    again = vm.request("fi")
    assert first["state"] in ("queued", "downloading")
    assert again["state"] in ("queued", "downloading", "ready")
    final = vm.wait("fi", timeout=30)
    assert final["state"] == "ready"
    assert done and done[0]["state"] == "ready"
    assert sum(1 for h in _Handler.hits if h.endswith(".onnx")) == 1
    # already present → no new job, no new transfer
    assert vm.request("fi")["state"] == "ready"
    assert sum(1 for h in _Handler.hits if h.endswith(".onnx")) == 1


def test_request_reports_failure_and_can_retry(server, voice_dir, monkeypatch):
    monkeypatch.setattr(vm, "HF_BASE", f"{server}/missing")
    vm.request("cs")
    st = vm.wait("cs", timeout=60)
    assert st["state"] == "failed" and "could not download" in st["error"]
    monkeypatch.setattr(vm, "HF_BASE", server)
    vm.request("cs")
    assert vm.wait("cs", timeout=30)["state"] == "ready"


def test_online_only_language_starts_no_job(voice_dir):
    assert vm.request("ko")["state"] == "online_only"
    assert "ko" not in vm._JOBS
