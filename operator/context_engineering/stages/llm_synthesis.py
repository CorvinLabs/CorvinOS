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

import atexit
import json
import re
import shutil
import subprocess
from typing import Any

from .base import StageTelemetry
from .registry import register_stage

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# 45s was the original budget; a real Haiku synthesis (thinking tokens + a ~7k
# cache-creation prefix) measured 19–45s+ across six live tasks on 2026-08-18, so
# one task in six hit the ceiling and degraded. 60s covers the observed spread
# without letting a hung child stall the turn indefinitely; per-stage config
# (`timeout_s`) lets an operator on a slower model raise it.
_TIMEOUT_S = 60

# `needs` is not a wish-list of technologies — it is the ORDER the forge stages
# execute. Asked for "the tools/skills the worker will need", a model answers with
# a tech stack ("Python csv module oder pandas", "pydantic", "argparse") or with
# built-ins it already has ("Read", "Bash"), and ToolForge dutifully forged each one
# into an echo-template tool called `cel_pydantic` (observed 2026-08-18). Demanding
# a NEW-tool OBJECT (name + description + input_schema) and a skill BODY makes the
# distinction the model's job, where it belongs, and gives the stages a shape they
# can refuse: anything less specific is not forgeable and is skipped + counted.
_SYS = (
    "You are CorvinOS's context engineer. Given a user task and retrieved context, "
    "assemble the tightest, most useful briefing for a worker that must solve it: "
    "restate the goal, select ONLY relevant context, name the constraints. "
    "Then declare what the worker is MISSING, as strict JSON: "
    '{"brief": "<the briefing>", "needs": {"tools": [{"name": "snake_case_name", '
    '"description": "<what it does>", "input_schema": {"type": "object"}}], '
    '"skills": [{"name": "snake_case_name", "body": "<the instructions, markdown>"}]}}. '
    "needs.tools is for a DETERMINISTIC helper the worker lacks (validate an id "
    "format, transform a fixed structure) — never a built-in (Read, Write, Edit, "
    "Bash, Grep, Glob, WebFetch, WebSearch), and never a programming language, "
    "library, framework or technique. Most tasks need none; say so with []. "
    "needs.skills is the load-bearing one: whenever the task calls for domain "
    "knowledge, a method or a convention the briefing above does not already carry, "
    "write it out as a skill — `name` plus the INSTRUCTIONS THEMSELVES in `body` "
    "(markdown, concrete, actionable), never a bare title. "
    "Output the raw JSON object ONLY — no markdown code fences, no prose "
    "before or after it."
)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_llm_json(text: str) -> "dict | None":
    """Extract the model's JSON object from a `claude -p` reply. None when there
    is none.

    A bare ``json.loads`` on the reply was the LIVE defect (found 2026-08-18 by an
    unmocked E2E): every real Haiku reply wraps the object in a ```json fence and
    appends prose, so the stage recorded `parse_error` on EVERY turn — which left
    `scratch['needs']` unset, which made the ToolForge and SkillForge stages forge
    nothing at all. The whole active pipeline silently degraded to the
    deterministic brief. All 15 existing tests mocked `subprocess.run` with a
    clean JSON string, so none of them could see it (the same "mock the boundary
    under test" class as the ADR-0283 R7 SkillForge defect).

    Three passes, cheapest first, each fail-safe: raw parse → fenced block →
    first balanced ``{...}`` span. Never `eval`, never a regex-built dict."""
    if not isinstance(text, str) or not text.strip():
        return None
    for cand in _json_candidates(text):
        try:
            obj = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _json_candidates(text: str):
    """Yield the substrings that might be the JSON object, most likely first."""
    yield text.strip()
    for m in _FENCE_RE.finditer(text):
        yield m.group(1).strip()
    # Balanced-brace scan for an unfenced object embedded in prose. String-aware
    # so a brace inside a value ("use {} for a dict") cannot end the span early.
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break
        start = text.find("{", start + 1)


_CLOUD_HOST = "api.anthropic.com"

# Lazily-created empty cwd for the synthesis subprocess (see _neutral_cwd).
_CWD: "str | None" = None


def _cleanup_cwd() -> None:
	"""Cleanup handler registered with atexit to delete the temp directory."""
	global _CWD
	if _CWD and _CWD != "":
		try:
			shutil.rmtree(_CWD, ignore_errors=True)
		except Exception:  # noqa: BLE001
			pass
		_CWD = None


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


