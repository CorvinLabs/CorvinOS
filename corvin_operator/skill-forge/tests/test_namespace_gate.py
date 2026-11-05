"""Layer 9 — namespace gate for skill-forge.

Mirrors operator/forge/tests/test_namespace_gate.py for SkillForge:

  1. ``coder`` may register ``code.review_checklist``.
  2. ``coder`` may NOT register ``inbox.foo`` — error envelope, audit
     event ``skill.namespace_denied`` fires.
  3. Missing caller persona → refused (LRN-1: never a wildcard); the
     operator's wildcard is a launcher persona (``skill-forge``) whose launch the
     resolver recorded (RL-5, ``skill_forge/launch_record.py``).
  4. Persona not in policy.persona_namespaces → namespaced to its OWN name
     (RL-6: ``delegate`` and custom personas were wildcards until 2026-11-05).
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
SKILL_FORGE = REPO_ROOT / "corvin_operator" / "skill-forge"
FORGE = REPO_ROOT / "corvin_operator" / "forge"
if str(SKILL_FORGE) not in sys.path:
    sys.path.insert(0, str(SKILL_FORGE))
from skill_forge import launch_record  # noqa: E402

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


_MEMBER_TIER_SERVER_BOOT = (
    "import runpy\n"
    "from corvin_operator.license import capability_api as _ca\n"
    "_ca.active_tier = lambda **_k: 'member'\n"
    "runpy.run_module('skill_forge.mcp_server', run_name='__main__')\n"
)


# -- minimal stdio MCP client ------------------------------------------------

class _SFMCPClient:
    """Drives skill_forge.mcp_server over stdio with a controlled env."""

    def __init__(self, *, corvin_home: Path,
                 caller_persona: str | None = None,
                 launched: bool | None = None,
                 launch_env: str | None = None) -> None:
        env = dict(os.environ)
        env.pop("CORVIN_CALLER_PERSONA", None)
        env.pop("SKILL_FORGE_PERSONA", None)
        env.pop(launch_record.LAUNCH_ENV, None)
        if caller_persona:
            env["CORVIN_CALLER_PERSONA"] = caller_persona
        # A wildcard launcher persona is the operator only for a server the resolver
        # launched (RL-5): write the record the resolver writes, put its nonce in env.
        if launched is None:
            launched = caller_persona in launch_record.WILDCARD_PERSONAS
        env["CORVIN_TENANT_ID"] = "_default"
        self.launch_value = ""
        if launched and caller_persona:
            self.launch_value = launch_record.issue(
                corvin_home / "tenants" / "_default", "_default", caller_persona)
            env[launch_record.LAUNCH_ENV] = self.launch_value
        elif launch_env is not None:
            env[launch_record.LAUNCH_ENV] = launch_env  # a value the caller chose (forged / foreign)
        env["CORVIN_HOME"] = str(corvin_home)
        env["CORVIN_FORCE_SCOPE"] = "user"
        env["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT_TMP
        # Both packages need to be importable:
        env["PYTHONPATH"] = (
            f"{SKILL_FORGE}{os.pathsep}{FORGE}"
            + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        )
        # Skill authoring is licence-gated (ADR-0701 G2, member tier,
        # fail-closed). The server runs in a subprocess, so the conftest's
        # in-process tier pin does not reach it: pin the tier resolver in
        # the child the same way (the gate itself still runs for real).
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _MEMBER_TIER_SERVER_BOOT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env, encoding="utf-8", errors="replace",
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
        for line in p.read_text(encoding="utf-8").splitlines():
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


# -- Case 3: missing persona → refused; named wildcard → any name ------------

OPERATOR = "skill-forge"  # launcher persona; the wildcard only with a launch record (RL-5)


def test_no_caller_persona_is_refused_not_wildcard():
    print("\n[case 3: no caller persona → every mutation refused (LRN-1)]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-3-"))
    try:
        client = _SFMCPClient(corvin_home=sandbox, caller_persona=None)
        try:
            client.initialize()
            for tool, args in (
                ("skill_create", _create_args("anything.goes")),
                ("skill_grade", {"name": "anything.goes", "run_id": "r", "score": 1.0}),
                ("skill_promote", {"name": "anything.goes", "to": "user", "force": True}),
                ("skill_purge", {"name": "anything.goes", "reason": "x"}),
            ):
                r = _call(client, tool, args)
                assert r.get("isError") is True, (tool, r)
                assert "no caller persona" in r["content"][0]["text"], (tool, r)
            t("absent persona refused on every mutating tool", True)
        finally:
            client.close()
        client = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR)
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "skill_create",
                                "arguments": _create_args("anything.goes")})
            ok = r["result"].get("isError") is False
            t("named wildcard persona registers any name", ok,
              detail=("" if ok else r["result"]["content"][0]["text"]))
            assert ok, r
        finally:
            client.close()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 4: unknown persona → its OWN namespace (RL-6) -----------------------

def test_unknown_persona_is_namespaced_to_its_own_name():
    print("\n[case 4: persona NOT in persona_namespaces → own-name namespace (RL-6)]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-4-"))
    try:
        client = _SFMCPClient(
            corvin_home=sandbox, caller_persona="totally-unknown-persona",
        )
        try:
            client.initialize()
            r = _call(client, "skill_create", _create_args("freeform.thing"))
            assert r.get("isError") is True, r
            assert "totally_unknown_persona." in r["content"][0]["text"], r
            r = _call(client, "skill_create", _create_args("totally_unknown_persona.thing"))
            assert r.get("isError") is False, r["content"][0]["text"]
            t("unknown persona is namespaced to its own name", True)
        finally:
            client.close()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# -- Case 8 (RL-5/RL-6): who is the operator's wildcard ------------------------

def _force_promote(client: _SFMCPClient, name: str) -> dict:
    r = _call(client, "skill_create", _create_args(name, scope="session"))
    assert r.get("isError") is False, r["content"][0]["text"]
    return _call(client, "skill_promote", {"name": name, "to": "user", "force": True})


def test_rl6_delegate_and_custom_personas_are_not_wildcards():
    print("\n[case 8a: delegate / custom persona cannot force-promote (RL-6)]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-8a-"))
    try:
        for persona in ("delegate", "my-custom-persona"):
            c = _SFMCPClient(corvin_home=sandbox, caller_persona=persona)
            try:
                c.initialize()
                r = _call(c, "skill_create", _create_args("zz.rl_skill"))
                assert r.get("isError") is True and "namespace-gate" in r["content"][0]["text"], r
                r = _force_promote(c, persona.replace("-", "_") + ".rl_skill")
                assert r.get("isError") is True, (persona, r)
            finally:
                c.close()
        t("delegate / custom personas namespaced, no force-promote", True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_rl5_wildcard_name_without_a_launch_record_is_namespaced():
    print("\n[case 8b: CORVIN_CALLER_PERSONA=skill-forge without a record (RL-5)]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-8b-"))
    try:
        # the self-started server a worker could launch from Bash: no record
        c = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR, launched=False)
        try:
            c.initialize()
            r = _call(c, "skill_create", _create_args("anything.goes"))
            assert r.get("isError") is True, r
            r = _force_promote(c, "skill_forge.self_started")
            assert r.get("isError") is True, r
        finally:
            c.close()
        # a forged nonce (no record behind it) vouches for nothing either
        env_value = "_default:" + "0" * 32
        c = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR,
                          launched=False, launch_env=env_value)
        try:
            c.initialize()
            r = _call(c, "skill_create", _create_args("anything.goes"))
            assert r.get("isError") is True, r
        finally:
            c.close()
        # a record written for ANOTHER persona does not vouch for skill-forge
        value = launch_record.issue(sandbox / "tenants" / "_default", "_default", "forge")
        c = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR,
                          launched=False, launch_env=value)
        try:
            c.initialize()
            r = _call(c, "skill_create", _create_args("anything.goes"))
            assert r.get("isError") is True, r
        finally:
            c.close()
        t("wildcard name without a matching record is namespaced", True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_rl5_recorded_launch_is_the_wildcard_and_its_record_is_removed_at_exit():
    print("\n[case 8c: resolver-recorded launch = wildcard; record removed at exit (RL-5)]")
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-8c-"))
    try:
        c = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR)
        launches = launch_record.launches_dir(sandbox / "tenants" / "_default")
        try:
            c.initialize()
            assert len(list(launches.glob("*.json"))) == 1
            r = _force_promote(c, "anything.recorded")
            assert r.get("isError") is False, r["content"][0]["text"]
        finally:
            c.close()
        assert not list(launches.glob("*.json")), "the launch record outlived its server"
        t("recorded launch is the wildcard; record removed at exit", True)
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
        owner = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR)
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
            # skill_create now auto-seeds a bootstrap grade (registration-chain
            # gap fix, 2026-09-28 — see CONCEPT-0001's fifth-pass amendment):
            # grades[0] is that capped 0.3 self-seed, grades[1] is the owner's
            # real "good-1"/0.9 grade from setup above. The substantive
            # assertion is unchanged: the coder's denied zero-grade attempt
            # must not have landed as a THIRD entry.
            assert len(spec["grades"]) == 2, "the zero-grade must not have landed"
            assert spec["grades"][0]["run_id"] == "skill-forge-mcp-bootstrap"
            assert spec["grades"][0]["score"] == 0.3
            assert spec["grades"][1]["run_id"] == "good-1"
            assert spec["grades"][1]["score"] == 0.9
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

        operator = _SFMCPClient(corvin_home=sandbox, caller_persona=OPERATOR)
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


def test_rl5_the_resolver_records_the_launch_the_server_honours():
    """The cowork resolver's record (written before the spawn) is what the real server
    verifies: the resolver's env value turns ``forge`` into the wildcard; a namespaced
    persona gets no record at all (RL-5)."""
    print("\n[case 8d: resolver-written launch record → real server wildcard (RL-5)]")
    import importlib.util  # noqa: PLC0415
    sandbox = Path(tempfile.mkdtemp(prefix="sf-ns-8d-"))
    saved = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(sandbox)
    os.environ["CORVIN_TENANT_ID"] = "_default"
    try:
        lib = REPO_ROOT / "corvin_operator" / "cowork" / "lib"
        spec = importlib.util.spec_from_file_location("_rl5_resolver", lib / "resolver.py")
        resolver = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(lib))
        try:
            spec.loader.exec_module(resolver)
        finally:
            sys.path.remove(str(lib))
        assert resolver._skill_forge_launch("coder") is None
        name, value = resolver._skill_forge_launch("forge")
        assert name == launch_record.LAUNCH_ENV and value.startswith("_default:")
        rec = launch_record.launches_dir(sandbox / "tenants" / "_default")
        assert len(list(rec.glob("*.json"))) == 1
        if os.name == "posix":
            assert (next(rec.glob("*.json")).stat().st_mode & 0o777) == 0o600
        c = _SFMCPClient(corvin_home=sandbox, caller_persona="forge",
                          launched=False, launch_env=value)
        try:
            c.initialize()
            r = _force_promote(c, "anything.via_resolver")
            assert r.get("isError") is False, r["content"][0]["text"]
        finally:
            c.close()
        t("resolver-recorded launch honoured by the real server", True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(sandbox, ignore_errors=True)


def main() -> int:
    test_coder_can_register_in_namespace()
    test_coder_blocked_outside_namespace()
    test_no_caller_persona_is_refused_not_wildcard()
    test_unknown_persona_is_namespaced_to_its_own_name()
    test_rl6_delegate_and_custom_personas_are_not_wildcards()
    test_rl5_wildcard_name_without_a_launch_record_is_namespaced()
    test_rl5_recorded_launch_is_the_wildcard_and_its_record_is_removed_at_exit()
    test_rl5_the_resolver_records_the_launch_the_server_honours()
    test_caller_persona_in_audit_details()
    test_coder_cannot_grade_purge_or_promote_foreign_skill()
    test_create_scope_gate()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
