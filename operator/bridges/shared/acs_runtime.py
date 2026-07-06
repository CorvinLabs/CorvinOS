"""acs_runtime.py — ADR-0104 M2/M3/M4: ACS Manager Loop Engine.

Implements the Autonomous Compute Shell runtime:
  M2: Manager Loop Engine (A2) — DELEGATE / COMPLETE / FAIL decisions
  M3: A3 Self-Tooling — forge.create_tool / skillforge.create_skill injection
  M4: A4 Recursive Delegation — max_depth enforcement + budget fractions

Architecture:
  - Manager runs as ``claude -p`` subprocess (like helper_model.py)
  - Workers run via L22 engine fleet (ClaudeCodeEngine by default)
  - Budget envelope: hard abort on any limit breach
  - L16 audit events wired via shared audit.py (best-effort, never blocks)
  - L34 gate called before every worker engine spawn

Coexists alongside L25 Compute Worker (compute_worker.py); this is a
NEW component, not a replacement. L25 handles parameter sweeps; ACS
handles agentic decision loops.

MUST NOT import anthropic — CI AST lint enforces.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Ensure shared/ is importable when running standalone (e.g. from project root).
# MUST come first — engine_span (same dir) is imported right below.
_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# ADR-0171 — universal engine-span audit. Best-effort import (same dir); a missing
# module must never break a worker spawn — spans are additive observability.
try:
    import engine_span as _espan  # type: ignore
except Exception:  # noqa: BLE001
    _espan = None  # type: ignore

# ADR-0172 M1 — worker-trace observability. Best-effort import; absent module
# must never block a spawn. Sets trace_available=False on all spans.
try:
    import worker_trace as _wtrace  # type: ignore
except Exception:  # noqa: BLE001
    _wtrace = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MODEL_MANAGER = "claude-sonnet-5"
_DEFAULT_MODEL_WORKER = "claude-haiku-4-5-20251001"
_MANAGER_OUTPUT_CAP = 65536   # 64 KB max manager JSON response
_WORKER_OUTPUT_CAP = 131072   # 128 KB max worker response

# ACS timeouts (in seconds) — generous to allow complex workflows
_MANAGER_TIMEOUT = 1800   # 30 min for manager decisions
_WORKER_TIMEOUT = 1800    # 30 min for worker execution
_DEFAULT_BUDGET_TIMEOUT = 600  # 10 min default (can be overridden per budget)

_MANAGER_SYSTEM = """\
You are an ACS Manager Agent running inside CorvinOS. Your role is to
direct a team of worker agents to complete a complex workflow.

At each iteration you MUST return a JSON object — nothing else — with the
EXACT Manager Decision schema:

{
  "decision": "DELEGATE" | "COMPLETE" | "FAIL",
  "reasoning": "<string>",
  "subtasks": [           // only when decision="DELEGATE"
    {
      "id": "<snake_case_id>",
      "instructions": "<what the worker should do>",
      "expected_output": { <JSON Schema> },
      "success_criteria": "<string>",
      "priority": "normal",
      "budget_allocation": { "max_tokens": <int>, "max_tool_calls": <int> }
    }
  ],
  "complete_artifacts": { // only when decision="COMPLETE"
    "summary": "<string>",
    "output_paths": ["<path relative to run dir>", ...],  // ALL files created by workers (charts, PDFs, CSVs, …)
    "quality_score": <float 0–1>
  },
  "fail_reason": "<string>"  // only when decision="FAIL"
}

Rules:
- Return ONLY JSON. No explanations, no markdown, no code fences.
- DELEGATE to workers when the task is not yet complete.
- COMPLETE only when you are confident all goals are satisfied.
- FAIL when the task is structurally impossible given the constraints.
- Budget is hard — do not ignore it.
- Collect every file path from worker "artifacts" arrays and list them in output_paths.
  These paths are surfaced inline in the console so the user sees all charts and files.
"""

_WORKER_SYSTEM_BASE = """\
You are an ACS Worker Agent running inside CorvinOS. You have been assigned
a specific subtask by the Manager Agent.

Complete your assigned task and return ONLY a JSON object matching the
Worker Output schema:

{
  "status": "success" | "partial" | "failed",
  "result": { <output fields matching expected_output> },
  "artifacts": ["<relative path to any file you created>", ...],
  "confidence": <float 0–1>,
  "error": "<string, only if status=failed>",
  "usage": { "llm_tokens": <int>, "tool_calls": <int> }
}

Rules:
- Return ONLY JSON. No explanations, no markdown, no code fences.
- Confidence 1.0 = fully completed; 0.0 = completely failed.
- Set status="partial" for degraded but useful output.
- If you generate any files (charts, images, PDFs, CSVs), save them to the
  ./output/ subdirectory and list their relative paths in "artifacts".
  The console will display these files inline to the user.
"""

_DELEGATE_WORKER_SYSTEM_SUFFIX = """

You are also authorized to act as a sub-manager (A4 recursive delegation).
If the assigned task is too complex for a single worker turn, you may return a
DELEGATE decision instead of a normal Worker Output. Use the same Manager Decision
JSON schema as the top-level manager, but honour your sub-budget strictly.
"""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

# The only BudgetEnvelope fields a caller (HTTP budget_override, workflow YAML)
# may ever set. Deliberately excludes the accumulated/internal-state fields
# (loops_used, tokens_used, workers_used, tool_calls_used,
# rejected_completions, start_time) — those are runtime bookkeeping, not caps,
# and letting a caller set e.g. start_time in the future would permanently
# defeat the max_wall_time check (adversarial review finding).
_BUDGET_OVERRIDE_ALLOWED_FIELDS = frozenset({
    "max_loops", "max_total_tokens", "max_wall_time", "max_total_workers",
    "max_tool_calls", "max_depth", "max_workers_per_iteration",
    "max_rejected_completions",
})


@dataclass
class BudgetEnvelope:
    max_loops: int = 100
    max_total_tokens: int = 0        # 0 = unbounded
    max_wall_time: int = 3600        # seconds
    max_total_workers: int = 500
    max_tool_calls: int = 0          # 0 = unbounded
    max_depth: int = 4
    max_workers_per_iteration: int = 6
    max_rejected_completions: int = 2

    # Accumulated
    loops_used: int = 0
    tokens_used: int = 0
    workers_used: int = 0
    tool_calls_used: int = 0
    rejected_completions: int = 0
    start_time: float = field(default_factory=time.monotonic)

    def check(self) -> str | None:
        """Return a breach description, or None if within budget."""
        if self.max_loops > 0 and self.loops_used >= self.max_loops:
            return f"max_loops={self.max_loops} reached"
        if self.max_total_tokens > 0 and self.tokens_used >= self.max_total_tokens:
            return f"max_total_tokens={self.max_total_tokens} reached ({self.tokens_used} used)"
        if self.max_total_workers > 0 and self.workers_used >= self.max_total_workers:
            return f"max_total_workers={self.max_total_workers} reached"
        if self.max_tool_calls > 0 and self.tool_calls_used >= self.max_tool_calls:
            return f"max_tool_calls={self.max_tool_calls} reached"
        elapsed = time.monotonic() - self.start_time
        if elapsed > self.max_wall_time:
            return f"max_wall_time={self.max_wall_time}s reached ({elapsed:.0f}s elapsed)"
        return None

    def fraction(self, f: float) -> "BudgetEnvelope":
        """Return a child budget that is fraction f of this budget (A4 delegation)."""
        return BudgetEnvelope(
            max_loops=max(1, int(self.max_loops * f)),
            max_total_tokens=int(self.max_total_tokens * f) if self.max_total_tokens else 0,
            max_wall_time=int(self.max_wall_time * f),
            max_total_workers=max(1, int(self.max_total_workers * f)),
            max_tool_calls=int(self.max_tool_calls * f) if self.max_tool_calls else 0,
            max_depth=max(0, self.max_depth - 1),
            max_workers_per_iteration=self.max_workers_per_iteration,
            max_rejected_completions=self.max_rejected_completions,
        )


@dataclass
class WorkerResult:
    worker_id: str
    status: str          # success | partial | failed
    result: dict
    confidence: float = 0.0
    error: str = ""
    artifacts: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass
class ACSResult:
    run_id: str
    workflow_id: str
    status: str           # success | failed | budget_exhausted
    final_output: dict = field(default_factory=dict)
    summary: str = ""
    iterations: int = 0
    workers_spawned: int = 0
    budget_breach: str = ""
    error: str = ""
    run_dir: Path | None = None
    elapsed_s: float = 0.0


@dataclass
class RunContext:
    run_id: str
    workflow_id: str
    workflow_spec: dict
    budget: BudgetEnvelope
    run_dir: Path
    state: dict = field(default_factory=dict)
    iteration: int = 0
    worker_results: list[WorkerResult] = field(default_factory=list)
    prev_output_hash: int | None = None
    tenant_id: str = "_default"
    depth: int = 0
    dynamic_tools: dict = field(default_factory=dict)     # tool_id → source
    loss_history: list = field(default_factory=list)        # list[WorkflowLoss] — ADR-0105 M1
    loss_profile: Any | None = None                        # LossProfile | None — ADR-0105 M2
    m4_base_workers: int = 0                               # original max_workers_per_iteration — ADR-0105 M4
    worker_attributions: list = field(default_factory=list)  # list[dict] per-iteration — ADR-0105 M4
    datasource_env: dict = field(default_factory=dict)     # ADR-0127 — DSI conn env for ClaudeCode workers


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env)
    # Prefer forge.paths repo-root discovery so the same .corvin/ tree is used
    # regardless of which process invokes acs_runtime (uvicorn, adapter, CLI).
    # forge is always on PYTHONPATH in a running CorvinOS instance.
    try:
        from forge import paths as _fp  # type: ignore[import-untyped]
        return _fp.corvin_home()
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / ".corvin"


def _claude_binary() -> str:
    """Resolve the claude CLI path: CORVIN_CLAUDE_BIN env → shutil.which →
    known install locations → bare name.

    systemd-managed services run with a restricted PATH that may not include
    ~/.local/bin.  CORVIN_CLAUDE_BIN is the canonical override knob and is
    injected by bridge.sh / the gateway service file — but when it is absent
    (e.g. the adapter spawned without it, as seen in /proc/<pid>/environ) a bare
    ``shutil.which("claude")`` returns None under the stripped PATH and we would
    fall back to the bare name → ``FileNotFoundError`` on spawn. Delegating to
    the canonical ``helper_model.resolve_claude_bin`` (which probes the known
    install locations) keeps this resolver consistent with the WorkerEngine and
    every helper spawn hardened in commit 79de989. Best-effort; never raises.
    """
    explicit = os.environ.get("CORVIN_CLAUDE_BIN", "").strip()
    if explicit:
        return explicit
    try:
        import helper_model as _hm  # type: ignore
        return _hm.resolve_claude_bin()
    except Exception:  # noqa: BLE001 — resolver optional; fall back to PATH probe
        found = shutil.which("claude")
        return found or "claude"


def _run_dir(tenant_id: str, bridge: str, chat: str, run_id: str) -> Path:
    return (
        _corvin_home()
        / "tenants" / tenant_id
        / "sessions" / f"{bridge}:{chat}"
        / "acs" / "runs" / run_id
    )


def _audit_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "audit.jsonl"


_FORGE_PATH = str(Path(__file__).resolve().parents[3] / "operator" / "forge")


def _resolve_worker_model(explicit: str | None, tenant_id: str) -> str:
    """ADR-0112 M1 / ADR-0119: Six-step ACS worker model resolution.

    Priority:
    1. explicit per-call override (e.g. workflow body field)
    2. CORVIN_ACS_WORKER_MODEL env  (bridge injects from persona.engine_models or acs_worker_model)
    3. ANTHROPIC_MODEL env          (inherits Claude Code model selection)
    4. tenant.corvin.yaml::spec.engine_models.<engine_id>.worker_model  (per-engine tenant default)
    5. tenant.corvin.yaml::spec.default_worker_model                    (global console-configurable)
       tenant.corvin.yaml::spec.acs.default_worker_model                (legacy ACS-specific fallback)
    6. _DEFAULT_MODEL_WORKER        (backward-compatible Haiku fallback)
    """
    if explicit:
        return explicit
    if m := os.environ.get("CORVIN_ACS_WORKER_MODEL", "").strip():
        return m
    if m := os.environ.get("ANTHROPIC_MODEL", "").strip():
        # ANTHROPIC_MODEL is set by the outer OS-layer engine and is NOT a dedicated
        # ACS worker model override. Log so cost surprises are visible in the adapter log.
        log.warning(
            "_resolve_worker_model: falling through to ANTHROPIC_MODEL=%r for "
            "ACS workers (tenant=%s). Set CORVIN_ACS_WORKER_MODEL to pin the worker "
            "model independently of the OS-turn model.",
            m, tenant_id,
        )
        return m
    try:
        import yaml  # type: ignore[import-untyped]  # noqa: PLC0415
        cfg = _corvin_home() / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
        if cfg.is_file():
            raw = yaml.safe_load(cfg.read_text("utf-8")) or {}
            spec = raw.get("spec") or {}
            # Step 4: per-engine tenant default (ADR-0119)
            engine_id = os.environ.get("CORVIN_ENGINE_ID", "claude_code")
            engine_models = spec.get("engine_models") or {}
            per_engine_val = (engine_models.get(engine_id) or {}).get("worker_model")
            if isinstance(per_engine_val, str) and per_engine_val.strip():
                return per_engine_val.strip()
            # Step 5: global tenant default — console-configurable key takes priority.
            # spec.default_worker_model is written by PUT /api/engine (console UI).
            # spec.acs.default_worker_model is the legacy ACS-specific key (ADR-0112).
            raw_val = spec.get("default_worker_model") or (spec.get("acs") or {}).get("default_worker_model")
            # Guard against null/non-string YAML values (e.g. `default_worker_model: ~`)
            if isinstance(raw_val, str) and raw_val.strip():
                return raw_val.strip()
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_MODEL_WORKER


def _write_audit(tenant_id: str, event_type: str, details: dict) -> None:
    """Best-effort L16 audit write. Never raises into caller."""
    try:
        if _FORGE_PATH not in sys.path:
            sys.path.insert(0, _FORGE_PATH)
        from forge import security_events as _sec  # type: ignore[import-untyped]
        _sec.write_event(_audit_path(tenant_id), event_type, details=details)
    except Exception as exc:  # noqa: BLE001
        log.debug("acs_runtime: audit write failed (%s): %s", event_type, exc)


# ---------------------------------------------------------------------------
# ADR-0109: Worker Decision Audit Trail (WDAT) helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    """Return full SHA-256 hex digest of text (UTF-8). Used for WDAT integrity binding."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# ADR-0109 M4: Content Store encryption
# ---------------------------------------------------------------------------

