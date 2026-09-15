"""P-G — subprocess sandbox for community ContextStages (ADR-0289).

Until now `register_stage` REFUSED every non-`builtin` stage, because there was
no isolation for one (ADR-0285 R2). This module is that isolation: a community
stage never executes in the console/bridge process — it runs in the SAME jail
the forged-tool runner uses (`forge.sandbox`: bwrap namespaces, no network,
stripped env, POSIX rlimits), talking JSON over stdin/stdout.

Three properties make this safe to turn on (see `_sandbox_child.py` for the
child half):

* **Additive only.** The child receives a PROJECTION (task text, text_sections,
  a JSON-safe scratch) and returns a PATCH of what it ADDED. The RichTaskBrief
  — the single source of truth the prompt is built from — never crosses the
  boundary, so a hostile stage can add context but can never replace or delete
  the retrieval a first-party stage produced (ADR-0277).
* **No provisioning.** A sandboxed stage may not bind tools or skills. The dual
  channel (ADR-0281) stays first-party; widening it needs its own decision.
* **Fail-closed everywhere.** No sandbox on this host (Windows/macOS without
  Docker) → the stage does not run at all, exactly as before P-G. A timeout, a
  crash, a non-JSON reply, an oversized reply → the stage is recorded `failed`
  and the pipeline continues on the context it already had.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import StageTelemetry

_CHILD = Path(__file__).resolve().parent / "_sandbox_child.py"

#: A context stage shapes a prompt; it is not a compute job. Tighter than the
#: forged-tool defaults on purpose — a stage that needs 10 CPU-seconds is
#: misusing the phase, and the turn is waiting on it.
_CPU_SECONDS = 5
_ADDRESS_SPACE_MB = 256
_WALL_TIMEOUT_S = 20.0
#: Bound on what we will even parse back (the child bounds itself too, but a
#: hostile child controls its own code — the parent must not trust that).
_MAX_REPLY_BYTES = 256 * 1024


def _forge_sandbox():
    """Import forge's sandbox layer (operator/forge is not always on sys.path).

    Returns the module or None — None means "no isolation available", which the
    caller must treat as REFUSE, never as "run it anyway"."""
    try:
        _op = str(Path(__file__).resolve().parents[2])
        if _op not in sys.path:
            sys.path.insert(0, _op)
        _fg = str(Path(_op) / "forge")
        if _fg not in sys.path:
            sys.path.insert(0, _fg)
        from forge import sandbox as _sb  # noqa: PLC0415
        return _sb
    except Exception:  # noqa: BLE001
        return None


def sandbox_available() -> bool:
    """True when a community stage COULD run here. False on a host with neither
    bwrap nor Docker — there the palette stays builtin-only (ADR-0284 R2)."""
    try:
        _op = str(Path(__file__).resolve().parents[2])
        if _op not in sys.path:
            sys.path.insert(0, _op)
        _fg = str(Path(_op) / "forge")
        if _fg not in sys.path:
            sys.path.insert(0, _fg)
        from forge.sandbox_provider import is_sandbox_available  # noqa: PLC0415
        return bool(is_sandbox_available())
    except Exception:  # noqa: BLE001 — unknown → refuse (fail-closed)
        return False


def _project_bundle(bundle: Any) -> dict:
    """The read-only view the child gets. NOTE what is absent: `brief`,
    `tools_to_bind`, `skills_to_bind`, `synthesised_prompt`. A community stage
    is given the CONTEXT, never the authority."""
    scratch = {}
    for k, v in (getattr(bundle, "scratch", None) or {}).items():
        if str(k).startswith("_"):
            continue          # internal slots (_ctx, _deferred, _forged_*)
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue          # non-serialisable projection — simply not shared
        scratch[str(k)] = v
    return {
        "task": str(getattr(bundle, "task", "") or ""),
        "text_sections": [str(s) for s in
                          (getattr(bundle, "text_sections", None) or [])],
        "scratch": scratch,
    }


def _apply_patch(bundle: Any, patch: dict) -> int:
    """Apply the child's ADDITIVE patch. Returns how many sections landed."""
    sections = patch.get("text_sections_added") or []
    added = 0
    for s in sections:
        if isinstance(s, str) and s.strip():
            bundle.text_sections.append(s)
            added += 1
    for k, v in (patch.get("scratch_added") or {}).items():
        key = str(k)
        # Never let a sandboxed stage write an INTERNAL slot (`_ctx`,
        # `_deferred`, `_forged_tools`) or overwrite a first-party projection —
        # additive means "new keys only".
        if key.startswith("_") or key in bundle.scratch:
            continue
        bundle.scratch[key] = v
    return added


