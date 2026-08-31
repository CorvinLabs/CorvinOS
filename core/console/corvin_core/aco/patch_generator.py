"""ACO Layer 6 — engine-backed patch generator (ADR-0178).

The fully-automatic ``patch_source`` for ``run_maintenance_loop``: takes an ACO L4
diagnosis, hands the engine (LLM) the diagnosis + the current target-file contents,
and parses the engine's reply into a :class:`Patch`.

SECURITY — engine-generated code is NEVER auto-merged to ``main``. We force the
patch's ``risk_class`` to ``"engine_generated"`` (deliberately NOT in the L6
low-risk allowlist), so ``l6_gate`` always sets ``requires_ack`` and the loop can
only ever produce a PR for human review — regardless of what risk_class the LLM
claims. An LLM cannot talk its way to a direct-to-main merge. The l6_gate
(tests-green, hard-block, path-scope) remains the backstop on top of that.

The ``llm`` is an injected ``str -> str`` callable (testable with a stub).
``default_llm`` builds a WorkerEngine-backed one, or returns None when no engine
is available (→ the loop escalates; it never invents code).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .maintenance_loop import Patch, PatchEdit

logger = logging.getLogger(__name__)

# Engine-generated patches are ALWAYS ack-gated (never auto-merged). This token
# is intentionally absent from maintenance_loop._LOW_RISK_CLASSES.
ENGINE_RISK_CLASS = "engine_generated"

_MAX_EDITS = 8
_MAX_FILE_BYTES = 200_000


def _target_files(diagnosis: dict) -> list[str]:
    files = diagnosis.get("files")
    if isinstance(files, list) and files:
        return [str(f) for f in files]
    one = diagnosis.get("file")
    return [str(one)] if one else []


def build_prompt(diagnosis: dict, file_contents: dict[str, str]) -> str:
    """Construct the patch-generation prompt (metadata + current file bodies)."""
    diag_json = json.dumps({k: diagnosis.get(k) for k in
                            ("id", "root_cause", "layer", "anomaly_class", "repro",
                             "file", "files", "summary") if k in diagnosis},
                           indent=2, ensure_ascii=False)
    parts = [
        "You are a CorvinOS maintenance engine. Fix the diagnosed bug.",
        "Return ONLY a JSON object, no prose, of the exact shape:",
        '{"summary": "<one line>", "edits": '
        '[{"path": "<repo-relative>", "new_content": "<FULL new file content>"}]}',
        "",
        "Your patch MUST contain BOTH of these, or it will be rejected:",
        "  1. A NEW regression test (path under a 'tests/' dir, file named "
        "test_<something>.py) that REPRODUCES the bug: it MUST FAIL on the current "
        "code and PASS only after your fix. A test that already passes proves "
        "nothing and the patch is rejected.",
        "  2. The actual FIX in the diagnosed source file(s).",
        "The reproduction gate will run your test on the UNPATCHED code (must fail), "
        "then with your fix (must pass), then the full suite (must stay green). "
        "Only then is the fix committed. So make the test genuinely exercise the bug.",
        "",
        "Rules: edit only the diagnosed file(s) plus your new test; return the "
        "COMPLETE new content of each file (not a diff); make the smallest change "
        "that fixes the root cause; never touch LICENSE/NOTICE/CLA/audit/policy/keys.",
        "",
        "DIAGNOSIS:",
        diag_json,
        "",
        "CURRENT FILE(S):",
    ]
    for path, body in file_contents.items():
        parts.append(f"--- {path} ---")
        parts.append(body)
    return "\n".join(parts)


def parse_patch(text: str, diagnosis: dict, *, max_edits: int = _MAX_EDITS) -> Optional[Patch]:
    """Robustly extract the JSON patch from the engine reply. Returns None on any
    malformation (the loop then escalates — never invents code)."""
    if not text:
        return None
    # strip ```json fences / leading prose: take the outermost {...}.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    raw_edits = obj.get("edits")
    if not isinstance(raw_edits, list) or not raw_edits:
        return None
    if len(raw_edits) > max_edits:
        return None
    edits: list[PatchEdit] = []
    for e in raw_edits:
        if not isinstance(e, dict):
            return None
        path = e.get("path")
        content = e.get("new_content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None
        # repo-relative only — no absolute, no traversal (the loop also enforces
        # this, but the generator must not even propose an escape).
        norm = path.replace("\\", "/")
        seg0 = norm.split("/")[0]
        # reject absolute, traversal, and ANY colon in the first segment
        # (Windows drive `C:` and NTFS ADS `file:stream`) — not just index 1.
        if norm.startswith("/") or ".." in norm.split("/") or ":" in seg0:
            return None
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            return None
        edits.append(PatchEdit(path=norm, new_content=content))
    return Patch(
        diagnosis_id=str(diagnosis.get("id") or "diag"),
        summary=str(obj.get("summary") or "engine maintenance fix")[:200],
        risk_class=ENGINE_RISK_CLASS,   # FORCED — never the LLM's self-claim.
        edits=edits,
    )


def engine_patch_source(*, repo_dir: str | Path,
                        llm: Optional[Callable[[str], str]] = None,
                        max_edits: int = _MAX_EDITS) -> Callable[[dict], Optional[Patch]]:
    """Build a patch_source for run_maintenance_loop. Reads the diagnosis's target
    file(s) from repo_dir, prompts ``llm``, parses the reply. llm=None → always
    None (escalate)."""
    repo = Path(repo_dir)

    def src(diagnosis: dict) -> Optional[Patch]:
        if llm is None:
            return None
        targets = _target_files(diagnosis)
        if not targets:
            return None
        bodies: dict[str, str] = {}
        repo_r = repo.resolve()
        for rel in targets:
            norm = rel.replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/") or ":" in norm.split("/")[0]:
                return None
            fp = (repo / norm)
            # Resolve + confirm containment so a symlink inside the repo can't
            # leak an out-of-repo file's contents into the prompt (review LOW).
            try:
                rp = fp.resolve()
                if rp != repo_r and repo_r not in rp.parents:
                    return None
                if fp.is_file() and fp.stat().st_size <= _MAX_FILE_BYTES:
                    bodies[norm] = fp.read_text(encoding="utf-8")
                else:
                    bodies[norm] = ""   # new file
            except OSError:
                bodies[norm] = ""
        try:
            reply = llm(build_prompt(diagnosis, bodies))
        except Exception as exc:  # noqa: BLE001 — engine failure → escalate
            logger.debug("engine patch llm failed: %s", exc)
            return None
        return parse_patch(reply, diagnosis, max_edits=max_edits)

    return src


def default_llm(*, model: Optional[str] = None, timeout: float = 300.0,
                working_dir: Optional[str | Path] = None) -> Optional[Callable[[str], str]]:
    """Build a WorkerEngine-backed ``str -> str`` completion, or None if no engine
    is importable/available (→ escalate). Best-effort; never raises at build time."""
    try:
        import sys as _sys
        from pathlib import Path as _P
        _shared = _P(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        if str(_shared) not in _sys.path:
            _sys.path.insert(0, str(_shared))
        from agents.claude_code import ClaudeCodeEngine  # type: ignore
        from agents import collect  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    def _llm(prompt: str) -> str:
        engine = ClaudeCodeEngine()
        # SECURITY (review 2026-06-29, CRITICAL): the patch generator must be a
        # PURE text→text completion — NEVER an agentic run with Write/Edit/Bash.
        # mode="restricted" emits `--disallowedTools "*"`, so the engine cannot
        # touch the filesystem or shell out (no out-of-gate writes, no `git push`)
        # before parse_patch + l6_gate + the human ack ever run. The file content
        # it needs is already embedded in the prompt (build_prompt), so it needs
        # no Read tool. We also DON'T set working_dir → no implicit repo cwd.
        kw: dict[str, Any] = {"timeout": timeout, "mode": "restricted"}
        if model:
            kw["model"] = model
        result = collect(engine.spawn(prompt, **kw))
        return result.final_text or ""

    return _llm
