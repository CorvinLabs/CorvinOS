"""Minimal MCP server exposing SkillForge over stdio.

Transport: line-delimited JSON-RPC 2.0 on stdin/stdout (per MCP spec).

Tools surfaced:
  - skill_create   — register a new skill (markdown body)
  - skill_promote  — move a skill across scopes (gated by grades)
  - skill_grade    — append a grade
  - skill_list     — list skills (with shadowing across scopes)
  - skill_get      — fetch a single skill spec + body
  - skill_purge    — delete a skill
  - skill_diff     — diff body against next-higher scope copy

Skills are pure markdown — no subprocess execution, no sandbox.
"""
from __future__ import annotations

import difflib
import json
import os
import sys
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

_uah_write = None
try:
    _sf_shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
    if _sf_shared.is_dir() and str(_sf_shared) not in sys.path:
        sys.path.insert(0, str(_sf_shared))
    from activity_writer import write_chat_activity as _uah_write  # type: ignore
except Exception:
    _uah_write = None

from . import launch_record
from .multi_registry import MultiSkillRegistry, VALID_SCOPES, detect_scope
from .registry import (
    LinterError,
    PromotionGateError,
    SkillRegistry,
    SkillSpec,
    VALID_TYPES,
)


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "claude-skill-forge"
SERVER_VERSION = "0.1.0"

# Cap for a self-awarded grade, mirroring skill_inject's _AUTO_GRADE_CAP_MAX
# and registry_bridge.py's BOOTSTRAP_GRADE (the console Skill-Creator path).
# A bootstrap seed must never look like earned usage.
BOOTSTRAP_GRADE = 0.3
BOOTSTRAP_NOTES = (
    "manual bootstrap seed by skill_create (MCP) — NOT earned usage; "
    "required so the injection gate (n_grades >= 1) can see the skill at all"
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


SKILL_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "type", "description", "body_md"],
    "properties": {
        "name":        {"type": "string"},
        "type":        {"type": "string", "enum": list(VALID_TYPES)},
        "description": {"type": "string"},
        "body_md":     {"type": "string"},
        "claim":       {"type": "object"},
        "scope":       {"type": "string", "enum": list(VALID_SCOPES)},
        "overwrite":   {"type": "boolean", "default": False},
        # scope=project|user bypasses every promotion gate (task->session
        # ->project->user). Only an operator/wildcard caller may mint there,
        # and only with an explicit force=True (audited).
        "force":       {"type": "boolean", "default": False},
    },
}
#: Scopes a NAMESPACED persona may mint into directly. Anything higher must
#: be earned through skill_promote's grade gates (adversarial review D-05).
PERSONA_CREATE_SCOPES: tuple[str, ...] = ("task", "session")
#: Scopes that require operator/wildcard caller AND force=True on create.
GATED_CREATE_SCOPES: tuple[str, ...] = ("project", "user")
SKILL_PROMOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "to"],
    "properties": {
        "name":  {"type": "string"},
        "to":    {"type": "string", "enum": ["session", "project", "user"]},
        "force": {"type": "boolean", "default": False},
    },
}
SKILL_GRADE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "run_id", "score"],
    "properties": {
        "name":   {"type": "string"},
        "run_id": {"type": "string"},
        "score":  {"type": "number", "minimum": 0, "maximum": 1},
        "notes":  {"type": "string"},
    },
}
SKILL_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scope": {"type": "string", "enum": list(VALID_SCOPES)},
        "type":  {"type": "string", "enum": list(VALID_TYPES)},
    },
}
SKILL_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}
SKILL_PURGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "reason"],
    "properties": {
        "name":   {"type": "string"},
        "reason": {"type": "string"},
    },
}
SKILL_DIFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name":    {"type": "string"},
        "against": {"type": "string", "enum": list(VALID_SCOPES)},
    },
}