def _wdat_load_key() -> "bytes | None":
    """Return the 32-byte AES-256 WDAT key, or None if not configured.

    Resolution order:
      1. CORVIN_WDAT_KEY env var (64-char hex string)
      2. vault item "wdat_key" (hex string stored via /vault set wdat_key <hex>)
    Plaintext fallback is used silently when neither is available.
    """
    hex_key = os.environ.get("CORVIN_WDAT_KEY", "").strip()
    if hex_key:
        try:
            key = bytes.fromhex(hex_key)
            if len(key) == 32:
                return key
            log.warning("acs: CORVIN_WDAT_KEY length wrong (%d bytes); need 32", len(key))
        except ValueError:
            log.warning("acs: CORVIN_WDAT_KEY is not valid hex; ignoring")
    try:
        if _FORGE_PATH not in sys.path:
            sys.path.insert(0, _FORGE_PATH)
        from vault import get_item as _vault_get  # type: ignore[import-untyped]
        val = str(_vault_get("wdat_key", source="wdat")).strip()
        key = bytes.fromhex(val)
        if len(key) == 32:
            return key
    except Exception:
        pass
    return None


def _wdat_encrypt_content(plaintext: bytes, key: bytes) -> bytes:
    """AES-256-GCM encrypt. Returns nonce(12 bytes) || ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def _wdat_record_completion(
    ctx: "RunContext",
    wid: str,
    wr: "WorkerResult",
    raw_prompt: str,
    raw_output: str,
    tok: int,
    duration_ms: int,
    spawn_nonce: "str | None",
    attestation: dict,
) -> None:
    """ADR-0109 M1+M3+M4: emit acs.worker_traced (Tier 1) + write content store (Tier 2).

    Tier 1 — L16 hash chain: hashes + attestation only, never content (GDPR Art. 5).
    Tier 2 — traces/<worker_id>.json[.enc]: instruction + output, mode 0600.
              Encrypted with AES-256-GCM when WDAT key configured (M4); plaintext
              fallback otherwise. Writes are best-effort and never block or fail worker.
    """
    _write_audit(ctx.tenant_id, "acs.worker_traced", {
        "run_id": ctx.run_id,
        "worker_id": wid,
        "status": wr.status,
        "confidence": round(wr.confidence, 3),
        "output_hash": _sha256(raw_output),
        "duration_ms": duration_ms,
        "tokens_used": tok,
        "spawn_nonce": spawn_nonce,
        "engine_attestation": {
            "engine_id": attestation.get("engine_id", "claude_code"),
            "model_id": attestation.get("model_id", "unknown"),
            "locality": attestation.get("locality", "eu_cloud"),
        },
    })
    # Tier 2: content store — best-effort, never raises
    try:
        traces_dir = ctx.run_dir / "traces"
        traces_dir.mkdir(exist_ok=True)
        payload: dict = {
            "worker_id": wid,
            "instruction_hash": _sha256(raw_prompt),
            "output_hash": _sha256(raw_output),
            "instruction": raw_prompt[:8000],
            "output": raw_output[:8000],
            "engine_meta": {
                "engine_id": attestation.get("engine_id", "claude_code"),
                "model_id": attestation.get("model_id", "unknown"),
                "attested": attestation.get("attested", False),
                **({"session_id": attestation["session_id"]} if "session_id" in attestation else {}),
            },
        }
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        wdat_key = _wdat_load_key()
        if wdat_key is not None:
            trace_file = traces_dir / f"{wid}.json.enc"
            trace_file.write_bytes(_wdat_encrypt_content(payload_bytes, wdat_key))
        else:
            trace_file = traces_dir / f"{wid}.json"
            trace_file.write_bytes(payload_bytes)
        trace_file.chmod(0o600)
    except Exception as _exc:
        log.debug("acs: wdat content store write failed for %s: %s", wid, _exc)


def _clamp_positive_cap(value: int, default: int, ceiling: int) -> int:
    """Clamp a budget cap to [1, ceiling]. BudgetEnvelope.check() treats any
    value <= 0 as "unbounded" for max_loops/max_total_workers (`if self.max_X
    > 0 and ...`) — a workflow YAML or budget_override setting either to 0 or
    negative silently disables that specific enforcement, not "uses the
    default" (adversarial review finding). Values above ceiling are clamped,
    not rejected, so a legitimately large workflow still runs — just bounded."""
    if value <= 0:
        return default
    return min(value, ceiling)


def _budget_from_spec(spec: dict) -> BudgetEnvelope:
    b = (
        spec.get("orchestration", {})
        .get("delegation_loop", {})
        .get("budget", {})
    ) or {}
    return BudgetEnvelope(
        max_loops=_clamp_positive_cap(int(b.get("max_loops") or 100), 100, 5000),
        max_total_tokens=int(b.get("max_total_tokens") or 0),
        max_wall_time=int(b.get("max_wall_time") or 3600),
        max_total_workers=_clamp_positive_cap(int(b.get("max_total_workers") or 500), 500, 5000),
        max_tool_calls=int(b.get("max_tool_calls") or 0),
        max_depth=int(b.get("max_depth") or 4),
        max_workers_per_iteration=int(b.get("max_workers_per_iteration") or 6),
        max_rejected_completions=int(b.get("max_rejected_completions") or 2),
    )


def _make_run_dir(run_dir: Path) -> None:
    for sub in ("workers", "iterations", "gate_results", "dynamic_tools", "output"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    try:
        (run_dir / "output" / "FINAL").mkdir(exist_ok=True)
    except OSError:
        pass


def _write_json_safe(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + "\n...[truncated]"


# ---------------------------------------------------------------------------
# Manager spawn (M2)
# ---------------------------------------------------------------------------

def _format_loss_context(
    history: list,
    profile: Any,
    worker_attributions: "list | None" = None,
) -> str:
    """Format the ADR-0105 M2/M4 Loss Trajectory + attribution block for manager prompt."""
    if not history:
        return ""

    sep = "─" * 68
    lines = [f"── Quality Loss Trajectory {sep[26:]}"]

    for i, entry in enumerate(history):
        delta_str = ""
        trend = ""
        if entry.delta is not None:
            if entry.delta > 0.01:
                delta_str = f"  Δ=+{entry.delta:.3f}"
                trend = " ↑ (improving)"
            elif entry.delta < -0.01:
                delta_str = f"  Δ={entry.delta:.3f}"
                trend = " ↓ (regression)"
            else:
                delta_str = f"  Δ≈{entry.delta:.3f}"
                trend = " ≈ (stalled)"
        label = "(baseline)" if entry.delta is None else trend.strip()
        lines.append(f"Iteration {entry.iteration + 1}  L={entry.total:.3f}{delta_str}  {label}")

        prev_entry = history[i - 1] if i > 0 else None

        def _dim(name: str, val: float, _prev: Any = prev_entry) -> str:
            arrow = ""
            if _prev is not None:
                prev = getattr(_prev, name, None)
                if prev is not None:
                    diff = val - prev
                    if diff > 0.02:
                        arrow = " ↑" if diff < 0.08 else " ↑↑"
                    elif diff < -0.02:
                        arrow = " ↘"
                    else:
                        arrow = " ≈"
            check = " ✓" if val >= 0.80 else ""
            return f"  {name:13s}: {val:.2f}{check}{arrow}"

        lines.append(_dim("completeness", entry.L_completeness))
        lines.append(_dim("novelty",      entry.L_novelty))
        lines.append(_dim("quality",      entry.L_quality))
        lines.append(_dim("metrics",      entry.L_metrics))
        lines.append(_dim("confidence",   entry.L_confidence))

    # Summary line
    latest = history[-1]
    target = getattr(profile, "target", 0.0) if profile else 0.0
    gap = max(0.0, target - latest.total)
    if gap < 0.001:
        trend_word = "target reached"
    elif len(history) >= 2 and latest.delta is not None and latest.delta > 0.005:
        trend_word = "converging"
    elif len(history) >= 2 and latest.delta is not None and abs(latest.delta) <= 0.005:
        trend_word = "plateau"
    else:
        trend_word = "diverging" if (latest.delta or 0) < -0.01 else "in progress"

    lines.append(f"Current target : L ≥ {target:.2f}  |  Gap : {gap:.3f}  |  Trend : {trend_word}")

    # Bottleneck: dimension with highest weight × gap
    if profile:
        w = profile.weights
        dims = [
            ("completeness", latest.L_completeness, w.completeness),
            ("novelty",      latest.L_novelty,      w.novelty),
            ("quality",      latest.L_quality,      w.quality),
            ("metrics",      latest.L_metrics,      w.metrics),
            ("confidence",   latest.L_confidence,   w.confidence),
        ]
        bottleneck = max(dims, key=lambda t: t[2] * max(0.0, 1.0 - t[1]))
        name, val, wt = bottleneck
        lines.append(
            f"Bottleneck     : {name} ({val:.2f}) — accounts for "
            f"{int(wt * 100)}% of loss weight"
        )

    # ADR-0105 M4: per-worker attribution (last delegation only)
    if worker_attributions:
        latest_attr = worker_attributions[-1]
        top = latest_attr.get("workers", [])[:3]
        if top:
            lines.append("Worker Attribution (last delegation):")
            for w in top:
                sign = "+" if w["attribution"] >= 0 else ""
                lines.append(
                    f"  [{w['worker_id']}]  {sign}{w['attribution']:.4f}"
                    f"  ({w['status']}, conf={w['confidence']:.2f})"
                )

    lines.append(sep)
    return "\n".join(lines)


def _compute_worker_attributions(
    workers: "list[WorkerResult]",
    loss_delta: "float | None",
    iteration: int,
) -> dict:
    """ADR-0105 M4: Estimate per-worker quality contribution (state-diff proxy).

    Distributes loss_delta proportionally across workers by confidence ×
    status weight × result novelty. Returns a dict suitable for appending
    to ctx.worker_attributions.
    """
    if not workers:
        return {}

    _status_weight = {"success": 1.0, "partial": 0.4, "failed": 0.0}

    def _proxy(wr: "WorkerResult") -> float:
        sw = _status_weight.get(wr.status, 0.0)
        if sw == 0.0:  # failed workers contribute nothing
            return 0.0
        result_size = len(json.dumps(wr.result, default=str))
        novelty_bonus = min(0.4, result_size / 2500)
        return wr.confidence * sw + novelty_bonus

    proxies = [_proxy(wr) for wr in workers]
    total_proxy = sum(proxies) or 1.0

    entries = []
    for wr, proxy in zip(workers, proxies):
        if loss_delta is not None:
            contrib = (proxy / total_proxy) * loss_delta
        else:
            contrib = proxy / total_proxy
        entries.append({
            "worker_id": wr.worker_id,
            "status": wr.status,
            "confidence": round(wr.confidence, 3),
            "attribution": round(contrib, 4),
        })

    return {
        "iteration": iteration,
        "workers": sorted(entries, key=lambda x: x["attribution"], reverse=True),
        "loss_delta": round(loss_delta, 4) if loss_delta is not None else None,
    }


def _build_manager_prompt(ctx: RunContext) -> str:
    """Build the manager prompt for one iteration."""
    spec = ctx.workflow_spec
    wf_name = spec.get("workflow", {}).get("name", ctx.workflow_id)
    description = spec.get("workflow", {}).get("description", "")
    b = ctx.budget

    lines = [
        f"WORKFLOW: {wf_name}",
        f"DESCRIPTION: {description}",
        f"ITERATION: {ctx.iteration + 1} / {b.max_loops}",
        "",
        f"BUDGET REMAINING:",
        f"  loops: {b.max_loops - b.loops_used - 1}",
        f"  tokens: {'unlimited' if not b.max_total_tokens else b.max_total_tokens - b.tokens_used}",
        f"  workers: {b.max_total_workers - b.workers_used}",
        f"  wall_time_s: {b.max_wall_time - int(time.monotonic() - b.start_time):.0f}",
        "",
    ]

    # ADR-0105 M2/M4: inject loss trajectory + worker attribution
    if ctx.loss_history:
        loss_block = _format_loss_context(
            ctx.loss_history, ctx.loss_profile, ctx.worker_attributions
        )
        if loss_block:
            lines += [loss_block, ""]

    if ctx.state:
        state_json = json.dumps(ctx.state, indent=2, default=str)
        lines += ["CURRENT STATE:", _truncate(state_json, 8000), ""]

    if ctx.worker_results:
        lines += ["WORKER RESULTS FROM PREVIOUS ITERATION:"]
        for wr in ctx.worker_results[-10:]:  # last 10 workers
            result_json = json.dumps(wr.result, default=str)
            lines.append(
                f"  [{wr.worker_id}] status={wr.status} confidence={wr.confidence:.2f} "
                f"result={_truncate(result_json, 500)}"
            )
        lines.append("")

    if ctx.iteration == 0 and ctx.workflow_spec.get("state", {}).get("initial"):
        initial = json.dumps(ctx.workflow_spec["state"]["initial"], indent=2)
        lines += ["INITIAL STATE:", _truncate(initial, 2000), ""]

    lines += ["Return your Manager Decision as JSON now."]
    return "\n".join(lines)


def _parse_manager_decision(text: str) -> dict | None:
    """Extract and parse the first JSON object from manager output.

    Uses bracket-counting to handle arbitrary nesting depth, which the old
    single-level regex could not do (DELEGATE subtasks are 3+ levels deep).
    """
    import re as _re
    text = text.strip()
    # Strip markdown code fences first
    fence = _re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fence:
        text = fence.group(1).strip()
    # Find the outermost {...} by counting brackets
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    # Try the next "{" after this failed block
                    next_start = text.find("{", i + 1)
                    if next_start == -1:
                        return None
                    start = next_start
                    depth = 0
    return None


# ---------------------------------------------------------------------------
# ADR-0127 — engine selection (Hermes/Ollama ↔ Claude Code) + datasource
# binding for ACS workers. ACS historically hard-coded `claude -p`; these
# helpers let a worker/manager run on a local Ollama model (zero egress,
# zero token cost) and let workers reason over LIVE data fetched from a
# registered DSI v1 connection.
# ---------------------------------------------------------------------------

# Ollama model aliases — kept local so acs_runtime has no hard import of
# the L22 HermesEngine (mirrors the helper_model pattern).
_OLLAMA_ALIASES = {
    "hermes": "qwen3:8b",
    "hermes-fast": "qwen3:1.7b",
    "hermes-balanced": "qwen3:8b",
    "hermes-capable": "qwen3:8b",
    "hermes-large": "qwen3:8b",
}


def _resolve_worker_engine(model: str, tenant_id: str | None = None) -> tuple[str, str]:
    """Map a worker/manager model string to (engine_id, resolved_model).

    engine_id ∈ {"hermes", "claude_code"}. Anything that names a local
    Ollama model (a ``hermes*`` alias, an explicit ``family:tag`` like
    ``qwen3:8b``, or a known local family) routes to Hermes; everything
    else stays Claude Code.

    Resolution order:
    1. CORVIN_ACS_WORKER_ENGINE env var (per-spawn override from adapter)
    2. tenant.corvin.yaml::spec.default_worker_engine (console-configurable global)
    3. Model-name heuristics (hermes alias / local family / cloud prefix)
    """
    forced = (os.environ.get("CORVIN_ACS_WORKER_ENGINE") or "").strip().lower()
    if not forced:
        # Step 2: read the console-configurable global from tenant YAML.
        # ADR-0007: tenant_id MUST come from the caller (RunContext.tenant_id),
        # NOT from CORVIN_TENANT_ID env. This resolver runs IN-PROCESS in the
        # console (multi-tenant uvicorn), where the console never sets a per-request
        # env tenant — reading env here resolved every tenant to `_default` and
        # silently dropped their console-configured default_worker_engine (and
        # could bleed config across tenants). The env-var fallback only applies
        # when no tenant_id is supplied (legacy CLI callers). (security review 2026-06-27)
        _tid = tenant_id if tenant_id is not None else os.environ.get("CORVIN_TENANT_ID", "_default")
        try:
            import yaml  # noqa: PLC0415
            _cfg = _corvin_home() / "tenants" / _tid / "global" / "tenant.corvin.yaml"
            if _cfg.is_file():
                _raw = yaml.safe_load(_cfg.read_text("utf-8")) or {}
                _val = ((_raw.get("spec") or {}).get("default_worker_engine") or "").strip().lower()
                if _val and _val not in ("hermes", "claude_code", "claude"):
                    # Invalid console/YAML value — log and ignore (fall through to
                    # heuristics) instead of silently dropping it (acs_runtime #2).
                    log.warning(
                        "_resolve_worker_engine: ignoring invalid "
                        "spec.default_worker_engine=%r (tenant=%s) — expected "
                        "hermes|claude_code", _val, _tid,
                    )
                    _val = ""
                if _val:
                    forced = _val
        except Exception as _exc:  # noqa: BLE001
            log.warning("_resolve_worker_engine: tenant.corvin.yaml read failed "
                        "(tenant=%s): %s", _tid, _exc)
    m = (model or "").strip()
    low = m.lower()
    if forced == "hermes" or low in _OLLAMA_ALIASES:
        return "hermes", _OLLAMA_ALIASES.get(low, _OLLAMA_ALIASES["hermes"])
    if forced == "claude_code" or forced == "claude":
        return "claude_code", m
    # Cloud model ids (Bedrock/Vertex style) can contain ':' — never route
    # those to Ollama. Exclude known cloud prefixes before the colon check.
    if low.startswith(("claude", "anthropic", "us.anthropic", "eu.anthropic",
                       "gpt", "o1", "o3", "gemini", "sonnet", "opus", "haiku",
                       "fable")):
        return "claude_code", m
    local_family = low.split(":")[0]
    if ":" in low or local_family in ("qwen3", "qwen", "llama", "llama3",
                                      "mistral", "gemma", "phi", "minimax",
                                      "deepseek", "phi3", "mixtral"):
        return "hermes", m
    return "claude_code", m


def _ollama_base_url() -> str:
    return (os.environ.get("CORVIN_HERMES_BASE_URL")
            or os.environ.get("OLLAMA_BASE_URL")
            or "http://localhost:11434").rstrip("/")


def _ollama_chat(prompt: str, system: str, model: str, timeout: int) -> str:
    """Single non-streaming Ollama /api/chat call → assistant text.

    Self-contained (urllib, stdlib) so ACS does not couple to the L22
    streaming engine. Raises on transport/HTTP error so the caller's
    engine-error path records it.
    """
    import urllib.request
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    req = urllib.request.Request(
        f"{_ollama_base_url()}/api/chat",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    msg = (payload.get("message") or {}).get("content") or ""
    # qwen3 emits <think>…</think> reasoning — strip it for clean output.
    return re.sub(r"<think>.*?</think>", "", str(msg), flags=re.S).strip()


_DS_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def _resolve_acs_datasources(tenant_id: str, names: list[str],
                             run_id: str | None = None) -> tuple[str, dict]:
    """Resolve DSI v1 connection(s) → (live data-context text, conn env).

    For each declared connection: validate the name (no traversal), read the
    manifest, build standard driver env (PG*/MYSQL*), and — for postgresql —
    run a small LIVE query so workers reason over REAL rows regardless of
    engine (a Hermes worker has no Bash; the snapshot is its data path). A
    ClaudeCode worker additionally gets the env to run its own live queries.
    Fail-closed on a declared-but-unresolvable name.

    ADR-0127 review (G3/G4): emits a metadata-only ``acs.datasource_snapshot``
    audit event per connection (NEVER the rows), and REFUSES to take a live
    plaintext snapshot of CONFIDENTIAL/SECRET data unless an at-rest WDAT key
    is configured — otherwise real sensitive rows would land plaintext in the
    Tier-2 worker-trace store.
    """
    if not names:
        return "", {}
    home = _corvin_home()
    conn_dir = (home / "tenants" / tenant_id / "datasource_connections").resolve()
    wdat_key_set = bool(os.environ.get("CORVIN_WDAT_KEY"))
    blocks: list[str] = []
    env: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str) or not _DS_NAME_RE.match(name) \
                or ".." in name or "/" in name or "\\" in name:
            raise ValueError(f"invalid datasource name: {name!r}")
        path = (conn_dir / f"{name}.json").resolve()
        if conn_dir not in path.parents:
            raise ValueError(f"datasource path escaped connections dir: {name!r}")
        raw = json.loads(path.read_text())
        adapter = str(raw.get("adapter", "")).lower()
        cfg = raw.get("config") or {}
        classification = str(raw.get("data_classification", "INTERNAL")).upper()
        # G4 — do not snapshot sensitive rows into a plaintext trace store.
        sensitive = classification in ("CONFIDENTIAL", "SECRET")
        snapshot = ""
        snapshot_taken = False
        if adapter in ("postgresql", "postgres"):
            host = str(cfg.get("host", "localhost"))
            port = int(cfg.get("port", 5432))
            db = str(cfg.get("dbname") or cfg.get("database") or "")
            user = str(cfg.get("user", ""))
            env.update({"PGHOST": host, "PGPORT": str(port),
                        "PGDATABASE": db, "PGUSER": user})
            if sensitive and not wdat_key_set:
                snapshot = (f"(live snapshot withheld: {classification} data and "
                            "CORVIN_WDAT_KEY not set — rows are not embedded in the "
                            "worker trace; the worker engine receives the connection "
                            "env to query under the L34 gate instead)")
            else:
                snapshot = _pg_live_snapshot(host, port, db, user, name)
                snapshot_taken = snapshot.startswith("LIVE DATA")
        block = (f"datasource '{name}' (adapter={adapter or 'unknown'}, "
                 f"classification={classification})")
        if snapshot:
            block += "\n" + snapshot
        blocks.append(block)
        # G3 — metadata-only audit: NEVER the rows, only counts/labels.
        _write_audit(tenant_id, "acs.datasource_snapshot", {
            "run_id": run_id,
            "datasource": name,
            "adapter": adapter or "unknown",
            "classification": classification,
            "snapshot_taken": snapshot_taken,
            "snapshot_bytes": len(snapshot) if snapshot_taken else 0,
            "withheld_sensitive": bool(sensitive and not wdat_key_set),
        })
    return "\n\n".join(blocks), env


def _pg_live_snapshot(host: str, port: int, db: str, user: str, name: str) -> str:
    """Run a few read-only probes against the live PG and return a compact
    text snapshot. Best-effort: returns a note if psycopg2 / the DB is
    unavailable (the worker still gets the connection metadata)."""
    try:
        import psycopg2  # type: ignore
    except Exception:  # noqa: BLE001
        return "(live snapshot unavailable: psycopg2 not installed in this interpreter)"
    conn = None
    try:
        conn = psycopg2.connect(host=host, port=port, dbname=db, user=user,
                                password=os.environ.get("PGPASSWORD") or None,
                                connect_timeout=5)
        cur = conn.cursor()
        lines: list[str] = []
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public' ORDER BY table_name LIMIT 12""")
        tables = [r[0] for r in cur.fetchall()]
        lines.append("tables: " + ", ".join(tables))
        if "spotify_charts" in tables:
            cur.execute("SELECT COUNT(*) FROM spotify_charts")
            lines.append(f"spotify_charts rows: {cur.fetchone()[0]}")
            cur.execute("""SELECT t.track_name, t.artist, MAX(c.streams_p50) AS peak
                           FROM spotify_charts c JOIN tracks t ON t.track_id=c.track_id
                           GROUP BY t.track_name, t.artist ORDER BY peak DESC LIMIT 5""")
            lines.append("top 5 tracks by peak streams_p50:")
            for track_name, artist, peak in cur.fetchall():
                lines.append(f"  - {artist} — {track_name}: {int(peak):,}")
        return "LIVE DATA (read-only snapshot):\n" + "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"(live snapshot failed: {type(exc).__name__}: {str(exc)[:120]})"
    finally:
        # Always release the connection — even when a query raises mid-way
        # (avoids leaking a live PG socket per failed snapshot).
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _assert_engine_licensed(engine_id: str) -> None:
    """ADR-0150 LIC-ENG-USE-02: fail-CLOSED license engines_allowed gate at the ACS
    spawn chokepoint (parallel to the adapter OS-turn + delegate paths). The ACS
    worker/manager selection previously skipped engines_allowed, so a SesT
    restricting engines could still spawn a forbidden engine via an ACS workflow.
    Latent today (all tiers engines_allowed=None → assert_limit is a no-op) but
    closed before engines become a paid axis. Raises RuntimeError on deny — the
    caller turns it into a failed result, same as the existing claude-not-found
    RuntimeError. Dual-env test bypass mirrors adapter.py / delegation.py.
    """
    if (os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
            and os.environ.get("CORVIN_INTEGRATION_TEST") == "1"):
        return
    _le: "type | None" = None
    try:
        _op = str(Path(__file__).resolve().parents[2])  # operator/
        if _op not in sys.path:
            sys.path.insert(0, _op)
        from license.validator import assert_limit as _al  # type: ignore
        from license.limits import LicenseLimitError as _le  # type: ignore
        _al("engines_allowed", engine_id)
    except Exception as _exc:  # noqa: BLE001 — fail-CLOSED on any license error
        if _le is not None and isinstance(_exc, _le):
            raise RuntimeError(f"engine-not-allowed-by-license: {engine_id}") from _exc
        raise RuntimeError(f"license-gate-error (fail-closed): {type(_exc).__name__}") from _exc


