"""P-G child entry point — runs ONE community ContextStage inside the sandbox.

This file is the only CorvinOS code that executes next to untrusted stage code,
so it deliberately imports NOTHING from CorvinOS: not the stages package, not
forge, not the console. Inside the jail only this file, the stage module and the
stdlib are visible, and the wire format is plain JSON on stdin/stdout.

Protocol (ADR-0289):

    stdin   {"stage_id": str, "module_path": str, "bundle": {...}, "ctx": {...}}
    stdout  {"ok": true,  "patch": {...}, "telemetry": {...}}
          | {"ok": false, "error": "<slug>: <detail>"}

The stage receives DUCK-TYPED views, never a real ContextBundle: a community
stage cannot import CorvinOS types and must not depend on them. It sees
``bundle.task`` / ``bundle.text_sections`` / ``bundle.scratch`` and
``ctx.tenant_id`` / ``ctx.session_id`` / ``ctx.config``, and returns
``(bundle, telemetry)`` exactly like a first-party stage.

What comes back is a PATCH, not a bundle: the parent applies only the additive
fields it recognises. The RichTaskBrief never crosses the boundary in either
direction — a sandboxed stage can add context, never replace the retrieval the
prompt is built from (ADR-0277 additive-to-context-never-to-authority).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time

#: Hard caps on what a child may hand back. A hostile stage that returns 100 MB
#: of text would otherwise be a memory/DoS vector in the PARENT, which has no
#: rlimits of its own — the jail bounds the child, this bounds the reply.
MAX_SECTION_CHARS = 8000
MAX_SECTIONS = 16
MAX_SCRATCH_CHARS = 16000


class _BundleView:
    """The read-only-ish view a sandboxed stage sees. Plain object on purpose."""

    def __init__(self, task: str, text_sections: list, scratch: dict) -> None:
        self.task = task
        self.text_sections = list(text_sections or [])
        self.scratch = dict(scratch or {})
        # Present so a stage written against the first-party contract does not
        # AttributeError — but they are dead ends here: the parent ignores them
        # (a sandboxed stage may not provision the worker, ADR-0289 D2).
        self.brief = None
        self.tools_to_bind: list = []
        self.skills_to_bind: list = []
        self.synthesised_prompt = None


class _CtxView:
    def __init__(self, tenant_id: str, session_id: str, config: dict) -> None:
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.config = dict(config or {})
        self.workdir = None
        self.task_obj = None


def _load_stage(module_path: str, stage_id: str):
    """Import the stage module by file path and find its stage object."""
    spec = importlib.util.spec_from_file_location(
        f"corvin_community_stage_{stage_id}", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_path!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stage = getattr(mod, "STAGE", None)
    if stage is None:
        # Fall back to the first module-level object whose `id` matches.
        for name in dir(mod):
            cand = getattr(mod, name)
            if getattr(cand, "id", None) == stage_id and hasattr(cand, "run"):
                stage = cand
                break
    if stage is None:
        raise AttributeError(
            f"module exposes no STAGE and no object with id={stage_id!r}")
    return stage


def _bounded_sections(sections) -> list:
    out: list = []
    for s in list(sections or [])[:MAX_SECTIONS]:
        if not isinstance(s, str):
            s = str(s)
        out.append(s[:MAX_SECTION_CHARS])
    return out


def _json_safe(value):
    """Keep only JSON-primitive structures — a stage cannot smuggle an object."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_safe(v) for v in value[:64]]
    if isinstance(value, dict):
        return {str(k)[:64]: _json_safe(v) for k, v in list(value.items())[:64]}
    return str(value)[:512]


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"bad_request: {exc}"}))
        return 0

    stage_id = str(req.get("stage_id") or "")
    module_path = str(req.get("module_path") or "")
    b = req.get("bundle") or {}
    c = req.get("ctx") or {}

    bundle = _BundleView(str(b.get("task") or ""),
                         b.get("text_sections") or [],
                         b.get("scratch") or {})
    ctx = _CtxView(str(c.get("tenant_id") or "_default"),
                   str(c.get("session_id") or ""), c.get("config") or {})
    before_sections = len(bundle.text_sections)
    before_scratch = set(bundle.scratch)

    t0 = time.time()
    try:
        stage = _load_stage(module_path, stage_id)
        result = stage.run(bundle, ctx)
    except Exception as exc:  # noqa: BLE001 — any stage error is a clean failure
        print(json.dumps({"ok": False,
                          "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 0
    duration_ms = (time.time() - t0) * 1000.0

    out_bundle, tel = (result if isinstance(result, tuple) and len(result) == 2
                       else (bundle, None))
    sections = getattr(out_bundle, "text_sections", None) or []
    scratch = getattr(out_bundle, "scratch", None) or {}

    # ADDITIVE ONLY: hand back what the stage ADDED, never the whole state. A
    # stage cannot delete or rewrite a prior stage's section or scratch key.
    patch = {
        "text_sections_added": _bounded_sections(sections[before_sections:]),
        "scratch_added": {
            str(k)[:64]: _json_safe(v)
            for k, v in scratch.items() if k not in before_scratch
        },
    }
    if len(json.dumps(patch["scratch_added"])) > MAX_SCRATCH_CHARS:
        patch["scratch_added"] = {}

    telemetry = {
        "status": str(getattr(tel, "status", "ok") or "ok")[:24],
        "confidence_tier": (str(getattr(tel, "confidence_tier", "") or "")[:16]
                            or None),
        "duration_ms": duration_ms,
        "reason": (str(getattr(tel, "reason", "") or "")[:64] or None),
    }
    print(json.dumps({"ok": True, "patch": patch, "telemetry": telemetry}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
