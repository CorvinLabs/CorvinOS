"""Console request paths must never block forever on an advisory file lock.

Three ``fcntl.flock(LOCK_EX)`` calls with NO timeout sat on operator request
paths:

  * ``routes/workflows.py::_wf_create_lock``  — POST /workflows, POST /workflows/import
  * ``routes/workflows.py::_append_chat_line`` — WS /workflows/{wid}/chat
  * ``routes/chat_settings.py::_save_channel`` — PATCH /chat-settings/{ch}/{key}

A wedged holder (a crashed writer whose fd the kernel had not reaped, an NFS
mount, a debugger-stopped process) hung the request FOREVER; no ``try/except``
can catch a hang. The chat one was worse — it ran inside an ``async def``
handler, so it stalled the whole console event loop, not just one socket.

Same contract as ``core.infinite_session.event_store`` /
``rollback_manager``: ``LOCK_EX | LOCK_NB`` with a bounded deadline, and every
caller converting the deadline into a clean refusal (HTTP 503, or an in-band
``{"type": "error", "code": 503}`` frame on the WebSocket) or a documented
degrade for pure housekeeping writes.

Every test drives the REAL router through FastAPI's TestClient with a REAL
console session (nothing about auth is stubbed) and holds the lock from an
INDEPENDENT file description — exactly what a foreign process holding it looks
like to ``flock``.

Run:  .venv/bin/python -m pytest core/console/tests/test_route_lock_nonblocking.py
"""
from __future__ import annotations

import fcntl
import io
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"), str(_CONSOLE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

TENANT = "_default"

# Keep the wedged-lock tests fast: the deadline is what is under test, not its
# default value. 5.0s is the "did it block?" ceiling in every assertion.
SHORT_DEADLINE = 0.2


def _reset_modules() -> None:
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _console(tmp_path: Path):
    """Real console router + real session cookie + CSRF, under a temp CORVIN_HOME."""
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / TENANT / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="lock-test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


@contextmanager
def _held(lock_path: Path):
    """Hold ``lock_path`` from an independent file description."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


@contextmanager
def _audited(module):
    """Capture the module's console_audit calls without touching the real chain."""
    with patch.object(module, "console_audit") as mock:
        yield mock


def _failed_reasons(mock) -> list[str]:
    return [c.kwargs.get("reason") for c in mock.action_failed.call_args_list]


# ── POST /workflows + POST /workflows/import ──────────────────────────────


def _wf_create_lock_path() -> Path:
    from forge import paths as _fp
    return _fp.tenant_home(TENANT) / ".wf_create.lock"