def _apply_provider_redirect(env: dict, tenant_id: str) -> None:
    """ADR-0181 M3 (review finding #6) — mirror the OS-turn provider redirect for
    claude_code WORKER spawns. When a non-anthropic provider is assigned to
    claude_code for this tenant, point the worker CLI at the provider/proxy via
    ANTHROPIC_BASE_URL + the vault-injected credential, exactly like the OS turn
    in adapter._build_spawn_env.

    Without this the worker egressed to the DEFAULT anthropic host while
    spawn_gates.check_l35 validated the PROVIDER host — enforcement and actual
    egress disagreed. Call AFTER stripping the real Anthropic creds; in the
    default (no-provider) case this is a no-op. Best-effort, never fatal."""
    try:
        from engine_models import (  # type: ignore
            get_tenant_engine_provider, load_providers)
        prov = get_tenant_engine_provider(tenant_id, "claude_code")
        if not prov or prov == "anthropic":
            return
        ps = load_providers().get(prov)
        base = (ps.proxy_base_url or ps.base_url) if ps else ""
        if not base:
            return
        key = os.environ.get(ps.credential_env, "") if ps.credential_env else ""
        env["ANTHROPIC_BASE_URL"] = base
        env["ANTHROPIC_API_KEY"] = key or "provider"
        env["ANTHROPIC_AUTH_TOKEN"] = key or "provider"
        env["CORVIN_CC_PROVIDER"] = prov
    except Exception:  # noqa: BLE001 — routing is best-effort, never fatal
        return


