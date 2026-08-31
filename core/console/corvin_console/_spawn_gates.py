"""Shared fail-closed pre-spawn gate for ALL owner-console LLM spawn surfaces.

CRITICAL compliance (EU AI Act Art. 5 + 50 / GDPR / ADR-0143 / ADR-0042 /
ADR-0043 / ADR-0141) — single chokepoint.
---------------------------------------------------------------------------
The owner console spawns an OS-turn / ``claude -p`` from several authenticated
surfaces: the web-chat WebSocket (``chat_runtime.stream_turn``), the workflow
node runner + delegation-loop manager/worker (``routes/workflows.py``), the
design-assistant (``routes/workflows.py::_run_claude_for_design``) and the
floating console assistant (``routes/assistant.py``). The bridge adapter runs
the SAME fail-closed, audit-first pre-spawn gates before EVERY OS-turn spawn:

  (a) L44 acceptable-use (house-rules)         — ADR-0143, never fails open
  (b) ADR-0141 Tier-3 capability presence       — mandatory security layers
  (c) L34 data-classification flow guard        — ADR-0042
  (d) L35 network egress lockdown               — ADR-0043

Round-3 wired (a)+(b) into ``chat_runtime`` only. Round-4 found the gate is
STILL bypassed on the OTHER console spawn surfaces, and that even chat_runtime
omits (c)+(d). This module lifts the round-3 house-rules + capability logic
VERBATIM out of ``chat_runtime`` (so behaviour is identical) and adds L34/L35
via the canonical ``spawn_gates`` SSOT — exposing ONE function every console
spawn site calls before it spawns.

Fail-closed contract
---------------------
``check_console_spawn_or_refusal`` returns ``None`` when the spawn is permitted,
else a user-facing refusal string. ANY exception inside the orchestration is
caught and turned into a refusal (never an allow). The house-rules and
capability gates are independently fail-closed (a missing/unparseable layer
refuses). The L34/L35 helpers are fail-OPEN on operational errors (missing
module / malformed tenant config) but fail-CLOSED on an explicit policy DENY —
this preserves the canonical ``spawn_gates`` contract (ADR-0158) byte-for-byte;
the orchestration's own ``except`` only converts unexpected orchestration-level
errors into a refusal, it does not weaken the L34/L35 operational fail-open.

Audit-first
-----------
Every deny writes its L16 event BEFORE the refusal string is returned:
  * ``house_rules.denied`` / ``house_rules.escalated`` — emitted synchronously
    inside ``gate.classify`` to the per-tenant forge chain
    (``<tenant_home>/global/forge/audit.jsonl``) by the injected writer.
  * ``security.capability_missing`` (CRITICAL) — emitted by the LIP registry
    inside ``assert_capabilities_present()``.
  * ``data_flow.blocked`` (L34) / ``egress.blocked`` (L35) — emitted inside
    ``DataFlowGuard.validate`` / ``EgressGate.validate`` before the refusal
    string is built.

All events are metadata-only (rule_id / action / reason-code / classification /
engine_id — NEVER the prompt text), matching the bridge adapter's PII floor.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
# core/console/corvin_console/_spawn_gates.py → repo root is parents[3].
_REPO = _THIS_DIR.parents[3]

# house_rules / egress_gate / security_capabilities / spawn_gates / data_classification
# all live under operator/bridges/shared; forge.* under operator/forge. chat_runtime
# already adds these at import, but make it explicit so a direct import of this
# module (tests, other callers) resolves the same modules.
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
_FORGE_PATH = _REPO / "operator" / "forge"
for _p in (str(_BRIDGES_SHARED), str(_FORGE_PATH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Re-export the delegation-fanout engine_id SSOT (operator/bridges/shared/
# data_classification). chat_runtime classifies a delegated turn under this id;
# importing it here keeps the producer (chat_runtime) and the L34 registry from
# drifting — the drift that silently blocked every delegated web-chat turn
# (web:VErk2UPDjg, fixed in 49457d3). Fail-safe default mirrors the registry key
# so a broken import never changes the routed engine_id (the L34 guard inside
# spawn_gates already fails closed if data_classification is unimportable).
try:
    from data_classification import DELEGATION_ENGINE_ID  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover - defensive; broken install only
    DELEGATION_ENGINE_ID = "acs"

_log = logging.getLogger(__name__)


# ── L44 acceptable-use (house-rules) — DELEGATES to spawn_gates SSOT ──────────


def _check_house_rules_or_fail(
    *, prompt: str | None, channel: str, chat_key: str, tenant_id: str,
    persona: str = "assistant", engine_id: str = "",
) -> str | None:
    """L44 (ADR-0143) acceptable-use pre-spawn gate.

    DELEGATES to the canonical ``spawn_gates.check_l44`` (ADR-0158) — the
    SINGLE L44 implementation shared by the bridge adapter, acs_runtime and
    this console. ``check_l44`` is MANDATORY + FAIL-CLOSED (missing module,
    tampered/unparseable policy, or any gate/classifier error all refuse),
    AUDIT-FIRST (``house_rules.{denied,escalated}`` lands on the per-tenant
    L16 forge chain — resolved via ``forge.paths.tenant_global_dir(tenant_id)``,
    with ``VOICE_AUDIT_PATH`` override — before the refusal returns) and
    metadata-only. Returns ``None`` when permitted (allow / warn), else a
    user-facing refusal string."""
    from spawn_gates import check_l44 as _sg_l44  # type: ignore
    return _sg_l44(
        prompt, tenant_id,
        persona=persona or "assistant", channel=channel, chat_key=chat_key,
        engine_id=engine_id or "claude_code",
    )


# ── ADR-0141 Tier-3 capability presence — lifted VERBATIM from chat_runtime ───


def _check_capabilities_or_fail(*, channel: str, chat_key: str) -> str | None:
    """ADR-0141 Tier-3 — mandatory security-layer presence gate before spawn.

    Returns ``None`` when every mandatory capability is registered, else a
    user-facing refusal string. Fail-closed: an unimportable registry blocks the
    spawn. Mirrors the adapter's pre-spawn cap check; emits
    ``security.capability_missing`` (CRITICAL) inside the registry on a miss."""
    try:
        import security_capabilities as _sec_caps  # type: ignore
    except Exception:  # noqa: BLE001 — mandatory registry absent → fail closed
        _log.error("[LIP] security_capabilities unimportable — spawn blocked (fail-closed)")
        return ("[security] Layer-integrity registry unavailable — request blocked. "
                "Contact the operator to inspect the installation.")
    try:
        try:
            _sec_caps.assert_capabilities_present()
        except _sec_caps.CapabilityMissingError:
            # A path reached before boot's explicit bootstrap (or in a fresh
            # process): lazy-register from the canonical set, then re-assert. A
            # genuinely deleted layer still cannot be registered, so the
            # re-assert still blocks it (fail-closed preserved).
            _sec_caps.bootstrap_core_capabilities()
            _sec_caps.assert_capabilities_present()
        return None
    except _sec_caps.CapabilityMissingError as _e:
        _log.error("[LIP] mandatory capabilities missing: %s channel=%s chat=%s",
                   getattr(_e, "missing", "?"), channel, chat_key)
        return ("[security] A mandatory security layer is missing — request blocked "
                "(fail-closed). Contact the operator.")
    except Exception as _cap_exc:  # noqa: BLE001 — any registry error → fail closed
        _log.error("[LIP] capability gate error (%s) — spawn blocked (fail-closed)",
                   type(_cap_exc).__name__)
        return ("[security] Layer-integrity check error — request blocked "
                "(fail-closed). Contact the operator.")


# ── L34 / L35 via canonical spawn_gates SSOT (ADR-0042 / ADR-0043 / ADR-0158) ─


def _check_l34_l35_or_fail(
    *, engine_id: str, tenant_id: str, prompt: str | None, persona: str | None,
    channel: str, chat_key: str, classification: str | None = None,
) -> str | None:
    """L34 data-classification + L35 egress pre-spawn gates.

    Delegates to the canonical ``spawn_gates.check_l34`` / ``check_l35`` (the
    SAME SSOT the adapter + acs_runtime use). Each returns ``None`` when the
    spawn is permitted (gate passes, no tenant policy, or operational error —
    fail-open per ADR-0158) and a refusal string on an explicit policy DENY.
    ``DataFlowGuard.validate`` / ``EgressGate.validate`` emit ``data_flow.blocked``
    / ``egress.blocked`` to the L16 chain BEFORE the refusal is returned
    (audit-first). ``cc_local_mode`` mirrors the adapter's ADR-0126 remap.

    ``classification`` (e.g. ``"PUBLIC"``): when provided, bypasses the heuristic
    ``classify_task()`` and uses this level directly.  Use for spawn sites where
    the data-sensitivity is known at call time (e.g. the console assistant serves
    UI help — PUBLIC).

    A missing ``spawn_gates`` module raises here and is caught by the caller's
    fail-closed orchestration wrapper.
    """
    from spawn_gates import check_l34 as _sg_l34, check_l35 as _sg_l35  # type: ignore
    cc_local = os.environ.get("CORVIN_CC_LOCAL_MODE") == "1"
    l34 = _sg_l34(
        engine_id, tenant_id,
        classification=classification, prompt=prompt,
        persona=persona, channel=channel, chat_key=chat_key,
        cc_local_mode=cc_local,
    )
    if l34 is not None:
        return l34
    l35 = _sg_l35(
        engine_id, tenant_id,
        persona=persona, channel=channel, chat_key=chat_key,
    )
    if l35 is not None:
        return l35
    return None


# ── Public API — the ONE function every console spawn site calls ──────────────


def check_console_spawn_or_refusal(
    prompt: str | None,
    *,
    tenant_id: str,
    persona: str = "assistant",
    channel: str = "web",
    chat_key: str = "",
    engine_id: str = "claude_code",
    classification: str | None = None,
) -> str | None:
    """Run the four fail-closed pre-spawn gates the bridge adapter runs.

    Returns ``None`` when the spawn is PERMITTED. Returns a user-facing refusal
    string when ANY gate blocks the spawn — the caller MUST NOT spawn and SHOULD
    surface the string to the user.

    Order (cheapest-meaningful first, fail-closed throughout):
        (a) L44 acceptable-use (house-rules)   — ADR-0143
        (b) ADR-0141 Tier-3 capability presence
        (c) L34 data-classification flow guard — ADR-0042
        (d) L35 network egress lockdown        — ADR-0043

    ``classification`` (e.g. ``"PUBLIC"``): optional explicit override for the
    L34 heuristic. When supplied the gate skips ``classify_task()`` and uses
    this level directly. Useful when the caller knows the data-sensitivity at
    call time (e.g. the console assistant handles only UI-help queries — PUBLIC).

    The whole sequence is wrapped: ANY unexpected exception becomes a refusal
    (never an allow). Each gate audits its own L16 deny event before returning.
    """
    try:
        hr = _check_house_rules_or_fail(
            prompt=prompt, channel=channel, chat_key=chat_key,
            tenant_id=tenant_id, persona=persona, engine_id=engine_id,
        )
        if hr is not None:
            return hr

        caps = _check_capabilities_or_fail(channel=channel, chat_key=chat_key)
        if caps is not None:
            return caps

        flow = _check_l34_l35_or_fail(
            engine_id=engine_id, tenant_id=tenant_id, prompt=prompt,
            persona=persona, channel=channel, chat_key=chat_key,
            classification=classification,
        )
        if flow is not None:
            return flow

        return None
    except Exception as _exc:  # noqa: BLE001 — orchestration error → fail closed
        _log.error("[console-spawn-gate] orchestration error (%s) — spawn blocked "
                   "(fail-closed) channel=%s chat=%s", type(_exc).__name__,
                   channel, chat_key)
        return ("[security] Pre-spawn safety gate error — request blocked "
                "(fail-closed). Contact the operator.")


__all__ = ["check_console_spawn_or_refusal"]
