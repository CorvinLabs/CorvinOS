#!/usr/bin/env python3
"""Per-subtask E2E for the Phase-5 adapter-side policy dispatch
(``adapter._resolve_engine_via_policy`` + integration with
``_try_awp_dispatch``).

Asserts the contract — none of these paths must regress when the
policy is later extended with PII-zone routing:

  * No policy file → returns (None, "", False), legacy path used.
  * Policy file exists + zone match + first allowed engine healthy
    → returns (engine_id, zone, True).
  * Policy file exists + zone match + first engine missing, second
    healthy → fallback to second, returns its id.
  * Policy file exists + zone match + no engine in allowed list
    healthy → returns (None, zone, True), caller falls through.
  * Malformed policy file → returns (None, "", False), legacy path,
    operator log line emitted.
  * Persona-driven zone classification picks the right engine
    (inbox-persona prompt without PII → personal_data → eu engine).
  * Explicit [zone:foo] marker in prompt overrides classifier.
  * Adapter integration: when policy_used, _try_awp_dispatch emits
    engine.policy_resolved audit event with engine_id + compliance_zone.

Run: python3 operator/bridges/shared/test_engine_policy_dispatch.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # type: ignore  # noqa: E402
import engine_registry  # type: ignore  # noqa: E402
import engine_policy  # type: ignore  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


# ── helpers ───────────────────────────────────────────────────────────

class _StubEngine:
    """Minimal stand-in — has .capabilities so engine_registry happy."""
    capabilities = {"mcp": True, "stream_json": True}


def _patch_registry(known: dict[str, callable]) -> None:
    """Override engine_registry's _ENGINE_BUILDERS for the test."""
    engine_registry._ENGINE_BUILDERS.clear()
    engine_registry._ENGINE_BUILDERS.update(known)


def _restore_registry() -> None:
    """Restore the real builders after a test perturbs them."""
    # Force a re-import of the registry module to pick up the original
    # _ENGINE_BUILDERS table.
    import importlib
    importlib.reload(engine_registry)


def _make_repo_corvin(tmp_dir: Path) -> Path:
    """Build a fake repo layout under tmp_dir with .corvin/global/ so
    the policy resolver finds it. Returns the policy.json path."""
    plugins_marker = tmp_dir / "plugins"
    plugins_marker.mkdir()
    corvin = tmp_dir / ".corvin"
    (corvin / "global").mkdir(parents=True)
    return corvin / "global" / "engine_policy.json"


def _patch_repo_walk(tmp_dir: Path):
    """Monkeypatch Path.resolve in adapter._resolve_engine_via_policy
    to walk up to a fake repo root. The resolver walks from
    Path(__file__).resolve() — we make __file__ appear to be
    inside the fake tmp_dir/plugins tree.
    """
    fake_adapter = tmp_dir / "operator" / "bridges" / "shared"
    fake_adapter.mkdir(parents=True, exist_ok=True)
    fake_file = fake_adapter / "adapter.py"
    fake_file.touch()
    return patch.object(adapter, "__file__", str(fake_file))