def run_stage_sandboxed(stage_id: str, module_path: Any, bundle: Any, ctx: Any,
                        ) -> "tuple[Any, StageTelemetry]":
    """Run one community stage out-of-process. Never raises."""
    sb = _forge_sandbox()
    if sb is None or not sandbox_available():
        return bundle, StageTelemetry(
            stage=stage_id, status="skipped", reason="no_sandbox")

    impl = Path(module_path)
    if not impl.is_file():
        return bundle, StageTelemetry(
            stage=stage_id, status="failed", reason="module_missing")

    envelope = json.dumps({
        "stage_id": stage_id,
        "module_path": str(impl),
        "bundle": _project_bundle(bundle),
        "ctx": {
            "tenant_id": str(getattr(ctx, "tenant_id", "_default") or "_default"),
            "session_id": str(getattr(ctx, "session_id", "") or ""),
            "config": getattr(ctx, "config", None) or {},
        },
    })

    inner = [sys.executable, str(_CHILD)]
    ro_binds = [_CHILD, impl, *sb.interpreter_ro_binds()]
    try:
        cmd = sb.build_bwrap_cmd(inner, impl, extra_ro_binds=ro_binds,
                                 allow_network=False)
    except Exception:  # noqa: BLE001 — cannot build the jail → refuse
        return bundle, StageTelemetry(
            stage=stage_id, status="skipped", reason="no_sandbox")

    limits = sb.Limits(cpu_seconds=_CPU_SECONDS,
                       address_space_mb=_ADDRESS_SPACE_MB)
    try:
        proc = subprocess.run(
            cmd, input=envelope, capture_output=True, text=True,
            timeout=_WALL_TIMEOUT_S, env=sb.stripped_env(),
            preexec_fn=(lambda: sb.apply_rlimits(limits)) if os.name != "nt" else None,
        )
    except subprocess.TimeoutExpired:
        return bundle, StageTelemetry(
            stage=stage_id, status="failed", reason="timeout")
    except Exception:  # noqa: BLE001
        return bundle, StageTelemetry(
            stage=stage_id, status="failed", reason="spawn_failed")

    out = (proc.stdout or "")[:_MAX_REPLY_BYTES]
    try:
        reply = json.loads(out)
    except Exception:  # noqa: BLE001 — a stage that prints garbage is a failure
        return bundle, StageTelemetry(
            stage=stage_id, status="failed", reason="bad_reply")

    if not isinstance(reply, dict) or not reply.get("ok"):
        return bundle, StageTelemetry(
            stage=stage_id, status="failed", reason="stage_error")

    added = _apply_patch(bundle, reply.get("patch") or {})
    tel = reply.get("telemetry") or {}
    return bundle, StageTelemetry(
        stage=stage_id,
        status=str(tel.get("status") or "ok")[:24],
        confidence_tier=tel.get("confidence_tier"),
        duration_ms=tel.get("duration_ms"),
        reason=tel.get("reason") or (None if added else "no_output"),
    )


class SandboxedStage:
    """Registry-facing proxy for a community stage.

    The registry stores THIS, never the community object — there is no code path
    on which a non-builtin stage's `run` executes in-process. `effect` is forced
    to `pure`: a sandboxed stage has no network and cannot provision, so it can
    never be an egress/forge stage.
    """

    trust = "community"
    effect = "pure"

    def __init__(self, stage_id: str, module_path: Any, requires: tuple = ()):
        self.id = str(stage_id)
        self.module_path = str(module_path)
        self.requires = tuple(requires or ())

    def run(self, bundle, ctx):
        return run_stage_sandboxed(self.id, self.module_path, bundle, ctx)
