"""decision_registry.py — AI Decision Registry for Corvin.

ADR-0073 G-010 / EU AI Act Art. 13-14 (transparency + human oversight).

Provides a per-session decision audit trail for messages tagged with
[decision:significant]. High-risk tenants (spec.risk_classification: high_risk)
additionally receive a mandatory review-pending prefix in the AI response and
a human-review gate notification to admin-role users.

Module surface:
    * detect_significant_decision(text) → bool
    * record_decision(*, decision_id, channel, chat_key, risk_tier, engine_id,
                      persona, audit_path) → None
    * get_decision(decision_id, *, registry_path) → dict | None
    * list_pending_decisions(*, registry_path) → list[dict]
    * mark_reviewed(decision_id, *, reviewer, outcome, registry_path) → None

Audit events emitted:
    ai.decision_recorded   — when a significant decision is tagged
    ai.decision_reviewed   — when an admin marks a decision reviewed
    ai.decision_pending    — when a high-risk decision awaits human review

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Detection ─────────────────────────────────────────────────────────────────

_SIGNIFICANT_TAG_RE = re.compile(r"\[decision:significant\]", re.IGNORECASE)
_RISK_CLASSIFICATION_RE = re.compile(r"\[decision:([a-z_]+)\]", re.IGNORECASE)


def detect_significant_decision(text: str) -> bool:
    """Return True if the message contains [decision:significant] tag."""
    return bool(_SIGNIFICANT_TAG_RE.search(text))


def extract_decision_class(text: str) -> str:
    """Extract the decision class from [decision:<class>] tag, or 'significant'."""
    m = _RISK_CLASSIFICATION_RE.search(text)
    return m.group(1).lower() if m else "significant"


# ── Registry storage ──────────────────────────────────────────────────────────

_REGISTRY_FILENAME = "decision_registry.jsonl"


def _registry_path(base: Path) -> Path:
    return base / _REGISTRY_FILENAME


@dataclass
class DecisionRecord:
    decision_id: str
    timestamp: str
    channel: str
    chat_key: str
    risk_tier: str           # "limited_risk" | "high_risk"
    engine_id: str
    persona: str
    decision_class: str      # "significant" or other tag
    review_status: str       # "pending" | "approved" | "rejected" | "none"
    reviewed_by: str         # reviewer uid_hash or ""
    reviewed_at: str         # ISO timestamp or ""
    review_outcome: str      # "approved" | "rejected" | ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_decision(
    *,
    decision_id: str,
    channel: str,
    chat_key: str,
    risk_tier: str,
    engine_id: str,
    persona: str,
    decision_class: str = "significant",
    audit_path: Path | None = None,
) -> DecisionRecord:
    """Write a decision record to the session registry and emit an audit event."""
    record = DecisionRecord(
        decision_id=decision_id,
        timestamp=_now_iso(),
        channel=channel,
        chat_key=chat_key,
        risk_tier=risk_tier,
        engine_id=engine_id,
        persona=persona,
        decision_class=decision_class,
        review_status="pending" if risk_tier == "high_risk" else "none",
        reviewed_by="",
        reviewed_at="",
        review_outcome="",
    )

    if audit_path is not None:
        reg = _registry_path(audit_path)
        _append_record(reg, asdict(record))
        _emit_audit_event(
            "ai.decision_recorded" if risk_tier != "high_risk" else "ai.decision_pending",
            audit_path,
            decision_id=decision_id,
            channel=channel,
            chat_key=chat_key,
            risk_tier=risk_tier,
            engine_id=engine_id,
            persona=persona,
            decision_class=decision_class,
            review_status=record.review_status,
        )

    return record


def get_decision(decision_id: str, *, registry_path: Path) -> dict[str, Any] | None:
    """Retrieve a decision record by ID. Returns None if not found."""
    reg = _registry_path(registry_path)
    if not reg.exists():
        return None
    for line in reg.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("decision_id") == decision_id:
                return rec
        except json.JSONDecodeError:
            continue
    return None


def list_pending_decisions(*, registry_path: Path) -> list[dict[str, Any]]:
    """Return all pending decisions from the registry."""
    reg = _registry_path(registry_path)
    if not reg.exists():
        return []
    pending = []
    for line in reg.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("review_status") == "pending":
                pending.append(rec)
        except json.JSONDecodeError:
            continue
    return pending


def mark_reviewed(
    decision_id: str,
    *,
    reviewer_hash: str,
    outcome: str,
    registry_path: Path,
    audit_path: Path | None = None,
) -> bool:
    """Update decision review status. Returns True if found and updated."""
    if outcome not in ("approved", "rejected"):
        raise ValueError(f"outcome must be 'approved' or 'rejected', got {outcome!r}")

    reg = _registry_path(registry_path)
    if not reg.exists():
        return False

    lines = reg.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines = []
    for line in lines:
        try:
            rec = json.loads(line)
            if rec.get("decision_id") == decision_id and rec.get("review_status") == "pending":
                rec["review_status"] = outcome
                rec["reviewed_by"] = reviewer_hash
                rec["reviewed_at"] = _now_iso()
                rec["review_outcome"] = outcome
                updated = True
            new_lines.append(json.dumps(rec, separators=(",", ":")))
        except json.JSONDecodeError:
            new_lines.append(line)

    if updated:
        _atomic_write(reg, "\n".join(new_lines) + "\n")
        if audit_path is not None:
            _emit_audit_event(
                "ai.decision_reviewed",
                audit_path,
                decision_id=decision_id,
                reviewer_hash=reviewer_hash,
                outcome=outcome,
            )

    return updated


# ── Response helpers ──────────────────────────────────────────────────────────

def build_decision_prefix(record: DecisionRecord) -> str:
    """Build a notification prefix to prepend to AI responses for significant decisions."""
    if record.risk_tier == "high_risk":
        return (
            f"⚠️ **[DECISION:{record.decision_id[:8]}]** "
            f"This response involves a significant AI decision. "
            f"Status: **AWAITING HUMAN REVIEW** before action is taken. "
            f"Reviewers: use `/decision-review {record.decision_id[:8]}` to approve or reject.\n\n"
        )
    return (
        f"ℹ️ [Decision logged: {record.decision_id[:8]}] "
        f"This response has been recorded as a significant AI decision.\n\n"
    )


# ── Internals ─────────────────────────────────────────────────────────────────

def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    try:
        fd = path.open("a", encoding="utf-8")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            fd.write(line)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
    except OSError:
        pass  # best-effort


def _atomic_write(path: Path, content: str) -> None:
    import os, tempfile
    tmp = Path(tempfile.mktemp(dir=path.parent, prefix=".dec_reg_"))
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _emit_audit_event(event_type: str, audit_path: Path, **details: Any) -> None:
    """Best-effort audit chain emission."""
    try:
        import sys
        repo_root = Path(__file__).resolve().parents[3]
        for p in [str(Path(__file__).resolve().parent), str(repo_root / "operator" / "forge")]:
            if p not in sys.path:
                sys.path.insert(0, p)
        from forge.security_events import write_event  # type: ignore
        # write_event signature: (path, event_type, *, severity, details, ...).
        # The prior call passed event_type as path, "INFO" as event_type, a 3rd
        # positional (forbidden — severity is keyword-only) and audit_path= (not
        # a valid kwarg) → it raised TypeError on every call and never wrote.
        write_event(audit_path, event_type, severity="INFO", details=details)
    except Exception:
        pass


# ── CLI (for /decision-review) ────────────────────────────────────────────────

def cli_review(argv: list[str], *, session_dir: Path, reviewer_hash: str) -> str:
    """Process a /decision-review command. Returns a user-facing response string."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("decision_id", nargs="?", default="")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reject", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return "Usage: /decision-review <id> [--approve|--reject]"

    if not args.decision_id:
        pending = list_pending_decisions(registry_path=session_dir)
        if not pending:
            return "No pending decisions in this session."
        lines = ["**Pending decisions:**"]
        for r in pending[:10]:
            lines.append(f"- `{r['decision_id'][:8]}` — {r['timestamp']} — class: {r.get('decision_class', '?')}")
        return "\n".join(lines)

    # Try short-ID prefix match
    did = args.decision_id
    rec = get_decision(did, registry_path=session_dir)
    if rec is None:
        # prefix search
        pending = list_pending_decisions(registry_path=session_dir)
        matches = [r for r in pending if r["decision_id"].startswith(did)]
        if len(matches) == 1:
            rec = matches[0]
            did = rec["decision_id"]
        elif len(matches) > 1:
            return f"Ambiguous ID prefix `{did}` — be more specific."
        else:
            return f"Decision `{did}` not found."

    if rec.get("review_status") != "pending":
        return f"Decision `{did[:8]}` is already `{rec.get('review_status')}`."

    if not (args.approve or args.reject):
        return (
            f"**Decision `{did[:8]}`**\n"
            f"- Timestamp: {rec.get('timestamp')}\n"
            f"- Class: {rec.get('decision_class')}\n"
            f"- Risk tier: {rec.get('risk_tier')}\n"
            f"- Status: {rec.get('review_status')}\n\n"
            f"Use `--approve` or `--reject` to record your review."
        )

    outcome = "approved" if args.approve else "rejected"
    ok = mark_reviewed(did, reviewer_hash=reviewer_hash, outcome=outcome, registry_path=session_dir)
    if ok:
        return f"Decision `{did[:8]}` marked **{outcome}** by reviewer `{reviewer_hash[:6]}`."
    return f"Failed to update decision `{did[:8]}`."


def generate_decision_id() -> str:
    return str(uuid.uuid4())