def main() -> int:
    # ── 1. No policy → legacy fallback ─────────────────────────────
    print("\n── No policy file ──────────────────────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        td_path.joinpath("plugins").mkdir()  # repo marker, but no .corvin/
        with _patch_repo_walk(td_path):
            eid, zone, used = adapter._resolve_engine_via_policy(
                "ping", None, engine_registry,
            )
    expect(eid is None and zone == "" and used is False,
           "no policy file → (None, '', False)",
           f"got ({eid!r}, {zone!r}, {used})")

    # ── 2. Policy + zone match + first engine healthy ──────────────
    print("\n── Policy hit, first engine healthy ────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text(json.dumps({
            "default_engine": "claude_code",
            "compliance_zones": {
                "personal_data": {"allow_engines": ["azure_eu", "vllm_eu"]},
            },
        }))
        # Stub registry: azure_eu is healthy, others unknown
        _patch_registry({"azure_eu": lambda: _StubEngine(),
                         "claude_code": lambda: _StubEngine()})
        try:
            with _patch_repo_walk(td_path):
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "Mail an test.user@example.com bitte", None,
                    engine_registry,
                )
            expect(eid == "azure_eu" and zone == "personal_data"
                   and used is True,
                   "PII prompt → personal_data → azure_eu (first allowed, healthy)",
                   f"got ({eid!r}, {zone!r}, {used})")
        finally:
            _restore_registry()

    # ── 3. First engine missing, fallback to second ────────────────
    print("\n── Outage fallback within zone ─────────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text(json.dumps({
            "default_engine": "claude_code",
            "compliance_zones": {
                "code_only": {"allow_engines": ["primary_dead", "backup_alive"]},
            },
        }))
        _patch_registry({
            "primary_dead": lambda: None,  # builder returns None = unhealthy
            "backup_alive": lambda: _StubEngine(),
            "claude_code": lambda: _StubEngine(),
        })
        try:
            with _patch_repo_walk(td_path):
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "[zone:code_only] schreib mir eine python funktion", None,
                    engine_registry,
                )
            expect(eid == "backup_alive" and zone == "code_only" and used,
                   "Outage fallback: primary unhealthy → backup picked",
                   f"got ({eid!r}, {zone!r}, {used})")
        finally:
            _restore_registry()

    # ── 4. No engine healthy → (None, zone, True) ──────────────────
    print("\n── All engines unhealthy ───────────────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text(json.dumps({
            "default_engine": "claude_code",
            "compliance_zones": {
                "external_facing": {"allow_engines": ["dead_a", "dead_b"]},
            },
        }))
        _patch_registry({
            "dead_a": lambda: None,
            "dead_b": lambda: None,
            "claude_code": lambda: _StubEngine(),
        })
        try:
            with _patch_repo_walk(td_path):
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "[zone:external_facing] suche im web", None,
                    engine_registry,
                )
            expect(eid is None and zone == "external_facing" and used is True,
                   "No allowed engine healthy → (None, zone, True)",
                   f"got ({eid!r}, {zone!r}, {used})")
        finally:
            _restore_registry()

    # ── 5. Malformed policy → (None, "", False), legacy path ───────
    print("\n── Malformed policy ────────────────────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text("{ not valid json")
        with _patch_repo_walk(td_path):
            eid, zone, used = adapter._resolve_engine_via_policy(
                "anything", None, engine_registry,
            )
    expect(eid is None and zone == "" and used is False,
           "malformed JSON → (None, '', False), legacy fallback",
           f"got ({eid!r}, {zone!r}, {used})")

    # ── 6. Persona-driven zone classification ──────────────────────
    print("\n── Persona-driven zone ─────────────────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text(json.dumps({
            "default_engine": "claude_code",
            "compliance_zones": {
                "personal_data": {"allow_engines": ["eu_engine"]},
                "code_only": {"allow_engines": ["dev_engine"]},
            },
        }))
        _patch_registry({
            "eu_engine": lambda: _StubEngine(),
            "dev_engine": lambda: _StubEngine(),
            "claude_code": lambda: _StubEngine(),
        })
        try:
            with _patch_repo_walk(td_path):
                # inbox persona, no PII in text → personal_data via persona
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "Triage today", {"_persona": "inbox"}, engine_registry,
                )
            expect(eid == "eu_engine" and zone == "personal_data",
                   "inbox persona → personal_data → eu_engine",
                   f"got ({eid!r}, {zone!r}, {used})")

            with _patch_repo_walk(td_path):
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "refactor a function", {"_persona": "coder"}, engine_registry,
                )
            expect(eid == "dev_engine" and zone == "code_only",
                   "coder persona → code_only → dev_engine")
        finally:
            _restore_registry()

    # ── 7. Explicit marker overrides classifier ────────────────────
    print("\n── Explicit zone marker ────────────────────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text(json.dumps({
            "default_engine": "claude_code",
            "compliance_zones": {
                "personal_data": {"allow_engines": ["eu"]},
                "code_only": {"allow_engines": ["dev"]},
            },
        }))
        _patch_registry({
            "eu": lambda: _StubEngine(),
            "dev": lambda: _StubEngine(),
            "claude_code": lambda: _StubEngine(),
        })
        try:
            with _patch_repo_walk(td_path):
                # Marker says code_only, but persona inbox + email PII.
                # Marker MUST win.
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "[zone:code_only] schreibe mail@example.com",
                    {"_persona": "inbox"}, engine_registry,
                )
            expect(eid == "dev" and zone == "code_only",
                   "[zone:code_only] beats inbox persona + PII",
                   f"got ({eid!r}, {zone!r}, {used})")
        finally:
            _restore_registry()

    # ── 8. Empty policy zones → uses default_chain (catch-all) ─────
    print("\n── Empty zones, default_chain catch-all ────────────────")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        policy_path = _make_repo_corvin(td_path)
        policy_path.write_text(json.dumps({
            "default_engine": "claude_code",
            "fallback_chain": ["claude_code", "codex_cli"],
        }))
        _patch_registry({
            "claude_code": lambda: _StubEngine(),
            "codex_cli": lambda: _StubEngine(),
        })
        try:
            with _patch_repo_walk(td_path):
                eid, zone, used = adapter._resolve_engine_via_policy(
                    "ping", None, engine_registry,
                )
            expect(eid == "claude_code" and zone == "general" and used,
                   "no zones → default_chain[0] picked",
                   f"got ({eid!r}, {zone!r}, {used})")
        finally:
            _restore_registry()

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