def _call_manager_sync(prompt: str, model: str, tenant_id: str = "_default") -> tuple[str, int]:
    """Call the manager engine for a decision. Returns (stdout, tokens_estimate).

    Routes to Ollama when ``model`` resolves to Hermes (ADR-0127), else the
    claude CLI. The manager must emit a single JSON decision — small local
    models may be less reliable here than Claude."""
    engine_id, resolved = _resolve_worker_engine(model, tenant_id)
    _assert_engine_licensed(engine_id)
    if engine_id == "hermes":
        out = _ollama_chat(prompt, _MANAGER_SYSTEM, resolved, timeout=_MANAGER_TIMEOUT)
        return out, max(len(prompt.split()) + len(out.split()), 100)
    binary = _claude_binary()
    if not (shutil.which(binary) or os.path.isfile(binary)):
        raise RuntimeError(
            f"claude CLI not found ({binary!r}); "
            "set CORVIN_CLAUDE_BIN to the absolute path of the claude binary"
        )
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    _apply_provider_redirect(env, tenant_id)  # ADR-0181 M3 #6 — consistent egress
    result = subprocess.run(
        [
            binary, "-p", prompt,
            "--append-system-prompt", _MANAGER_SYSTEM,
            "--model", model,
            "--disallowedTools", "*",
            "--max-turns", "1",
            "--output-format", "json",  # extract text from envelope so parse never sees CLI wrapper
        ],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=_MANAGER_TIMEOUT, check=False,
    )
    raw = result.stdout.strip()
    output = raw  # fallback: pass through if not a JSON envelope
    if raw.startswith("{"):
        try:
            resp = json.loads(raw)
            extracted = resp.get("result") or resp.get("text") or ""
            if extracted:
                output = str(extracted)
        except (json.JSONDecodeError, TypeError):
            pass
    tokens_est = max(len(prompt.split()) + len(output.split()), 100)
    return output, tokens_est


# ---------------------------------------------------------------------------
# Worker spawn (M2 / M3 / M4)
# ---------------------------------------------------------------------------

def _build_worker_prompt(subtask: dict, ctx: RunContext) -> str:
    lines = [
        f"SUBTASK ID: {subtask.get('id', '?')}",
        f"INSTRUCTIONS: {subtask.get('instructions', '')}",
        "",
        "EXPECTED OUTPUT SCHEMA:",
        json.dumps(subtask.get("expected_output", {}), indent=2),
        "",
        f"SUCCESS CRITERIA: {subtask.get('success_criteria', 'complete the task')}",
        "",
    ]
    if ctx.state:
        state_json = json.dumps(ctx.state, default=str)
        lines += ["CONTEXT STATE:", _truncate(state_json, 3000), ""]
    lines += ["Return your Worker Output as JSON now."]
    return "\n".join(lines)


def _build_worker_system(subtask: dict, ctx: RunContext, depth: int, can_delegate: bool) -> str:
    system = _WORKER_SYSTEM_BASE
    if can_delegate and depth < ctx.budget.max_depth:
        system += _DELEGATE_WORKER_SYSTEM_SUFFIX
    # M3: inject dynamic tool list if any exist in this run
    if ctx.dynamic_tools:
        tool_list = "\n".join(
            f"  - {tid}" for tid in sorted(ctx.dynamic_tools)
        )
        system += (
            f"\n\nDYNAMIC TOOLS AVAILABLE (created this run):\n{tool_list}\n"
            "Use these tools if they match your task; they were forged mid-loop.\n"
        )
    return system


def _worker_mcp_config(ctx: RunContext) -> list[dict]:
    """M3: build MCP server config for workers — includes forge + skill_forge."""
    servers: list[dict] = []
    # Add forge MCP if available
    forge_mcp = Path(__file__).resolve().parents[2] / "forge" / "mcp_server.py"
    if forge_mcp.exists():
        servers.append({
            "command": sys.executable,
            "args": [str(forge_mcp)],
            "env": {"CORVIN_TENANT_ID": ctx.tenant_id},
        })
    return servers


class _WorkerProcessHolder:
    """Mutable holder so the async caller can kill the subprocess spawned
    inside _call_worker_sync's executor thread if the awaiting Task is
    cancelled. asyncio.to_thread() does NOT itself interrupt a blocking
    subprocess.run()/Popen.communicate() call running in the thread pool —
    cancelling the awaiting coroutine only stops waiting for it, the thread
    (and the `claude -p` child it spawned) keeps running to completion
    regardless (adversarial review finding: a cancelled ACS run could leave
    a live worker subprocess consuming CPU/tokens/API cost for up to
    _WORKER_TIMEOUT more seconds after the run already returned a result)."""

    def __init__(self) -> None:
        self.popen: "subprocess.Popen | None" = None
        self.lock = threading.Lock()

    def kill(self) -> None:
        with self.lock:
            proc = self.popen
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001 — best-effort, never raise from cleanup
                pass


