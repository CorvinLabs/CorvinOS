"""LLM synthesis stage (ADR-0282, P-C) — CorvinOS as the context brain.

`effect=egress`: it runs POST-gate (after Gate-1 approved the task) and sends the
task + gathered context to an LLM to produce the single best worker prompt. NOT in
the default pipeline — opt-in via `spec.context_engineering.pipeline` config.

Load-bearing safety (ADR-0282 R1/R2, baked in):
- Own quota pool (`enforce_ce_llm_quota`); over budget / unavailable / timeout /
  ANY exception → skip, keep the deterministic brief (degrade, never block). The
  stage carries its own fallback: on skip it leaves `synthesised_prompt=None` so
  render_brief_to_text's deterministic output stands.
- The `claude -p` subprocess is blocking; the runner calls this stage via
  `asyncio.to_thread` (build_context_post_gate), so it never blocks the event loop.
- Egress compliance + Gate-2 re-inspection of the synthesised prompt are the
  CALLER's job (the turn path), per ADR-0282 R2 — this stage only produces it.
- Content-free audit: the caller records model/tokens/sha256, never the text.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .base import StageTelemetry
from .registry import register_stage

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT_S = 45

_SYS = (
    "You are CorvinOS's context engineer. Given a user task and retrieved context, "
    "assemble the tightest, most useful briefing for a worker that must solve it: "
    "restate the goal, select ONLY relevant context, name the constraints, and list "
    "the tools/skills the worker will need. Respond as strict JSON: "
    '{"brief": "<the briefing>", "needs": {"tools": [], "skills": []}}.'
)


_CLOUD_HOST = "api.anthropic.com"


def _l35_egress_permitted(tenant_id: str) -> bool:
    """FAIL-CLOSED L35 residency check for the synthesis cloud call (review R2 C2).

    Unlike the bridge's ordering probe (which fails OPEN by design — it only picks
    which classifier to try first), this is an ENFORCEMENT gate on a real egress:
    the tenant's EgressGate decides. An absent policy uses the EgressGate's own
    permissive default (L34 opt-in residency, ADR-0173 — permissive for PUBLIC), so
    the active brain is not disabled everywhere; but a malformed/unreadable policy,
    a missing EgressGate module, or ANY error → DENY (return False), closing the
    fail-OPEN hole where a broken deny-policy leaked task text to the cloud."""
    try:
        from pathlib import Path as _Path  # noqa: PLC0415
        from egress_gate import EgressGate  # type: ignore  # noqa: PLC0415
        # Resolve the tenant config through the CANONICAL path helper (review R3
        # finding B3) — hand-rolling CORVIN_HOME/tenants/<tid>/global could diverge
        # from the real layout (the runtime-vs-maintenance split) and read a
        # non-existent file → permissive default → egress allowed despite a deny.
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        cfg = _Path(tenant_global_dir(tenant_id or "_default")) / "tenant.corvin.yaml"
        doc = {}
        if cfg.is_file():
            import yaml  # type: ignore  # noqa: PLC0415
            doc = yaml.safe_load(cfg.read_text("utf-8")) or {}
        gate = EgressGate.from_tenant_config(doc, audit_writer=None)
        return bool(gate.validate(_CLOUD_HOST).allowed)
    except Exception:  # noqa: BLE001 — enforcement gate fails CLOSED
        return False


def _resolve_bin() -> str:
    try:
        from helper_model import resolve_claude_bin  # noqa: PLC0415
        return resolve_claude_bin() or "claude"
    except Exception:  # noqa: BLE001
        return "claude"


def _context_digest(bundle: Any) -> str:
    br = bundle.brief
    mc = getattr(br, "memory_context", None)
    mems = [getattr(m, "title", None) or getattr(m, "filename", "?")
            for m in (getattr(mc, "matches", []) if mc else [])][:5]
    adrs = [getattr(d, "decision_id", "?") for d in (getattr(br, "related_decisions", None) or [])][:5]
    sks = [getattr(s, "title", None) or getattr(s, "skill_id", "?")
           for s in (getattr(br, "recommended_skills", None) or [])][:5]
    bls = list(getattr(br, "blockers", None) or [])[:5]
    return (f"Memories: {mems}\nRelated ADRs: {adrs}\n"
            f"Skills: {sks}\nBlockers: {bls}")


class LLMSynthesisStage:
    id = "llm_synthesis"
    requires: tuple = ("memory", "graph", "skill")
    effect = "egress"
    trust = "builtin"

    def run(self, bundle, ctx):
        cfg = ctx.config or {}
        # Egress guards run BEFORE the quota charge (review R2 finding B4: a turn
        # under a zero-egress policy must not burn a ce_llm unit for a call it will
        # never make). Two floors, both checked before any egress:
        #  1. operator toggle — the stage only synthesises if egress_ok is set;
        #  2. L35 residency (review R2 finding C2) — the REAL fail-closed check:
        #     the tenant's EgressGate must permit the cloud host, else skip. Absent
        #     gate / deny / any error → skip (never leak task text to the cloud).
        if not cfg.get("egress_ok"):
            return bundle, StageTelemetry(stage=self.id, status="skipped",
                                          reason="egress_not_allowed")
        if not _l35_egress_permitted(ctx.tenant_id):
            return bundle, StageTelemetry(stage=self.id, status="skipped",
                                          reason="egress_denied_l35")

        # gate: own pool, degrade-not-block — charged only once both egress floors pass
        try:
            from ..license_gate import enforce_ce_llm_quota  # noqa: PLC0415
            if not enforce_ce_llm_quota(ctx.tenant_id):
                return bundle, StageTelemetry(stage=self.id, status="skipped",
                                              reason="ce_llm_budget")
        except Exception:  # noqa: BLE001 — broken gate → skip (deterministic stands)
            return bundle, StageTelemetry(stage=self.id, status="skipped",
                                          reason="gate_unavailable")

        model = cfg.get("model") or _DEFAULT_MODEL
        prompt = (f"TASK:\n{bundle.task}\n\nRETRIEVED CONTEXT:\n"
                  f"{_context_digest(bundle)}")
        try:
            out = subprocess.run(
                [_resolve_bin(), "-p", prompt, "--append-system-prompt", _SYS,
                 "--model", model, "--disallowedTools", "*",
                 "--output-format", "json", "--max-turns", "1"],
                capture_output=True, text=True, timeout=_TIMEOUT_S, check=True)
        except Exception as e:  # noqa: BLE001 — timeout / nonzero / unavailable → degrade
            return bundle, StageTelemetry(stage=self.id, status="failed",
                                          reason="llm_unavailable", error=str(e)[:120])

        try:
            payload = json.loads(out.stdout)
            # claude -p --output-format json wraps the reply; the assistant text
            # is the model's JSON string — parse defensively.
            text = payload.get("result") if isinstance(payload, dict) else None
            inner = json.loads(text) if isinstance(text, str) else (
                payload if isinstance(payload, dict) else {})
            brief_text = inner.get("brief")
            needs = inner.get("needs")
            needs = needs if isinstance(needs, dict) else {}  # finding #8
        except Exception as e:  # noqa: BLE001 — unparseable → degrade
            return bundle, StageTelemetry(stage=self.id, status="failed",
                                          reason="parse_error", error=str(e)[:120])

        if not brief_text:
            return bundle, StageTelemetry(stage=self.id, status="skipped",
                                          reason="empty_synthesis")
        bundle.synthesised_prompt = brief_text
        bundle.scratch["needs"] = {"tools": list(needs.get("tools", []))[:8],
                                   "skills": list(needs.get("skills", []))[:8]}
        return bundle, StageTelemetry(
            stage=self.id, status="ok", confidence_tier="high",
            sources=[{"id": "synthesis", "score": 1.0}])


register_stage(LLMSynthesisStage())