def _neutral_cwd() -> str:
    """An empty directory to run the synthesis subprocess in.

    Without it the child INHERITS the host's working directory — for the console
    that is the CorvinOS checkout, so `claude -p` loads the repo's CLAUDE.md and
    skill tree into a call that needs none of it. Measured 2026-08-19: 18.8k
    cache-creation tokens from the repo vs. 7.4k from an empty directory, and the
    slower call pushed real console turns past the timeout (`llm_timeout` in the
    live trace). Three reasons this belongs here, not in the caller: the stage is
    the one that knows the call is context-free (it passes the task + digest as
    TEXT and forbids every tool), the cost is per turn, and repo content should not
    ride along into a cloud call the operator understands as "synthesis".

    Created once per process; a failure falls back to the inherited cwd (None),
    which is the pre-2026-08-19 behaviour — degraded, never broken.

    Cleaned up at process exit via atexit handler to prevent temp-directory
    accumulation on long-running services (ADR-0391 fix)."""
    global _CWD
    if _CWD is None:
        try:
            import tempfile  # noqa: PLC0415
            # Deliberately OUTSIDE the repo: `claude` walks PARENT directories for
            # CLAUDE.md, so a scratch dir under <repo>/.corvin/ would load it again.
            _CWD = tempfile.mkdtemp(prefix="corvin-ce-synthesis-")
            # Register cleanup handler once at creation time
            atexit.register(_cleanup_cwd)
        except Exception:  # noqa: BLE001
            _CWD = ""
    return _CWD or None


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
        try:
            timeout_s = float(cfg.get("timeout_s") or _TIMEOUT_S)
        except (TypeError, ValueError):
            timeout_s = _TIMEOUT_S
        # The task is DATA to brief about, never a question to answer, and the
        # format instruction has to live in the USER prompt — not only in the
        # appended system prompt (measured 2026-08-19): `claude -p` is an agent
        # whose own system prompt says "answer the user", so an imperative task
        # ("Baue ein Werkzeug…") produced JSON while a question ("Erkläre mir, wie…")
        # made it answer the question in prose and the stage degraded on a turn that
        # cost a full cloud call. Framing + a closing format line wins that contest.
        prompt = (
            "Below is a task SOMEONE ELSE will carry out. Do NOT carry it out and "
            "do NOT answer it. Produce only the JSON briefing described in your "
            "instructions.\n\n"
            f"TASK:\n{bundle.task}\n\nRETRIEVED CONTEXT:\n"
            f"{_context_digest(bundle)}\n\n"
            'Reply with the JSON object only: {"brief": "…", "needs": '
            '{"tools": [], "skills": []}}')
        try:
            out = subprocess.run(
                [_resolve_bin(), "-p", prompt, "--append-system-prompt", _SYS,
                 "--model", model, "--disallowedTools", "*",
                 "--output-format", "json", "--max-turns", "1"],
                # stdin=DEVNULL: without it the child INHERITS the host's stdin and
                # `claude -p` blocks up to 3s polling it ("no stdin data received in
                # 3s") on every synthesis — and a host holding an open stdin (a
                # service, a TTY) can stall it until the 45s timeout kills the turn's
                # enrichment. It never has stdin input to read; say so explicitly.
                stdin=subprocess.DEVNULL, cwd=_neutral_cwd(),
                capture_output=True, text=True, timeout=timeout_s, check=True)
        except subprocess.TimeoutExpired as e:
            # DISTINCT from llm_unavailable (found 2026-08-18): collapsing both into
            # one reason made the console's pipeline view report "unavailable" for a
            # call that ran fine and was merely slower than the budget — the
            # operator's fix for the two is opposite (raise `timeout_s` vs. install
            # the binary), so the trace has to tell them apart.
            return bundle, StageTelemetry(stage=self.id, status="failed",
                                          reason="llm_timeout", error=str(e)[:120])
        except Exception as e:  # noqa: BLE001 — nonzero / unavailable → degrade
            return bundle, StageTelemetry(stage=self.id, status="failed",
                                          reason="llm_unavailable", error=str(e)[:120])

        try:
            payload = json.loads(out.stdout)
            # claude -p --output-format json wraps the reply; the assistant text
            # is the model's answer, which in practice carries a ```json fence and
            # trailing prose — `parse_llm_json` extracts the object from either
            # shape (raw / fenced / embedded). Anything else → degrade.
            text = payload.get("result") if isinstance(payload, dict) else None
            inner = parse_llm_json(text) if isinstance(text, str) else None
            if inner is None:
                # The model replied, but not in JSON — on a vague task it answers
                # conversationally ("Um dir das zu sagen, bräuchte ich…"). Report
                # that distinctly: the operator's response differs from "JSON with
                # no brief in it" (prompt problem vs. task-shape problem), and the
                # old code fell through to the `claude -p` ENVELOPE here, whose keys
                # are is_error/usage/result — so `.get("brief")` was always None and
                # every such turn reported the misleading `empty_synthesis`.
                return bundle, StageTelemetry(stage=self.id, status="skipped",
                                              reason="non_json_reply")
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
