"""Layer 28.2 per-subtask E2E — user_model (ADR-0016).

Covers:
  L28.2/1  UserModel.empty + load returns None when absent
  L28.2/2  save → load round-trip preserves curated fields
  L28.2/3  save sets mode 0o600
  L28.2/4  forget removes file + emits audit
  L28.2/5  schema validation caps list length + entry length
  L28.2/6  schema validation drops non-string list items + extra keys
  L28.2/7  _extract_json_object tolerates prose wrapping
  L28.2/8  distill happy path with stub judge → audit + saved
  L28.2/9  distill: judge-unparseable → no save, audit failure
  L28.2/10 distill: judge-timeout → no save, audit failure
  L28.2/11 distill: recall-empty → no save, audit failure
  L28.2/12 distill: judge-unavailable (empty stdout) → no save
  L28.2/13 audit metadata-only: distill audit carries field NAMES,
           never spec values
  L28.2/14 render_block: empty model returns ""; full model has block
  L28.2/15 render_block lang switch: de header vs en header
  L28.2/16 persona-ACL helper defaults False
  L28.2/17 per-tenant isolation
  L28.2/18 cost contract — no anthropic SDK import
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _section(label: str) -> None:
    print(f"\n[{label}]")


def _fresh_module(tmp: Path, tenant: str = "_default"):
    os.environ["CORVIN_HOME"] = str(tmp)
    os.environ["CORVIN_TENANT_ID"] = tenant
    (tmp / "tenants" / tenant / "global" / "memory" / "user_model").mkdir(
        parents=True, exist_ok=True
    )
    (tmp / "tenants" / tenant / "global" / "forge").mkdir(parents=True, exist_ok=True)
    sys.modules.pop("user_model", None)
    return importlib.import_module("user_model")


def _audit_events(tmp: Path, tenant: str = "_default") -> list[dict]:
    path = tmp / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


# ──────────────────────────────────────────────────────────────────────────
def case_empty_load_none() -> None:
    _section("L28.2/1: UserModel.empty + load returns None when absent")
    tmp = Path(tempfile.mkdtemp(prefix="um-1-"))
    try:
        m = _fresh_module(tmp)
        empty = m.UserModel.empty("d", "c1")
        assert empty.channel == "d" and empty.chat_key == "c1"
        assert empty.distill_count == 0
        assert empty.preferences == []
        assert m.load("d", "c-absent") is None
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_save_load_round_trip() -> None:
    _section("L28.2/2: save → load round-trip")
    tmp = Path(tempfile.mkdtemp(prefix="um-2-"))
    try:
        m = _fresh_module(tmp)
        original = m.UserModel(
            channel="discord", chat_key="c1",
            created_at=1700000000.0, updated_at=1700000100.0,
            distill_count=3,
            communication_style="concise, asks for trade-offs",
            preferences=["German chat", "voice-note replies"],
            recurring_topics=["Corvin layers", "compliance"],
            goals=["close Hermes memory gap"],
            patterns=["finishes ideas before iterating"],
            do_not_assume=["technical novice"],
        )
        path = m.save(original)
        loaded = m.load("discord", "c1")
        assert loaded is not None
        assert loaded.communication_style == original.communication_style
        assert loaded.preferences == original.preferences
        assert loaded.recurring_topics == original.recurring_topics
        assert loaded.goals == original.goals
        assert loaded.distill_count == 3
        # On-disk shape is canonical
        raw = json.loads(path.read_text())
        assert raw["apiVersion"] == "corvin/v1"
        assert raw["kind"] == "UserModel"
        assert raw["metadata"]["chat_key"] == "c1"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_save_sets_mode_0600() -> None:
    _section("L28.2/3: save sets mode 0o600")
    tmp = Path(tempfile.mkdtemp(prefix="um-3-"))
    try:
        m = _fresh_module(tmp)
        path = m.save(m.UserModel.empty("d", "c1"))
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, oct(mode)
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_forget_removes_file() -> None:
    _section("L28.2/4: forget removes file + emits audit")
    tmp = Path(tempfile.mkdtemp(prefix="um-4-"))
    try:
        m = _fresh_module(tmp)
        m.save(m.UserModel.empty("d", "c1"))
        assert m.load("d", "c1") is not None
        # Wipe audit so forget event is the only new entry
        (tmp / "tenants" / "_default" / "global" / "forge" / "audit.jsonl").write_text("")
        ok = m.forget("d", "c1")
        assert ok is True
        assert m.load("d", "c1") is None
        # Idempotent — second forget returns False, no crash
        assert m.forget("d", "c1") is False
        evts = _audit_events(tmp)
        types = [e["event_type"] for e in evts]
        assert types.count("memory.user_model_forgotten") == 2
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_schema_caps_length() -> None:
    _section("L28.2/5: schema caps list length + entry length")
    tmp = Path(tempfile.mkdtemp(prefix="um-5-"))
    try:
        m = _fresh_module(tmp)
        # 50 entries → capped to MAX_LIST_ENTRIES (10)
        oversize_list = [f"entry-{i}" for i in range(50)]
        # 5000-char entry → capped to MAX_ENTRY_CHARS (200)
        long_entry = "a" * 5000
        spec_in = {
            "preferences": oversize_list + [long_entry],
            "communication_style": "x" * 1000,
        }
        out = m._validate_spec(spec_in)
        assert len(out["preferences"]) == m.MAX_LIST_ENTRIES
        # Each entry capped
        for e in out["preferences"]:
            assert len(e) <= m.MAX_ENTRY_CHARS
        # Scalar capped
        assert len(out["communication_style"]) == m.MAX_SCALAR_CHARS
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_schema_drops_non_string_and_extra_keys() -> None:
    _section("L28.2/6: schema drops non-string + extra keys")
    tmp = Path(tempfile.mkdtemp(prefix="um-6-"))
    try:
        m = _fresh_module(tmp)
        spec_in = {
            "preferences": ["ok", 42, None, "  ", "valid"],
            "rogue_extra_field": ["should-not-survive"],
            "communication_style": 12345,
        }
        out = m._validate_spec(spec_in)
        assert out["preferences"] == ["ok", "valid"]
        assert "rogue_extra_field" not in out
        assert out["communication_style"] == ""
        # Non-dict input → empty
        assert m._validate_spec(None) == {}
        assert m._validate_spec("nope") == {}
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_extract_json_object_tolerant() -> None:
    _section("L28.2/7: _extract_json_object tolerates prose wrapping")
    tmp = Path(tempfile.mkdtemp(prefix="um-7-"))
    try:
        m = _fresh_module(tmp)
        # Bare JSON
        assert m._extract_json_object('{"a": 1}') == {"a": 1}
        # Wrapped in prose
        wrapped = 'Sure, here is the spec:\n{"a": 1, "b": [2, 3]}\nLet me know.'
        assert m._extract_json_object(wrapped) == {"a": 1, "b": [2, 3]}
        # Fenced code block
        fenced = '```json\n{"x": "y"}\n```'
        assert m._extract_json_object(fenced) == {"x": "y"}
        # No JSON → None
        assert m._extract_json_object("just prose, no json here") is None
        assert m._extract_json_object("") is None
        # Non-object → None
        assert m._extract_json_object("[1, 2, 3]") is None
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_stub_recall(turns: list[tuple[str, str]]) -> object:
    """Build a stub recall function returning fake Recall-shaped objects."""
    class _R:
        def __init__(self, u, a):
            self.user_text = u
            self.assistant_text = a
            self.ts = 1700000000.0
            self.channel = "discord"
            self.chat_key = "c1"
            self.persona = ""
            self.msg_id = ""
            self.run_id = ""
            self.score = 0.0
    def _recall(query, *, channel=None, chat_key=None, limit=20, **kwargs):
        return [_R(u, a) for u, a in turns][:limit]
    return _recall


def case_distill_happy_path() -> None:
    _section("L28.2/8: distill happy path with stub judge")
    tmp = Path(tempfile.mkdtemp(prefix="um-8-"))
    try:
        m = _fresh_module(tmp)
        # Stub recall: 3 turn-pairs
        recall_fn = _make_stub_recall([
            ("Was machst du?", "Lass mich nachsehen."),
            ("Danke fuer die Trade-off-Erklaerung", "Gerne."),
            ("Bitte konzentriere dich auf den Hauptpunkt", "Verstanden."),
        ])
        # Stub judge — returns canned JSON
        def fake_judge(prompt, timeout_s, bin_path):
            return json.dumps({
                "communication_style": "concise, asks for trade-offs",
                "preferences": ["short answers"],
                "recurring_topics": ["task focus"],
                "goals": [],
                "patterns": [],
                "do_not_assume": [],
            })
        res = m.distill("discord", "c1",
                        recall_fn=recall_fn, judge_fn=fake_judge)
        assert res.ok is True, res.reason
        assert res.model is not None
        assert res.model.communication_style == "concise, asks for trade-offs"
        assert res.model.distill_count == 1
        # Saved to disk
        on_disk = m.load("discord", "c1")
        assert on_disk is not None
        assert on_disk.preferences == ["short answers"]
        # Audit event present
        evts = _audit_events(tmp)
        kinds = [e["event_type"] for e in evts]
        assert "memory.user_model_distilled" in kinds, kinds
        det = next(e for e in evts if e["event_type"] == "memory.user_model_distilled")["details"]
        assert det["distill_count"] == 1
        assert "communication_style" in det["changed_fields"]
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_distill_unparseable() -> None:
    _section("L28.2/9: distill — judge-unparseable")
    tmp = Path(tempfile.mkdtemp(prefix="um-9-"))
    try:
        m = _fresh_module(tmp)
        recall_fn = _make_stub_recall([("u", "a")])
        def bad_judge(prompt, timeout_s, bin_path):
            return "this is not json at all"
        res = m.distill("discord", "c1",
                        recall_fn=recall_fn, judge_fn=bad_judge)
        assert res.ok is False
        assert res.reason == "judge-unparseable"
        assert m.load("discord", "c1") is None
        evts = _audit_events(tmp)
        kinds = [e["event_type"] for e in evts]
        assert "memory.user_model_distill_failed" in kinds
        det = next(e for e in evts if e["event_type"] == "memory.user_model_distill_failed")["details"]
        assert det["reason"] == "judge-unparseable"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_distill_timeout() -> None:
    _section("L28.2/10: distill — judge-timeout")
    tmp = Path(tempfile.mkdtemp(prefix="um-10-"))
    try:
        m = _fresh_module(tmp)
        recall_fn = _make_stub_recall([("u", "a")])
        import subprocess
        def slow_judge(prompt, timeout_s, bin_path):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout_s)
        res = m.distill("discord", "c1",
                        recall_fn=recall_fn, judge_fn=slow_judge)
        assert res.ok is False
        assert res.reason == "judge-timeout"
        evts = _audit_events(tmp)
        det = next(e for e in evts if e["event_type"] == "memory.user_model_distill_failed")["details"]
        assert det["reason"] == "judge-timeout"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_distill_recall_empty() -> None:
    _section("L28.2/11: distill — recall-empty")
    tmp = Path(tempfile.mkdtemp(prefix="um-11-"))
    try:
        m = _fresh_module(tmp)
        empty_recall = _make_stub_recall([])
        called = []
        def judge(prompt, timeout_s, bin_path):
            called.append(True)
            return "{}"
        res = m.distill("discord", "c1",
                        recall_fn=empty_recall, judge_fn=judge)
        assert res.ok is False
        assert res.reason == "recall-empty"
        # Judge never called when no recall material
        assert called == []
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_distill_judge_unavailable() -> None:
    _section("L28.2/12: distill — judge-unavailable (empty stdout)")
    tmp = Path(tempfile.mkdtemp(prefix="um-12-"))
    try:
        m = _fresh_module(tmp)
        recall_fn = _make_stub_recall([("u", "a")])
        def empty_judge(prompt, timeout_s, bin_path):
            return ""
        res = m.distill("discord", "c1",
                        recall_fn=recall_fn, judge_fn=empty_judge)
        assert res.ok is False
        assert res.reason == "judge-unavailable"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_audit_metadata_only() -> None:
    _section("L28.2/13: audit metadata only — never spec values")
    tmp = Path(tempfile.mkdtemp(prefix="um-13-"))
    try:
        m = _fresh_module(tmp)
        recall_fn = _make_stub_recall([("u", "a")])
        secret_value = "UNIQUE_USER_FACT_ZZZ_42"
        def judge(prompt, timeout_s, bin_path):
            return json.dumps({
                "communication_style": "",
                "preferences": [secret_value],
                "recurring_topics": [],
                "goals": [],
                "patterns": [],
                "do_not_assume": [],
            })
        res = m.distill("discord", "c1",
                        recall_fn=recall_fn, judge_fn=judge)
        assert res.ok is True
        # Audit events must NOT contain the secret value
        evts = _audit_events(tmp)
        for e in evts:
            blob = json.dumps(e)
            assert secret_value not in blob, (
                f"value leaked into audit event: {blob}"
            )
        # But the on-disk file DOES carry it (operator-only)
        on_disk = m.load("discord", "c1")
        assert secret_value in on_disk.preferences
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_render_block_empty_vs_full() -> None:
    _section("L28.2/14: render_block empty vs full")
    tmp = Path(tempfile.mkdtemp(prefix="um-14-"))
    try:
        m = _fresh_module(tmp)
        # None → empty
        assert m.render_block(None) == ""
        # Empty model → empty
        empty = m.UserModel.empty("d", "c1")
        assert m.render_block(empty) == ""
        # Model with content → block
        full = m.UserModel(
            channel="d", chat_key="c1",
            created_at=0.0, updated_at=0.0,
            communication_style="concise",
            preferences=["German chat"],
        )
        block = m.render_block(full)
        assert "<user_context>" in block
        assert "communication_style: concise" in block
        assert "German chat" in block
        assert "</user_context>" in block
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_render_block_lang_switch() -> None:
    _section("L28.2/15: render_block lang switch")
    tmp = Path(tempfile.mkdtemp(prefix="um-15-"))
    try:
        m = _fresh_module(tmp)
        full = m.UserModel(channel="d", chat_key="c1",
                           created_at=0.0, updated_at=0.0,
                           communication_style="concise")
        de = m.render_block(full, lang="de")
        en = m.render_block(full, lang="en")
        assert "Beobachtetes Profil" in de
        assert "Observed profile" in en
        # Unknown lang falls back to de
        unknown = m.render_block(full, lang="fr")
        assert "Beobachtetes Profil" in unknown
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_persona_acl() -> None:
    _section("L28.2/16: persona-ACL helper defaults False")
    tmp = Path(tempfile.mkdtemp(prefix="um-16-"))
    try:
        m = _fresh_module(tmp)
        assert m.is_user_model_permitted(None) is False
        assert m.is_user_model_permitted({}) is False
        assert m.is_user_model_permitted({"user_model_enabled": False}) is False
        assert m.is_user_model_permitted({"user_model_enabled": True}) is True
        assert m.is_user_model_permitted("yes") is False  # type: ignore[arg-type]
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_per_tenant_isolation() -> None:
    _section("L28.2/17: per-tenant isolation")
    tmp = Path(tempfile.mkdtemp(prefix="um-17-"))
    try:
        m = _fresh_module(tmp, tenant="acme")
        # tenant acme
        (tmp / "tenants" / "globex" / "global" / "memory" / "user_model").mkdir(
            parents=True, exist_ok=True
        )
        (tmp / "tenants" / "globex" / "global" / "forge").mkdir(parents=True, exist_ok=True)
        acme = m.UserModel(channel="d", chat_key="c1",
                           created_at=0.0, updated_at=0.0,
                           preferences=["acme-marker"])
        globex = m.UserModel(channel="d", chat_key="c1",
                             created_at=0.0, updated_at=0.0,
                             preferences=["globex-marker"])
        m.save(acme, tenant_id="acme")
        m.save(globex, tenant_id="globex")
        a_load = m.load("d", "c1", tenant_id="acme")
        g_load = m.load("d", "c1", tenant_id="globex")
        assert a_load.preferences == ["acme-marker"]
        assert g_load.preferences == ["globex-marker"]
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_no_anthropic_sdk_import() -> None:
    _section("L28.2/18: cost contract — no anthropic SDK import")
    src = Path(__file__).resolve().parent / "user_model.py"
    body = src.read_text()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "import anthropic" not in stripped, line
        assert "from anthropic" not in stripped, line
        for client in ("openai", "google.generativeai"):
            assert (f"import {client}" not in stripped
                    and f"from {client}" not in stripped), line
    print("  passed")


def case_distill_pii_redacted() -> None:
    _section("L28.2/19: distill PII-redacts LLM output before saving (ADR-0072 V-008)")
    tmp = Path(tempfile.mkdtemp(prefix="um-19-"))
    try:
        m = _fresh_module(tmp)
        recall_fn = _make_stub_recall([
            ("Can you reach me at test@example.com?", "Sure."),
        ])
        fake_email = "test@example.com"

        def judge_with_pii(prompt, timeout_s, bin_path):
            # Simulates the LLM embedding a PII email address in the spec
            return json.dumps({
                "communication_style": f"prefers email at {fake_email}",
                "preferences": [f"contact: {fake_email}"],
                "recurring_topics": [],
                "goals": [],
                "patterns": [],
                "do_not_assume": [],
            })

        res = m.distill("discord", "c1",
                        recall_fn=recall_fn, judge_fn=judge_with_pii)
        assert res.ok is True, f"distill failed: {res.reason}"
        # On-disk file must NOT contain the raw email address
        on_disk = m.load("discord", "c1")
        assert on_disk is not None, "model file must be saved"
        disk_json = json.dumps(on_disk.to_disk())
        assert fake_email not in disk_json, (
            f"PII email {fake_email!r} found in saved user model: {disk_json}"
        )
        # The fields should still exist (redacted, not blank)
        assert on_disk.communication_style != "", "communication_style should be non-empty after redaction"
        assert len(on_disk.preferences) == 1, "preferences list should have one entry"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── pytest-compatible wrappers ────────────────────────────────────────────

def test_distill_pii_redacted():
    """Pytest entry point for case_distill_pii_redacted (ADR-0072 V-008)."""
    case_distill_pii_redacted()


if __name__ == "__main__":
    case_empty_load_none()
    case_save_load_round_trip()
    case_save_sets_mode_0600()
    case_forget_removes_file()
    case_schema_caps_length()
    case_schema_drops_non_string_and_extra_keys()
    case_extract_json_object_tolerant()
    case_distill_happy_path()
    case_distill_unparseable()
    case_distill_timeout()
    case_distill_recall_empty()
    case_distill_judge_unavailable()
    case_audit_metadata_only()
    case_render_block_empty_vs_full()
    case_render_block_lang_switch()
    case_persona_acl()
    case_per_tenant_isolation()
    case_no_anthropic_sdk_import()
    case_distill_pii_redacted()
    print("\nAll L28.2 cases passed.")
