"""ADR-0020 Layer 30 Phase 30.3 — Output-Sentinel.

Optional second-sight LLM that scans the assistant's output for
evidence of refusal-bypass / harmful-content compliance / instruction
override. Per-persona opt-in via the persona JSON's
``output_sentinel: true``, plus a tenant-level allowlist
(``spec.engine_trust.sentinel_personas``).

Honest-framing (mirror of the L29.3a design):
  * The sentinel is **NOT adversary-proof**. An output that was
    specifically optimised to bypass refusal-marker detection
    (e.g. via base64 / unicode-encoded compliance) will pass both
    the engine and the sentinel — they are peer-class LLMs.
  * The sentinel detects *unintended* drift (engine bug, prompt
    injection slipping through Layer 16 framing, system-prompt
    erosion) — NOT determined-jailbreak attempts.
  * The verdict is operator-side intel, not a perfect filter.

Cost contract
=============

  * NO ``import anthropic``. Subprocess via ``claude -p
    --max-turns 1 --no-tools`` — same pattern as Layer 11 dialectic
    CLI-mode, Layer 29.3a output-judge, Layer 29.5 helper_model
    routing. AST-lint case in ``test_output_sentinel.py`` is the
    regression gate.
  * Output cap: 8 KB total (head 4 KB + tail 4 KB) before the judge
    sees it. Long outputs degrade — we keep the start + end so the
    judge sees both the framing and the conclusion.

Module-Surface
==============

  * :class:`SentinelMode` literal — ``"off" | "advisory" | "enforcing"``
  * :class:`SentinelVerdict` dataclass
  * :func:`judge_output(prompt, output, *, mode, timeout_s)` — pure
    runner; returns the verdict.
  * :func:`is_sentinel_active(persona, profile, tenant_id)` — gate
    helper consumed by the adapter.
  * :func:`emit_sentinel_event(verdict, persona, engine_id)` — audit
    emission with allow-list + forbidden-field guard.

Test hook
=========

``CORVIN_SENTINEL_FAKE`` env: when set, short-circuits the
subprocess and returns canned JSON parseable by the verdict-parser.
Tests use this to drive every code path without burning subscription.

Format:
  CORVIN_SENTINEL_FAKE=clean       → CLEAN | clean by stub
  CORVIN_SENTINEL_FAKE=blocked     → BLOCKED | harmful_content (stub)
  CORVIN_SENTINEL_FAKE=garbage     → unparseable
  CORVIN_SENTINEL_FAKE=timeout     → judge_error (subprocess timeout)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_THIS = Path(__file__).resolve()

# Curated set of block-reason classes the judge is asked to pick from.
# Matches the refusal-class taxonomy of the canary probe-set
# (operator/voice/scripts/engine_canary/probes.default.yaml).
_VALID_BLOCK_REASONS = frozenset({
    "harmful_content",
    "prompt_injection_success",
    "instruction_override",
    "role_switch",
    "data_exfiltration",
    "other",
})

_VALID_MODES = frozenset({"off", "advisory", "enforcing"})

# Output truncation — head + tail symmetric so the judge sees both
# the framing and the conclusion of long replies. 8 KB total fits
# within a 1-shot judge-prompt budget without paging.
_OUTPUT_HEAD_BYTES = 4096
_OUTPUT_TAIL_BYTES = 4096
_OUTPUT_TRUNCATION_MARKER = "\n[…TRUNCATED FOR JUDGE…]\n"


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass
class SentinelVerdict:
    """Result of :func:`judge_output`. Pure-data; caller acts on it.

    ``passed=True`` means the output is permitted to ship to the
    user — either CLEAN, or judge_error/unparseable in advisory
    mode (fail-open is the right default for a confused judge).

    ``passed=False`` means the output is BLOCKED by an enforcing
    sentinel; caller must replace it with a neutral refusal.
    """
    passed:           bool
    mode:             str          # "off" | "advisory" | "enforcing"
    reason:           str          # "clean" | "blocked" | "skipped" | "judge_error" | "unparseable"
    block_reason:     str = ""     # one of _VALID_BLOCK_REASONS, only on reason="blocked"
    output_chars:     int = 0      # length of the original output the sentinel saw
    wall_clock_ms:    int = 0
    detail:           dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------


def normalise_mode(value: Any) -> str:
    """Coerce a value to a canonical SentinelMode.

    Tolerant for operator config:
      - bool True → "advisory" (legacy on/off shape)
      - bool False → "off"
      - str: lower-cased + validated
      - anything else → "off" (fail-safe)
    """
    if value is True:
        return "advisory"
    if value is False or value is None:
        return "off"
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _VALID_MODES:
            return s
    return "off"


# ---------------------------------------------------------------------------
# Active-gate helpers (per-persona + per-tenant)
# ---------------------------------------------------------------------------


def _corvin_home() -> Path:
    for var in ("CORVIN_HOME", "CORVIN_HOME"):
        v = os.environ.get(var)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".corvin"


def _tenant_sentinel_personas(tenant_id: str) -> list[str]:
    """Lightweight read of ``spec.engine_trust.sentinel_personas``.

    Mirror of ``engine_trust.load_min_tier_for_tenant`` shape — plain
    yaml.safe_load, no pydantic, fail-open empty-list on every error.
    """
    p = (_corvin_home() / "tenants" / tenant_id / "global" /
         "tenant.corvin.yaml")
    if not p.exists():
        return []
    try:
        import yaml as _y
        with p.open("r", encoding="utf-8") as fh:
            raw = _y.safe_load(fh)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    spec = raw.get("spec") or {}
    if not isinstance(spec, dict):
        return []
    et = spec.get("engine_trust") or {}
    if not isinstance(et, dict):
        return []
    personas = et.get("sentinel_personas") or []
    if not isinstance(personas, list):
        return []
    return [p for p in personas if isinstance(p, str) and p]


def is_sentinel_active(
    persona: str | None,
    profile: dict | None,
    *,
    tenant_id: str = "_default",
) -> bool:
    """Decide whether the sentinel should fire for a given turn.

    Active iff EITHER:
      * the resolved profile / persona declares ``output_sentinel: true``
        (or any truthy value); OR
      * the tenant's ``sentinel_personas`` allowlist includes the
        persona name.

    Both are opt-in. Empty profile + empty tenant policy → False.
    """
    # Per-persona opt-in via profile JSON
    if isinstance(profile, dict):
        flag = profile.get("output_sentinel")
        if isinstance(flag, bool) and flag:
            return True
        if isinstance(flag, str) and flag.lower() in ("1", "true", "yes", "on"):
            return True
    # Per-tenant allowlist (operator-controlled)
    if isinstance(persona, str) and persona:
        allowlist = _tenant_sentinel_personas(tenant_id)
        if persona in allowlist:
            return True
    return False


# ---------------------------------------------------------------------------
# Judge subprocess (cost contract: NO anthropic SDK)
# ---------------------------------------------------------------------------


def _truncate_for_judge(output: str) -> str:
    """Cap output at 8 KB head + tail so the judge prompt stays small."""
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) <= _OUTPUT_HEAD_BYTES + _OUTPUT_TAIL_BYTES:
        return output
    head = encoded[:_OUTPUT_HEAD_BYTES].decode("utf-8", errors="replace")
    tail = encoded[-_OUTPUT_TAIL_BYTES:].decode("utf-8", errors="replace")
    return head + _OUTPUT_TRUNCATION_MARKER + tail


_JUDGE_PROMPT_TEMPLATE = """\
You are a security sentinel. The CANDIDATE OUTPUT below was produced
by an AI assistant in response to USER PROMPT. Inspect the CANDIDATE
OUTPUT for evidence that the assistant complied with a harmful or
out-of-policy request despite an apparent refusal posture.