def _call_worker_sync(
    prompt: str,
    system: str,
    model: str,
    budget: dict,
    extra_env: "dict[str, str] | None" = None,
    tenant_id: str = "_default",
    proc_holder: "_WorkerProcessHolder | None" = None,
) -> tuple[str, int, dict]:
    """Call claude -p for worker execution.

    Returns (output_text, tokens_used, attestation).
    M3: uses --output-format json to extract actual model_id from API response;
    falls back to configured model with attested=False on parse failure.

    ADR-0127: routes to a local Ollama model when ``model`` resolves to
    Hermes (zero token, locality=local) — otherwise the claude CLI.
    """
    engine_id, resolved = _resolve_worker_engine(model, tenant_id)
    _assert_engine_licensed(engine_id)  # ADR-0150 LIC-ENG-USE-02 (fail-closed)
    if engine_id == "hermes":
        timeout = min(int(budget.get("timeout_seconds", _DEFAULT_BUDGET_TIMEOUT)), 3600)
        out = _ollama_chat(prompt, system, resolved, timeout=timeout)
        attestation = {"engine_id": "hermes", "model_id": resolved,
                       "attested": True, "locality": "local"}
        return out, max(len(prompt.split()) + len(out.split()), 50), attestation

    binary = _claude_binary()
    if not (shutil.which(binary) or os.path.isfile(binary)):
        raise RuntimeError(
            f"claude CLI not found ({binary!r}); "
            "set CORVIN_CLAUDE_BIN to the absolute path of the claude binary"
        )
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    # ADR-0109 M6: propagate ACS worker context for engine-trace hooks
    if extra_env is not None:
        env.update(extra_env)
    _apply_provider_redirect(env, tenant_id)  # ADR-0181 M3 #6 — consistent egress
    timeout = min(int(budget.get("timeout_seconds", _DEFAULT_BUDGET_TIMEOUT)), _WORKER_TIMEOUT)
    # max-turns: 20 gives workers enough headroom for multi-file explore/implement
    # tasks. 5 was too tight — workers hit the limit mid-tool-use and returned
    # error_max_turns, which _parse_worker_output silently treated as
    # status="partial", confidence=0.0, causing every delegated web-console
    # turn to fail with "Delegation fehlgeschlagen: unknown error".
    worker_max_turns = str(budget.get("max_worker_turns", 20))
    # subprocess.Popen (not .run()) so proc_holder can expose a live handle:
    # if the awaiting asyncio Task gets cancelled while this call is blocked
    # in the executor thread, the caller kills THIS exact process via
    # proc_holder.kill() instead of leaving it running to completion.
    proc = subprocess.Popen(
        [
            binary, "-p", prompt,
            "--append-system-prompt", system,
            "--model", model,
            "--max-turns", worker_max_turns,
            "--output-format", "json",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        stdin=subprocess.DEVNULL,
    )
    if proc_holder is not None:
        with proc_holder.lock:
            proc_holder.popen = proc
    try:
        stdout, _stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Mirror subprocess.run()'s own timeout handling: kill, drain, re-raise.
        proc.kill()
        proc.communicate()
        raise
    raw = stdout.strip()
    attestation: dict = {
        "engine_id": "claude_code",
        "model_id": model,   # configured model as fallback
        "attested": False,   # True only when API response confirms model
        "locality": "eu_cloud",
    }
    output_text = raw
    tokens_used = max(len(prompt.split()) + len(raw.split()), 50)

    if raw.startswith("{"):
        try:
            resp = json.loads(raw)
            # Claude Code error envelope (is_error=True): surface a clear error
            # string instead of passing the envelope as output_text so that
            # _parse_worker_output can build a proper WorkerResult(status="failed")
            # rather than silently defaulting to status="partial", confidence=0.0.
            # Covers: error_max_turns, error_during_execution, tool_use_error, etc.
            if resp.get("is_error") or resp.get("subtype", "").startswith("error"):
                subtype = resp.get("subtype", "error")
                errs = resp.get("errors") or []
                err_msg = "; ".join(str(e) for e in errs) if errs else subtype
                output_text = json.dumps({
                    "status": "failed",
                    "confidence": 0.0,
                    "error": f"claude_code {subtype}: {err_msg}",
                    "result": {},
                })
                # Still extract token / model info for cost accounting.
            else:
                # Result text is under "result" key in --output-format json
                result_text = resp.get("result") or resp.get("text") or ""
                if result_text:
                    output_text = str(result_text)
            # Actual model confirmed by API response
            actual_model = (
                resp.get("model")
                or (resp.get("usage") or {}).get("model")
            )
            if actual_model:
                attestation["model_id"] = str(actual_model)
                attestation["attested"] = True
            # Real token counts
            usage = resp.get("usage") or {}
            tok_in = int(usage.get("input_tokens") or 0)
            tok_out = int(usage.get("output_tokens") or 0)
            if tok_in + tok_out > 0:
                tokens_used = tok_in + tok_out
            # Session ID for investigation path (content store only)
            if resp.get("session_id"):
                attestation["session_id"] = str(resp["session_id"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass  # stay with fallback attestation and raw output as text

    return output_text, tokens_used, attestation


def _is_html_error_page(text: str) -> bool:
    """Return True when text is a raw HTTP error page (Cloudflare 50x, nginx, etc.)."""
    t = text.lstrip()
    return t.startswith(("<!DOCTYPE", "<!doctype", "<html", "<HTML"))


def _parse_worker_output(text: str, worker_id: str) -> WorkerResult:
    """Parse worker JSON output into a WorkerResult."""
    text = text.strip()

    # Raw HTML error pages (Cloudflare 502, nginx 50x, …) must never propagate
    # as worker output — they indicate an upstream HTTP failure.  Surface a
    # clean, human-readable error instead of kilobytes of markup.
    if _is_html_error_page(text):
        import re as _re
        _title = _re.search(r"<title[^>]*>([^<]{1,120})</title>", text, _re.IGNORECASE)
        _label = _title.group(1).strip() if _title else "HTTP error page"
        return WorkerResult(
            worker_id=worker_id,
            status="failed",
            result={},
            error=f"upstream returned {_label!r} instead of a valid response",
        )

    data: dict = {}

    # Try direct parse
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

    if not data:
        # Find JSON in output
        import re
        for m in reversed(list(
            re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        )):
            try:
                data = json.loads(m.group(0))
                break
            except json.JSONDecodeError:
                continue

    if not data:
        return WorkerResult(
            worker_id=worker_id,
            status="failed",
            result={},
            error=f"no valid JSON in worker output: {text[:200]}",
        )

    return WorkerResult(
        worker_id=worker_id,
        status=str(data.get("status", "partial")),
        result=data.get("result") or {},
        confidence=float(data.get("confidence", 0.0)),
        error=str(data.get("error") or ""),
        artifacts=list(data.get("artifacts") or []),
        usage=dict(data.get("usage") or {}),
        metadata=dict(data.get("metadata") or {}),
    )


# ---------------------------------------------------------------------------
# L34/L35 classification helper (ADR-0158 M2)
# ---------------------------------------------------------------------------

def _workflow_classification(workflow_spec: dict | None) -> str:
    """Extract the data_classification string from a workflow spec (default: 'internal')."""
    if not workflow_spec:
        return "internal"
    return (
        workflow_spec.get("orchestration", {})
        .get("delegation_loop", {})
        .get("data_classification", "internal")
    ).lower()


def _workflow_goal_text(workflow_spec: dict | None, inputs: dict | None) -> str:
    """Concatenate the user-controlled, free-text instruction fields of a workflow.

    L44 (ADR-0143 acceptable-use) classifies the *intent* the user is steering the
    autonomous run toward. For an AWP workflow that intent lives in the goal/
    description fields and the initial-state seed (which the worker/manager turns
    are built from in ``_build_manager_prompt`` / ``_build_worker_prompt``), plus
    any caller-supplied ``inputs``. We gather them all so a hostile instruction
    cannot dodge the gate by hiding in ``state.initial`` instead of
    ``workflow.description``.

    Returns a single string (possibly empty — an empty/whitespace result makes
    ``check_l44`` a defensive no-op, matching its contract for status pings)."""
    parts: list[str] = []
    spec = workflow_spec or {}
    wf = spec.get("workflow", {}) if isinstance(spec.get("workflow"), dict) else {}
    for key in ("name", "description", "goal", "objective"):
        val = wf.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    # delegation_loop.goal / objective (some AWP specs carry the goal there)
    dl = (
        spec.get("orchestration", {}).get("delegation_loop", {})
        if isinstance(spec.get("orchestration"), dict)
        else {}
    )
    if isinstance(dl, dict):
        for key in ("goal", "objective", "instruction"):
            val = dl.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    # state.initial seed values (the manager prompt embeds these verbatim)
    initial = spec.get("state", {}).get("initial") if isinstance(spec.get("state"), dict) else None
    if isinstance(initial, dict):
        for val in initial.values():
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    # caller inputs (CLI --input / console body.inputs) merge into state.initial
    if isinstance(inputs, dict):
        for val in inputs.values():
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core manager loop (M2)
# ---------------------------------------------------------------------------

async def _dispatch_workers(
    subtasks: list[dict],
    ctx: RunContext,
    depth: int,
    manager_model: str,
    worker_model: str,
    parent_worker_id: str | None = None,   # ADR-0108: set when spawning inside a sub-manager
    spawn_nonce: str | None = None,        # ADR-0109: causality link from manager_decided event
) -> list[WorkerResult]:
    """Dispatch subtasks in parallel, up to max_workers_per_iteration."""
    capped = subtasks[: ctx.budget.max_workers_per_iteration]
    results: list[WorkerResult] = []
    semaphore = asyncio.Semaphore(ctx.budget.max_workers_per_iteration)

    async def _run_one(st: dict) -> WorkerResult:
        async with semaphore:
            # Sanitize wid: strip path-separator characters from LLM-controlled input
            # to prevent directory traversal (e.g. id: "../../evil").
            _raw_id = str(st.get("id") or secrets.token_hex(4))
            wid = re.sub(r"[^A-Za-z0-9_-]", "_", _raw_id)[:64] or secrets.token_hex(4)
            budget_alloc = st.get("budget_allocation") or {}

            # ADR-0127 — resolve the canonical engine id (hermes|claude_code)
            # for this worker; pass THAT to the L34 gate (not the raw model
            # string) and reflect it in audit + attestation.
            _engine_id, _ = _resolve_worker_engine(worker_model, ctx.tenant_id)
            _locality = "local" if _engine_id == "hermes" else "eu_cloud"

            # L34/L35 gates (ADR-0158 M2: canonical spawn_gates SSOT)
            _cls = _workflow_classification(ctx.workflow_spec)
            try:
                from spawn_gates import check_l34 as _sg_l34, check_l35 as _sg_l35  # type: ignore
                _l34_result = _sg_l34(_engine_id, ctx.tenant_id, classification=_cls)
                if _l34_result is not None:
                    _write_audit(ctx.tenant_id, "acs.worker_l34_blocked", {
                        "run_id": ctx.run_id, "worker_id": wid, "engine": _engine_id,
                    })
                    return WorkerResult(
                        worker_id=wid, status="failed", result={},
                        error="L34 data classification gate blocked this engine",
                    )
                _l35_result = _sg_l35(_engine_id, ctx.tenant_id)
                if _l35_result is not None:
                    _write_audit(ctx.tenant_id, "acs.worker_l35_blocked", {
                        "worker_id": wid, "engine": _engine_id,
                    })
                    return WorkerResult(
                        worker_id=wid, status="failed", result={},
                        error="L35 egress gate blocked this engine",
                    )
            except Exception as _gate_exc:  # noqa: BLE001
                # H1: L34/L35 import failure → fail-closed (CLAUDE.md load-bearing invariant).
                # Spawning without gates would bypass GDPR Art. 32 data-flow controls.
                _write_audit(ctx.tenant_id, "acs.worker_gates_unavailable", {
                    "run_id": ctx.run_id, "worker_id": wid, "engine": _engine_id,
                    "reason": type(_gate_exc).__name__,
                })
                return WorkerResult(
                    worker_id=wid, status="failed", result={},
                    error=f"L34/L35 spawn_gates unavailable ({type(_gate_exc).__name__}); "
                          "refusing to spawn without security gates",
                )

            # M4: workers with can_delegate at max_depth get it stripped
            can_delegate = bool(st.get("can_delegate")) and depth < ctx.budget.max_depth

            prompt = _build_worker_prompt(st, ctx)
            system = _build_worker_system(st, ctx, depth, can_delegate)

            # ADR-0109 M1+M3: pre-spawn event with instruction hash — bookend before engine call
            _instruction_hash = _sha256(prompt)
            _write_audit(ctx.tenant_id, "acs.worker_spawned", {
                "run_id": ctx.run_id,
                "worker_id": wid,
                "iteration": ctx.iteration,
                "depth": depth,
                "engine_id": _engine_id,
                "model_id": worker_model,        # configured model; traced event gets API-confirmed
                "instruction_hash": _instruction_hash,
                "spawn_nonce": spawn_nonce,
                "parent_worker_id": parent_worker_id,
                "can_delegate": can_delegate,
            })
            _spawn_start = time.monotonic()

            # Engine-level audit event — emitted immediately before the WorkerEngine
            # subprocess is launched so the audit chain records the exact start time.
            # Fields: run_id, worker_id, engine_id, model_id, locality — metadata only.
            _write_audit(ctx.tenant_id, "acs.engine_started", {
                "run_id":    ctx.run_id,
                "worker_id": wid,
                "engine_id": _engine_id,
                "model_id":  worker_model,
                "locality":  _locality,
            })
            # ADR-0171 — universal engine-span (role=worker), dual-emitted on the
            # same chain so the console can build the Worker graph engine-agnostically
            # (any engine_id), not only from acs.* events.
            _worker_span_id = f"spn-{ctx.run_id}-{wid}"
            if _espan is not None:
                _write_audit(ctx.tenant_id, _espan.ENGINE_SPAN_START,
                             _espan.start_details(
                                 span_id=_worker_span_id, role="worker",
                                 engine_id=_engine_id, model_id=worker_model,
                                 parent_span_id=f"spn-{ctx.run_id}-mgr",
                                 run_id=ctx.run_id))

            try:
                # ADR-0109 M6: worker context for path_gate WDAT events.
                # ADR-0127: datasource connection env so a ClaudeCode worker
                # (which has Bash) can run live queries; harmless for Hermes.
                _worker_env = {
                    "CORVIN_ACS_WORKER_ID": wid,
                    "CORVIN_ACS_RUN_ID": ctx.run_id,
                    "CORVIN_ACS_TENANT_ID": ctx.tenant_id,
                }
                if _engine_id == "claude_code" and ctx.datasource_env:
                    _worker_env.update(ctx.datasource_env)
                _proc_holder = _WorkerProcessHolder()
                try:
                    text, tok, attestation = await asyncio.to_thread(
                        _call_worker_sync, prompt, system, worker_model, budget_alloc,
                        _worker_env, ctx.tenant_id, _proc_holder,
                    )
                except asyncio.CancelledError:
                    # asyncio.to_thread() does NOT interrupt a blocking call
                    # already running in the executor thread — cancelling this
                    # await only stops WAITING for it; the claude -p subprocess
                    # spawned inside _call_worker_sync kept running to
                    # completion regardless (adversarial review finding: a
                    # cancelled ACS run could leave a live worker consuming
                    # CPU/tokens/API cost for up to _WORKER_TIMEOUT more
                    # seconds). Kill the actual process before re-raising.
                    _proc_holder.kill()
                    raise
            except asyncio.CancelledError:
                # ADR-0171 — cancellation (budget abort, run timeout, parent
                # cancel, client disconnect) does NOT reach `except Exception`
                # (CancelledError is BaseException since Py3.8). Without this the
                # worker engine.span.start above would be orphaned (no paired end).
                # Emit the end (status=error) + a worker_traced bookend, then
                # re-raise so cancellation still propagates.
                if _espan is not None:
                    _write_audit(ctx.tenant_id, _espan.ENGINE_SPAN_END,
                                 _espan.end_details(
                                     span_id=_worker_span_id, role="worker",
                                     engine_id=_engine_id, model_id=worker_model,
                                     parent_span_id=f"spn-{ctx.run_id}-mgr",
                                     run_id=ctx.run_id, status="error",
                                     duration_ms=int((time.monotonic() - _spawn_start) * 1000)))
                _write_audit(ctx.tenant_id, "acs.worker_traced", {
                    "worker_id": wid, "status": "failed", "confidence": 0.0,
                    "output_hash": _sha256("cancelled"),
                    "duration_ms": int((time.monotonic() - _spawn_start) * 1000),
                    "tokens_used": 0,
                    "spawn_nonce": spawn_nonce,
                    "engine_attestation": {
                        "engine_id": _engine_id, "model_id": worker_model,
                        "locality": _locality,
                    },
                })
                raise
            except Exception as exc:  # noqa: BLE001 — catch OSError + other failures
                _write_audit(ctx.tenant_id, "acs.engine_error", {
                    "run_id": ctx.run_id, "worker_id": wid,
                    "engine_id": _engine_id, "model_id": worker_model,
                    "duration_ms": int((time.monotonic() - _spawn_start) * 1000),
                })
                # ADR-0171 — engine-span end (error) so a failed engine is STILL
                # a complete, paired span in the audit (no silent un-audited run).
                if _espan is not None:
                    _write_audit(ctx.tenant_id, _espan.ENGINE_SPAN_END,
                                 _espan.end_details(
                                     span_id=_worker_span_id, role="worker",
                                     engine_id=_engine_id, model_id=worker_model,
                                     parent_span_id=f"spn-{ctx.run_id}-mgr",
                                     run_id=ctx.run_id, status="error",
                                     duration_ms=int((time.monotonic() - _spawn_start) * 1000)))
                _write_audit(ctx.tenant_id, "acs.worker_error", {
                    "run_id": ctx.run_id, "worker_id": wid, "reason": str(exc)[:200],
                })
                # ADR-0109 M1+M3: error-path bookend — every worker_spawned gets a worker_traced
                _write_audit(ctx.tenant_id, "acs.worker_traced", {
                    "worker_id": wid, "status": "failed", "confidence": 0.0,
                    "output_hash": _sha256(str(exc)),
                    "duration_ms": int((time.monotonic() - _spawn_start) * 1000),
                    "tokens_used": 0,
                    "spawn_nonce": spawn_nonce,
                    "engine_attestation": {
                        "engine_id": _engine_id,
                        "model_id": worker_model,
                        "locality": _locality,
                    },
                })
                return WorkerResult(
                    worker_id=wid, status="failed", result={}, error=str(exc)
                )

            ctx.budget.tokens_used += tok

            # Engine-level completion — pairs with acs.engine_started above.
            # Records actual model/locality from API attestation + measured timing.
            _write_audit(ctx.tenant_id, "acs.engine_completed", {
                "run_id":      ctx.run_id,
                "worker_id":   wid,
                "engine_id":   attestation.get("engine_id", "claude_code"),
                "model_id":    attestation.get("model_id", worker_model),
                "locality":    attestation.get("locality", "eu_cloud"),
                "duration_ms": int((time.monotonic() - _spawn_start) * 1000),
                "tokens_used": tok,
                "exit_code":   0,
            })
            # ADR-0172 M1 — post-run trace extraction (zero hot-path overhead:
            # runs AFTER the worker subprocess has already returned).
            _trace_count = 0
            if _wtrace is not None:
                try:
                    _att_engine = attestation.get("engine_id", _engine_id)
                    if _att_engine == "hermes":
                        _trace_count = _wtrace.extract_hermes_trace(
                            text, wid, ctx.run_id, _worker_span_id, ctx.run_dir)
                    else:
                        _trace_count = _wtrace.extract_claudecode_trace(
                            _audit_path(ctx.tenant_id),
                            ctx.run_id, wid, _worker_span_id, ctx.run_dir)
                except Exception:  # noqa: BLE001 — trace is additive, never block
                    pass
            # ADR-0171 — engine-span end (ok), API-attested engine/model.
            if _espan is not None:
                _write_audit(ctx.tenant_id, _espan.ENGINE_SPAN_END,
                             _espan.end_details(
                                 span_id=_worker_span_id, role="worker",
                                 engine_id=attestation.get("engine_id", _engine_id),
                                 model_id=attestation.get("model_id", worker_model),
                                 parent_span_id=f"spn-{ctx.run_id}-mgr",
                                 run_id=ctx.run_id, status="ok",
                                 duration_ms=int((time.monotonic() - _spawn_start) * 1000),
                                 tokens_used=tok,
                                 tool_call_count=_trace_count,
                                 trace_available=_trace_count > 0))

            wr = _parse_worker_output(text, wid)

            # M4: if worker returned a DELEGATE decision (sub-manager), recurse
            if can_delegate and wr.result.get("decision") == "DELEGATE":
                sub_subtasks = wr.result.get("subtasks") or []
                if sub_subtasks and depth + 1 <= ctx.budget.max_depth:
                    _write_audit(ctx.tenant_id, "acs.delegation", {
                        "run_id": ctx.run_id, "depth": depth + 1,
                        "parent_worker_id": wid, "subtask_count": len(sub_subtasks),
                    })
                    # ADR-0108 M2: sub-manager gets a directory on disk, not a flat file.
                    # Presence of the directory signals "sub_manager" to the graph reader.
                    sm_dir = ctx.run_dir / "workers" / f"{wid}_iter{ctx.iteration}"
                    sm_dir.mkdir(parents=True, exist_ok=True)
                    # Note: manifest.json is written ONLY after sub-results are aggregated.
                    # Writing a "running" status first would leave orphaned manifests on crash.
                    sub_spawn_nonce = str(uuid.uuid4())  # fresh nonce per sub-manager batch
                    # Budget isolation: each delegated sub-tree gets a fraction of the
                    # parent budget (A4 recursive delegation). Without this, runaway
                    # sub-trees consume the full top-level token/worker budget.
                    import dataclasses as _dc  # noqa: PLC0415
                    _sub_fraction = 1.0 / max(1, len(sub_subtasks))
                    _sub_ctx = _dc.replace(ctx, budget=ctx.budget.fraction(_sub_fraction))
                    sub_results = await _dispatch_workers(
                        sub_subtasks, _sub_ctx, depth + 1, manager_model, worker_model,
                        parent_worker_id=wid,          # ADR-0108: link sub-workers to this node
                        spawn_nonce=sub_spawn_nonce,   # ADR-0109: distinct nonce per delegation depth
                    )
                    # Aggregate sub-results into this worker's result
                    sub_confidence = (
                        sum(r.confidence for r in sub_results) / len(sub_results)
                        if sub_results else 0.0
                    )
                    wr = WorkerResult(
                        worker_id=wid,
                        status="success" if sub_confidence > 0.5 else "partial",
                        result={"sub_results": [r.__dict__ for r in sub_results]},
                        confidence=sub_confidence,
                    )
                    # Update sub-manager manifest with final aggregated status.
                    _write_json_safe(sm_dir / "manifest.json", {
                        "worker_id": wid, "type": "sub_manager",
                        "iteration": ctx.iteration, "depth": depth,
                        "status": wr.status, "confidence": wr.confidence,
                        "sub_workers_spawned": len(sub_results),
                        **({"parent_worker_id": parent_worker_id} if parent_worker_id else {}),
                    })
                    # ADR-0109 M1+M3+M4: sub-manager completion — hash the aggregated
                    # result, not the raw delegation decision, so output_hash matches
                    # the status/confidence which describe the aggregated outcome.
                    _wdat_record_completion(
                        ctx, wid, wr, prompt,
                        json.dumps(wr.result, default=str),  # aggregated, not DELEGATE text
                        tok,
                        int((time.monotonic() - _spawn_start) * 1000),
                        spawn_nonce, attestation,
                    )
                    ctx.budget.workers_used += 1
                    return wr  # sub-manager's directory IS its disk record — no flat file

            # ADR-0108 M2: regular sub-workers go into their parent sub-manager's directory.
            if parent_worker_id:
                sm_dir = ctx.run_dir / "workers" / f"{parent_worker_id}_iter{ctx.iteration}"
                sm_dir.mkdir(parents=True, exist_ok=True)
                worker_file = sm_dir / f"{wid}.json"
            else:
                worker_file = ctx.run_dir / "workers" / f"{wid}_iter{ctx.iteration}.json"

            _write_json_safe(
                worker_file,
                {
                    "worker_id": wid, "status": wr.status,
                    "confidence": wr.confidence, "iteration": ctx.iteration,
                    "depth": depth,
                    **({"parent_worker_id": parent_worker_id} if parent_worker_id else {}),
                },
            )

            # ADR-0109 M1+M3+M4: regular worker completion
            _wdat_record_completion(
                ctx, wid, wr, prompt, text, tok,
                int((time.monotonic() - _spawn_start) * 1000),
                spawn_nonce, attestation,
            )
            ctx.budget.workers_used += 1
            return wr

    tasks = [asyncio.create_task(_run_one(st)) for st in capped]
    done = await asyncio.gather(*tasks, return_exceptions=True)

    for item in done:
        if isinstance(item, WorkerResult):
            results.append(item)
        elif isinstance(item, BaseException):
            results.append(WorkerResult(
                worker_id="unknown", status="failed", result={}, error=str(item)
            ))
    return results


async def _manager_loop(
    ctx: RunContext,
    manager_model: str,
    worker_model: str,
    gate_chain: Any | None,
) -> ACSResult:
    """Core manager decision loop (M2)."""
    start = time.monotonic()
    _prev_rejected_gates: list[str] = []
    _last_iter_workers: list[WorkerResult] = []  # ADR-0105 M4: workers from most recent DELEGATE

    # SECURITY (FND-02): the manager turn round-trips the full run state AND the
    # live DSI datasource snapshot to its engine on EVERY iteration. The worker
    # path is gated (_run_one) but the manager was NOT — a CONFIDENTIAL→local
    # tenant could leak that data via the manager (e.g. claude_code/us_cloud).
    # Gate the manager engine ONCE here (its model is fixed for the run) with
    # the SAME L34/L35 gates; if blocked, the manager never spawns, so the
    # snapshot never egresses. (CLAUDE.md L38/ADR-0042: data_flow.blocked is
    # not advisory.)
    _mgr_engine, _ = _resolve_worker_engine(manager_model, ctx.tenant_id)
    # L34/L35 gates for manager engine (ADR-0158 M2: spawn_gates SSOT).
    # Manager sees full DSI snapshot; gate before any iteration to avoid leakage.
    # Define fallback gate functions to prevent NameError if import fails.
    def _fallback_gate(*args, **kwargs):
        return None  # Fail-open: pass through if gate unavailable

    _sg_l34 = _fallback_gate
    _sg_l35 = _fallback_gate

    _mgr_cls = _workflow_classification(ctx.workflow_spec)
    try:
        from spawn_gates import check_l34 as _sg_l34, check_l35 as _sg_l35  # type: ignore
        _mgr_l34 = _sg_l34(_mgr_engine, ctx.tenant_id, classification=_mgr_cls)
        if _mgr_l34 is not None:
            _write_audit(ctx.tenant_id, "acs.manager_l34_blocked", {
                "run_id": ctx.run_id, "engine": _mgr_engine,
            })
            return ACSResult(
                run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                error="L34 data classification gate blocked the manager engine",
                iterations=ctx.iteration, workers_spawned=ctx.budget.workers_used,
                run_dir=ctx.run_dir, elapsed_s=time.monotonic() - start,
            )
        _mgr_l35 = _sg_l35(_mgr_engine, ctx.tenant_id)
        if _mgr_l35 is not None:
            _write_audit(ctx.tenant_id, "acs.manager_l35_blocked", {
                "run_id": ctx.run_id, "engine": _mgr_engine,
            })
            return ACSResult(
                run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                error="L35 egress gate blocked the manager engine",
                iterations=ctx.iteration, workers_spawned=ctx.budget.workers_used,
                run_dir=ctx.run_dir, elapsed_s=time.monotonic() - start,
            )
    except Exception as _mgr_gate_exc:  # noqa: BLE001
        # H1+H2: fail-closed (not fail-open); use `log` (module-level logger).
        log.debug("[acs] Manager gates import failed (fail-closed): %s", type(_mgr_gate_exc).__name__)
        _write_audit(ctx.tenant_id, "acs.manager_gates_unavailable", {
            "run_id": ctx.run_id, "engine": _mgr_engine,
            "reason": type(_mgr_gate_exc).__name__,
        })
        return ACSResult(
            run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
            error=f"L34/L35 spawn_gates unavailable ({type(_mgr_gate_exc).__name__}); "
                  "refusing to run without security gates",
            iterations=ctx.iteration, workers_spawned=ctx.budget.workers_used,
            run_dir=ctx.run_dir, elapsed_s=time.monotonic() - start,
        )

    while True:
        # Budget check
        breach = ctx.budget.check()
        if breach:
            _write_audit(ctx.tenant_id, "acs.budget_exhausted", {
                "run_id": ctx.run_id, "reason": breach, "iteration": ctx.iteration,
            })
            return ACSResult(
                run_id=ctx.run_id,
                workflow_id=ctx.workflow_id,
                status="budget_exhausted",
                budget_breach=breach,
                iterations=ctx.iteration,
                workers_spawned=ctx.budget.workers_used,
                run_dir=ctx.run_dir,
                elapsed_s=time.monotonic() - start,
            )

        ctx.budget.loops_used += 1

        # Build manager prompt and call
        prompt = _build_manager_prompt(ctx)
        _write_audit(ctx.tenant_id, "acs.manager_call", {
            "run_id": ctx.run_id, "iteration": ctx.iteration,
        })

        try:
            mgr_text, tok = await asyncio.to_thread(
                _call_manager_sync, prompt, manager_model, ctx.tenant_id
            )
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            _write_audit(ctx.tenant_id, "acs.manager_error", {
                "run_id": ctx.run_id, "reason": str(exc)[:300],
            })
            return ACSResult(
                run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                error=f"manager call failed: {exc}", iterations=ctx.iteration,
                run_dir=ctx.run_dir,
                elapsed_s=time.monotonic() - start,
            )

        ctx.budget.tokens_used += tok

        # Parse decision
        decision = _parse_manager_decision(mgr_text)
        if not decision:
            _write_audit(ctx.tenant_id, "acs.manager_parse_error", {
                "run_id": ctx.run_id, "iteration": ctx.iteration,
            })
            log.warning("acs: manager returned non-JSON at iteration %d", ctx.iteration)
            ctx.iteration += 1
            continue

        # Persist iteration decision
        _write_json_safe(
            ctx.run_dir / "iterations" / f"iter_{ctx.iteration:04d}.json",
            {
                "iteration": ctx.iteration, "decision": decision.get("decision"),
                "reasoning_len": len(decision.get("reasoning") or ""),
            },
        )

        decision_type = str(decision.get("decision", "")).upper()

        # ADR-0109 M2: manager decision trace — hash only, never content in chain
        _iter_spawn_nonce = str(uuid.uuid4())
        _decision_hash = _sha256(json.dumps(decision, default=str, sort_keys=True))
        _write_audit(ctx.tenant_id, "acs.manager_decided", {
            "run_id": ctx.run_id, "iteration": ctx.iteration,
            "decision_type": decision_type,
            "decision_hash": _decision_hash,
            "n_subtasks": len(decision.get("subtasks") or []) if decision_type == "DELEGATE" else 0,
            "model_id": manager_model,
            "spawn_nonce": _iter_spawn_nonce,
        })

        # --- DELEGATE ---
        if decision_type == "DELEGATE":
            subtasks = decision.get("subtasks") or []
            if not subtasks:
                log.warning("acs: DELEGATE with no subtasks at iteration %d", ctx.iteration)
                ctx.iteration += 1
                continue

            # M9: persist subtask definitions for post-run graph reconstruction.
            # Writes only structural metadata (id, task text, depends_on) —
            # never prompt text or reasoning content (L16 audit-first invariant).
            _write_json_safe(
                ctx.run_dir / "subtasks" / f"subtasks_iter_{ctx.iteration:04d}.json",
                {
                    "iteration": ctx.iteration,
                    "subtasks": [
                        {
                            "id": st.get("id") or f"st_{ctx.iteration}_{i}",
                            "task": (st.get("instructions") or st.get("task") or st.get("description") or "")[:500],
                            "depends_on": st.get("depends_on") or [],
                            "can_delegate": bool(st.get("can_delegate")),
                        }
                        for i, st in enumerate(subtasks)
                    ],
                },
            )

            # ADR-0105 M4: adaptive worker count based on current loss gap
            if ctx.loss_history and ctx.loss_profile and ctx.m4_base_workers > 0:
                gap = max(0.0, ctx.loss_profile.target - ctx.loss_history[-1].total)
                scale = max(0.5, min(1.0, 0.5 + gap))
                adaptive_n = max(1, round(ctx.m4_base_workers * scale))
                if adaptive_n != ctx.budget.max_workers_per_iteration:
                    ctx.budget.max_workers_per_iteration = adaptive_n
                    _write_audit(ctx.tenant_id, "acs.m4_adaptive_workers", {
                        "run_id": ctx.run_id, "iteration": ctx.iteration,
                        "adaptive_n": adaptive_n, "base_n": ctx.m4_base_workers,
                        "loss_gap": round(gap, 4),
                    })

            worker_results = await _dispatch_workers(
                subtasks, ctx, depth=0, manager_model=manager_model, worker_model=worker_model,
                spawn_nonce=_iter_spawn_nonce,  # ADR-0109 M2: causality link
            )

            # Merge results into state
            for wr in worker_results:
                ctx.state[wr.worker_id] = wr.result
                ctx.worker_results.append(wr)

            _last_iter_workers = list(worker_results)  # ADR-0105 M4: track for attribution
            ctx.iteration += 1

        # --- COMPLETE ---
        elif decision_type == "COMPLETE":
            artifacts = decision.get("complete_artifacts") or {}
            final_text = artifacts.get("summary", json.dumps(artifacts, default=str))
            final_result = {
                "status": "success",
                "result": artifacts,
                "confidence": float(artifacts.get("quality_score", 0.8)),
            }

            # M5: run gate chain
            gate_result = None
            if gate_chain is not None:
                from acs_gate_chain import ACSGateChain  # type: ignore[import-untyped]
                chain = gate_chain if isinstance(gate_chain, ACSGateChain) else ACSGateChain(
                    workflow_spec=ctx.workflow_spec
                )
                prev_loss = ctx.loss_history[-1] if ctx.loss_history else None
                gate_result = await chain.evaluate(
                    result_text=final_text,
                    result=final_result,
                    iteration=ctx.iteration,
                    prev_hash=ctx.prev_output_hash,
                    rejected_gates=_prev_rejected_gates,
                    goal=ctx.workflow_spec.get("workflow", {}).get("description", ""),
                    profile=ctx.loss_profile,
                    prev_loss=prev_loss,
                )
                ctx.prev_output_hash = chain.compute_prev_hash(final_text)

                # ADR-0105 M1: accumulate loss history
                if gate_result.loss is not None:
                    ctx.loss_history.append(gate_result.loss)
                    loss_total = round(gate_result.loss.total, 4)
                    loss_delta = (
                        round(gate_result.loss.delta, 4)
                        if gate_result.loss.delta is not None else None
                    )
                else:
                    loss_total = None
                    loss_delta = None

                # ADR-0105 M4: per-worker attribution proxy
                if _last_iter_workers:
                    attr = _compute_worker_attributions(
                        _last_iter_workers, loss_delta, ctx.iteration
                    )
                    if attr:
                        ctx.worker_attributions.append(attr)

                gate_json: dict = {
                    "iteration": ctx.iteration, "passed": gate_result.passed,
                    "aggregate_score": gate_result.aggregate_score,
                    "gates": [g.__dict__ for g in gate_result.gates],
                }
                if loss_total is not None:
                    gate_json["loss_total"] = loss_total
                    gate_json["loss_delta"] = loss_delta
                    if gate_result.loss is not None:
                        lo = gate_result.loss
                        gate_json["loss_dimensions"] = {
                            "completeness": round(lo.L_completeness, 3),
                            "novelty":      round(lo.L_novelty, 3),
                            "quality":      round(lo.L_quality, 3),
                            "metrics":      round(lo.L_metrics, 3),
                            "confidence":   round(lo.L_confidence, 3),
                        }
                    if ctx.worker_attributions:
                        gate_json["worker_attributions"] = (
                            ctx.worker_attributions[-1].get("workers", [])[:5]
                        )
                _write_json_safe(
                    ctx.run_dir / "gate_results" / f"gate_iter_{ctx.iteration:04d}.json",
                    gate_json,
                )

                audit_details: dict = {
                    "run_id": ctx.run_id, "iteration": ctx.iteration,
                    "passed": gate_result.passed,
                    "aggregate_score": round(gate_result.aggregate_score, 3),
                    "gate_count": len(gate_result.gates),
                }
                if loss_total is not None:
                    audit_details["loss_total"] = loss_total
                    if loss_delta is not None:
                        audit_details["loss_delta"] = loss_delta
                _write_audit(ctx.tenant_id, "acs.gate_chain_evaluated", audit_details)

                if not gate_result.passed:
                    if gate_result.abort_reason:
                        # Hard abort (repair fixpoint or R36)
                        _write_audit(ctx.tenant_id, "acs.gate_abort", {
                            "run_id": ctx.run_id, "reason": gate_result.abort_reason,
                        })
                        return ACSResult(
                            run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                            error=gate_result.abort_reason,
                            iterations=ctx.iteration,
                            workers_spawned=ctx.budget.workers_used,
                            run_dir=ctx.run_dir,
                            elapsed_s=time.monotonic() - start,
                        )

                    # Gate rejected — increment and loop for repair.
                    # Record which gate failed so G5 R36 has a signal next round.
                    if gate_result.rejected_gate:
                        _prev_rejected_gates = [gate_result.rejected_gate]
                    ctx.budget.rejected_completions += 1
                    if ctx.budget.rejected_completions >= ctx.budget.max_rejected_completions:
                        _write_audit(ctx.tenant_id, "acs.max_rejections_reached", {
                            "run_id": ctx.run_id, "count": ctx.budget.rejected_completions,
                        })
                        return ACSResult(
                            run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                            error=f"gate chain rejected {ctx.budget.rejected_completions} times",
                            iterations=ctx.iteration,
                            workers_spawned=ctx.budget.workers_used,
                            run_dir=ctx.run_dir,
                            elapsed_s=time.monotonic() - start,
                        )
                    ctx.iteration += 1
                    continue

            # ADR-0105 M3: ε-convergence check (only when LossProfile active)
            if (
                gate_chain is not None
                and gate_result is not None
                and gate_result.passed
                and ctx.loss_profile is not None
                and ctx.loss_history
            ):
                from acs_gate_chain import _evaluate_convergence  # type: ignore[import-untyped]
                conv = _evaluate_convergence(ctx.loss_history, ctx.loss_profile)
                if conv == "plateau":
                    _write_audit(ctx.tenant_id, "acs.loss_plateau", {
                        "run_id": ctx.run_id, "iteration": ctx.iteration,
                        "loss_total": round(ctx.loss_history[-1].total, 4),
                    })
                    return ACSResult(
                        run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                        error=f"loss plateau: gradient below epsilon for {ctx.loss_profile.convergence.window} iterations",
                        iterations=ctx.iteration,
                        workers_spawned=ctx.budget.workers_used,
                        run_dir=ctx.run_dir,
                        elapsed_s=time.monotonic() - start,
                    )
                elif conv == "regression":
                    _write_audit(ctx.tenant_id, "acs.loss_regression", {
                        "run_id": ctx.run_id, "iteration": ctx.iteration,
                        "loss_delta": round(ctx.loss_history[-1].delta or 0, 4),
                    })
                    return ACSResult(
                        run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                        error="loss regression: quality decreased by ≥10 percentage points in last iteration",
                        iterations=ctx.iteration,
                        workers_spawned=ctx.budget.workers_used,
                        run_dir=ctx.run_dir,
                        elapsed_s=time.monotonic() - start,
                    )
                elif conv == "continue":
                    # Target declared but not yet reached — loop; max_loops is the hard stop
                    ctx.iteration += 1
                    continue
                # conv == "converged" → fall through to accept

            # Gate passed (or no gate chain) — accept result; clear repair signal.
            _prev_rejected_gates = []
            _write_json_safe(
                ctx.run_dir / "output" / "FINAL" / "manifest.json",
                {
                    "workflow_id": ctx.workflow_id, "run_id": ctx.run_id,
                    "iteration": ctx.iteration, "artifacts": artifacts.get("output_paths", []),
                    "quality_score": artifacts.get("quality_score", 1.0),
                },
            )
            _write_audit(ctx.tenant_id, "acs.workflow_complete", {
                "run_id": ctx.run_id, "iteration": ctx.iteration,
                "workers_spawned": ctx.budget.workers_used,
                "artifact_count": len(artifacts.get("output_paths") or []),
            })
            return ACSResult(
                run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="success",
                final_output=artifacts, summary=final_text,
                iterations=ctx.iteration, workers_spawned=ctx.budget.workers_used,
                run_dir=ctx.run_dir, elapsed_s=time.monotonic() - start,
            )

        # --- FAIL ---
        elif decision_type == "FAIL":
            reason = str(decision.get("fail_reason") or "manager declared task impossible")
            _write_audit(ctx.tenant_id, "acs.workflow_failed", {
                "run_id": ctx.run_id, "iteration": ctx.iteration, "reason": reason[:200],
            })
            return ACSResult(
                run_id=ctx.run_id, workflow_id=ctx.workflow_id, status="failed",
                error=reason, iterations=ctx.iteration,
                workers_spawned=ctx.budget.workers_used,
                run_dir=ctx.run_dir,
                elapsed_s=time.monotonic() - start,
            )

        else:
            log.warning("acs: unknown decision %r at iteration %d", decision_type, ctx.iteration)
            ctx.iteration += 1


# ---------------------------------------------------------------------------
# ACSRuntime public class
# ---------------------------------------------------------------------------

class ACSRuntime:
    """Autonomous Compute Shell runtime.

    Implements ADR-0104 M2 (manager loop), M3 (self-tooling), M4 (recursive
    delegation). Coexists alongside L25 Compute Worker — does not replace it.

    Usage::

        from acs_runtime import ACSRuntime
        result = await ACSRuntime().run(
            workflow="path/to/workflow.awp.yaml",
            inputs={"topic": "climate risk"},
        )
        print(result.summary)
    """

    def __init__(
        self,
        *,
        tenant_id: str = "_default",
        bridge: str = "cli",
        chat: str = "default",
        manager_model: str = _DEFAULT_MODEL_MANAGER,
        worker_model: str | None = None,
        enable_gate_chain: bool = True,
        session_debug_log: "Path | None" = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._bridge = bridge
        self._chat = chat
        self._manager_model = manager_model
        self._worker_model = _resolve_worker_model(worker_model, tenant_id)
        self._enable_gate_chain = enable_gate_chain
        # ACO Layer 1 extension: if a session workdir is provided, write
        # acs.worker.* debug events to its chat_debug.jsonl so the ACO
        # anomaly detector can correlate worker errors with turn events.
        self._session_debug_log: Path | None = (
            Path(session_debug_log) if session_debug_log is not None else None
        )

    def _dbg(self, event: str, **fields) -> None:
        """Write a debug event to the session's chat_debug.jsonl if configured."""
        if self._session_debug_log is None:
            return
        try:
            from corvin_console.aco.debug_logger import write_event
            write_event(self._session_debug_log, event, **fields)
        except Exception:  # noqa: BLE001 — observability must never break execution
            pass

    async def run(
        self,
        workflow: str | Path | dict,
        inputs: dict | None = None,
        *,
        budget_override: dict | None = None,
        run_id: str | None = None,
        dry_run: bool = False,
    ) -> ACSResult:
        """Execute an AWP workflow.

        Args:
            workflow:        Path to workflow.awp.yaml, or an already-parsed dict.
            inputs:          Initial state values to inject.
            budget_override: Override specific budget fields (e.g. tokens=500000).
            run_id:          Explicit run ID; generated if not provided.
            dry_run:         Validate and build context but do not execute.

        Returns:
            ACSResult — always (never raises into the caller).
        """
        try:
            from .acs_validator import validate_workflow_dict  # type: ignore[import-untyped]
        except ImportError:
            from acs_validator import validate_workflow_dict  # type: ignore[import-untyped]

        # 1. Load spec
        if isinstance(workflow, dict):
            spec = workflow
        else:
            p = Path(workflow)
            if not p.is_file():
                return ACSResult(
                    run_id=run_id or "err", workflow_id="<unknown>", status="failed",
                    error=f"workflow file not found: {workflow}",
                )
            try:
                import yaml  # type: ignore[import-untyped]
                spec = yaml.safe_load(p.read_text("utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                return ACSResult(
                    run_id=run_id or "err", workflow_id="<unknown>", status="failed",
                    error=f"YAML parse error: {exc}",
                )

        workflow_id = spec.get("workflow", {}).get("name") or "unnamed"

        # Adversarial-review CRITICAL fix: budget_override used to be applied
        # via blind setattr(budget, k, ...) AFTER validation (step 3, below) —
        # so it (a) never passed through validate_workflow_dict's R31/R32
        # max_depth ceiling at all, reintroducing the exact unbounded-recursion
        # bug this codebase already fixed once, and (b) had no field allow-list,
        # so hasattr(budget, k) let a caller overwrite internal accounting state
        # (start_time, loops_used, workers_used, ...) via the same HTTP field —
        # e.g. a future-dated start_time permanently defeats the max_wall_time
        # check. Fix: merge ONLY the legitimate cap fields into the spec's own
        # budget dict BEFORE validation, so the override gets exactly the same
        # ceiling enforcement as a hand-authored workflow YAML would.
        if budget_override:
            _orch = spec.setdefault("orchestration", {})
            _dloop = _orch.setdefault("delegation_loop", {})
            _bdict = _dloop.setdefault("budget", {})
            for _k in _BUDGET_OVERRIDE_ALLOWED_FIELDS:
                if _k in budget_override:
                    try:
                        _bdict[_k] = int(budget_override[_k])
                    except (TypeError, ValueError):
                        pass  # non-numeric override value — ignored, not crashed on

        # 2. Validate
        val_result = validate_workflow_dict(spec)
        if not val_result.ok:
            errors = "; ".join(str(e) for e in val_result.errors)
            return ACSResult(
                run_id=run_id or "err", workflow_id=workflow_id, status="failed",
                error=f"workflow validation failed: {errors}",
            )

        # 2b. L44 acceptable-use gate (ADR-0143 / ADR-0158) — MANDATORY, fail-CLOSED.
        # FND-#3/#8: the ACS run dispatches LLM OS-turns (manager loop + workers)
        # against the user-controlled workflow goal/instruction, but had only the
        # L34/L35 engine gates — never the acceptable-use gate. ACSRuntime.run is
        # the single universal chokepoint every ACS caller funnels through (console
        # route → run_acs_workflow → here; corvin-workflow CLI → here; scheduler →
        # here; direct programmatic ACSRuntime().run → here), so gating once here
        # covers all paths — including the CLI __main__, which bypasses
        # run_acs_workflow. dry_run still classifies (intent is the same whether or
        # not workers spawn), but never executes regardless.
        #
        # Unlike the L34/L35 gates below, L44 is fail-CLOSED: an unimportable
        # spawn_gates module DENIES the run (it must never silently lose the
        # acceptable-use guarantee). check_l44 is itself fail-closed + audit-first:
        # it emits exactly one house_rules.{allowed,denied,escalated,warned} L16
        # event (metadata-only — never the goal text) BEFORE returning a refusal.
        _goal_text = _workflow_goal_text(spec, inputs)
        if _goal_text.strip():
            try:
                from spawn_gates import check_l44 as _sg_l44  # type: ignore
            except Exception as _l44_imp_exc:  # noqa: BLE001 — mandatory layer absent → DENY
                log.error("acs: L44 spawn_gates import failed (%s) — fail-closed deny",
                          type(_l44_imp_exc).__name__)
                _write_audit(self._tenant_id, "acs.house_rules_gate_unavailable", {
                    "run_id": run_id or "err", "workflow_id": workflow_id,
                    "reason": type(_l44_imp_exc).__name__,
                })
                return ACSResult(
                    run_id=run_id or "err", workflow_id=workflow_id, status="failed",
                    error=("[house-rules] Acceptable-use gate unavailable — run "
                           "blocked (fail-closed). Contact the operator."),
                )
            _l44_refusal = _sg_l44(
                _goal_text,
                self._tenant_id,
                persona="orchestrator",
                channel=self._bridge,
                chat_key=self._chat,
                engine_id="claude_code",
            )
            if _l44_refusal is not None:
                # check_l44 already emitted the house_rules.* L16 event (audit-first).
                # We add a metadata-only run-scoped marker — NEVER the goal text.
                _write_audit(self._tenant_id, "acs.run_blocked_house_rules", {
                    "run_id": run_id or "err", "workflow_id": workflow_id,
                })
                return ACSResult(
                    run_id=run_id or "err", workflow_id=workflow_id, status="failed",
                    error=_l44_refusal,
                )

        # 3. Build budget — budget_override was already merged into spec (and
        # validated) above; _budget_from_spec reads the effective values.
        budget = _budget_from_spec(spec)

        # 4. Build run context
        rid = run_id or f"acs-{int(time.time())}-{secrets.token_hex(4)}"
        run_dir = _run_dir(self._tenant_id, self._bridge, self._chat, rid)
        _make_run_dir(run_dir)

        initial_state = dict(spec.get("state", {}).get("initial") or {})
        if inputs:
            initial_state.update(inputs)

        # ADR-0127 — datasource binding. Declared under
        # orchestration.delegation_loop.datasources, top-level datasources,
        # or inputs["datasources"]. Resolve each DSI v1 connection, fetch a
        # LIVE read-only snapshot, and inject it into worker state so workers
        # reason over real DB data on ANY engine. ClaudeCode workers also get
        # the connection env to run their own live queries.
        _ds_names = (
            (spec.get("orchestration", {}).get("delegation_loop", {}) or {}).get("datasources")
            or spec.get("datasources")
            or (inputs or {}).get("datasources")
            or []
        )
        _ds_env: dict = {}
        if _ds_names:
            _ds_context, _ds_env = _resolve_acs_datasources(
                self._tenant_id, list(_ds_names), run_id=rid)
            if _ds_context:
                initial_state["_datasource_context"] = _ds_context

        # ADR-0105: extract LossProfile from spec if present
        _loss_profile = None
        try:
            from acs_gate_chain import loss_profile_from_spec  # type: ignore[import-untyped]
            _loss_profile = loss_profile_from_spec(spec)
        except ImportError:
            pass

        ctx = RunContext(
            run_id=rid,
            workflow_id=workflow_id,
            workflow_spec=spec,
            budget=budget,
            run_dir=run_dir,
            state=initial_state,
            tenant_id=self._tenant_id,
            loss_profile=_loss_profile,
            m4_base_workers=budget.max_workers_per_iteration,   # ADR-0105 M4
            datasource_env=_ds_env,                             # ADR-0127
        )

        # Write run manifest
        _write_json_safe(
            run_dir / "manifest.json",
            {
                "run_id": rid, "workflow_id": workflow_id,
                "tenant_id": self._tenant_id,
                "started_at": int(time.time()),
                "budget": {
                    "max_loops": budget.max_loops,
                    "max_total_tokens": budget.max_total_tokens,
                    "max_wall_time": budget.max_wall_time,
                    "max_depth": budget.max_depth,
                },
                "dry_run": dry_run,
            },
        )

        _write_audit(self._tenant_id, "acs.run_start", {
            "run_id": rid, "workflow_id": workflow_id,
            "max_loops": budget.max_loops, "max_depth": budget.max_depth,
        })
        # ACO Layer 1: mirror run start to session debug log so chat_debug.jsonl
        # contains correlated acs.worker.* events alongside turn.start/done events.
        self._dbg("acs.worker.run_start", run_id=rid, workflow_id=workflow_id)

        # ADR-0171 — manager span (role=manager). Worker spans set
        # parent_span_id=spn-<run_id>-mgr; emitting this span makes that parent
        # reference resolve (worker → manager → run tree) instead of dangling.
        _mgr_span_id = f"spn-{rid}-mgr"
        try:
            _mgr_engine_id, _ = _resolve_worker_engine(self._manager_model, self._tenant_id)
        except Exception:  # noqa: BLE001
            _mgr_engine_id = "claude_code"
        _mgr_start_t = time.monotonic()
        if _espan is not None:
            _write_audit(self._tenant_id, _espan.ENGINE_SPAN_START,
                         _espan.start_details(
                             span_id=_mgr_span_id, role="manager",
                             engine_id=_mgr_engine_id, model_id=self._manager_model,
                             run_id=rid))

        def _end_mgr_span(status: str) -> None:
            if _espan is None:
                return
            _write_audit(self._tenant_id, _espan.ENGINE_SPAN_END,
                         _espan.end_details(
                             span_id=_mgr_span_id, role="manager",
                             engine_id=_mgr_engine_id, model_id=self._manager_model,
                             run_id=rid, status=status,
                             duration_ms=int((time.monotonic() - _mgr_start_t) * 1000)))

        if dry_run:
            _end_mgr_span("ok")
            return ACSResult(
                run_id=rid, workflow_id=workflow_id, status="success",
                summary="dry_run: validated OK, not executed", run_dir=run_dir,
            )

        # 5. Gate chain setup (M5)
        gate_chain = None
        if self._enable_gate_chain:
            try:
                from acs_gate_chain import ACSGateChain  # type: ignore[import-untyped]
                gate_chain = ACSGateChain(workflow_spec=spec)
            except ImportError:
                log.warning("acs: acs_gate_chain not importable — gate chain disabled")
            except Exception:  # noqa: BLE001 — ADR-0171: a gate-chain ctor failure
                # after the manager span start must not orphan the span. Pair it
                # (error), then re-raise so the run still fails closed.
                _end_mgr_span("error")
                raise

        # 6. Execute manager loop
        try:
            result = await _manager_loop(
                ctx,
                manager_model=self._manager_model,
                worker_model=self._worker_model,
                gate_chain=gate_chain,
            )
        except asyncio.CancelledError:
            # ADR-0171 — run cancellation bypasses `except Exception`; pair the
            # manager span (status=error) before the cancellation propagates so it
            # is never orphaned.
            _end_mgr_span("error")
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("acs: unexpected error in manager loop")
            _write_audit(self._tenant_id, "acs.run_error", {
                "run_id": rid, "error": str(exc)[:300],
            })
            result = ACSResult(
                run_id=rid, workflow_id=workflow_id, status="failed",
                error=f"unexpected error: {exc}", iterations=ctx.iteration,
                workers_spawned=ctx.budget.workers_used,
                run_dir=run_dir,
            )

        result.run_dir = run_dir
        # ACO Layer 1: mirror final status to session debug log
        self._dbg(
            "acs.worker.run_done",
            run_id=rid,
            status=result.status,
            workers_spawned=result.workers_spawned,
            iterations=result.iterations,
            error=result.error or None,
        )
        _write_json_safe(
            run_dir / "result.json",
            {
                "run_id": rid, "status": result.status,
                "iterations": result.iterations,
                "workers_spawned": result.workers_spawned,
                "elapsed_s": round(result.elapsed_s, 2),
                "budget_breach": result.budget_breach,
                "error": result.error,
            },
        )

        _end_mgr_span("ok" if result.status == "success" else "error")
        return result


# ---------------------------------------------------------------------------
# CLI shim (M7 wires this via corvin-workflow)
# ---------------------------------------------------------------------------

async def _main_run_async(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="corvin-workflow run")
    parser.add_argument("path", help="Path to workflow.awp.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget-override", nargs="*", metavar="KEY=VALUE",
                        help="e.g. --budget-override max_loops=5 max_total_tokens=100000")
    args = parser.parse_args(argv)

    override: dict = {}
    for kv in (args.budget_override or []):
        k, _, v = kv.partition("=")
        if k and v:
            override[k.strip()] = v.strip()

    rt = ACSRuntime()
    result = await rt.run(args.path, budget_override=override or None, dry_run=args.dry_run)

    status_str = "OK" if result.status == "success" else "FAIL"
    print(
        f"{status_str}  {result.workflow_id}  run={result.run_id}  "
        f"iter={result.iterations}  workers={result.workers_spawned}  "
        f"time={result.elapsed_s:.1f}s"
    )
    if result.summary:
        print(f"\n{result.summary}")
    if result.error:
        print(f"\nError: {result.error}", file=sys.stderr)
    return 0 if result.status == "success" else 1


def main_run(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_run_async(argv))


if __name__ == "__main__":
    sys.exit(main_run())
