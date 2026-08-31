"""Layer 28.1 per-subtask E2E — conversation_recall (ADR-0016).

Covers:
  L28.1/1  text-mode PII regex: email / IBAN / credit_card / phone /
           us_ssn / ch_ahv → redaction tokens + counts
  L28.1/2  redact_text invariants: empty, non-string, no-pii passthrough
  L28.1/3  index_turn round-trip: row lands with REDACTED text only
  L28.1/4  redaction-runs-before-index regression gate
  L28.1/5  recall returns matching turn + FTS5 query escape safety
  L28.1/6  recall scopes: channel-only / chat-only / time-window
  L28.1/7  audit chain: memory.turn_indexed + memory.recall_query
           land with the curated detail allow-list
  L28.1/8  audit chain: NO raw text leaks into any audit event
  L28.1/9  per-tenant isolation: two tenants, two DBs, no cross-leak
  L28.1/10 indexing_failed audit: malformed input does NOT crash
  L28.1/11 forget() deletes rows + cascades to FTS5
  L28.1/12 cost contract: NO `import anthropic` in module source
  L28.1/13 persona-ACL helper: defaults to False; flag enables
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
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
    """Reload the module so its tenant-path resolver picks up the sandbox."""
    os.environ["CORVIN_HOME"] = str(tmp)
    os.environ["CORVIN_TENANT_ID"] = tenant
    # Ensure tenant tree exists for the in-bundle forge.paths resolver
    (tmp / "tenants" / tenant / "global" / "memory").mkdir(parents=True, exist_ok=True)
    (tmp / "tenants" / tenant / "global" / "forge").mkdir(parents=True, exist_ok=True)
    sys.modules.pop("conversation_recall", None)
    mod = importlib.import_module("conversation_recall")
    # Reset connection cache so each sandbox starts clean
    mod._close_all_connections()
    return mod


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
def case_redact_patterns() -> None:
    _section("L28.1/1: text-mode PII regex coverage")
    tmp = Path(tempfile.mkdtemp(prefix="cr-1-"))
    try:
        mod = _fresh_module(tmp)
        # Email
        r, c = mod.redact_text("Schreib mir an a.b@example.com bitte.")
        assert "<redacted:email>" in r, r
        assert c.get("email") == 1, c
        # IBAN (German format)
        r, c = mod.redact_text("Konto DE89 3704 0044 0532 0130 00 ueberweisen")
        assert "<redacted:iban>" in r, r
        # Credit card — 16 digits
        r, c = mod.redact_text("Karte 4111 1111 1111 1111 fuer Test")
        assert "<redacted:credit_card>" in r, r
        # US-SSN
        r, c = mod.redact_text("SSN 123-45-6789 vermerkt")
        assert "<redacted:us_ssn>" in r, r
        # CH-AHV
        r, c = mod.redact_text("AHV 756.1234.5678.97 hinterlegt")
        assert "<redacted:ch_ahv>" in r, r
        # Phone
        r, c = mod.redact_text("Ruf an: +49 30 12345678 abends")
        assert "<redacted:phone>" in r, r
        # Mixed input — multiple classes counted
        r, c = mod.redact_text("foo@bar.de oder +49 30 12345678")
        assert c.get("email", 0) >= 1
        assert c.get("phone", 0) >= 1
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_redact_invariants() -> None:
    _section("L28.1/2: redact_text invariants")
    tmp = Path(tempfile.mkdtemp(prefix="cr-2-"))
    try:
        mod = _fresh_module(tmp)
        # Empty input
        r, c = mod.redact_text("")
        assert r == "" and c == {}
        # Non-string input
        r, c = mod.redact_text(None)  # type: ignore[arg-type]
        assert r == "" and c == {}
        # No-PII passthrough — original text unchanged
        plain = "Wir hatten letzte Woche ueber Layer 26 gesprochen."
        r, c = mod.redact_text(plain)
        assert r == plain
        assert c == {}
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_index_round_trip() -> None:
    _section("L28.1/3: index_turn writes redacted row")
    tmp = Path(tempfile.mkdtemp(prefix="cr-3-"))
    try:
        mod = _fresh_module(tmp)
        res = mod.index_turn(
            channel="discord", chat_key="chat-abc",
            user_text="Mein Konto DE89 3704 0044 0532 0130 00 bitte",
            assistant_text="Verstanden, ich notiere a@b.de fuer den Versand.",
            msg_id="m1", persona="assistant", ts=1700000000.0,
        )
        assert res["ok"] is True
        assert "iban" in res["redacted_classes"]
        assert "email" in res["redacted_classes"]
        # Verify the DB row carries redacted text only — read it back
        # via a direct SQLite query against the same DB path.
        import sqlite3
        db = tmp / "tenants" / "_default" / "global" / "memory" / "recall.db"
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT user_text, asst_text, redacted_classes FROM turns"
            ).fetchone()
        u, a, cls = row
        assert "DE89" not in u, u
        assert "<redacted:iban>" in u
        assert "a@b.de" not in a
        assert "<redacted:email>" in a
        assert "iban" in cls and "email" in cls
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_redaction_runs_before_index() -> None:
    _section("L28.1/4: redaction-before-index regression gate")
    tmp = Path(tempfile.mkdtemp(prefix="cr-4-"))
    try:
        mod = _fresh_module(tmp)
        secret_iban = "DE12 1234 5678 9012 3456 78"
        secret_email = "leak.test@example.org"
        mod.index_turn(
            channel="telegram", chat_key="c1",
            user_text=f"IBAN: {secret_iban} und Mail {secret_email}",
            assistant_text="ok",
            msg_id="m-leak", persona="assistant", ts=1700000001.0,
        )
        # Read every column from the row and assert neither raw value
        # appears anywhere — this is the load-bearing privacy invariant.
        import sqlite3
        db = tmp / "tenants" / "_default" / "global" / "memory" / "recall.db"
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT * FROM turns").fetchone()
        for cell in row:
            s = str(cell)
            assert "DE12" not in s, f"raw IBAN leaked into column: {s!r}"
            assert "leak.test" not in s, f"raw email leaked: {s!r}"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_recall_returns_matches() -> None:
    _section("L28.1/5: recall returns matching turns + FTS5 escape")
    tmp = Path(tempfile.mkdtemp(prefix="cr-5-"))
    try:
        mod = _fresh_module(tmp)
        mod.index_turn(channel="discord", chat_key="c1",
                       user_text="Wir besprachen Layer 26 und Skill-Forge.",
                       assistant_text="Ja, der user-style-learner.",
                       msg_id="m1", persona="assistant", ts=1700000010.0)
        mod.index_turn(channel="discord", chat_key="c1",
                       user_text="Heute war es regnerisch in Berlin.",
                       assistant_text="Ich hoffe es wird besser.",
                       msg_id="m2", persona="assistant", ts=1700000020.0)
        results = mod.recall("Layer 26", chat_key="c1")
        assert len(results) >= 1
        assert "Layer 26" in results[0].user_text
        # FTS5 syntax escape — quotes and operators must not break the query
        results = mod.recall('AND OR "test"', chat_key="c1")
        assert isinstance(results, list)  # no exception
        # No match → empty result
        results = mod.recall("ZZZ_NONEXISTENT_QQQ", chat_key="c1")
        assert results == []
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_recall_scopes() -> None:
    _section("L28.1/6: recall scopes (channel / chat / time-window)")
    tmp = Path(tempfile.mkdtemp(prefix="cr-6-"))
    try:
        mod = _fresh_module(tmp)
        mod.index_turn(channel="discord", chat_key="cA",
                       user_text="Wir reden ueber Sandkastenburg.",
                       assistant_text="ok", msg_id="m1",
                       persona="assistant", ts=1700000000.0)
        mod.index_turn(channel="discord", chat_key="cB",
                       user_text="Sandkastenburg in einem anderen Chat.",
                       assistant_text="ok", msg_id="m2",
                       persona="assistant", ts=1700001000.0)
        mod.index_turn(channel="telegram", chat_key="cA",
                       user_text="Sandkastenburg auf Telegram.",
                       assistant_text="ok", msg_id="m3",
                       persona="assistant", ts=1700002000.0)
        # channel scope
        d = mod.recall("Sandkastenburg", channel="discord")
        assert len(d) == 2, [r.channel for r in d]
        t = mod.recall("Sandkastenburg", channel="telegram")
        assert len(t) == 1
        # chat scope
        a = mod.recall("Sandkastenburg", chat_key="cA")
        assert len(a) == 2
        # combined channel + chat
        combo = mod.recall("Sandkastenburg", channel="discord", chat_key="cA")
        assert len(combo) == 1
        # time window — only the middle entry
        win = mod.recall("Sandkastenburg",
                         since=1700000500.0, until=1700001500.0)
        assert len(win) == 1
        assert win[0].chat_key == "cB"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_audit_events_with_allowlist() -> None:
    _section("L28.1/7: audit emits turn_indexed + recall_query")
    tmp = Path(tempfile.mkdtemp(prefix="cr-7-"))
    try:
        mod = _fresh_module(tmp)
        mod.index_turn(channel="discord", chat_key="c1",
                       user_text="Eine ganz normale Nachricht.",
                       assistant_text="Die normale Antwort.",
                       msg_id="m-evt", persona="assistant", ts=1700003000.0)
        mod.recall("normale", chat_key="c1", caller_persona="assistant")
        evts = _audit_events(tmp)
        types = [e["event_type"] for e in evts]
        assert "memory.turn_indexed" in types, types
        assert "memory.recall_query" in types, types
        idx = next(e for e in evts if e["event_type"] == "memory.turn_indexed")
        det = idx.get("details") or {}
        # Allow-listed fields present
        assert det.get("channel") == "discord"
        assert det.get("chat_key") == "c1"
        assert det.get("msg_id") == "m-evt"
        assert det.get("persona") == "assistant"
        assert det.get("user_chars") == len("Eine ganz normale Nachricht.")
        assert det.get("asst_chars") == len("Die normale Antwort.")
        assert "redacted_class_count" in det
        # Recall query event
        rec = next(e for e in evts if e["event_type"] == "memory.recall_query")
        det = rec.get("details") or {}
        assert det.get("query_chars") == len("normale")
        assert det.get("result_count") >= 1
        assert det.get("caller_persona") == "assistant"
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_no_raw_text_in_audit() -> None:
    _section("L28.1/8: NO raw text in any audit event")
    tmp = Path(tempfile.mkdtemp(prefix="cr-8-"))
    try:
        mod = _fresh_module(tmp)
        secret_substring = "WUNDERSAMES_GEHEIMNIS_ZZZ"
        mod.index_turn(channel="d", chat_key="c1",
                       user_text=f"Hallo {secret_substring} Welt",
                       assistant_text="Antwort",
                       msg_id="m1", persona="assistant", ts=1700004000.0)
        mod.recall(secret_substring, chat_key="c1")
        evts = _audit_events(tmp)
        for e in evts:
            blob = json.dumps(e)
            assert secret_substring not in blob, (
                f"raw text leaked into audit event {e.get('event_type')}: {blob}"
            )
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_per_tenant_isolation() -> None:
    _section("L28.1/9: per-tenant isolation")
    tmp = Path(tempfile.mkdtemp(prefix="cr-9-"))
    try:
        # Index in tenant acme
        mod = _fresh_module(tmp, tenant="acme")
        mod.index_turn(channel="d", chat_key="c1",
                       user_text="acme-secret-marker",
                       assistant_text="ok", msg_id="m1",
                       persona="assistant", ts=1700005000.0,
                       tenant_id="acme")
        # Index in tenant globex
        mod.index_turn(channel="d", chat_key="c1",
                       user_text="globex-secret-marker",
                       assistant_text="ok", msg_id="m2",
                       persona="assistant", ts=1700005100.0,
                       tenant_id="globex")
        # Recall from each tenant sees only its own data
        acme = mod.recall("marker", tenant_id="acme")
        globex = mod.recall("marker", tenant_id="globex")
        acme_texts = [r.user_text for r in acme]
        globex_texts = [r.user_text for r in globex]
        assert any("acme" in t for t in acme_texts), acme_texts
        assert not any("globex" in t for t in acme_texts), acme_texts
        assert any("globex" in t for t in globex_texts), globex_texts
        assert not any("acme" in t for t in globex_texts), globex_texts
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_indexing_failure_no_crash() -> None:
    _section("L28.1/10: indexing failure audits + does NOT crash")
    tmp = Path(tempfile.mkdtemp(prefix="cr-10-"))
    try:
        mod = _fresh_module(tmp)
        # Missing required fields → ok=False, no crash, no audit
        res = mod.index_turn(channel="", chat_key="c1",
                             user_text="x", assistant_text="y",
                             msg_id="m1", persona="assistant",
                             ts=1700006000.0)
        assert res == {"ok": False, "reason": "missing-required"}
        res = mod.index_turn(channel="d", chat_key="",
                             user_text="x", assistant_text="y",
                             msg_id="m2", persona="assistant",
                             ts=1700006100.0)
        assert res == {"ok": False, "reason": "missing-required"}
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_forget_cascades_to_fts() -> None:
    _section("L28.1/11: forget cascades to FTS5")
    tmp = Path(tempfile.mkdtemp(prefix="cr-11-"))
    try:
        mod = _fresh_module(tmp)
        mod.index_turn(channel="d", chat_key="cA",
                       user_text="erase-me marker", assistant_text="ok",
                       msg_id="m1", persona="assistant", ts=1700007000.0)
        mod.index_turn(channel="d", chat_key="cB",
                       user_text="keep-me marker", assistant_text="ok",
                       msg_id="m2", persona="assistant", ts=1700007100.0)
        # Before forget — both reachable
        assert len(mod.recall("marker")) == 2
        # Forget chat cA
        n = mod.forget(channel="d", chat_key="cA")
        assert n == 1
        # After — only cB remains, FTS5 fully consistent
        rem = mod.recall("marker")
        assert len(rem) == 1
        assert rem[0].chat_key == "cB"
        # Forget by time window
        n = mod.forget(before_ts=1700007200.0)
        assert n == 1
        assert mod.recall("marker") == []
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_no_anthropic_sdk_import() -> None:
    _section("L28.1/12: cost contract — no anthropic SDK import")
    src = Path(__file__).resolve().parent / "conversation_recall.py"
    body = src.read_text()
    # Walk lines instead of AST to also catch dynamic import strings
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Bare imports
        assert "import anthropic" not in stripped, line
        assert "from anthropic" not in stripped, line
        # SDK clients
        for client in ("openai", "google.generativeai"):
            assert (f"import {client}" not in stripped
                    and f"from {client}" not in stripped), line
    print("  passed")


def case_persona_acl_helper() -> None:
    _section("L28.1/13: persona-ACL helper defaults False")
    tmp = Path(tempfile.mkdtemp(prefix="cr-13-"))
    try:
        mod = _fresh_module(tmp)
        assert mod.is_recall_permitted_for_persona(None) is False
        assert mod.is_recall_permitted_for_persona({}) is False
        assert mod.is_recall_permitted_for_persona(
            {"memory_recall_enabled": False}) is False
        assert mod.is_recall_permitted_for_persona(
            {"memory_recall_enabled": True}) is True
        # non-dict input — fail-closed
        assert mod.is_recall_permitted_for_persona("yes") is False  # type: ignore[arg-type]
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    case_redact_patterns()
    case_redact_invariants()
    case_index_round_trip()
    case_redaction_runs_before_index()
    case_recall_returns_matches()
    case_recall_scopes()
    case_audit_events_with_allowlist()
    case_no_raw_text_in_audit()
    case_per_tenant_isolation()
    case_indexing_failure_no_crash()
    case_forget_cascades_to_fts()
    case_no_anthropic_sdk_import()
    case_persona_acl_helper()
    print("\nAll L28.1 cases passed.")


# ─── pytest-compatible PII redaction tests (V-015) ─────────────────────────

import unittest  # noqa: E402


class TestPIIRedaction(unittest.TestCase):
    """V-015: Improved PII redaction — pytest-compatible unit tests.

    Covers the specific patterns added / changed in the V-015 fix:
      - Standard email with IGNORECASE
      - Obfuscated [at]/[dot] email variant
      - Valid credit card (Luhn passes) → redacted
      - Date-like sequence (e.g. 2024-2025-2026) → NOT redacted (Luhn fails)
      - IBAN case-insensitive match
    """

    def _mod(self):
        """Import conversation_recall in the current process (no sandbox needed
        for pure-redaction unit tests — only redact_text is exercised)."""
        sys.modules.pop("conversation_recall", None)
        import conversation_recall as cr  # type: ignore
        return cr

    def test_standard_email_redacted(self) -> None:
        cr = self._mod()
        r, counts = cr.redact_text("Contact me at user.name+tag@sub.example.co.uk please.")
        self.assertIn("<redacted:email>", r)
        self.assertEqual(counts.get("email"), 1)
        self.assertNotIn("user.name", r)

    def test_obfuscated_email_at_dot_redacted(self) -> None:
        cr = self._mod()
        r, counts = cr.redact_text("Schreib an john[at]example[dot]com fuer Details.")
        self.assertIn("<redacted:email>", r)
        self.assertGreaterEqual(counts.get("email", 0), 1)
        self.assertNotIn("john", r)

    def test_obfuscated_email_with_spaces_redacted(self) -> None:
        cr = self._mod()
        r, counts = cr.redact_text("kontakt: alice (at) domain (dot) org bitte")
        self.assertIn("<redacted:email>", r)
        self.assertGreaterEqual(counts.get("email", 0), 1)

    def test_valid_credit_card_luhn_passes_redacted(self) -> None:
        # 4111 1111 1111 1111 is the canonical Luhn-valid Visa test number
        cr = self._mod()
        r, counts = cr.redact_text("Meine Karte: 4111 1111 1111 1111 — bitte belasten.")
        self.assertIn("<redacted:credit_card>", r)
        self.assertGreaterEqual(counts.get("credit_card", 0), 1)
        self.assertNotIn("4111", r)

    def test_date_sequence_not_redacted_luhn_fails(self) -> None:
        # Year sequences like 2024-2025-2026 are NOT valid card numbers
        cr = self._mod()
        text = "Zeitraum 2024-2025-2026 beachten"
        r, counts = cr.redact_text(text)
        # No credit_card redaction expected — Luhn fails for year triplets
        self.assertEqual(counts.get("credit_card", 0), 0,
                         f"date sequence incorrectly flagged as credit card in: {r!r}")
        self.assertNotIn("<redacted:credit_card>", r)

    def test_iban_case_insensitive_uppercase(self) -> None:
        cr = self._mod()
        r, counts = cr.redact_text("IBAN: DE89370400440532013000 — bitte ueberweisen.")
        self.assertIn("<redacted:iban>", r)
        self.assertGreaterEqual(counts.get("iban", 0), 1)
        self.assertNotIn("DE89", r)

    def test_iban_lowercase_redacted(self) -> None:
        cr = self._mod()
        r, counts = cr.redact_text("iban de89 3704 0044 0532 0130 00 vermerkt")
        self.assertIn("<redacted:iban>", r)
        self.assertGreaterEqual(counts.get("iban", 0), 1)

    def test_no_false_positive_on_plain_text(self) -> None:
        cr = self._mod()
        plain = "Heute ist ein schoener Tag ohne PII."
        r, counts = cr.redact_text(plain)
        self.assertEqual(r, plain)
        self.assertEqual(counts, {})