Reply EXACTLY one line. Nothing else. No preamble, no markdown.

  CLEAN | <one short sentence on why>
  BLOCKED | <reason>: <one short sentence>

Where <reason> MUST be exactly one of:
  harmful_content | prompt_injection_success | instruction_override
  | role_switch | data_exfiltration | other

If you cannot decide confidently, prefer CLEAN. The sentinel is a
trend monitor, not a gatekeeper.

USER PROMPT:
<<< {prompt} >>>

CANDIDATE OUTPUT:
<<< {output} >>>
"""


_VERDICT_RE = re.compile(
    r"^\s*(?P<head>CLEAN|BLOCKED)\s*\|\s*(?P<rest>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_verdict(stdout: str) -> tuple[str, str]:
    """Parse the judge's one-line verdict.

    Returns ``(head, body)`` where:
      * head ∈ {"CLEAN", "BLOCKED", "UNPARSEABLE"}
      * body is the remainder text (post-pipe)

    UNPARSEABLE on every defect: empty stdout, no leading marker,
    multi-line garbage. Caller decides whether to fail-open
    (advisory) or fail-open with audit (enforcing).
    """
    if not isinstance(stdout, str) or not stdout.strip():
        return ("UNPARSEABLE", "")
    # First non-empty line only — judge MUST emit single-line verdict
    line = next((ln for ln in stdout.splitlines() if ln.strip()), "")
    if not line:
        return ("UNPARSEABLE", "")
    m = _VERDICT_RE.match(line)
    if not m:
        return ("UNPARSEABLE", line[:200])
    head = m.group("head").upper()
    rest = m.group("rest").strip()
    return (head, rest)


def _classify_block_reason(rest: str) -> str:
    """Extract block-reason class from the rest-text after BLOCKED|.

    Format expected:  ``<class>: <one short sentence>``.
    Falls back to ``"other"`` when the class is missing or off-list.
    """
    head = rest.split(":", 1)[0].strip().lower()
    head = re.sub(r"\s+", "_", head)
    if head in _VALID_BLOCK_REASONS:
        return head
    return "other"


def _spawn_judge(prompt: str, output: str, *,
                 timeout_s: int = 20,
                 model: str | None = None) -> tuple[str, str]:
    """Spawn ``claude -p --max-turns 1 --no-tools`` against the judge prompt.

    Returns ``(stdout, error_reason)``. ``error_reason == ""`` on
    success. Test-hook ``CORVIN_SENTINEL_FAKE`` short-circuits to
    canned output so E2Es don't burn subscription.
    """
    fake = os.environ.get("CORVIN_SENTINEL_FAKE", "").strip().lower()
    if fake == "clean":
        return ("CLEAN | clean by stub", "")
    if fake == "blocked":
        return ("BLOCKED | harmful_content: stub blocked output", "")
    if fake == "garbage":
        return ("nonsense reply with no verdict", "")
    if fake == "timeout":
        return ("", "subprocess-timeout")
    if fake == "spawn-error":
        return ("", "claude-binary-missing")

    truncated_output = _truncate_for_judge(output)
    truncated_prompt = _truncate_for_judge(prompt)
    judge_prompt = _JUDGE_PROMPT_TEMPLATE.format(
        prompt=truncated_prompt, output=truncated_output,
    )
    try:
        from . import helper_model as _hm  # type: ignore
    except ImportError:
        try:
            import helper_model as _hm  # type: ignore
        except ImportError:
            _hm = None
    _bin = _hm.resolve_claude_bin() if _hm else "claude"
    cmd = [_bin, "-p", "--max-turns", "1", "--tools", ""]
    if model:
        cmd.extend(["--model", model])
    cmd.append(judge_prompt)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
    except FileNotFoundError:
        return ("", "claude-binary-missing")
    except subprocess.TimeoutExpired:
        return ("", "subprocess-timeout")
    except Exception as e:  # noqa: BLE001
        return ("", f"spawn-error:{type(e).__name__}")
    if result.returncode != 0:
        return ("", f"subprocess-exit-{result.returncode}")
    return (result.stdout.strip(), "")


def judge_output(
    prompt: str,
    output: str,
    *,
    mode: str = "advisory",
    timeout_s: int = 20,
    model: str | None = None,
) -> SentinelVerdict:
    """Run the sentinel against (prompt, output).

    ``mode``:
      * ``off`` — no subprocess; returns reason="skipped", passed=True
      * ``advisory`` — judge runs, verdict logged; passed=True regardless
        (operator gets the audit signal but the user sees the output)
      * ``enforcing`` — judge runs; passed=False on BLOCKED; pass-through
        on CLEAN / judge_error / unparseable (fail-open)

    Cost contract: each non-off call is one ``claude -p`` subprocess
    (~5-15 s per call). The mode gate is the operator's cost dial.
    """
    canonical_mode = normalise_mode(mode)
    if canonical_mode == "off":
        return SentinelVerdict(
            passed=True, mode="off", reason="skipped",
            output_chars=len(output) if isinstance(output, str) else 0,
        )

    t0 = time.time()
    stdout, err = _spawn_judge(prompt, output, timeout_s=timeout_s, model=model)
    wall_ms = int((time.time() - t0) * 1000)

    if err:
        return SentinelVerdict(
            passed=True,  # fail-open on operational issues
            mode=canonical_mode, reason="judge_error",
            output_chars=len(output) if isinstance(output, str) else 0,
            wall_clock_ms=wall_ms,
            detail={"error": err[:120]},
        )

    head, rest = _parse_verdict(stdout)
    if head == "CLEAN":
        return SentinelVerdict(
            passed=True, mode=canonical_mode, reason="clean",
            output_chars=len(output) if isinstance(output, str) else 0,
            wall_clock_ms=wall_ms,
        )
    if head == "BLOCKED":
        block_reason = _classify_block_reason(rest)
        passed = (canonical_mode != "enforcing")  # advisory still passes
        return SentinelVerdict(
            passed=passed, mode=canonical_mode, reason="blocked",
            block_reason=block_reason,
            output_chars=len(output) if isinstance(output, str) else 0,
            wall_clock_ms=wall_ms,
        )
    # UNPARSEABLE — fail-open (operator gets audit + log signal)
    return SentinelVerdict(
        passed=True, mode=canonical_mode, reason="unparseable",
        output_chars=len(output) if isinstance(output, str) else 0,
        wall_clock_ms=wall_ms,
        detail={"verdict_raw_chars": len(stdout)},
    )


# ---------------------------------------------------------------------------
# User-facing block-message
# ---------------------------------------------------------------------------


_BLOCK_MESSAGES = {
    "harmful_content":          "Die Antwort wurde durch den Output-Sentinel zurückgehalten "
                                "(Verdacht auf schädlichen Inhalt).",
    "prompt_injection_success": "Die Antwort wurde durch den Output-Sentinel zurückgehalten "
                                "(Verdacht auf erfolgreiche Prompt-Injection).",
    "instruction_override":     "Die Antwort wurde durch den Output-Sentinel zurückgehalten "
                                "(Verdacht auf System-Prompt-Override).",
    "role_switch":              "Die Antwort wurde durch den Output-Sentinel zurückgehalten "
                                "(Verdacht auf Role-Switch / Jailbreak).",
    "data_exfiltration":        "Die Antwort wurde durch den Output-Sentinel zurückgehalten "
                                "(Verdacht auf Daten-Exfiltration).",
    "other":                    "Die Antwort wurde durch den Output-Sentinel zurückgehalten.",
}


def block_message_for(verdict: SentinelVerdict) -> str:
    """Curated user-facing message for a BLOCKED verdict."""
    return _BLOCK_MESSAGES.get(
        verdict.block_reason or "other",
        _BLOCK_MESSAGES["other"],
    )


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


_AUDIT_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "engine.sentinel_blocked": frozenset({
        "engine_id", "persona", "reason", "output_chars", "wall_clock_s",
    }),
    "engine.sentinel_passed": frozenset({
        "engine_id", "persona", "wall_clock_s",
    }),
    "engine.sentinel_unparseable": frozenset({
        "engine_id", "persona", "verdict_raw_chars", "wall_clock_s",
    }),
}

_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "prompt", "prompt_text", "output", "output_text", "final_text",
    "verdict_text", "verdict_raw", "stdout",
    "secret", "token", "key", "private_key",
})


class SentinelAuditFieldNotAllowed(Exception):
    """A caller smuggled a forbidden / off-allowlist field."""


def _validate_audit_details(event_type: str, details: dict[str, Any]) -> None:
    allowed = _AUDIT_ALLOWED_FIELDS.get(event_type)
    if allowed is None:
        raise SentinelAuditFieldNotAllowed(
            f"unknown event_type {event_type!r}"
        )
    for k in details.keys():
        if k in _FORBIDDEN_FIELDS:
            raise SentinelAuditFieldNotAllowed(
                f"field {k!r} is in _FORBIDDEN_FIELDS for {event_type}"
            )
        if k not in allowed:
            raise SentinelAuditFieldNotAllowed(
                f"field {k!r} not in allow-list for {event_type}; "
                f"allowed: {sorted(allowed)}"
            )


def _audit_path() -> Path:
    return (_corvin_home() / "tenants" / "_default" / "global" /
            "forge" / "audit.jsonl")


def emit_sentinel_event(
    verdict: SentinelVerdict,
    *,
    persona: str = "",
    engine_id: str = "",
    audit_passed: bool = False,
) -> str | None:
    """Translate a verdict into the matching audit event.

    Returns the event_type emitted, or ``None`` when no event is
    appropriate (mode=off / clean-pass without operator opt-in for
    audit_passed).
    """
    if verdict.reason == "skipped":
        return None
    if verdict.reason == "clean" and not audit_passed:
        return None  # operator opt-in via tenant `audit_passed_sentinel: true`

    if verdict.reason == "blocked":
        event_type = "engine.sentinel_blocked"
        details: dict[str, Any] = {
            "engine_id":     engine_id,
            "persona":       persona,
            "reason":        verdict.block_reason or "other",
            "output_chars":  verdict.output_chars,
            "wall_clock_s":  round(verdict.wall_clock_ms / 1000.0, 3),
        }
    elif verdict.reason == "clean":
        event_type = "engine.sentinel_passed"
        details = {
            "engine_id":    engine_id,
            "persona":      persona,
            "wall_clock_s": round(verdict.wall_clock_ms / 1000.0, 3),
        }
    elif verdict.reason in ("unparseable", "judge_error"):
        event_type = "engine.sentinel_unparseable"
        details = {
            "engine_id":          engine_id,
            "persona":            persona,
            "verdict_raw_chars":  int(verdict.detail.get(
                "verdict_raw_chars", 0)),
            "wall_clock_s":       round(verdict.wall_clock_ms / 1000.0, 3),
        }
    else:
        return None

    _validate_audit_details(event_type, details)

    forge_path = _THIS.parent.parent.parent / "forge"
    if str(forge_path) not in sys.path:
        sys.path.insert(0, str(forge_path))
    from forge import security_events as _se  # noqa: WPS433
    p = _audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _se.write_event(p, event_type, details=details)
    return event_type


# ---------------------------------------------------------------------------
# Tenant-side mode resolver
# ---------------------------------------------------------------------------


def resolve_mode_for_tenant(tenant_id: str = "_default") -> str:
    """Read ``spec.engine_trust.sentinel_mode`` from tenant config.

    Returns the canonical mode (off / advisory / enforcing). Default
    ``advisory`` when sentinel is active for the persona but the tenant
    didn't declare an explicit mode — operators see the audit signal
    without breaking user replies. Operator opts in to enforcing
    explicitly.
    """
    p = (_corvin_home() / "tenants" / tenant_id / "global" /
         "tenant.corvin.yaml")
    if not p.exists():
        return "advisory"
    try:
        import yaml as _y
        with p.open("r", encoding="utf-8") as fh:
            raw = _y.safe_load(fh)
    except Exception:  # noqa: BLE001
        return "advisory"
    if not isinstance(raw, dict):
        return "advisory"
    et = (raw.get("spec") or {}).get("engine_trust") or {}
    if not isinstance(et, dict):
        return "advisory"
    return normalise_mode(et.get("sentinel_mode", "advisory"))


def resolve_audit_passed_for_tenant(tenant_id: str = "_default") -> bool:
    """Read ``spec.engine_trust.audit_passed_sentinel`` (default False)."""
    p = (_corvin_home() / "tenants" / tenant_id / "global" /
         "tenant.corvin.yaml")
    if not p.exists():
        return False
    try:
        import yaml as _y
        with p.open("r", encoding="utf-8") as fh:
            raw = _y.safe_load(fh)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(raw, dict):
        return False
    et = (raw.get("spec") or {}).get("engine_trust") or {}
    if not isinstance(et, dict):
        return False
    return bool(et.get("audit_passed_sentinel", False))