META_TOOLS: list[dict[str, Any]] = [
    {"name": "skill_create",  "description": "Register a new skill (markdown body, lint-checked).",  "inputSchema": SKILL_CREATE_SCHEMA},
    {"name": "skill_promote", "description": "Promote a skill to a higher scope (gated by grades).", "inputSchema": SKILL_PROMOTE_SCHEMA},
    {"name": "skill_grade",   "description": "Append a grade (run_id, score 0..1, optional notes).", "inputSchema": SKILL_GRADE_SCHEMA},
    {"name": "skill_list",    "description": "List skills with shadowing across scopes.",            "inputSchema": SKILL_LIST_SCHEMA},
    {"name": "skill_get",     "description": "Fetch a skill spec + full SKILL.md body.",             "inputSchema": SKILL_GET_SCHEMA},
    {"name": "skill_purge",   "description": "Delete a skill (logs reason in audit).",               "inputSchema": SKILL_PURGE_SCHEMA},
    {"name": "skill_diff",    "description": "Diff a skill body against the next-higher-scope copy.", "inputSchema": SKILL_DIFF_SCHEMA},
]


class SkillForgeMCPServer:
    def __init__(self, *, stdin=None, stdout=None, stderr=None):
        # Layer 9 — caller-persona namespace gate. The bridge adapter exports
        # CORVIN_CALLER_PERSONA per turn so any persona can opt into
        # skill-forge while only being able to register skills under its own
        # prefix. Empty / missing => unprivileged, NOT wildcard (LRN-1, below).
        # The persona_namespaces map is owned by forge.policy.Policy — both
        # plugins read the same source of truth so a coder may register both
        # ``code.foo`` (forge tool) and ``code.bar`` (skill-forge skill).
        # The env var is this subprocess's TRANSPORT for the persona; the
        # registry itself never reads it — it is handed the value explicitly
        # and enforces the same gate on every mutation (defence in depth).
        #
        # SKILL_FORGE_PERSONA is the name the cowork resolver puts on THIS
        # server's own env (resolver._inject_skill_forge_capability); it is the
        # caller persona too. Reading only CORVIN_CALLER_PERSONA left the console
        # web-chat worker (which the resolver wires, but which never exports
        # CORVIN_CALLER_PERSONA) a wildcard caller that could force-promote
        # across scopes and grade its own run ids as organic (review 2026-11-05
        # LRN-1). An ABSENT persona is NOT a wildcard: it is unprivileged and
        # owns no namespace (see _is_unprivileged_caller / _namespace_check).
        #
        # RL-5/RL-6 (refutation 2026-11-05): the wildcard is ONLY one of the
        # explicit launcher names (launch_record.WILDCARD_PERSONAS: ``skill-forge``,
        # ``forge``) AND only for a server the cowork resolver launched - its
        # CORVIN_SKILL_FORGE_LAUNCH nonce must match the resolver's record under
        # ``<tenant>/skill-forge/launches/``. Every other persona (``delegate``,
        # custom personas, a wildcard name without a record, e.g. a server a
        # worker started from Bash) is namespaced to its OWN name. Attribution,
        # not isolation: see launch_record's boundary note.
        self.caller_persona = (
            (os.environ.get("CORVIN_CALLER_PERSONA") or "").strip()
            or (os.environ.get("SKILL_FORGE_PERSONA") or "").strip()
        )
        self._launch_record = self._verify_launch(os.environ.get(launch_record.LAUNCH_ENV, ""))
        self._policy: Any = None  # lazy — resolved on first need
        self.multi = MultiSkillRegistry(caller_persona=self.caller_persona or None,
                                        policy=_ServerPolicy(self))
        self._stdin = stdin or sys.stdin
        if stdin is None:
            # MCP stdio is UTF-8. On Windows without Python UTF-8 mode sys.stdin decodes the
            # ANSI code page (cp1252): every non-ASCII tool argument arrived mojibaked.
            try:
                if (sys.stdin.encoding or "").replace("-", "").lower() != "utf8":
                    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - a non-TextIOWrapper stdin: leave it
                pass
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._stdout_lock = threading.Lock()
        self._initialized = False
        self._shutting_down = False

    def _verify_launch(self, value: str):
        """The resolver's launch record vouching for a wildcard persona, or None."""
        if self.caller_persona not in launch_record.WILDCARD_PERSONAS or not value:
            return None
        try:
            from forge.paths import tenant_home  # type: ignore  # validates the id
        except ImportError:
            return None
        path = launch_record.verify(value, self.caller_persona, tenant_home)
        if path is not None:
            import atexit  # noqa: PLC0415
            atexit.register(launch_record.release, path)
        return path

    def caller_namespace(self) -> str | None:
        """The prefix this caller may mutate: ``""`` = the operator's wildcard,
        ``None`` = no persona (owns nothing). A persona with a ``persona_namespaces``
        entry gets that prefix; a wildcard launcher name with a verified launch record
        gets ``""``; every other persona gets its own name (RL-5/RL-6), with every
        character a skill name cannot carry mapped to ``_`` (``my-persona`` →
        ``my_persona.``)."""
        if not self.caller_persona:
            return None
        policy = self._get_policy()
        prefix = policy.namespace_for(self.caller_persona) if policy not in (None, False) else None
        if prefix:
            return prefix
        if self.caller_persona in launch_record.WILDCARD_PERSONAS and self._launch_record is not None:
            return ""
        import re  # noqa: PLC0415
        return re.sub(r"[^A-Za-z0-9_]", "_", self.caller_persona)

    def _get_policy(self):
        """Lazy-load the bundle-default forge.policy.Policy. Returns None
        when the forge package is absent (standalone test setups) — the
        gate then falls back to wildcard, which is the safe legacy default."""
        if self._policy is not None:
            return self._policy
        try:
            # registry.py already prepends the forge package onto sys.path
            # when it imports forge.security_events; reuse that path.
            from forge.policy import Policy  # type: ignore
        except ImportError:
            self._policy = False  # sentinel: tried and failed
            return None
        # Use the forge workspace under the SAME scope_root the multi
        # registry will write to — that's where a per-deployment workspace
        # policy.json lives. We use scope=user as the conservative default
        # for the policy fetch (it's the highest scope and matches
        # plugin-wide defaults). Bundle defaults are always merged in.
        try:
            forge_scope_root = self.multi._root_for("user").parent / "forge"
            self._policy = Policy.load(forge_scope_root)
        except Exception:
            try:
                self._policy = Policy()  # bare instance + bundle merge
            except Exception:
                self._policy = False
                return None
        return self._policy

    def _namespace_check(self, name: str) -> tuple[bool, str]:
        """Returns (allowed, reason). Wildcard cases return (True, '').

        A caller with NO persona owns no namespace, so every mutation by name is
        refused (LRN-1): an unidentified caller is never the operator."""
        if not self.caller_persona:
            return False, (
                "namespace-gate: no caller persona (neither CORVIN_CALLER_PERSONA "
                "nor SKILL_FORGE_PERSONA is set); an unidentified caller may not "
                "mutate skills. The operator names a wildcard persona "
                "(e.g. 'skill-forge') explicitly."
            )
        prefix = self.caller_namespace()
        if prefix == "" or name == prefix or name.startswith(prefix + "."):
            return True, ""
        return False, (
            f"namespace-gate: persona {self.caller_persona!r} may only register skills "
            f"starting with {prefix + '.'!r}"
        )

    def _is_unprivileged_caller(self) -> bool:
        """True unless the caller is the operator's wildcard: a launcher persona
        (``skill-forge`` / ``forge``) whose launch the resolver recorded. Unprivileged
        callers may not force a promotion, mint into project/user, or write organic
        grades. Fail-closed: an absent persona, a namespaced persona, a custom or
        ``delegate`` persona and a self-started server are unprivileged (LRN-1, RL-5/6)."""
        return self.caller_namespace() != ""

    def _deny_if_outside_namespace(self, msgid: Any, name: str, *, tool: str) -> bool:
        """Layer 9 namespace gate for EVERY mutating tool.

        Returns True when the call was denied (envelope already sent). The gate
        used to apply to skill_create only, so a ``coder`` persona could
        zero-grade or purge ``assistant.*`` skills (adversarial review D-04).
        """
        if not isinstance(name, str) or not name:
            return False
        allowed, reason = self._namespace_check(name)
        if allowed:
            return False
        allowed_prefix = self.caller_namespace()
        self._emit_audit_event(
            "skill.namespace_denied",
            tool=name,
            details={
                "reason": reason,
                "operation": tool,
                "caller_persona": self.caller_persona,
                "allowed_prefix": allowed_prefix,
            },
        )
        self._tool_envelope(msgid, ok=False, error=reason)
        return True

    # -- transport ---------------------------------------------------------

    def _send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        with self._stdout_lock:
            self._stdout.write(line)
            self._stdout.flush()

    def _respond(self, msgid: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": msgid, "result": result})

    def _error(self, msgid: Any, code: int, message: str,
               data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"jsonrpc": "2.0", "id": msgid, "error": err})

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _log(self, *args: Any) -> None:
        print(*args, file=self._stderr, flush=True)

    # -- main loop ---------------------------------------------------------

    def serve(self) -> int:
        for raw in self._stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self._error(None, PARSE_ERROR, "parse error")
                continue
            try:
                self._dispatch(msg)
            except Exception as e:
                self._log("server: unhandled", repr(e))
                self._log(traceback.format_exc())
                msgid = msg.get("id") if isinstance(msg, dict) else None
                self._error(msgid, INTERNAL_ERROR, f"internal error: {e}")
            if self._shutting_down:
                break
        return 0

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._error(msg.get("id"), INVALID_REQUEST, "invalid request")
            return
        method = msg.get("method")
        msgid = msg.get("id")
        params = msg.get("params") or {}
        is_notification = "id" not in msg

        if method == "initialize":
            self._handle_initialize(msgid, params)
        elif method == "notifications/initialized":
            self._initialized = True
        elif method == "tools/list":
            self._respond(msgid, {"tools": META_TOOLS})
        elif method == "tools/call":
            self._handle_tools_call(msgid, params)
        elif method == "shutdown":
            self._respond(msgid, None)
            self._shutting_down = True
        elif method == "ping":
            self._respond(msgid, {})
        elif is_notification:
            return
        else:
            self._error(msgid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _handle_initialize(self, msgid: Any, params: dict) -> None:
        client_info = params.get("clientInfo", {})
        self._log(f"initialize from {client_info.get('name', '?')}")
        self._respond(
            msgid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    # -- tools/call --------------------------------------------------------

    def _handle_tools_call(self, msgid: Any, params: dict) -> None:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str):
            self._error(msgid, INVALID_PARAMS, "missing tool name")
            return
        try:
            handler = getattr(self, f"_call_{name}", None)
            if handler is None:
                self._tool_error(msgid, f"unknown skill-forge tool: {name}")
                return
            handler(msgid, args)
        except LinterError as e:
            self._tool_envelope(msgid, ok=False,
                                error="linter rejected: " + "; ".join(e.violations))
        except PromotionGateError as e:
            self._tool_envelope(msgid, ok=False,
                                error="promotion gate: " + str(e))
        except (KeyError, ValueError, FileExistsError) as e:
            self._tool_envelope(msgid, ok=False, error=f"{type(e).__name__}: {e}")

    def _emit_audit_event(self, event_type: str, *, tool: str = "",
                          details: dict | None = None) -> None:
        """Append a security event to the unified audit chain (shared with
        forge). Best-effort — silent on failure to avoid hiding the original
        operation. Mirrors the registry._audit pattern but is reachable
        from the MCP server when no SkillSpec exists yet (e.g. denial)."""
        try:
            from forge.security_events import write_event  # type: ignore
        except ImportError:
            return
        try:
            audit_path = self.multi.audit_path()
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            write_event(
                audit_path, event_type,
                tool=tool, details=details or {},
                hash_chain=True,
            )
        except OSError:
            pass

    def _call_skill_create(self, msgid: Any, args: dict) -> None:
        # Layer 9 — namespace gate. Coder may register code.* skills, browser
        # may register browser.* skills, etc. A NAMED persona missing from the
        # policy is the wildcard; NO persona at all is refused (LRN-1).
        name = args.get("name", "")
        if self._deny_if_outside_namespace(msgid, name, tool="skill_create"):
            return
        # Scope gate — minting straight into project/user skips every
        # promotion gate (D-05). Personas: task/session only. Operator /
        # wildcard: project/user need an explicit, audited force=True.
        ws_scope = args.get("scope") or detect_scope()
        force = bool(args.get("force", False))
        if ws_scope in GATED_CREATE_SCOPES:
            if self._is_unprivileged_caller():
                reason = (
                    f"scope-gate: persona {self.caller_persona!r} may only "
                    f"create skills in {PERSONA_CREATE_SCOPES}; earn "
                    f"{ws_scope!r} through skill_promote"
                )
                self._emit_audit_event(
                    "skill.scope_denied", tool=name,
                    details={"reason": reason, "operation": "skill_create",
                             "requested_scope": ws_scope,
                             "caller_persona": self.caller_persona},
                )
                self._tool_envelope(msgid, ok=False, error=reason)
                return
            if not force:
                reason = (
                    f"scope-gate: creating directly in scope {ws_scope!r} "
                    f"bypasses the promotion gates; pass force=True "
                    f"(operator-only, audited) or create in task/session"
                )
                self._emit_audit_event(
                    "skill.scope_denied", tool=name,
                    details={"reason": reason, "operation": "skill_create",
                             "requested_scope": ws_scope,
                             "caller_persona": self.caller_persona},
                )
                self._tool_envelope(msgid, ok=False, error=reason)
                return
        spec = self.multi.create(
            scope=ws_scope,
            name=args.get("name", ""),
            type=args.get("type", ""),
            body_md=args.get("body_md", ""),
            description=args.get("description", ""),
            claim=args.get("claim") or {},
            overwrite=bool(args.get("overwrite", False)),
        )
        if ws_scope in GATED_CREATE_SCOPES:
            self._emit_audit_event(
                "skill.create_forced_scope", tool=spec.name,
                details={"scope": ws_scope, "force": True,
                         "caller_persona": self.caller_persona,
                         "sha": spec.sha256},
            )
        # Bootstrap-grade seed (CONCEPT-0001 amendment): a skill just minted
        # via this MCP tool has n_grades == 0, and skill_inject.py's
        # injection gate (`n_grades < 1 or mean_score <= 0` -> skip) has no
        # organic path to a first grade for a skill nobody has used yet — it
        # would sit registered-but-invisible forever unless a caller
        # remembers a separate skill_grade follow-up call. Mirrors what
        # registry_bridge.py's promote_to_registry() already does for the
        # console Skill-Creator path. Fail-closed logging only: a failed
        # grade must not fail the registration itself, but the skill stays
        # non-injectable until graded, and the response says so.
        graded = False
        try:
            graded_spec = self.multi.grade(
                spec.name, run_id="skill-forge-mcp-bootstrap",
                score=BOOTSTRAP_GRADE, notes=BOOTSTRAP_NOTES, organic=False,
            )
            spec = graded_spec
            graded = True
        except Exception as exc:  # noqa: BLE001
            self._emit_audit_event(
                "skill.bootstrap_grade_failed", tool=spec.name,
                details={"scope": spec.scope, "error": str(exc)},
            )
        self._tool_envelope(msgid, ok=True, data={
            "ok": True, "sha": spec.sha256, "scope": spec.scope,
            "name": spec.name,
            "path": str((self.multi._root_for(spec.scope)
                         / "skills" / spec.name).resolve()),
            "bootstrap_graded": graded,
            "injectable": graded,
        })
        self._notify("notifications/tools/list_changed")
        # UAH: register chat-initiated skill creation in the activity feed.
        if _uah_write is not None:
            try:
                _uah_write(
                    action="skill.create",
                    panel="skills",
                    entity_id=spec.name,
                    summary=args.get("description", spec.name)[:200],
                    extra={"scope": spec.scope},
                )
            except Exception:
                pass

    def _call_skill_promote(self, msgid: Any, args: dict) -> None:
        name = args.get("name", "")
        if self._deny_if_outside_namespace(msgid, name, tool="skill_promote"):
            return
        to = args.get("to", "")
        force = bool(args.get("force", False))
        from_scope = self.multi.find_scope(name)
        if from_scope is None:
            self._tool_envelope(msgid, ok=False, error=f"unknown skill: {name}")
            return
        # force=True waives every promotion gate (an undefined path such as
        # session->user passes on force alone). It is the operator's override,
        # exactly like force on a project/user create: a namespaced persona
        # must EARN higher scopes through the grade gates (D-05, review
        # 2026-11-05 LR-1 — the create gate was routed around through promote).
        if force and self._is_unprivileged_caller():
            reason = (
                f"scope-gate: persona {self.caller_persona!r} may not pass "
                f"force=True to skill_promote; earn {to!r} through the grade gates"
            )
            self._emit_audit_event(
                "skill.scope_denied", tool=name,
                details={"reason": reason, "operation": "skill_promote",
                         "requested_scope": to,
                         "caller_persona": self.caller_persona},
            )
            self._tool_envelope(msgid, ok=False, error=reason)
            return
        spec = self.multi.promote(name, to=to, force=force)
        self._tool_envelope(msgid, ok=True, data={
            "ok": True, "from": from_scope, "to": to,
            "name": spec.name,
        })
        self._notify("notifications/tools/list_changed")

    def _call_skill_grade(self, msgid: Any, args: dict) -> None:
        name = args.get("name", "")
        # The persona that started an ACS run may grade the skills it forged (the
        # registry decides: ``owns_acs_skill``); every other cross-namespace grade is
        # still refused here and again inside the registry.
        if not self.multi.owns_acs_skill(name) and \
                self._deny_if_outside_namespace(msgid, name, tool="skill_grade"):
            return
        # A NAMESPACED persona grading through this tool is the graded party
        # grading itself: its run_id is a string it chose, not evidence of a
        # run, so the grade is a self-award and the registry clamps it to
        # AUTO_GRADE_CAP_MAX (review 2026-11-05 LR-2: three made-up run ids at
        # 1.0 lifted a persona's own skill over the session->project bar). The
        # organic quality signal is the operator's follow-up
        # (skill_inject.grade_from_user_followup). Only a NAMED wildcard
        # (operator) caller keeps the run_id-backed organic grade; an absent
        # persona never does (LRN-1).
        run_id = str(args.get("run_id", "") or "")
        spec = self.multi.grade(
            name=name,
            run_id=run_id,
            score=float(args.get("score", 0.0)),
            notes=args.get("notes", ""),
            organic=bool(run_id) and not self._is_unprivileged_caller(),
        )
        self._tool_envelope(msgid, ok=True, data={
            "ok": True, "n_grades": spec.n_grades,
            "mean_score": spec.mean_score,
        })

    def _call_skill_list(self, msgid: Any, args: dict) -> None:
        scope_filter = args.get("scope")
        type_filter = args.get("type")
        out = []
        import time as _t
        now = _t.time()
        for ws_scope, spec in self.multi.list_with_scope():
            if scope_filter and ws_scope != scope_filter:
                continue
            if type_filter and spec.type != type_filter:
                continue
            out.append({
                "name": spec.name,
                "scope": ws_scope,
                "type": spec.type,
                "n_grades": spec.n_grades,
                "mean_score": spec.mean_score,
                "age_days": (now - spec.created_at) / 86400.0,
            })
        self._tool_envelope(msgid, ok=True, data={"ok": True, "skills": out})

    def _call_skill_get(self, msgid: Any, args: dict) -> None:
        name = args.get("name", "")
        spec = self.multi.get(name)
        if spec is None:
            self._tool_envelope(msgid, ok=False, error=f"unknown skill: {name}")
            return
        body = self.multi.get_body(name) or ""
        self._tool_envelope(msgid, ok=True, data={
            "ok": True, "spec": asdict(spec), "body_md": body,
        })

    def _call_skill_purge(self, msgid: Any, args: dict) -> None:
        name = args.get("name", "")
        if self._deny_if_outside_namespace(msgid, name, tool="skill_purge"):
            return
        reason = args.get("reason", "")
        ok = self.multi.delete(name, reason=reason)
        if not ok:
            self._tool_envelope(msgid, ok=False, error=f"unknown skill: {name}")
            return
        self._tool_envelope(msgid, ok=True, data={"ok": True})
        self._notify("notifications/tools/list_changed")

    def _call_skill_diff(self, msgid: Any, args: dict) -> None:
        name = args.get("name", "")
        cur_scope = self.multi.find_scope(name)
        if cur_scope is None:
            self._tool_envelope(msgid, ok=False, error=f"unknown skill: {name}")
            return
        cur = self.multi.get_in_scope(name, cur_scope)
        cur_body = (
            self.multi._registry(cur_scope).get_body(name) or ""
        )
        # find the next-higher scope copy (or explicit 'against')
        against = args.get("against")
        if against is None:
            target_scope = None
            for s in VALID_SCOPES:
                if s == cur_scope:
                    break
                if self.multi.get_in_scope(name, s):
                    target_scope = s
            against = target_scope
        if against is None:
            self._tool_envelope(msgid, ok=True, data={
                "ok": True, "diff": "",
                "note": "no other-scope copy to diff against",
            })
            return
        other_body = self.multi._registry(against).get_body(name) or ""
        diff = "\n".join(difflib.unified_diff(
            other_body.splitlines(),
            cur_body.splitlines(),
            fromfile=f"{name}@{against}",
            tofile=f"{name}@{cur_scope}",
            lineterm="",
        ))
        self._tool_envelope(msgid, ok=True, data={
            "ok": True, "diff": diff,
            "from_scope": against, "to_scope": cur_scope,
        })

    # -- response helpers --------------------------------------------------

    def _tool_envelope(
        self, msgid: Any, *, ok: bool,
        data: dict | None = None, error: str | None = None,
    ) -> None:
        envelope: dict[str, Any] = {"ok": ok}
        if data is not None:
            envelope["data"] = data
        if error is not None:
            envelope["error"] = error
        text = json.dumps(envelope, indent=2)
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "isError": not ok,
            "structuredContent": envelope,
        }
        self._respond(msgid, result)

    def _tool_error(self, msgid: Any, text: str) -> None:
        self._tool_envelope(msgid, ok=False, error=text)


class _ServerPolicy:
    """The server's persona rule handed to the registry, so its defence-in-depth gate
    applies the SAME namespaces (own-name fallback, recorded wildcard) as the server."""

    def __init__(self, server: "SkillForgeMCPServer"):
        self._server = server

    def namespace_for(self, persona: str | None) -> str | None:
        prefix = self._server.caller_namespace()
        return prefix or None

    def namespace_check(self, persona: str | None, name: str) -> tuple[bool, str]:
        return self._server._namespace_check(name)


def main() -> int:
    return SkillForgeMCPServer().serve()


if __name__ == "__main__":
    raise SystemExit(main())
