"""Layer Forge's two model calls have a deadline (2026-10-08 review, R4-A3-2).

The review runs inside the request of POST /layer-forge/definitions and of every
layer in a bundle import; the route holds a thread-pool worker and one import slot
for as long as it takes. The Anthropic SDK default is a 10-minute timeout with 2
retries, so a hung call held that slot for up to ~30 minutes while the 429 answer
advertised ``Retry-After: 15``.

These tests use the REAL SDK against a local server that accepts connections and
never answers — the boundary a mocked ``client.messages.create`` cannot exercise.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("anthropic")

from core.orchestration.layer_forge import llm_plan, review  # noqa: E402

MANIFEST = {"id": "nordwind.deadline", "version": "1.0.0",
            "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
            "quality_gates": [], "enforcement_rules": []}


class _BlackHole:
    """Accepts TCP connections, reads the request, never answers. Counts attempts."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self._held: list[socket.socket] = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            self.connections += 1
            self._held.append(conn)          # keep it open: no answer, no close

    def close(self) -> None:
        self._stop = True
        for c in self._held:
            c.close()
        self.sock.close()


@pytest.fixture
def black_hole(monkeypatch):
    bh = _BlackHole()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{bh.port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key-0000000000000000")
    yield bh
    bh.close()


def _run_bounded(fn, limit_s):
    """Run fn in a thread; a call that ignores its deadline fails the test instead of hanging it."""
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(result=fn()), daemon=True)
    t0 = time.monotonic()
    t.start()
    t.join(limit_s)
    assert not t.is_alive(), f"the call was still running after {limit_s}s: no deadline"
    return out["result"], time.monotonic() - t0


def test_a_hung_review_ends_in_an_ERROR_verdict_after_one_retry(black_hole, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_TIMEOUT_S", 0.8)
    monkeypatch.setattr(review, "REVIEW_MAX_RETRIES", 1)
    verdict, took = _run_bounded(lambda: review.review_layer_definition(MANIFEST, []), 20)
    assert verdict.status == "ERROR"                       # fail-closed: the layer is not created
    assert 1.4 <= took < 8, took                           # two attempts of ~0.8 s (+ SDK backoff), not minutes
    assert black_hole.connections == 2                     # exactly one retry — not the SDK default of two


def test_the_production_deadline_is_what_the_docs_promise():
    assert review.REVIEW_TIMEOUT_S == 45.0 and review.REVIEW_MAX_RETRIES == 1
    assert review.REVIEW_WORST_CASE_S == 90.0
    assert llm_plan.PLAN_TIMEOUT_S == 60.0 and llm_plan.PLAN_MAX_RETRIES == 1


def test_a_hung_plan_call_raises_instead_of_waiting(black_hole, monkeypatch):
    monkeypatch.setattr(llm_plan, "PLAN_TIMEOUT_S", 0.8)
    monkeypatch.setattr(llm_plan, "PLAN_MAX_RETRIES", 0)

    def attempt():
        try:
            llm_plan.generate_manifest_from_intent("L34", "audit the data flow")
        except llm_plan.LLMPlanError as exc:
            return str(exc)
        return None

    err, took = _run_bounded(attempt, 20)
    assert err and "LLM call failed" in err
    assert took < 6 and black_hole.connections == 1


def test_the_plan_call_uses_only_arguments_the_installed_sdk_accepts():
    """messages.create() lost `temperature` in SDK 1.8.0: the PLAN phase raised TypeError before
    any network traffic, and every existing plan test mocked the client so none could see it.
    Binding the real call's keywords to the real signature catches that whole class."""
    import ast
    import inspect
    import pathlib

    import anthropic

    accepted = set(inspect.signature(anthropic.resources.messages.Messages.create).parameters)
    for path in (llm_plan.__file__, review.__file__):
        tree = ast.parse(pathlib.Path(path).read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "create"
                 and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "messages"]
        assert calls, f"no messages.create call found in {path} (the test would be vacuous)"
        for call in calls:
            used = {k.arg for k in call.keywords if k.arg}
            assert used <= accepted, f"{path}: {sorted(used - accepted)} not accepted by the installed SDK"
