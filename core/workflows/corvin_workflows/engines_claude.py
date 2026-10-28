"""Real WorkerEngine backed by the `claude` CLI in headless mode (`-p`).

Kept in a separate module from engines.py so importing corvin_workflows
never requires the `claude` binary to be on PATH — only `--engine claude`
(CLI) or explicit construction (tests) touches this module.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Optional

from .engines import EngineCall

# ── shared `claude -p` prompt guard (ADR-0648 / adversarial review R4) ─────
# The claude CLI expands `/`, `!` and `#` at byte 0 and `@<path>` ANYWHERE in a
# prompt — client-side, BEFORE the model runs, so no tool policy, permission
# mode or `--disallowedTools` restricts them. Every payload this module hands
# to the CLI therefore goes through the ONE shared neutraliser. The fallback is
# a RAISING stub, never a pass-through: an unimportable guard refuses the
# spawn, it never downgrades it to an unguarded one.
try:  # pragma: no cover - import shape
    import os as _pg_os, sys as _pg_sys
    _pg_d = _pg_os.path.dirname(_pg_os.path.abspath(__file__))
    while _pg_d != _pg_os.path.dirname(_pg_d):
        _pg_c = _pg_os.path.join(_pg_d, "corvin_operator", "bridges", "shared")
        if _pg_os.path.isdir(_pg_c):
            if _pg_c not in _pg_sys.path:
                _pg_sys.path.insert(0, _pg_c)
            break
        _pg_d = _pg_os.path.dirname(_pg_d)
    from prompt_guard import guard_prompt_head as _guard_prompt_head  # type: ignore
except Exception:  # pragma: no cover - guard unavailable => refuse, never bypass
    def _guard_prompt_head(_text):  # type: ignore[misc]
        raise RuntimeError(
            "shared claude-CLI prompt guard unavailable "
            "(corvin_operator/bridges/shared/prompt_guard.py) - refusing to build an "
            "unguarded `claude -p` payload"
        )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_DEFAULT_MODEL = "haiku"  # cheapest tier — this engine is for structural E2E proof, not quality
_DEFAULT_TIMEOUT_S = 60
_DENIED_TOOLS = "Bash Edit Write Read Glob Grep WebSearch WebFetch Task NotebookEdit"


class ClaudeEngineError(RuntimeError):
    pass


class SpawnRefusedError(ClaudeEngineError):
    """A worker spawn the pre-spawn gates refused. Nothing was spawned."""


_log = logging.getLogger(__name__)
_ENGINE_ID = "claude_code"
_GATE_CHANNEL = "workflow-engine"
_REASON_GATE_REFUSED = "gate_refused"
_REASON_GATE_UNAVAILABLE = "gate_unavailable"
_REFUSAL_UNAVAILABLE = (
    "[security] Pre-spawn safety gate unavailable — workflow step blocked (fail-closed). "
    "Contact the operator."
)


def _pre_spawn_refusal(prompt: str, tenant_id: str) -> tuple[str, str] | None:
    """WFB-R1-09: the four pre-spawn gates the console and bridge run before ANY `claude -p`.

    Returns ``None`` when the spawn is permitted, else ``(reason_code, refusal_text)``. Order and
    semantics mirror ``corvin_console._spawn_gates.check_console_spawn_or_refusal``: (a) L44
    house-rules, (b) ADR-0141 Tier-3 capability presence, (c) L34 flow guard, (d) L35 egress. The
    gates live in ``corvin_operator/bridges/shared`` (already on sys.path via the prompt-guard
    bootstrap above) and are imported here, not from ``corvin_console``: the scheduler and
    ``python -m corvin_workflows`` run with ``core/workflows`` alone on PYTHONPATH.

    Fail-closed: an import or gate error refuses the spawn. L44 and L35 each audit their own deny
    (``house_rules.*``, ``egress.blocked``); L34 audits ``data_flow.blocked``.
    """
    try:
        import spawn_gates as _sg  # type: ignore[import-not-found]
        import security_capabilities as _caps  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — a mandatory gate is absent → refuse
        _log.error("[workflow-engine] pre-spawn gates unimportable — spawn refused (fail-closed)")
        return _REASON_GATE_UNAVAILABLE, _REFUSAL_UNAVAILABLE
    try:
        hr = _sg.check_l44(
            prompt, tenant_id, persona="assistant", channel=_GATE_CHANNEL,
            chat_key=_GATE_CHANNEL, engine_id=_ENGINE_ID,
        )
        if hr is not None:
            return _REASON_GATE_REFUSED, hr
        try:
            _caps.assert_capabilities_present()
        except _caps.CapabilityMissingError:
            _caps.bootstrap_core_capabilities()
            _caps.assert_capabilities_present()
        flow = _sg.check_l34(
            _ENGINE_ID, tenant_id, prompt=prompt, persona="assistant",
            channel=_GATE_CHANNEL, chat_key=_GATE_CHANNEL,
        )
        if flow is not None:
            return _REASON_GATE_REFUSED, flow
        egress = _sg.check_l35(
            _ENGINE_ID, tenant_id, persona="assistant",
            channel=_GATE_CHANNEL, chat_key=_GATE_CHANNEL,
        )
        if egress is not None:
            return _REASON_GATE_REFUSED, egress
        return None
    except Exception as exc:  # noqa: BLE001 — any gate error → refuse, never allow
        _log.error("[workflow-engine] pre-spawn gate error (%s) — spawn refused (fail-closed)",
                   type(exc).__name__)
        return _REASON_GATE_UNAVAILABLE, _REFUSAL_UNAVAILABLE


def _audit_spawn_refused(tenant_id: str, reason_code: str) -> None:
    """WFB-R1-09: audit-first record of a refused DAG spawn, on the tenant's hash chain.

    Metadata only — no prompt, no refusal wording, no agent name. A failing audit write does not
    re-allow the spawn: the caller raises regardless.
    """
    try:
        import forge.paths as _fp  # type: ignore[import-not-found]
        import forge.security_events as _se  # type: ignore[import-not-found]
        _se.write_event(
            _fp.tenant_audit_chain(tenant_id), "workflow.spawn_refused",
            details={"tenant_id": tenant_id, "engine_id": _ENGINE_ID, "reason_code": reason_code},
        )
    except Exception as exc:  # noqa: BLE001 — the refusal stands even if the record cannot land
        _log.error("[workflow-engine] spawn_refused audit write failed (%s)", type(exc).__name__)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: first '{' to matching last '}' — the model sometimes adds a
    # sentence before/after the JSON despite instructions.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ClaudeEngineError(f"could not parse a JSON object from claude output: {text[:500]!r}")


def _split_envelope(stdout: str) -> tuple[str, dict[str, Any], str, Optional[float], bool]:
    """The CLI's ``--output-format json`` envelope as ``(text, usage, reported_model, cost_usd,
    is_error)``. Fail-soft: anything that is not an envelope is the plain reply text, with no
    usage and an unknown (empty) model, never a guess."""
    try:
        env = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout, {}, "", None, False
    if not isinstance(env, dict) or "result" not in env:
        return stdout, {}, "", None, False
    text = env["result"] if isinstance(env["result"], str) else ""
    usage = env["usage"] if isinstance(env.get("usage"), dict) else {}
    reported = ""
    per_model = env.get("modelUsage")
    if isinstance(per_model, dict) and per_model:
        def _out(item: tuple[str, Any]) -> int:
            v = item[1].get("outputTokens", 0) if isinstance(item[1], dict) else 0
            return v if isinstance(v, int) and not isinstance(v, bool) else 0
        reported = str(max(per_model.items(), key=_out)[0])
    cost = env.get("total_cost_usd")
    cost_usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return text, usage, reported, cost_usd, bool(env.get("is_error"))


def _usage_split(usage: dict[str, Any]) -> dict[str, int]:
    """The four billed token counts, through the ONE shared normaliser (ADR-0759)."""
    try:
        import engine_span as _es  # type: ignore[import-not-found]
        return _es.usage_split(usage)
    except Exception:  # noqa: BLE001 - never let a measurement helper fail a node
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}


def _span_writer(tenant: str):
    def _write(event_type: str, *, details: dict[str, Any], severity: str = "INFO", **_routing: Any) -> None:
        import forge.paths as _fp  # type: ignore[import-not-found]
        import forge.security_events as _se  # type: ignore[import-not-found]
        _se.write_event(_fp.tenant_audit_chain(tenant), event_type, details=details, severity=severity)
    return _write


def _span_ids(meta: dict[str, Any]) -> dict[str, str]:
    item = meta.get("item_index")
    node_id = str(meta.get("node_id", ""))
    return {"run_id": str(meta.get("run_id", "")),
            "turn_id": f"{node_id}#{item}" if item is not None else node_id}


def _span_start(tenant: str, meta: dict[str, Any], requested_model: str) -> str:
    """Open an ``engine.span`` for this spawn; returns its id ('' when the span cannot be written).
    Measurement only: a failing write is logged and the spawn proceeds."""
    try:
        import engine_span as _es  # type: ignore[import-not-found]
        span_id = _es.new_span_id()
        _es.emit_start(_span_writer(tenant), span_id=span_id, role="worker", engine_id=_ENGINE_ID,
                       model_id=requested_model, tenant_id=tenant, **_span_ids(meta))
        return span_id
    except Exception as exc:  # noqa: BLE001
        _log.error("[workflow-engine] engine.span.start not written (%s)", type(exc).__name__)
        return ""


def _span_end(tenant: str, meta: dict[str, Any], span_id: str, reported_model: str,
              usage: dict[str, Any], status: str, t0: float) -> None:
    """Close the span on the model the CLI REPORTED, never the one requested (ADR-0759)."""
    if not span_id:
        return
    try:
        import engine_span as _es  # type: ignore[import-not-found]
        _es.emit_end(_span_writer(tenant), span_id=span_id, role="worker", engine_id=_ENGINE_ID,
                     model_id=reported_model, status=status,
                     duration_ms=int((perf_counter() - t0) * 1000), tenant_id=tenant,
                     **_span_ids(meta), **_es.usage_split(usage))
    except Exception as exc:  # noqa: BLE001
        _log.error("[workflow-engine] engine.span.end not written (%s)", type(exc).__name__)


def _emit_resolution(tenant: str, call: EngineCall, prompt: str, span_id: str) -> None:
    """ADR-2308: write ``workflow.node_model_resolved`` now that the prompt (hence ``ctx``) exists."""
    meta = call.metadata
    resolution = meta.get("model_resolution")
    if resolution is None:
        return
    from . import model_resolution as _mr
    _mr.emit_node_model_resolved(
        tenant_id=tenant, wid=str(meta.get("workflow_id", "")), rid=str(meta.get("run_id", "")),
        node_id=str(meta.get("node_id", "")),
        node_class=_mr.node_class(meta.get("class_view") or {}, len(prompt)),
        resolution=resolution, span_id=span_id, item_index=meta.get("item_index"))


def _build_prompt(call: EngineCall) -> str:
    parts = [call.instructions.strip()]
    if call.inputs:
        parts.append(f"\nWorkflow inputs:\n{json.dumps(call.inputs, indent=2, default=str)}")
    if call.state:
        # Keep it bounded — full state can be large after several nodes.
        trimmed = {k: v for k, v in call.state.items() if not str(k).startswith("_")}
        if trimmed:
            parts.append(f"\nUpstream context (state):\n{json.dumps(trimmed, indent=2, default=str)}")
    parts.append(
        "\nRespond with ONLY a single JSON object and nothing else — no markdown fences, "
        "no commentary, no explanation before or after it."
    )
    return "\n".join(parts)


@dataclass
class ClaudeCliEngine:
    """Production WorkerEngine: one `claude -p` subprocess per spawn()."""

    # ADR-2308: this engine takes a model per call (EngineCall.model); the runner resolves one
    # for it. Not annotated, so it is a plain class attribute and not a dataclass field.
    accepts_node_model = True

    name: str = "claude-cli"
    model: str = _DEFAULT_MODEL
    timeout_s: int = _DEFAULT_TIMEOUT_S
    # WFB-R1-09: the tenant whose gates and audit chain judge every spawn. CLI / scheduler runs
    # take it from CORVIN_TENANT_ID; the console passes the authenticated session's tenant.
    tenant_id: str | None = None
    history: list[EngineCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if shutil.which("claude") is None:
            raise ClaudeEngineError(
                "ClaudeCliEngine requires the `claude` CLI on PATH — install Claude Code or use "
                "--engine stub"
            )

    def spawn(self, call: EngineCall) -> dict[str, Any]:
        self.history.append(call)
        prompt = _build_prompt(call)
        # WFB-R1-09: the gates judge the exact payload the worker receives (instructions, inputs
        # and upstream state). Refused → audit, then raise; nothing reaches the CLI below.
        tenant = self.tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
        refusal = _pre_spawn_refusal(prompt, tenant)
        if refusal is not None:
            reason_code, text = refusal
            _audit_spawn_refused(tenant, reason_code)
            raise SpawnRefusedError(
                f"spawn refused for agent={call.agent!r}: {text}"
            )
        # R4: whole-payload guard - `_build_prompt(call)` embeds the workflow
        # step's instructions and upstream node output. POSITIONAL argv, so the
        # sentinel also keeps a `-`-leading payload from becoming a CLI flag.
        # Reply contract unaffected: `_extract_json` parses stdout.
        # ADR-2308: a model resolved for this node wins; otherwise the engine's own model (the
        # alias it always used) stays, so a node without a model is unchanged.
        model = call.model or self.model
        meta = call.metadata
        span_id = _span_start(tenant, meta, model)
        _emit_resolution(tenant, call, prompt, span_id)
        cmd = [
            "claude", "-p", _guard_prompt_head(prompt),
            "--model", model,
            "--output-format", "json",
            "--disallowedTools", _DENIED_TOOLS,
        ]
        t0 = perf_counter()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            _span_end(tenant, meta, span_id, "", {}, "error", t0)
            raise ClaudeEngineError(
                f"claude call for agent={call.agent!r} timed out after {self.timeout_s}s"
            ) from e
        if proc.returncode != 0:
            _span_end(tenant, meta, span_id, "", {}, "error", t0)
            raise ClaudeEngineError(
                f"claude call for agent={call.agent!r} exited {proc.returncode}: "
                f"{proc.stderr.strip()[-2000:]}"
            )
        text, usage, reported, cost, is_error = _split_envelope(proc.stdout)
        _span_end(tenant, meta, span_id, reported, usage, "error" if is_error else "ok", t0)
        meta["usage"] = {"model_id": reported, "cost_usd": cost, **_usage_split(usage)}
        meta["span_id"] = span_id
        if is_error:
            raise ClaudeEngineError(
                f"claude call for agent={call.agent!r} reported an error: {text.strip()[-2000:]}"
            )
        return _extract_json(text)
