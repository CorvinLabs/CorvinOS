"""ulo_compliance.py — ULO post-turn compliance checker (ADR-0163 M2).

Runs after each bridge turn as a daemon-thread job.  For each active
User-Defined Learning Objective, asks Haiku-4.5 whether the structural
metadata of the response satisfies the objective.  Raw response text is
NEVER passed — only the :class:`ResponseMetadata` dict.

Subprocess pattern mirrors user_model.py: ``claude -p --max-turns 1
--tools ""`` via helper_model.py site ULO_COMPLIANCE.

GDPR Art. 5: metadata dict only; no raw text enters audit or disk store.
Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from ulo import load as _ulo_load, _save_all as _ulo_save_all  # type: ignore
    from ulo_metadata import ResponseMetadata                        # type: ignore
    from ulo_schema import sanitize_text                             # type: ignore
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from ulo import load as _ulo_load, _save_all as _ulo_save_all  # type: ignore
    from ulo_metadata import ResponseMetadata                        # type: ignore
    from ulo_schema import sanitize_text                             # type: ignore


# ── Helper-model site constant ────────────────────────────────────────────

SITE_ULO_COMPLIANCE = "ulo_compliance"

_EMA_ALPHA = 0.2   # exponential moving average decay for compliance_rate

# Per-chat locks — finer-grained than a single global lock so busy chats
# don't block each other's compliance checks.
_chat_locks: dict[str, threading.Lock] = {}
_chat_locks_meta = threading.Lock()


def _chat_lock(channel: str, chat_key: str) -> threading.Lock:
    key = f"{channel}\x00{chat_key}"
    with _chat_locks_meta:
        if key not in _chat_locks:
            _chat_locks[key] = threading.Lock()
        return _chat_locks[key]


# ── Prompt template ───────────────────────────────────────────────────────

_PROMPT_TMPL = """\
You are a compliance checker for a user-defined behavioural objective.
You receive ONLY structural metadata about an AI response (not the text itself).
Your job: decide whether the response is likely to satisfy the constraint.

Constraint: {constraint}

Structural metadata of the response:
{metadata_json}

Respond with ONLY a JSON object, no commentary:
{{
  "compliant": true or false,
  "confidence": 0.0 to 1.0,
  "reason_code": "compliant" | "language_mismatch" | "missing_code_blocks" | "wrong_format" | "too_short" | "too_long" | "missing_structure" | "other_violation"
}}

Rules:
- Use "language_mismatch" when detected_language doesn't match the required language.
- Use "missing_code_blocks" when the constraint requires code blocks but has_code_block is false.
- Use "wrong_format" for structural format violations (headings, lists, tables).
- Use "too_short" / "too_long" for word/char count constraints.
- Use "missing_structure" when required elements (tables, headings) are absent.
- Use "compliant" as reason_code when compliant=true.
- Prefer confidence 0.9+ for clear metadata signal, 0.5-0.7 when ambiguous.
"""


def _parse_result(raw: str) -> dict[str, Any] | None:
    """Extract the first valid compliance JSON object from the LLM output.

    Uses JSONDecoder.raw_decode to handle nested objects — unlike a flat
    ``[^{}]+`` regex, this correctly finds the outermost ``{...}`` that
    contains all required keys even when the model adds wrapper fields.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            d, _ = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if not isinstance(d.get("compliant"), bool):
            continue  # wrapper object — keep scanning for the verdict
        return {
            "compliant":   bool(d["compliant"]),
            "confidence":  float(d.get("confidence", 0.5)),
            "reason_code": str(d.get("reason_code", "other_violation")),
        }
    return None


