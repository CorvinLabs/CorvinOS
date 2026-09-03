"""Layer 9 — namespace gate for skill-forge.

Mirrors operator/forge/tests/test_namespace_gate.py for SkillForge:

  1. ``coder`` may register ``code.review_checklist``.
  2. ``coder`` may NOT register ``inbox.foo`` — error envelope, audit
     event ``skill.namespace_denied`` fires.
  3. Missing CORVIN_CALLER_PERSONA → wildcard, any name works.
  4. Persona not in policy.persona_namespaces → wildcard.
  5. Successful skill_create / skill_grade / skill_promote audit events
     carry ``details.caller_persona``.
  6. (D-04) the gate applies to EVERY mutating tool: ``coder`` can neither
     grade nor purge nor promote ``assistant.victim``.
  7. (D-05) scope gate on create: a namespaced persona may only mint into
     task/session; a wildcard caller needs force=True for project/user and
     the forced create is audited.

Audit events land in the TENANT CORE CHAIN
(``<CORVIN_HOME>/tenants/_default/global/forge/audit.jsonl``).

The skill-forge MCP server reuses ``forge.policy.Policy`` for the
namespace map (the bundle policy.json owns the source of truth).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_FORGE = REPO_ROOT / "operator" / "skill-forge"
FORGE = REPO_ROOT / "operator" / "forge"

# Sandbox the plugin-slot mirror so this test never touches the real
# operator/skill-forge/skills/dyn/ tree (test_registry.py does the same).
_SLOT_TMP = tempfile.mkdtemp(prefix="sf-ns-gate-slot-")
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT_TMP

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


GOOD_BODY = (
    "# code.review_checklist\n\n"
    "Five-step review pass: behaviour test first, structural smell, naming "
    "consistency, doc-as-DOD reminder, and a final read-through for any "
    "left-over scaffolding.\n"
)


# -- minimal stdio MCP client ------------------------------------------------

class _SFMCPClient:
    """Drives skill_forge.mcp_server over stdio with a controlled env."""

    def __init__(self, *, corvin_home: Path,
                 caller_persona: str | None = None) -> None:
        env = dict(os.environ)
        env.pop("CORVIN_CALLER_PERSONA", None)
        env.pop("SKILL_FORGE_PERSONA", None)
        if caller_persona:
            env["CORVIN_CALLER_PERSONA"] = caller_persona
        env["CORVIN_HOME"] = str(corvin_home)
        env["CORVIN_FORCE_SCOPE"] = "user"
        env["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT_TMP
        # Both packages need to be importable:
        env["PYTHONPATH"] = (
            f"{SKILL_FORGE}{os.pathsep}{FORGE}"
            + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "skill_forge.mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
        )
        self._next_id = 0
        self._buffered: list[dict] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self._buffered.append(msg)

    def request(self, method: str, params=None, *, timeout: float = 5.0):
        self._next_id += 1
        msgid = self._next_id
        msg = {"jsonrpc": "2.0", "id": msgid, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for i, m in enumerate(self._buffered):
                    if m.get("id") == msgid:
                        return self._buffered.pop(i)
            time.sleep(0.01)
        raise TimeoutError(f"no response to {method}")

    def initialize(self) -> dict:
        resp = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sf-ns-test", "version": "0.0"},
        })
        # notifications/initialized — fire-and-forget
        self.proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + "\n")
        self.proc.stdin.flush()
        return resp

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def _audit_events(corvin_home: Path) -> list[dict]:
    # Every SkillForge event lands in the tenant core chain (D-07a).
    paths = [
        corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl",
    ]
    out: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _create_args(name: str, scope: str = "session", **extra) -> dict:
    # session: the highest scope a namespaced persona may mint into (D-05).
    args = {
        "name": name, "type": "domain",
        "description": "ns-gate test skill",
        "body_md": GOOD_BODY, "scope": scope,
        "claim": {"predicted_delta_loss": 0.1},
    }
    args.update(extra)
    return args


def _call(client: _SFMCPClient, tool: str, args: dict) -> dict:
    return client.request("tools/call", {"name": tool, "arguments": args})["result"]


# -- Case 1: coder may register code.review_checklist ------------------------

def test_coder_can_register_in_namespace():
    print("\n[case 1: coder may register code.review_checklist]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-1-"))
    try:
        client = _SFMCPClient(corvin_home=sandbox, caller_persona="coder")
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "skill_create",
                                "arguments": _create_args(
                                    "code.review_checklist")})
            ok = r["result"].get("isError") is False
            t("skill_create returned non-error", ok,
              detail=("" if ok else r["result"]["content"][0]["text"]))
            envelope = r["result"].get("structuredContent", {})
            t("envelope ok=true",
              envelope.get("ok") is True)
        finally:
            client.close()
        events = _audit_events(sandbox)
        creates = [e for e in events
                   if e["event_type"] == "skill.create"
                   and e.get("tool") == "code.review_checklist"]
        t("skill.create event recorded", len(creates) == 1)
        assert len(creates) == 1, events
        if creates:
            t("audit details.caller_persona == 'coder'",
              creates[0].get("details", {}).get("caller_persona") == "coder")
            assert creates[0]["details"]["caller_persona"] == "coder"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 2: coder may NOT register inbox.foo --------------------------------

def test_coder_blocked_outside_namespace():
    print("\n[case 2: coder may NOT register inbox.foo]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-2-"))
    try:
        client = _SFMCPClient(corvin_home=sandbox, caller_persona="coder")
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "skill_create",
                                "arguments": _create_args("inbox.foo")})
            t("skill_create returned isError",
              r["result"].get("isError") is True)
            text = r["result"]["content"][0]["text"]
            t("error mentions namespace-gate",
              "namespace-gate" in text or "namespace_gate" in text)
            t("error mentions persona name 'coder'",
              "coder" in text)
        finally:
            client.close()
        events = _audit_events(sandbox)
        denied = [e for e in events
                  if e["event_type"] == "skill.namespace_denied"]
        t("skill.namespace_denied audit event recorded",
          len(denied) >= 1)
        assert len(denied) >= 1, events
        if denied:
            d = denied[-1]
            t("denied event names skill=inbox.foo",
              d.get("tool") == "inbox.foo")
            t("denied event records caller_persona=coder",
              d.get("details", {}).get("caller_persona") == "coder")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 3: missing env → wildcard ------------------------------------------

def test_no_caller_persona_falls_back_to_wildcard():
    print("\n[case 3: no CORVIN_CALLER_PERSONA → any name works]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-3-"))
    try:
        client = _SFMCPClient(corvin_home=sandbox, caller_persona=None)
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "skill_create",
                                "arguments": _create_args("anything.goes")})
            ok = r["result"].get("isError") is False
            t("any name registers without CALLER_PERSONA", ok,
              detail=("" if ok else r["result"]["content"][0]["text"]))
        finally:
            client.close()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 4: unknown persona → wildcard --------------------------------------

def test_unknown_persona_falls_back_to_wildcard():
    print("\n[case 4: persona NOT in persona_namespaces → wildcard]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-4-"))
    try:
        client = _SFMCPClient(
            corvin_home=sandbox, caller_persona="totally-unknown-persona",
        )
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "skill_create",
                                "arguments": _create_args("freeform.thing")})
            ok = r["result"].get("isError") is False
            t("unknown persona acts as wildcard", ok,
              detail=("" if ok else r["result"]["content"][0]["text"]))
        finally:
            client.close()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 5: caller_persona threaded through grade + promote -----------------

def test_caller_persona_in_audit_details():
    print("\n[case 5: caller_persona present in skill.create + skill.grade]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-5-"))
    try:
        client = _SFMCPClient(corvin_home=sandbox, caller_persona="browser")
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "skill_create",
                                "arguments": _create_args("web.scrape")})
            t("skill_create ok",
              r["result"].get("isError") is False,
              detail=("" if r["result"].get("isError") is False
                      else r["result"]["content"][0]["text"]))
            r2 = client.request("tools/call",
                                {"name": "skill_grade",
                                 "arguments": {
                                     "name": "web.scrape",
                                     "run_id": "demo-1", "score": 0.7,
                                 }})
            t("skill_grade ok",
              r2["result"].get("isError") is False)
        finally:
            client.close()
        events = _audit_events(sandbox)
        creates = [e for e in events
                   if e["event_type"] == "skill.create"
                   and e.get("tool") == "web.scrape"]
        grades = [e for e in events
                  if e["event_type"] == "skill.grade"
                  and e.get("tool") == "web.scrape"]
        t("skill.create event present", len(creates) >= 1)
        t("skill.grade event present", len(grades) >= 1)
        assert creates and grades, events
        if creates:
            t("create.details.caller_persona == 'browser'",
              creates[0].get("details", {}).get("caller_persona") == "browser")
        if grades:
            t("grade.details.caller_persona == 'browser'",
              grades[0].get("details", {}).get("caller_persona") == "browser")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 6 (D-04): gate applies to grade / purge / promote -------------------

def test_coder_cannot_grade_purge_or_promote_foreign_skill():
    print("\n[case 6: coder cannot grade/purge/promote assistant.victim]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-6-"))
    try:
        # the victim is created by an unrestricted (wildcard) caller
        owner = _SFMCPClient(corvin_home=sandbox, caller_persona=None)
        try:
            owner.initialize()
            r = _call(owner, "skill_create", _create_args("assistant.victim"))
            assert r.get("isError") is False, r["content"][0]["text"]
            r = _call(owner, "skill_grade", {"name": "assistant.victim",
                                             "run_id": "good-1", "score": 0.9})
            assert r.get("isError") is False, r["content"][0]["text"]
        finally:
            owner.close()

        coder = _SFMCPClient(corvin_home=sandbox, caller_persona="coder")
        try:
            coder.initialize()
            for tool, args in (
                ("skill_grade", {"name": "assistant.victim", "run_id": "x", "score": 0.0}),
                ("skill_purge", {"name": "assistant.victim", "reason": "hostile"}),
                ("skill_promote", {"name": "assistant.victim", "to": "project"}),
            ):
                r = _call(coder, tool, args)
                assert r.get("isError") is True, f"{tool} must be denied: {r}"
                text = r["content"][0]["text"]
                assert "namespace-gate" in text and "coder" in text, text
            # the coder can still see it (read tools are not gated)
            r = _call(coder, "skill_get", {"name": "assistant.victim"})
            assert r.get("isError") is False
            spec = r["structuredContent"]["data"]["spec"]
            assert spec["grades"] == [{**spec["grades"][0]}] and spec["grades"][0]["score"] == 0.9
            assert len(spec["grades"]) == 1, "the zero-grade must not have landed"
        finally:
            coder.close()

        events = _audit_events(sandbox)
        denied = [e for e in events if e["event_type"] == "skill.namespace_denied"
                  and e.get("tool") == "assistant.victim"]
        assert {d["details"].get("operation") for d in denied} == \
            {"skill_grade", "skill_purge", "skill_promote"}, denied
        assert all(d["details"].get("caller_persona") == "coder" for d in denied)
        assert not [e for e in events if e["event_type"] == "skill.delete"]
        t("case 6", True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 7 (D-05): scope gate on create ---------------------------------------

def test_create_scope_gate():
    print("\n[case 7: persona may not mint into project/user; wildcard needs force]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-7-"))
    try:
        coder = _SFMCPClient(corvin_home=sandbox, caller_persona="coder")
        try:
            coder.initialize()
            for scope in ("project", "user"):
                r = _call(coder, "skill_create", _create_args("code.direct", scope=scope, force=True))
                assert r.get("isError") is True, (scope, r)
                assert "scope-gate" in r["content"][0]["text"]
            r = _call(coder, "skill_create", _create_args("code.direct", scope="session"))
            assert r.get("isError") is False, r["content"][0]["text"]
            assert r["structuredContent"]["data"]["scope"] == "session"
        finally:
            coder.close()

        operator = _SFMCPClient(corvin_home=sandbox, caller_persona=None)
        try:
            operator.initialize()
            r = _call(operator, "skill_create", _create_args("ops.direct_user", scope="user"))
            assert r.get("isError") is True, r
            assert "force=True" in r["content"][0]["text"]
            r = _call(operator, "skill_create", _create_args("ops.direct_user", scope="user", force=True))
            assert r.get("isError") is False, r["content"][0]["text"]
            assert r["structuredContent"]["data"]["scope"] == "user"
        finally:
            operator.close()

        events = _audit_events(sandbox)
        scope_denied = [e for e in events if e["event_type"] == "skill.scope_denied"]
        assert {e["tool"] for e in scope_denied} == {"code.direct", "ops.direct_user"}, scope_denied
        assert {e["details"]["requested_scope"] for e in scope_denied
                if e["tool"] == "code.direct"} == {"project", "user"}
        forced = [e for e in events if e["event_type"] == "skill.create_forced_scope"]
        assert len(forced) == 1 and forced[0]["tool"] == "ops.direct_user"
        assert forced[0]["details"]["scope"] == "user"
        created_user = [e for e in events if e["event_type"] == "skill.create"
                        and e["details"].get("scope") == "user"]
        assert [e["tool"] for e in created_user] == ["ops.direct_user"]
        t("case 7", True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def main() -> int:
    test_coder_can_register_in_namespace()
    test_coder_blocked_outside_namespace()
    test_no_caller_persona_falls_back_to_wildcard()
    test_unknown_persona_falls_back_to_wildcard()
    test_caller_persona_in_audit_details()
    test_coder_cannot_grade_purge_or_promote_foreign_skill()
    test_create_scope_gate()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
