"""Review 2026-11-19 — A2A findings A2A-12 / A2A-14.

* A2A-12 — a timed-out A2A worker still closes a PRICEABLE span (engine-reported
  model + four-way split), driven through the real ``spawn_a2a_worker`` and the
  real chain writer.
* A2A-14 — attachment digests are 64 hex chars; an undecodable inbound
  attachment rejects the task; the task-stage store is capped per origin; the
  delegate MCP config is never written through a symlink.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (_HERE, _REPO / "corvin_operator" / "forge", _REPO / "core" / "a2a",
           _REPO / "core" / "delegate"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _test_isolation  # noqa: E402,F401 — never the live audit chain
import a2a_attachments as att_mod  # noqa: E402
import a2a_worker  # noqa: E402
import spawn_gates  # noqa: E402

REPORTED = "claude-sonnet-5"
USAGE = {"input_tokens": 7, "output_tokens": 11,
         "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40}


class _Ev:
    def __init__(self, **kw):
        self.type = kw.get("type")
        self.text = kw.get("text", "")
        self.usage = kw.get("usage")
        self.error = kw.get("error")
        self.raw = kw.get("raw")


class _TimingOutEngine:
    """An engine that worked (model, a tool call, usage) and then ran out of time."""
    name = "claude_code"
    timed_out = False

    def __init__(self, calls: list):
        self._calls = calls

    def spawn(self, prompt, **kwargs):
        self._calls.append(kwargs)
        yield _Ev(type="session_started",
                  raw={"type": "system", "subtype": "init", "model": REPORTED})
        yield _Ev(type="tool_call", raw={"type": "assistant", "message": {"content": []}})
        yield _Ev(type="text_delta", text="partial")
        self.timed_out = True
        yield _Ev(type="error", error="claude stream timeout")
        yield _Ev(type="turn_completed", raw={"type": "result"}, usage=USAGE)

    def cancel(self):
        pass


class _OkEngine:
    name = "claude_code"

    def __init__(self, calls: list):
        self._calls = calls

    def spawn(self, prompt, **kwargs):
        self._calls.append(kwargs)
        yield _Ev(type="text_delta", text='{"status": "ok", "result": {}}')
        yield _Ev(type="turn_completed", raw={"type": "result"}, usage={})

    def cancel(self):
        pass


@pytest.fixture()
def worker_env(tmp_path, monkeypatch):
    chain = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setattr(spawn_gates, "check_l44", lambda *a, **kw: None)
    home = Path(getattr(a2a_worker, "_CORVIN_HOME_SNAPSHOT_A2A", "/nonexistent"))
    for name in ("compute_quota.json", ".compute_quota.json.lock"):
        try:
            (home / "global" / "license" / name).unlink()
        except OSError:
            pass
    return chain


def _worker_ends(chain: Path) -> list[dict]:
    if not chain.is_file():
        return []
    out = []
    for line in chain.read_text("utf-8").splitlines():
        rec = json.loads(line) if line.strip() else {}
        if rec.get("event_type") == "engine.span.end" and rec["details"].get("role") == "worker":
            out.append(rec["details"])
    return out


def _spawn(engine, **kw):
    return a2a_worker.spawn_a2a_worker(
        instruction=kw.pop("instruction", "summarise the attached notes"),
        origin_id="peer-1", task_id=kw.pop("task_id", "t-1119"), persona="assistant",
        ttl_s=30, engine_factory=lambda: engine, **kw)


def test_a_timed_out_worker_still_closes_a_priceable_span(worker_env):
    calls: list = []
    res = _spawn(_TimingOutEngine(calls))
    assert calls, f"the engine never ran ({res.status}: {res.error})"   # positive control
    assert res.status == "timeout"
    ends = _worker_ends(worker_env)
    assert len(ends) == 1, ends
    end = ends[0]
    assert end["status"] == "error"
    assert end["model_id"] == REPORTED
    assert (end["input_tokens"], end["output_tokens"],
            end["cache_read_tokens"], end["cache_write_tokens"]) == (7, 11, 300, 40)
    assert end["tool_call_count"] == 1
    # The signable usage block travels with the timeout result too.
    assert res.usage, res


def test_worker_timeout_carries_the_drained_result():
    eng = _TimingOutEngine([])
    with pytest.raises(TimeoutError) as info:
        a2a_worker._collect_within_ttl(eng, "p", {}, 30)
    result = info.value.result
    assert a2a_worker._span_facts(result)[0] == REPORTED
    assert result.usage == USAGE


# ── A2A-14: attachments ─────────────────────────────────────────────────────


def _att_dict(content: bytes = b"hello", **over) -> dict:
    d = {"name": "a.txt", "mime": "text/plain",
         "sha256": hashlib.sha256(content).hexdigest(),
         "content_b64": base64.b64encode(content).decode()}
    d.update(over)
    return d


def test_positive_control_a_well_formed_attachment_parses_and_decodes():
    att = att_mod.Attachment.from_dict(_att_dict())
    assert att.decode() == b"hello"
    upper = att_mod.Attachment.from_dict(_att_dict(sha256=hashlib.sha256(b"hello").hexdigest().upper()))
    assert upper.decode() == b"hello"


@pytest.mark.parametrize("bad", [
    "", "abc", "g" * 64, "a" * 63, "a" * 65, "Max Mustermann 1", None, 12, ["a" * 64],
    "a" * 64 + "\n", "é" * 64,
])
def test_a_digest_that_is_not_64_hex_is_refused_at_parse(bad):
    with pytest.raises(att_mod.AttachmentError):
        att_mod.Attachment.from_dict(_att_dict(sha256=bad))


def test_decode_refuses_a_malformed_digest_on_a_hand_built_attachment():
    att = att_mod.Attachment(name="a.txt", mime="text/plain", sha256="é" * 64,
                             content_b64=base64.b64encode(b"x").decode())
    with pytest.raises(att_mod.AttachmentError):
        att.decode()


def test_audit_projection_never_carries_free_text_as_a_digest_prefix():
    bad = att_mod.Attachment(name="a.txt", mime="text/plain", sha256="Max Mustermann 12",
                             content_b64="")
    good = att_mod.Attachment.from_dict(_att_dict())
    details = att_mod.attachments_audit_details([bad, good])
    assert details["attachment_sha_prefixes"] == ["invalid", good.sha256[:16]]


def test_an_undecodable_inbound_attachment_rejects_the_task(worker_env):
    calls: list = []
    good = att_mod.Attachment.from_dict(_att_dict(b"fine", name="ok.txt"))
    # Well-formed digest that does not match the content: decode() must raise.
    tampered = att_mod.Attachment.from_dict(
        _att_dict(b"fine", name="bad.txt", sha256=hashlib.sha256(b"other").hexdigest()))
    # The L34 attachment reader is not what is under test here — let it pass so
    # the workspace drop is reached.
    with mock.patch.object(a2a_worker, "_inbound_attachment_texts", lambda atts: []):
        res = _spawn(_OkEngine(calls), inbound_attachments=[good, tampered], task_id="t-att")
    assert res.status == "rejected", res
    assert res.reason_code == "attachments"
    assert calls == [], "the engine ran on a different input set than the peer sent"


def test_positive_control_decodable_attachments_reach_the_engine(worker_env):
    calls: list = []
    good = att_mod.Attachment.from_dict(_att_dict(b"fine", name="ok.txt"))
    res = _spawn(_OkEngine(calls), inbound_attachments=[good], task_id="t-att-ok")
    assert res.status == "ok", (res.status, res.error)
    assert len(calls) == 1


# ── A2A-14: task-stage store, per-origin cap ────────────────────────────────


@pytest.fixture()
def task_state(tmp_path, monkeypatch):
    import a2a_task_state as ts
    monkeypatch.setattr(ts, "_root", lambda tenant_id=None: tmp_path / "feed")
    monkeypatch.setattr(ts, "_mem", {})
    monkeypatch.setattr(ts, "_loaded_dirs", set())
    monkeypatch.setattr(ts, "_MAX_LIVE_PER_ORIGIN", 20)
    monkeypatch.setattr(ts, "_MAX_LIVE", 50)
    return ts


def test_one_origin_cannot_evict_another_origins_stages(task_state):
    ts = task_state
    for i in range(5):
        assert ts.record_stage("quiet", f"q{i}", "processing")
    for i in range(400):                                   # a flood from one peer
        ts.record_stage("loud", f"l{i}", "delivered")
    for i in range(5):
        assert ts.lookup("quiet", f"q{i}")["stage"] == "processing", i
    loud = [k for k in ts._mem if k[0] == "loud"]
    assert len(loud) == 20
    assert ts.lookup("loud", "l399")["stage"] == "delivered"     # newest kept
    assert ts.lookup("loud", "l0")["stage"] == "unknown"         # its own oldest gone


def test_the_global_cap_takes_from_the_biggest_origin(task_state):
    ts = task_state
    for o in ("a", "b", "c"):
        for i in range(20):
            ts.record_stage(o, f"{o}{i}", "delivered")
    ts.record_stage("small", "s0", "processing")
    assert len(ts._mem) <= 50
    assert ts.lookup("small", "s0")["stage"] == "processing"


def test_finished_tasks_are_evicted_before_running_ones(task_state):
    ts = task_state
    ts.record_stage("o", "running", "processing")
    for i in range(30):
        ts.record_stage("o", f"done{i}", "delivered")
        ts.record_stage("o", f"done{i}", "completed")
    assert ts.lookup("o", "running")["stage"] == "processing"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_compaction_never_leaves_a_wider_than_0600_file(task_state, tmp_path):
    ts = task_state
    ts.record_stage("o", "t1", "delivered")
    path = tmp_path / "feed" / ts._FILE
    old = os.umask(0)
    try:
        ts._compact(path)
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".task_state.")] == []


# ── A2A-14: delegate MCP config never follows a symlink ─────────────────────


def _spec():
    from corvin_delegate import mcp_config_builder as mcb
    return mcb, [mcb.McpServerSpec(name="forge", command="python3", args=["-m", "x"], env={})]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_opencode_config_is_not_written_through_a_planted_symlink(tmp_path):
    mcb, specs = _spec()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious", "utf-8")
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "opencode.json").symlink_to(victim)
    mcb.materialise_opencode_config(specs=specs, working_dir=wd)
    assert victim.read_text("utf-8") == "precious"
    out = wd / "opencode.json"
    assert not out.is_symlink() and "forge" in out.read_text("utf-8")
    assert out.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_claude_config_is_not_written_through_a_planted_symlink(tmp_path):
    mcb, specs = _spec()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious", "utf-8")
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "mcp_config.json").symlink_to(victim)
    out = mcb.materialise_claude_code_config(specs=specs, tempdir=wd)
    assert victim.read_text("utf-8") == "precious"
    assert not Path(out["mcp_config_path"]).is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_codex_home_symlink_is_refused(tmp_path):
    mcb, specs = _spec()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / ".codex_home").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(mcb.UnsafeConfigTarget):
        mcb.materialise_codex_config(specs=specs, tempdir=wd)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard links")
def test_a_hard_linked_config_target_is_refused(tmp_path):
    mcb, specs = _spec()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious", "utf-8")
    wd = tmp_path / "wd"
    wd.mkdir()
    os.link(victim, wd / "opencode.json")
    with pytest.raises(mcb.UnsafeConfigTarget):
        mcb.materialise_opencode_config(specs=specs, working_dir=wd)
    assert victim.read_text("utf-8") == "precious"


def test_positive_control_plain_config_write_still_works(tmp_path):
    mcb, specs = _spec()
    out = mcb.materialise_codex_config(specs=specs, tempdir=tmp_path)
    cfg = Path(out["CODEX_HOME"]) / "config.toml"
    assert "forge" in cfg.read_text("utf-8")
    # Rewriting over our own earlier file is fine.
    mcb.materialise_codex_config(specs=specs, tempdir=tmp_path)


# ── A2A-14: the lenient-attestation bypass is on the chain ──────────────────


def test_lenient_attestation_bypass_is_audited(tmp_path, monkeypatch):
    """The real ``RemoteTriggerReceiver._validate``: min_trust set, no CA key.
    Strict refuses (``ca_not_configured``); lenient lets the envelope past the
    attestation check AND records the bypass. (``receive()`` itself stops at
    the A2A licence gate in a test process, before validation — so the
    validation method is driven directly.)"""
    import secrets
    instance_attestation = pytest.importorskip("instance_attestation")
    import remote_trigger_receiver as rtr
    import test_adr0099_security_hardening as helpers

    key = secrets.token_hex(32)
    cfg = {"origin_id": "att-origin", "hmac_key": key, "recv_key": secrets.token_hex(32),
           "enabled": True, "min_trust": "VERIFIED"}
    p = tmp_path / "att-origin.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    p.chmod(0o600)
    monkeypatch.setattr(instance_attestation, "get_ca_pubkey_bytes", lambda: None)

    def _run(lenient: str):
        rec = helpers._make_receiver(tmp_path)
        seen: list = []
        monkeypatch.setattr(rec, "_audit_best_effort",
                            lambda et, sev, det: seen.append((et, sev, dict(det))))
        monkeypatch.setenv("CORVIN_ATTESTATION_LENIENT", lenient)
        env = helpers._build_valid_envelope(key, "att-origin")
        env["sender_attestation"] = {"dummy": True}
        # Re-sign: the attestation block is HMAC-covered.
        import hashlib
        import hmac as _hmac
        payload = {k: v for k, v in env.items() if k != "signature"}
        env["signature"] = _hmac.new(
            bytes.fromhex(key),
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode(), hashlib.sha256).hexdigest()
        error = ""
        try:
            rec._validate(env)
        except rtr.ValidationError as exc:
            error = str(exc)
        return error, [s for s in seen if s[0] == "A2A.attestation_disabled_bypass"]

    strict_error, strict_bypass = _run("0")
    assert "ca_not_configured" in strict_error, strict_error     # control: the check is reached
    assert strict_bypass == []
    lenient_error, bypass = _run("1")
    assert "ca_not_configured" not in lenient_error
    assert bypass == [("A2A.attestation_disabled_bypass", "WARNING",
                       {"origin_id": "att-origin", "reason": "CORVIN_ATTESTATION_LENIENT"})]


# ── R2-A2A-6: the worker's tool set is an ALLOW list, for every A2A worker ───

import a2a_tool_policy as _policy  # noqa: E402

_OUTSIDE_EVERY_CLASS = ("Skill", "EnterWorktree", "ExitWorktree", "SendMessage", "ToolSearch")


class _ArgvEngine:
    """The real ClaudeCodeEngine argv builder with a recording stand-in for the
    process start: what it captures is the argv the CLI would be started with."""

    def __new__(cls, sink: list):
        from agents.claude_code import ClaudeCodeEngine

        class _Rec(ClaudeCodeEngine):
            def spawn(self, prompt, **kwargs):  # type: ignore[no-untyped-def]
                build = {k: kwargs[k] for k in (
                    "system", "allowed_tools", "disallowed_tools", "add_dirs",
                    "extra_args", "model", "resume_session_id") if k in kwargs}
                sink.append(self._build_args(prompt, binary=self.binary, **build))
                yield _Ev(type="text_delta", text='{"status": "ok", "result": {}}')
                yield _Ev(type="turn_completed", raw={"type": "result"}, usage={})

        return _Rec(binary="/nonexistent/claude")


def _tools_arg(argv: list) -> "list[str] | None":
    hits = [a for a in argv if isinstance(a, str) and a.startswith("--tools=")]
    assert len(hits) <= 1, argv
    if not hits:
        return None
    value = hits[0][len("--tools="):]
    return [t for t in value.split(",") if t]


def _argv_for(worker_env, **grants) -> list:
    sink: list = []
    policy = _policy.derive_tool_policy(grants)
    res = a2a_worker.spawn_a2a_worker(
        instruction="summarise the attached notes", origin_id="peer-1", task_id="t-tools",
        persona="assistant", ttl_s=30, engine_factory=lambda: _ArgvEngine(sink),
        allowed_tools=policy.allowed_tools, disallowed_tools=policy.disallowed_tools)
    assert res.status == "ok", (res.status, res.error)
    assert len(sink) == 1
    return sink[0]


def test_an_origin_without_grants_gets_an_empty_tool_set(worker_env, monkeypatch):
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    argv = _argv_for(worker_env)
    assert _tools_arg(argv) == [], argv
    assert "--dangerously-skip-permissions" in argv       # why an allow list is needed
    assert "--disallowedTools" in argv                    # second line still there
    assert "--strict-mcp-config" in argv


def test_tools_outside_every_allow_class_never_reach_a_peer_worker(worker_env, monkeypatch):
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    monkeypatch.setattr(a2a_worker._acs_runtime_module(), "_engine_env_credential",
                        lambda env: {})
    full = dict(allow_bash=True, allow_network=True, allow_read_files=True,
                allow_write_files=True, allow_subagents=True)
    tools = _tools_arg(_argv_for(worker_env, **full))
    assert tools is not None, "--tools= must be set even for a fully granted origin"
    for name in _OUTSIDE_EVERY_CLASS:
        assert name not in tools, name
    # Positive control: the granted classes ARE exposed.
    for name in ("Bash", "WebFetch", "Read", "Write", "Edit", "Agent"):
        assert name in tools, name
    assert set(tools) <= set(_policy.GRANTABLE_TOOLS)


def test_a_grant_exposes_exactly_its_own_class(worker_env, monkeypatch):
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    monkeypatch.setattr(a2a_worker._acs_runtime_module(), "_engine_env_credential",
                        lambda env: {})
    tools = _tools_arg(_argv_for(worker_env, allow_read_files=True))
    assert set(tools) == {"Read", "Grep", "Glob"}, tools
    tools = _tools_arg(_argv_for(worker_env, allow_network=True))
    assert set(tools) == {"WebFetch", "WebSearch"}, tools
    # bash without network is refused by the policy -> no shell tool exposed.
    assert _tools_arg(_argv_for(worker_env, allow_bash=True)) == []


def test_an_origins_allowed_tools_narrows_the_set(worker_env, monkeypatch):
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    monkeypatch.setattr(a2a_worker._acs_runtime_module(), "_engine_env_credential",
                        lambda env: {})
    tools = _tools_arg(_argv_for(
        worker_env, allow_read_files=True, allow_network=True,
        allowed_tools=["Read", "Skill", "WebFetch"]))
    assert tools == ["WebFetch", "Read"] or set(tools) == {"Read", "WebFetch"}, tools
    assert "Skill" not in tools


def test_exposed_tools_is_the_one_derivation_and_fails_closed():
    assert _policy.exposed_tools(None, list(_policy.GRANTABLE_TOOLS)) == []
    assert "Skill" not in _policy.exposed_tools(None, None)
    assert _policy.exposed_tools(["Bash(git *)"], None) == ["Bash"]
    assert not (set(_policy.GRANTABLE_TOOLS) & _policy.LEGACY_TOOL_NAMES)
    # The receiver and the conversation turn hand the SAME lists to the worker.


def test_conversation_turn_and_receiver_end_in_the_same_tool_set(worker_env, monkeypatch, tmp_path):
    """The conversation's local turn goes through the same spawn function, so
    its argv carries the same allow list as an inbound task of that origin."""
    import sys as _sys
    repo = Path(__file__).resolve().parents[3]
    if str(repo) not in _sys.path:
        _sys.path.insert(0, str(repo))
    import core.federation.conversation as conv
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    monkeypatch.setattr(a2a_worker._acs_runtime_module(), "_engine_env_credential",
                        lambda env: {})
    origins = tmp_path / "origins"
    origins.mkdir()
    cfg = {"origin_id": "peer-1", "enabled": True, "hmac_key": "ab" * 32,
           "recv_key": "cd" * 32, "allow_read_files": True}
    (origins / "peer-1.json").write_text(json.dumps(cfg), "utf-8")
    (origins / "peer-1.json").chmod(0o600)
    monkeypatch.setenv("REMOTE_ORIGINS_DIR", str(origins))
    sink: list = []
    monkeypatch.setattr(conv, "_engine_factory", lambda: _ArgvEngine(sink))
    out = conv._run_local_turn(instruction="hello there", task_id="t-conv", model="",
                               origin_id="peer-1", tenant_id="_default")
    assert out["ok"], out
    conv_tools = _tools_arg(sink[0])
    recv_tools = _tools_arg(_argv_for(worker_env, **cfg))
    assert conv_tools is not None and set(conv_tools) == set(recv_tools) == {"Read", "Grep", "Glob"}


# ── honor_marker=False: a peer's [class:] label is content ───────────────────


def test_the_a2a_l34_block_ignores_a_peers_class_marker(worker_env, monkeypatch):
    import data_classification as dc
    seen: list[dict] = []
    real = dc.classify_task

    def _spy(task, *a, **kw):
        seen.append(dict(kw))
        return real(task, *a, **kw)

    monkeypatch.setattr(dc, "classify_task", _spy)

    class _Guard:
        detector_on = False
        route_malformed = False

        def validate(self, **kw):
            return type("D", (), {"allowed": True, "reason": ""})()

    monkeypatch.setattr(dc, "load_guard_for_tenant", lambda tid: _Guard())
    calls: list = []
    res = _spawn(_OkEngine(calls), instruction="[class:public] summarise the notes")
    assert res.status == "ok", (res.status, res.error)
    assert seen and all(kw.get("honor_marker") is False for kw in seen), seen


# ── R2-A2A-8: a non-object invite payload is an InviteError, never a 500 ─────


@pytest.mark.parametrize("payload", [
    b"5", b"null", b'"v iid oid url rp hk rk pa mt iat"', b"[1, 2]", b"true", b"1.5",
])
def test_a_non_object_invite_payload_is_an_invite_error(payload):
    import a2a_invite as inv
    token = inv.TOKEN_PREFIX + inv._b64_enc(payload) + "." + inv._b64_enc(b"x" * 32)
    with pytest.raises(inv.InviteError, match="not an object"):
        inv.parse_invite(token)


def test_positive_control_a_generated_invite_still_parses(tmp_path, monkeypatch):
    import a2a_invite as inv
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    _tok, token_str = inv.generate_invite(
        iid="iid-1", origin_id="origin-1", url="https://peer.example")
    parsed = inv.parse_invite(token_str)
    assert parsed[0].iid == "iid-1" and parsed[0].exp is not None


# ── task-state eviction never takes the entry it was called for ──────────────


def test_a_first_recorded_terminal_stage_survives_its_own_insert(task_state, monkeypatch):
    ts = task_state
    monkeypatch_cap = 5
    monkeypatch.setattr(ts, "_MAX_LIVE_PER_ORIGIN", monkeypatch_cap)
    for i in range(monkeypatch_cap):                 # the origin is full of RUNNING tasks
        assert ts.record_stage("o", f"run{i}", "processing")
    terminal = sorted(ts.TERMINAL)[0]
    assert ts.record_stage("o", "refused-at-once", terminal)
    assert ts.lookup("o", "refused-at-once")["stage"] == terminal
    assert len([k for k in ts._mem if k[0] == "o"]) == monkeypatch_cap


def test_the_global_cap_never_takes_the_new_entry_either(task_state, monkeypatch):
    ts = task_state
    monkeypatch.setattr(ts, "_MAX_LIVE", 6)
    monkeypatch.setattr(ts, "_MAX_LIVE_PER_ORIGIN", 100)
    for i in range(6):
        ts.record_stage("big", f"b{i}", "processing")
    terminal = sorted(ts.TERMINAL)[0]
    ts.record_stage("big", "new-terminal", terminal)
    assert ts.lookup("big", "new-terminal")["stage"] == terminal
    assert len(ts._mem) == 6