def _run_claude(
    prompt: str,
    timeout_s: float = 30.0,
) -> tuple[str, bool, int | None]:
    """Call claude -p --max-turns 1 --tools '' with helper_model site.

    Returns ``(stdout, timed_out, exit_code)`` so the caller can log
    the distinction between a timeout and a missing binary (-127).
    """
    try:
        import helper_model as _hm  # type: ignore
    except ImportError:
        _hm = None

    model_args = _hm.claude_args(_hm.SITE_ULO_COMPLIANCE) if _hm else []
    bin_path = (
        _hm.resolve_claude_bin() if _hm is not None
        else os.environ.get("CORVIN_CLAUDE_BIN", "claude")
    )
    try:
        proc = subprocess.run(
            [bin_path, "-p", "--max-turns", "1", "--tools", "", *model_args],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.stdout or "", False, proc.returncode
    except subprocess.TimeoutExpired:
        return "", True, None
    except FileNotFoundError:
        return "", False, -127  # claude binary missing


def check_turn(
    channel: str,
    chat_key: str,
    metadata: ResponseMetadata,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Check all active objectives for this chat against the given metadata.

    Returns a list of result dicts: ``{objective_id, compliant, confidence,
    reason_code}``.  Updates each objective's compliance stats via a
    load-merge-save pattern that is safe against concurrent CRUD operations.
    Best-effort — any Haiku failure skips that objective.
    """
    # Snapshot objectives outside the lock — only for building prompts.
    objs_snapshot = _ulo_load(channel, chat_key, tenant_id)
    active = [o for o in objs_snapshot if o.active]
    if not active:
        return []

    metadata_json = json.dumps(metadata.to_dict(), ensure_ascii=False)
    results: list[dict[str, Any]] = []
    stat_updates: dict[str, dict[str, Any]] = {}  # id → computed stats

    try:
        import ulo_debug_log as _ulo_dbg  # type: ignore
    except Exception:  # noqa: BLE001
        _ulo_dbg = None  # type: ignore

    # Build all prompts and run subprocesses with no lock held.
    # The per-chat lock is only acquired for the short merge-save at the end.
    lock = _chat_lock(channel, chat_key)
    if _ulo_dbg is not None:
        _ulo_dbg.log_lock_wait(channel, chat_key,
                               wait_ms=0,
                               active_count=len(active))

    # Run all Haiku subprocess calls OUTSIDE the lock — each call blocks up
    # to 30 s; holding the per-chat lock during subprocess calls would starve
    # concurrent CRUD (add/pause/delete) for the entire compliance check duration.
    per_obj: list[tuple[Any, str, float, bool, int | None]] = []
    for obj in active:
        # sanitize_text (superset of NFKC) — the objective is interpolated into
        # the Haiku judge prompt, so the same injection guard applies here.
        safe_text = sanitize_text(obj.text)
        prompt_text = _PROMPT_TMPL.format(
            constraint=safe_text,
            metadata_json=metadata_json,
        )
        _sub_t0 = time.perf_counter()
        raw, _timed_out, _exit_code = _run_claude(prompt_text, timeout_s=30.0)
        _sub_ms = int((time.perf_counter() - _sub_t0) * 1000)
        per_obj.append((obj, raw, _sub_ms, _timed_out, _exit_code))

    # Process results (pure computation — no I/O, no lock).
    # stat_updates stores only the compliance verdict so the delta can be
    # applied to the FRESH baseline inside the lock (prevents stale-snapshot
    # undercount when two concurrent check_turn() calls race on the same chat).
    for obj, raw, _sub_ms, _timed_out, _exit_code in per_obj:
        parsed = _parse_result(raw)
        if parsed is None:
            if _ulo_dbg is not None:
                _ulo_dbg.log_compliance_call(
                    channel, chat_key, obj_id=obj.id,
                    subprocess_ms=_sub_ms, timed_out=_timed_out,
                    exit_code=_exit_code, raw_len=len(raw),
                    parse_ok=False, compliant=None, confidence=None,
                    reason_code=None, compliance_rate_new=None,
                    turns_checked=obj.turns_checked,
                    consecutive_failures=obj.consecutive_failures,
                )
            continue

        if _ulo_dbg is not None:
            # Log approximate stats from snapshot for observability (stale but
            # non-racy — only debug logging, not the authoritative store).
            _approx_tc = obj.turns_checked + 1
            _approx_cf = (max(0, obj.consecutive_failures - 1)
                         if parsed["compliant"] else obj.consecutive_failures + 1)
            _approx_cr: float | None = (
                (1.0 if parsed["compliant"] else 0.0) if obj.compliance_rate is None
                else (_EMA_ALPHA * (1.0 if parsed["compliant"] else 0.0)
                      + (1 - _EMA_ALPHA) * obj.compliance_rate)
            )
            _ulo_dbg.log_compliance_call(
                channel, chat_key, obj_id=obj.id,
                subprocess_ms=_sub_ms, timed_out=_timed_out,
                exit_code=_exit_code, raw_len=len(raw),
                parse_ok=True, compliant=parsed["compliant"],
                confidence=parsed["confidence"],
                reason_code=parsed["reason_code"],
                compliance_rate_new=_approx_cr,
                turns_checked=_approx_tc,
                consecutive_failures=_approx_cf,
            )

        # Store only the verdict — absolute values are computed from fresh
        # baseline inside the lock to prevent concurrent-call stale-snapshot race.
        stat_updates[obj.id] = {"compliant": parsed["compliant"]}
        results.append({
            "objective_id": obj.id,
            "compliant":    parsed["compliant"],
            "confidence":   parsed["confidence"],
            "reason_code":  parsed["reason_code"],
        })

    if stat_updates:
        # Short critical section: reload, apply delta to fresh baseline, save.
        # Using the lock here (not during subprocess calls) prevents two
        # concurrent check_turn() calls from both writing stale absolute values.
        with lock:
            fresh_objs = _ulo_load(channel, chat_key, tenant_id)
            changed = False
            for o in fresh_objs:
                if o.id not in stat_updates:
                    continue
                compliant = stat_updates[o.id]["compliant"]
                o.turns_checked += 1
                if compliant:
                    o.consecutive_failures = max(0, o.consecutive_failures - 1)
                else:
                    o.consecutive_failures += 1
                if o.compliance_rate is None:
                    o.compliance_rate = 1.0 if compliant else 0.0
                else:
                    o.compliance_rate = (
                        _EMA_ALPHA * (1.0 if compliant else 0.0)
                        + (1 - _EMA_ALPHA) * o.compliance_rate
                    )
                changed = True
            if changed:
                _ulo_save_all(channel, chat_key, fresh_objs, tenant_id)

    return results


# ── Reinforcement ─────────────────────────────────────────────────────────

def get_reinforcement_block(channel: str, chat_key: str, tenant_id: str | None = None) -> str:
    """Return a <ulo_reinforcement> block if any objective needs attention.

    Called by the adapter injection path — empty string when all objectives
    are within threshold, short reminder otherwise.
    """
    objs = _ulo_load(channel, chat_key, tenant_id)
    failing = [
        o for o in objs
        if o.active
        and o.compliance_rate is not None
        and o.compliance_rate < o.reinforcement_threshold
        and o.consecutive_failures >= 2
    ]
    if not failing:
        return ""

    lines = ["<ulo_reinforcement>",
             "Important — the following user preferences have been missed recently:"]
    for o in failing:
        pct = f"{o.compliance_rate:.0%}" if o.compliance_rate is not None else "?"
        # Full prompt-injection sanitisation (not just NFKC) — see ulo.render_block.
        safe_text = sanitize_text(o.text)
        lines.append(f"  • {safe_text}  (recent compliance: {pct})")
    lines.append("Please ensure the next response satisfies all of the above.")
    lines.append("</ulo_reinforcement>")
    return "\n".join(lines)


__all__ = [
    "SITE_ULO_COMPLIANCE",
    "check_turn",
    "get_reinforcement_block",
]