def test_create_workflow_refuses_503_when_create_lock_is_wedged(tmp_path):
    with _console(tmp_path) as client:
        from corvin_console.routes import workflows as wf
        with patch.object(wf, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(wf) as audit:
            with _held(_wf_create_lock_path()):
                started = time.monotonic()
                res = client.post("/v1/console/workflows", json={"id": "wf_lockbusy"})
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"create blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)
        audit.action_performed.assert_not_called()
        # Nothing was written: the refusal left the tenant untouched.
        from forge import paths as _fp
        assert not (_fp.tenant_home(TENANT) / "workflows" / "wf_lockbusy.meta.json").exists()


def test_create_workflow_succeeds_once_the_lock_is_free(tmp_path):
    """The bounded lock must stay a real mutex — the same create works when free."""
    with _console(tmp_path) as client:
        res = client.post("/v1/console/workflows", json={"id": "wf_lockfree"})
        assert res.status_code == 200, res.text
        assert res.json()["workflow"]["id"] == "wf_lockfree"


def test_import_workflow_refuses_503_when_create_lock_is_wedged(tmp_path):
    yaml_doc = (
        'awp: "1.0.0"\n'
        "workflow:\n"
        "  name: imported_lockbusy\n"
        '  description: "probe"\n'
        "orchestration:\n"
        "  engine: dag\n"
        "  graph: []\n"
    )
    with _console(tmp_path) as client:
        from corvin_console.routes import workflows as wf
        with patch.object(wf, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(wf) as audit:
            with _held(_wf_create_lock_path()):
                started = time.monotonic()
                res = client.post(
                    "/v1/console/workflows/import",
                    files={"file": ("probe.awp.yaml", io.BytesIO(yaml_doc.encode()), "text/yaml")},
                )
                elapsed = time.monotonic() - started

        assert res.status_code == 503, res.text
        assert res.json()["detail"] == "lock_busy"
        assert elapsed < 5.0, f"import blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)
        audit.action_performed.assert_not_called()


# ── PATCH /chat-settings/{channel}/{chat_key} ─────────────────────────────


@contextmanager
def _channel(tmp_path: Path, channel: str = "discord"):
    """Redirect the in-repo bridges dir to a temp copy and seed settings.json.

    ``chat_settings._VOICE_BRIDGES`` points at the CHECKED-IN
    ``operator/bridges/`` tree; a test must never write there.
    """
    from corvin_console.routes import chat_settings as cs
    bridges = tmp_path / "bridges"
    (bridges / channel).mkdir(parents=True, exist_ok=True)
    (bridges / channel / "settings.json").write_text(
        json.dumps({"chat_profiles": {"555": {"persona": "assistant"}}}), encoding="utf-8"
    )
    with patch.object(cs, "_VOICE_BRIDGES", bridges):
        yield bridges / channel / "settings.json"


def test_chat_settings_patch_refuses_503_when_lock_is_wedged(tmp_path):
    with _console(tmp_path) as client:
        from corvin_console.routes import chat_settings as cs
        with _channel(tmp_path) as settings_path:
            before = settings_path.read_text(encoding="utf-8")
            with patch.object(cs, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(cs) as audit:
                with _held(settings_path.with_suffix(".lock")):
                    started = time.monotonic()
                    res = client.patch(
                        "/v1/console/chat-settings/discord/555",
                        json={"persona": "coder"},
                    )
                    elapsed = time.monotonic() - started

            assert res.status_code == 503, res.text
            assert res.json()["detail"] == "lock_busy"
            assert elapsed < 5.0, f"patch blocked for {elapsed:.1f}s"
            assert "lock_busy" in _failed_reasons(audit)
            audit.action_performed.assert_not_called()
            # 503 means "nothing happened, retry" — prove it.
            assert settings_path.read_text(encoding="utf-8") == before


def test_chat_settings_patch_succeeds_once_the_lock_is_free(tmp_path):
    with _console(tmp_path) as client:
        with _channel(tmp_path) as settings_path:
            res = client.patch(
                "/v1/console/chat-settings/discord/555", json={"persona": "coder"}
            )
            assert res.status_code == 200, res.text
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            assert saved["chat_profiles"]["555"]["persona"] == "coder"


# ── WS /workflows/{wid}/chat ──────────────────────────────────────────────


def test_workflow_chat_ws_degrades_and_refuses_in_band_when_append_lock_is_wedged(tmp_path):
    """The append lock is inside an ``async def`` handler — blocking there stalls
    the whole event loop. Two behaviours are asserted while it is wedged:

    1. the opening line (pure housekeeping — regenerated on the next connect)
       DEGRADES: the operator still receives the prompt;
    2. a state-changing turn REFUSES in band with code 503 and an audited,
       content-free ``lock_busy`` record — the socket stays usable.
    """
    with _console(tmp_path) as client:
        from corvin_console.routes import workflows as wf

        res = client.post("/v1/console/workflows", json={"id": "wf_chatlock"})
        assert res.status_code == 200, res.text

        from forge import paths as _fp
        chat_lock = _fp.tenant_home(TENANT) / "workflows" / "wf_chatlock.chat.append.lock"

        with patch.object(wf, "LOCK_TIMEOUT_SECONDS", SHORT_DEADLINE), _audited(wf) as audit:
            with _held(chat_lock):
                started = time.monotonic()
                with client.websocket_connect("/v1/console/workflows/wf_chatlock/chat") as ws:
                    init = ws.receive_json()
                    assert init["type"] == "init"
                    # (1) opening line degrades — delivered even though unsaved
                    opening = ws.receive_json()
                    assert opening["type"] == "message" and opening["role"] == "assistant"

                    # (2) a state-changing turn refuses in band, socket stays open
                    ws.send_json({"type": "accept_template", "key": "daily-digest"})
                    frame = ws.receive_json()
                elapsed = time.monotonic() - started

        assert frame["type"] == "error", frame
        assert frame["code"] == 503, frame
        assert elapsed < 5.0, f"chat WS blocked for {elapsed:.1f}s"
        assert "lock_busy" in _failed_reasons(audit)


def test_workflow_chat_ws_persists_once_the_append_lock_is_free(tmp_path):
    """The bounded append lock must stay a real mutex and still write JSONL."""
    with _console(tmp_path) as client:
        assert client.post("/v1/console/workflows", json={"id": "wf_chatfree"}).status_code == 200

        with client.websocket_connect("/v1/console/workflows/wf_chatfree/chat") as ws:
            assert ws.receive_json()["type"] == "init"
            assert ws.receive_json()["type"] == "message"
            ws.send_json({"type": "accept_template", "key": "daily-digest"})
            frame = ws.receive_json()

        assert frame["type"] == "message", frame

        from forge import paths as _fp
        chat_path = _fp.tenant_home(TENANT) / "workflows" / "wf_chatfree.chat.jsonl"
        lines = [json.loads(x) for x in chat_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert len(lines) == 2, lines
        assert lines[0]["role"] == "assistant" and lines[1].get("phase_update") == "detailing"


# ── The lock helper itself ────────────────────────────────────────────────


def test_bounded_flock_raises_instead_of_waiting(tmp_path):
    with _console(tmp_path):
        from corvin_console.routes import workflows as wf

        lock_path = tmp_path / "probe.lock"
        with _held(lock_path):
            started = time.monotonic()
            with pytest.raises(wf.WorkflowLockBusy):
                with wf._bounded_flock(lock_path, "probe", timeout=SHORT_DEADLINE):
                    pass
            elapsed = time.monotonic() - started
        assert elapsed < 5.0
        # …and it is still a real mutex when nobody holds it.
        with wf._bounded_flock(lock_path, "probe", timeout=SHORT_DEADLINE):
            pass


# ── Live LLM E2E ──────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E") != "1", reason="set CLAUDE_LIVE_E2E=1 to run"
)
def test_workflow_chat_ws_real_design_turn_persists_both_lines(tmp_path):
    """The bounded append lock on the REAL design turn (spawns ``claude -p``).

    The refusal path is proven above with a wedged lock; this proves the other
    half — that with the lock free the same bounded append still persists both
    the user message and the LLM's reply around a real model call. The prompt
    deliberately avoids every ``_TEMPLATES`` keyword so ``_design_turn`` cannot
    short-circuit on a template match and really spawns ``claude -p``.
    """
    with _console(tmp_path) as client:
        assert client.post("/v1/console/workflows", json={"id": "wf_live"}).status_code == 200

        with client.websocket_connect("/v1/console/workflows/wf_live/chat") as ws:
            assert ws.receive_json()["type"] == "init"
            assert ws.receive_json()["type"] == "message"  # opening
            ws.send_json({"type": "user", "text": "Rename photos in a folder using their EXIF date."})
            frames = []
            while len(frames) < 3:
                frame = ws.receive_json()
                frames.append(frame)
                if frame.get("type") == "message" and frame.get("role") == "assistant":
                    break

        assert any(f.get("role") == "user" for f in frames), frames
        replies = [f for f in frames if f.get("role") == "assistant"]
        assert replies and replies[0]["content"].strip(), frames

        from forge import paths as _fp
        chat_path = _fp.tenant_home(TENANT) / "workflows" / "wf_live.chat.jsonl"
        roles = [json.loads(x)["role"] for x in chat_path.read_text().splitlines() if x.strip()]
        assert roles == ["assistant", "user", "assistant"], roles
