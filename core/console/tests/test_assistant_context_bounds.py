"""Unit tests — `AssistantMessageRequest.context` has no size/depth bound.

Confirmed blind spot (adversarial review, 2026-07-13): `message` is capped at
4000 chars and `HistoryEntry.content` at 2000 chars (list length <= 10) via
Pydantic ``Field(max_length=...)``, but `context: dict[str, Any]` has NO size,
depth, or per-value length constraint. ``_build_context_tag`` reads
``ctx["personas"]`` and joins arbitrary-length items with no cap, and the
resulting string is concatenated into ``full_prompt`` with no cap.

UPDATE (R3-C1, 2026-09-07): ``full_prompt`` used to be a SINGLE LITERAL ARGV
ELEMENT (``subprocess.run(["claude", "-p", ..., full_prompt])``), which added a
hard kernel boundary on top of the missing cap — a single argv string past
``MAX_ARG_STRLEN`` (~128 KB) makes Linux refuse exec with
``OSError: [Errno 7] Argument list too long`` (E2BIG). That argv placement is
gone: the prompt now travels on **stdin** (`input=…`) through the shared
``guard_prompt_head`` neutraliser, so E2BIG from a large context is no longer
reachable. The MISSING CAP itself is still open — an oversized `context` is
still accepted and still spent on tokens; it just no longer trips exec.

This suite:
  (1) proves the Pydantic model accepts an oversized/deeply-nested `context`
      with NO validation error (documents the missing cap that exists on the
      sibling fields) — STILL OPEN;
  (2) pins the FIXED transport: the oversized `context` reaches the subprocess
      on stdin, is absent from argv entirely, and carries the shared prompt
      guard;
  (3) proves the route's generic exception handler still masks a spawn-level
      ``OSError`` as an "ok" response with a benign message instead of
      surfacing a distinguishable signal — STILL OPEN.

Uses the same direct-call + monkeypatch pattern as test_console_spawn_gates.py
(no live LLM, no live house-rules classifier, deterministic).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


class AssistantContextBoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        self._patches: list[tuple] = []

    def _patch(self, obj, attr: str, value) -> None:
        self._patches.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def tearDown(self) -> None:
        for obj, attr, original in reversed(self._patches):
            setattr(obj, attr, original)
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_TENANT_ID", None)

    def _fake_session_record(self):
        from corvin_console import auth as _auth
        return _auth.SessionRecord(
            sid="s", sid_fingerprint="fp", tier="owner", tenant_id="_default",
            token_fingerprint="tf", csrf_secret="cs",
            created_at=0.0, last_seen_at=0.0, expires_at=2_000_000_000.0,
        )

    def _patch_assistant_compute_gate(self, asst):
        from corvin_console.routes import _compute_license_gate as _clg
        self._patch(_clg, "enforce_chat_turns", lambda *a, **k: None)

    def _patch_gate_pass(self, asst):
        """Bypass the house-rules pre-spawn gate deterministically (benign)."""
        self._patch(asst._spawn_gates, "check_console_spawn_or_refusal", lambda *a, **k: None)

    # ── (1) Pydantic model accepts unbounded `context` with no error ────────

    def test_context_field_accepts_oversized_payload_with_no_validation_error(self) -> None:
        from corvin_console.routes import assistant as asst

        # 50 MB of `personas` content — vastly larger than the 4000/2000-char
        # caps enforced on the sibling `message` / `history` fields.
        oversized_ctx = {"personas": ["x" * 1_000_000] * 50}
        req = asst.AssistantMessageRequest(message="hi", context=oversized_ctx)
        self.assertEqual(len(req.context["personas"]), 50)
        self.assertEqual(len(req.context["personas"][0]), 1_000_000)

    def test_context_field_accepts_deeply_nested_payload_with_no_validation_error(self) -> None:
        from corvin_console.routes import assistant as asst

        nested: dict = {}
        cur = nested
        for _ in range(5_000):
            cur["n"] = {}
            cur = cur["n"]

        # `context: dict[str, Any]` performs no recursive validation (Any
        # bypasses Pydantic's schema walk), so this is accepted instantly —
        # unlike a depth-checked schema, which would reject or blow the stack.
        req = asst.AssistantMessageRequest(message="hi", context=nested)
        self.assertIn("n", req.context)

    # ── (2) oversized `context` travels on stdin, never on argv ─────────────

    def test_oversized_context_travels_on_stdin_and_never_on_argv(self) -> None:
        """R3-C1 regression (inverted from the round-1 blind-spot test).

        The prompt used to be a positional argv element, which made the missing
        `context` cap an exec-level failure AND made the operator's own message
        parseable by the CLI as flags (`--version`) and slash commands
        (`/pwn`). It now goes on stdin, behind the shared neutraliser.
        """
        from corvin_console.routes import assistant as asst
        from agents.claude_code import PROMPT_HEAD_SENTINEL

        self._patch_assistant_compute_gate(asst)
        self._patch_gate_pass(asst)

        captured: dict = {}

        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def _capture_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _R()

        self._patch(asst.subprocess, "run", _capture_run)

        huge_personas = "x" * 2_000_000
        body = asst.AssistantMessageRequest(
            message="Where am I?",
            context={"personas": [huge_personas]},
        )
        resp = asst.assistant_message(body, self._fake_session_record())

        self.assertTrue(resp["ok"])
        argv = captured["argv"]
        stdin_text = captured["kwargs"]["input"]

        # Nothing user-derived is on argv — no E2BIG surface, and no argv
        # element the CLI could parse as a flag.
        for element in argv:
            self.assertNotIn(huge_personas, element)
            self.assertNotIn("Where am I?", element)
        self.assertNotIn("--", argv)

        # The prompt reaches the CLI on stdin, behind the shared sentinel.
        self.assertTrue(stdin_text.startswith(PROMPT_HEAD_SENTINEL + "\n"))
        self.assertIn(huge_personas, stdin_text)
        # STILL OPEN: no cap is applied anywhere in the pipeline.
        self.assertGreater(len(stdin_text), 2_000_000)

    # ── (3) the real E2BIG/OSError failure mode is silently swallowed ───────

    def test_oversized_context_oserror_is_silently_swallowed_as_generic_message(self) -> None:
        """Pins down the remaining bug: ANY OS-level spawn failure is caught by
        the route's blanket `except Exception` and turned into an
        indistinguishable-from-benign 200 response, with no 4xx and no
        operator-visible signal that a boundary was hit.

        E2BIG specifically is no longer reachable from a large `context` (the
        prompt moved to stdin in R3-C1), so it is simulated here — the masking
        behaviour it exposes is what is still open.
        """
        from corvin_console.routes import assistant as asst

        self._patch_assistant_compute_gate(asst)
        self._patch_gate_pass(asst)

        def _raise_e2big(argv, **kwargs):
            raise OSError(7, "Argument list too long")

        self._patch(asst.subprocess, "run", _raise_e2big)

        body = asst.AssistantMessageRequest(
            message="Where am I?",
            context={"personas": ["x" * 2_000_000]},
        )
        resp = asst.assistant_message(body, self._fake_session_record())

        # Current (still-buggy) behavior: masked as a benign-looking "ok" response
        # with a generic message — indistinguishable from any other transient
        # failure, and NOT surfaced as a 4xx / distinguishable error to the
        # operator or to any caller inspecting the response.
        self.assertTrue(resp["ok"])
        self.assertEqual(
            resp["response"], "An unexpected error occurred. Please try again."
        )


if __name__ == "__main__":
    unittest.main()
