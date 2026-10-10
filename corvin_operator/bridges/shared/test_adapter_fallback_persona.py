"""E2E: a turn with no pinned persona runs as the fallback persona, even with no
persona files installed.

Regression (2026-09-26): c93ef9915 deleted every persona JSON (bundle + cowork),
so ``cowork.list_available()`` returns ``[]`` and ``_apply_auto_routing`` returned
the profile untouched — no persona at all. The explicit-skill namespace gate is
fail-closed on an unresolved persona, so EVERY explicitly requested skill was
refused with ``persona_unresolved`` (visible in the live adapter journal).

Driven through ``adapter.process_one`` with a sandboxed inbox/outbox/audit and
the fake-claude hook. What must hold:

  * the turn is attributed to the fallback persona (``bridge.persona_routed``
    with persona ``assistant`` in the audit chain);
  * an explicit ``assistant.*`` skill request passes the namespace gate
    (diagnosed as ``not_found`` for a skill that is not on disk — never
    ``persona_unresolved``);
  * a request outside the fallback namespace is still refused
    (``wrong_namespace``) — the gate stays closed, only the identity is restored.

Run as: python3 corvin_operator/bridges/shared/test_adapter_fallback_persona.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "corvin_operator" / "bridges" / "shared"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _fresh_adapter(env_overrides: dict[str, str]):
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    return importlib.import_module("adapter")


def _run_turn(text: str, mode: str, *, with_args: bool = False):
    base = Path(tempfile.mkdtemp(prefix="adapter-fallback-persona-"))
    for d in ("inbox", "outbox", "processed"):
        (base / d).mkdir()
    audit_path = base / "audit.jsonl"
    env = {
        "ADAPTER_INBOX": str(base / "inbox"),
        "ADAPTER_OUTBOX": str(base / "outbox"),
        "ADAPTER_PROCESSED": str(base / "processed"),
        "ADAPTER_FAKE_CLAUDE": "1",
        "ADAPTER_ROUTING_MODE": mode,
        "VOICE_AUDIT_PATH": str(audit_path),
        "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
        "CORVIN_HOME": str(base / "home"),
        # No persona files anywhere — the state of this install since c93ef9915.
        "COWORK_USER_DIR": str(base / "cowork"),
        # The args dump makes the fake path build the real argv — including the
        # system prompt with the skill block — so the namespace gate runs.
        "ADAPTER_FAKE_ARGS_DUMP": str(base / "args.jsonl"),
    }
    prev = {k: os.environ.get(k) for k in env}
    try:
        adapter = _fresh_adapter(env)
        import skill_inject  # same module object the adapter uses
        skill_inject._request_diag_counts.clear()
        skill_inject._request_diag_seen.clear()
        in_file = base / "inbox" / "m1.json"
        in_file.write_text(json.dumps({"id": "m1", "channel": "discord", "from": "u-1",
                                       "chat_id": "c-1", "text": text, "ts": time.time()}))
        # No voice note: the summary step would otherwise call a real TTS provider.
        adapter.process_one(in_file, settings={"whitelist": ["u-1"], "voice_summary_mode": "never"})
        events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()] \
            if audit_path.exists() else []
        if with_args:
            dumps = [json.loads(l) for l in (base / "args.jsonl").read_text().splitlines() if l.strip()] \
                if (base / "args.jsonl").exists() else []
            return events, dict(skill_inject._request_diag_counts), dumps
        return events, dict(skill_inject._request_diag_counts)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _routed(events: list[dict]) -> list[str]:
    return [(e.get("details") or {}).get("persona") or e.get("persona")
            for e in events if e.get("event_type") == "bridge.persona_routed"]


def test_fallback_identity_restored():
    for mode in ("heuristic", "off"):
        print(f"\n[no persona files, routing mode={mode}]")
        events, diag = _run_turn("please use the skill assistant.demo_panel for this", mode)
        t("turn attributed to fallback persona 'assistant'", "assistant" in _routed(events),
          detail=str(_routed(events)))
        t("explicit assistant.* request passes the namespace gate",
          diag.get("persona_unresolved", 0) == 0 and diag.get("not_found", 0) >= 1, detail=str(diag))


def test_gate_stays_closed_outside_the_namespace():
    print("\n[request outside the fallback namespace]")
    _events, diag = _run_turn("please use the skill code.refactor_helper for this", "heuristic")
    t("cross-namespace request refused as wrong_namespace",
      diag.get("wrong_namespace", 0) >= 1 and diag.get("persona_unresolved", 0) == 0, detail=str(diag))


def _mcp_servers(argv: list[str]) -> dict:
    if "--mcp-config" not in argv:
        return {}
    cfg = argv[argv.index("--mcp-config") + 1]
    try:
        return (json.loads(Path(cfg).read_text()) or {}).get("mcpServers") or {}
    except (OSError, ValueError):
        # inline JSON form
        try:
            return (json.loads(cfg) or {}).get("mcpServers") or {}
        except ValueError:
            return {}


def test_unpinned_turn_gets_the_capability_profile():
    """ADR-0537 (accepted 2026-10-10): no persona routing, but every unpinned
    turn carries the one capability profile the console uses. Until then the
    fallback set only the identity, so Discord turns had no Forge/SkillForge
    MCP server and no forged tools."""
    print("\n[unpinned turn → capability profile]")
    _events, _diag, dumps = _run_turn("hello there", "heuristic", with_args=True)
    t("fake CLI argv was dumped", bool(dumps), detail=str(len(dumps)))
    if not dumps:
        return
    prof, argv = dumps[-1]["profile"] or {}, dumps[-1]["args"]
    t("profile carries forge_enabled + skill_forge_enabled",
      bool(prof.get("forge_enabled")) and bool(prof.get("skill_forge_enabled")),
      detail=str({k: prof.get(k) for k in ("forge_enabled", "skill_forge_enabled", "_auto_routed")}))
    servers = _mcp_servers(argv)
    t("real argv carries the forge + skill_forge MCP servers",
      "forge" in servers and "skill_forge" in servers, detail=str(sorted(servers)))
    env = (servers.get("forge") or {}).get("env") or {}
    t("forge MCP runs as the fallback identity (namespace gate)",
      env.get("FORGE_PERSONA") == "assistant", detail=str(env.get("FORGE_PERSONA")))


if __name__ == "__main__":
    test_fallback_identity_restored()
    test_gate_stays_closed_outside_the_namespace()
    test_unpinned_turn_gets_the_capability_profile()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
