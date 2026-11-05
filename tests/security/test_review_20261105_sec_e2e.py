"""Regression tests for the 2026-11-05 adversarial security review (SEC-2..SEC-13).

Each test drives the real boundary the finding named, in a throwaway
``CORVIN_HOME`` (never the live roots): the hook launcher through the command
``hooks.json`` ships, the console's Art. 17 path through the real ``/erase``
dispatch (``chat_router.dispatch`` — the same boundary every console erasure
test uses), the CLI through a subprocess, the boot tripwire's verifier and the
voice-audit CLI. The repro assertions of the review are kept verbatim where
they still apply.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
HOOKS = REPO / "corvin_operator" / "voice" / "hooks"
for _p in (SHARED, REPO / "corvin_operator" / "forge", REPO / "core" / "console", REPO):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    from forge import security_events as se
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    return h


def _console_erase(tenant: str, subject: str):
    from corvin_console import chat_router
    from entity_extract import EntityPlan  # type: ignore

    plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": subject}, confidence=1.0)
    plan.slots["confirm"] = chat_router._issue_erase_code(tenant, subject)
    result = asyncio.run(chat_router.dispatch(plan, tenant))
    assert result.status == "created", result
    return result, {l["layer_id"]: l for l in result.payload["layers"]}


# ── SEC-2: the PreToolUse gates fail CLOSED when no Python resolves ──────────

def _hook_commands() -> dict[str, str]:
    manifest = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    out = {}
    for group in manifest["hooks"].values():
        for entry in group:
            for h in entry["hooks"]:
                m = re.search(r"(\w+\.py)\s*$", h["command"])
                if m and "with_python.sh" in h["command"]:
                    out[m.group(1)] = h["command"]
    return out


def _bare_bin(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("sh", "dirname", "uname"):
        target = bindir / tool
        if not target.exists():
            os.symlink(shutil.which(tool), target)
    return bindir


def _run_hook(command: str, env: dict, payload: dict) -> subprocess.CompletedProcess:
    # Exactly what Claude Code does: the manifest's command line through a shell,
    # with ${CLAUDE_PLUGIN_ROOT} expanded from the environment.
    return subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("gate", ["path_gate.py", "auth_elevation_gate.py", "web_trust_gate.py"])
def test_sec2_pretooluse_gate_exits_2_without_python(tmp_path, gate):
    cmds = _hook_commands()
    assert "--fail-closed" in cmds[gate], cmds[gate]
    env = {"PATH": str(_bare_bin(tmp_path)), "HOME": str(tmp_path),
           "XDG_DATA_HOME": str(tmp_path / "xdg"), "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent)}
    payload = {"tool_name": "Bash",
               "tool_input": {"command": "rm -f /x/.corvin/tenants/_default/global/forge/audit.jsonl"}}
    proc = _run_hook(cmds[gate], env, payload)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "fail-closed gate" in proc.stderr


@pytest.mark.parametrize("hook", ["artifact_register.py", "notification_relay.py", "ldd_auto_optin.py"])
def test_sec2_best_effort_hook_stays_non_blocking(tmp_path, hook):
    cmds = _hook_commands()
    assert "--fail-closed" not in cmds[hook]
    env = {"PATH": str(_bare_bin(tmp_path)), "HOME": str(tmp_path),
           "XDG_DATA_HOME": str(tmp_path / "xdg"), "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent)}
    proc = _run_hook(cmds[hook], env, {})
    assert proc.returncode == 1, (proc.returncode, proc.stderr)
    assert "did not run" in proc.stderr


def test_sec2_gate_still_runs_when_python_resolves(tmp_path):
    """Positive control: with an interpreter the flag changes nothing — the real
    path_gate runs and allows a harmless command."""
    cmds = _hook_commands()
    home = tmp_path / "corvin"
    home.mkdir()
    env = {"PATH": str(_bare_bin(tmp_path)), "HOME": str(tmp_path),
           "XDG_DATA_HOME": str(tmp_path / "xdg"), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent), "CORVIN_PYTHON": sys.executable,
           "CORVIN_HOME": str(home), "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key")}
    proc = _run_hook(cmds["path_gate.py"], env,
                     {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-800:])


# ── SEC-5: the console erases the four root-level routing stores ─────────────

def _plant_root_stores(home: Path, uid: str, tenant: str = "_default") -> dict[str, Path]:
    import completion_notify  # noqa: PLC0415

    tid = completion_notify.register(channel="discord", chat_id="9001", sender=uid,
                                     label="summarise my medical letter", tenant_id=tenant)
    rec = completion_notify._record_path(tid)
    bg = home / "bg_watch.json"
    state = json.loads(bg.read_text()) if bg.exists() else {}
    state[f"discord:{uid}:{tenant}"] = {"channel": "discord", "from": uid, "chat_id": "9001",
                                       "tenant_id": tenant, "last_activity": 1.0, "notified_at": 0.0}
    bg.write_text(json.dumps(state), encoding="utf-8")
    tp = home / "task_progress" / f"tp_{tenant}_{abs(hash(uid)) % 10**8}.json"
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(json.dumps({"sender": uid, "task_id": f"t-{tenant}", "chat_id": "9001",
                              "tenant_id": tenant, "text": "progress"}), encoding="utf-8")
    run = home / "task_runs" / f"run-{tenant}-{abs(hash(uid)) % 10**8}.json"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(json.dumps({"sender": uid, "task_id": f"run-{tenant}", "tenant_id": tenant,
                               "instruction": "the subject's instruction text"}), encoding="utf-8")
    return {"completion_notify": rec, "bg_monitor": bg, "task_progress": tp, "task_supervisor": run}


def test_sec5_console_erasure_removes_root_routing_records(home):
    subject = "discord:424242"
    paths = _plant_root_stores(home, subject)
    keep = _plant_root_stores(home, "discord:777", tenant="_default")
    assert paths["completion_notify"].exists() and paths["completion_notify"].is_relative_to(home)
    result, layers = _console_erase("_default", subject)
    for lid in ("L-bg-monitor", "L-completion-notify", "L-task-progress", "L-task-supervisor"):
        assert layers[lid]["status"] == "applied", (lid, layers[lid])
    assert not paths["completion_notify"].exists(), "pending notification of the subject survived"
    assert not paths["task_progress"].exists() and not paths["task_supervisor"].exists()
    assert subject not in paths["bg_monitor"].read_text()
    # the other subject's records are untouched (no over-matching)
    assert keep["completion_notify"].exists() and keep["task_progress"].exists()
    assert "discord:777" in paths["bg_monitor"].read_text()
    # the outcome is in the erasure trail (it never was, from the CLI pre-step)
    trail = home / "tenants" / "_default" / "global" / "erasure" / f"{result.entity_id}.json"
    assert "L-completion-notify" in trail.read_text(encoding="utf-8")


def test_sec5_root_store_purge_is_tenant_scoped(home):
    subject = "discord:424242"
    other_tenant = _plant_root_stores(home, subject, tenant="acme")
    _console_erase("_default", subject)
    assert other_tenant["completion_notify"].exists(), "another tenant's record was erased"
    assert other_tenant["task_supervisor"].exists()


def test_sec5_cli_runs_the_same_layers(home, tmp_path):
    subject = "discord:424242"
    paths = _plant_root_stores(home, subject)
    env = dict(os.environ)
    proc = subprocess.run(
        [sys.executable, str(REPO / "corvin_operator" / "voice" / "scripts" / "corvin_erasure.py"),
         "--home", str(home), "--tenant", "_default", "--format", "json",
         "run", subject, "--requester", "operator:test"],
        capture_output=True, text=True, env=env, timeout=300, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    out = json.loads(proc.stdout)
    layers = {l["layer_id"]: l for l in out["per_layer"]}
    for lid in ("L-bg-monitor", "L-completion-notify", "L-task-progress", "L-task-supervisor"):
        assert layers[lid]["status"] == "applied", (lid, layers[lid])
    assert not paths["completion_notify"].exists() and not paths["task_supervisor"].exists()
    # no un-audited pre-step any more: nothing printed outside the JSON document
    assert "purged" not in proc.stdout


def test_sec5_a_purge_failure_is_reported_not_swallowed(home, monkeypatch):
    import completion_notify  # noqa: PLC0415
    from erasure_handlers import RootRoutingStoreHandler

    def boom(uid, *, tenant_id=None):
        raise OSError("read-only file system")

    monkeypatch.setattr(completion_notify, "purge_user", boom)
    r = RootRoutingStoreHandler(layer_id="L-completion-notify", module="completion_notify",
                                item="x").purge("discord:1", "er-1")
    assert r.status.value == "failed" and r.code == "store_error"


# ── SEC-6: disclosure / roles / consent are tenant-scoped and erased per uid ─

def test_sec6_disclosure_and_roles_live_under_the_tenant(home):
    import disclosure  # noqa: PLC0415
    import roles  # noqa: PLC0415
    tenant_global = home / "tenants" / "_default" / "global"
    assert disclosure._store_path("discord", "chan1").is_relative_to(tenant_global / "disclosure")
    assert roles._store_path("discord", "chan1").is_relative_to(tenant_global / "roles")


def test_sec6_legacy_root_file_is_read_as_fallback_never_moved(home):
    """RS-7 supersedes the one-time move: the root file is a read-only legacy layer."""
    import disclosure  # noqa: PLC0415
    legacy = home / "global" / "disclosure" / "discord__chan1.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"u1": {"action": "joined", "first_seen": 1.0}}), encoding="utf-8")
    p = disclosure._store_path("discord", "chan1")
    assert p.is_relative_to(home / "tenants" / "_default"), p
    assert disclosure.get_state("discord", "chan1", "u1")["seen"] is True
    assert legacy.exists() and not p.exists(), "a read moved or copied the legacy file"


def test_sec6_non_default_tenant_never_adopts_root_files(home, monkeypatch):
    import roles  # noqa: PLC0415
    legacy = home / "global" / "roles" / "discord__chan1.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    p = roles._store_path("discord", "chan1")
    assert p.is_relative_to(home / "tenants" / "acme") and legacy.exists()


def test_sec6_disclosure_source_has_no_corvinos_fallback():
    src = (SHARED / "disclosure.py").read_text(encoding="utf-8")
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert '".corvinOS"' not in code and "'.corvinOS'" not in code


def test_sec6_consent_is_tenant_scoped(home, monkeypatch):
    import consent  # noqa: PLC0415
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    consent.grant("discord", "chan1", "u-acme")
    acme = home / "tenants" / "acme" / "global" / "consent" / "discord__chan1.json"
    assert "u-acme" in acme.read_text()
    assert not (home / "tenants" / "_default" / "global" / "consent").exists()
    assert "u-acme" in consent.list_consents("discord", "chan1")
    assert consent.list_consents("discord", "chan1", tenant_id="_default") == {}


def test_sec6_uid_entries_are_erased_through_the_console(home):
    import consent  # noqa: PLC0415
    import disclosure  # noqa: PLC0415
    import roles  # noqa: PLC0415
    uid = "424242424242424242"
    other = "5151"
    paths = [disclosure._store_path("discord", "chan1"), roles._store_path("discord", "chan1"),
             consent._store_path("discord", "chan1", tenant_id="_default")]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({uid: {"action": "accepted", "first_seen": 1.0},
                                 other: {"action": "accepted", "first_seen": 1.0}}))
    legacy = home / "global" / "roles" / "discord__chan2.json"   # pre-migration file
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({uid: {"role": "member"}}), encoding="utf-8")
    _result, layers = _console_erase("_default", uid)
    assert layers["L-uid-stores"]["status"] == "applied", layers["L-uid-stores"]
    left = [str(p.relative_to(home)) for p in paths + [legacy] if uid in p.read_text()]
    assert not left, f"uid survived erasure in {left}"
    assert all(other in p.read_text() for p in paths), "another uid's entry was erased"


# ── SEC-8: a present-but-broken engine_trust config fails closed ─────────────

@pytest.mark.parametrize("body,code", [
    ("spec:\n  engine_trust:\n    min_tier: hihg\n", "invalid_min_tier"),
    ("spec:\n  engine_trust:\n    min_teir: high\n", "unknown_key"),
    ("spec:\n  engine_trust:\n    min_tier: high\n  bad: [\n", "yaml_unparseable"),
])
def test_sec8_malformed_engine_trust_is_strictest_and_audited(home, body, code):
    import engine_trust  # noqa: PLC0415
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(body)
    cfg.chmod(0o600)
    assert engine_trust._tenant_config_path("_default") == cfg
    tier = engine_trust.load_min_tier_for_tenant("_default")
    assert tier != "low", "operator asked for a high floor; a typo silently admitted every engine"
    assert tier == engine_trust.STRICTEST_TIER
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    recs = [json.loads(l) for l in chain.read_text().splitlines()]
    hit = [r for r in recs if r["event_type"] == "engine_trust.config_invalid"]
    assert hit and hit[-1]["severity"] == "CRITICAL" and hit[-1]["details"]["reason"] == code
    assert engine_trust.load_drift_policy_for_tenant("_default")["auto_block_on_drift"] is True


def test_sec8_absent_or_valid_config_unchanged(home):
    import engine_trust  # noqa: PLC0415
    assert engine_trust.load_min_tier_for_tenant("_default") == "low"   # no file
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("spec:\n  engine_trust:\n    min_tier: medium\n")
    assert engine_trust.load_min_tier_for_tenant("_default") == "medium"
    cfg.write_text("spec:\n  web_chat: {}\n")
    assert engine_trust.load_min_tier_for_tenant("_default") == "low"


# ── SEC-9: the ratchet sees the adapter's emitter; security names registered ─

SECURITY_NAMES = [
    "engine_trust.gate_fail_open",
    "consent.gate_unavailable_drop",
    "consent.group_chat_non_consenting_participants",
    "gate_pipeline.self_test_failed",
    "security.config_conflict",
    "house_rules.allowed_after_lowconf",
    "plugin.security_violation",
    "plugin.consent_granted",
    "tenant.path_migrated",
    "compliance.crash_recovery",
]


def test_sec9_ratchet_sees_adapter_audit_event_names():
    from forge import security_events as se  # noqa: PLC0415
    src = (REPO / "scripts" / "verify_audit_event_completeness.py").read_text()
    writers = re.search(r"_WRITER_CALLEES = \{(.*?)\}", src, re.S).group(1)
    for callee in ("_audit_event", "audit_emit", "_audit_log", "_emit_audit", "_audit_write",
                   "_write_audit", '"emit"', '"audit"'):
        assert callee in writers, callee
    assert "event_type" in src and "_EVENT_KEYWORDS" in src
    adapter = (SHARED / "adapter.py").read_text()
    for name in SECURITY_NAMES[:5]:
        assert f'"{name}"' in adapter, name  # positive control: it IS emitted
    for name in SECURITY_NAMES:
        assert name in se.EVENT_SEVERITY and name in se._EVENT_ALLOWLIST, name
    kept, _dropped = se.filter_audit_details(
        {"reason": "load_min_tier_error", "engine": "claude_code", "error": "x"},
        event_type="engine_trust.gate_fail_open")
    assert kept.get("engine") == "claude_code" and kept.get("reason") == "load_min_tier_error"
    base = set(json.loads((REPO / "scripts" / "audit_unregistered_baseline.json").read_text()))
    assert not (set(SECURITY_NAMES) & base), "a registered name must not stay in the baseline"
    assert "A2A.google_envelope_sent" not in base


def test_sec9_ratchet_passes_on_the_repo():
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "verify_audit_event_completeness.py")],
                          capture_output=True, text=True, timeout=600, cwd=str(REPO))
    assert proc.returncode == 0, proc.stdout[-2500:]
    assert "new: 0" in proc.stdout


# ── SEC-12: the env relaxation reaches the voice-audit CLI only ──────────────

_KEYLESS = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    R = Path(os.environ["REPO"])
    for p in (R / "corvin_operator" / "forge", R / "corvin_operator" / "bridges" / "shared", R / "core" / "compliance", R):
        sys.path.insert(0, str(p))
    from forge import security_events as se
    chain = Path(os.environ["CHAIN"])
    if os.environ["PHASE"] == "write":
        for i in range(3):
            se.write_event(chain, "test.event", details={"n": i, "tenant_id": "_default"})
        print(json.dumps({"mac": all("mac" in json.loads(l) for l in chain.read_text().splitlines())}))
    else:
        from corvin_compliance_reports import tripwire
        ok, problems, _n = tripwire._verify_chain(chain)
        ok2, _p2 = se.verify_chain(chain, allow_missing_key=True)
        print(json.dumps({"tripwire_ok": ok, "issues": sorted({p.get("issue") for p in problems}),
                          "cli_mode_ok": ok2}))
""")


def test_sec12_no_key_ok_does_not_relax_the_tripwire(tmp_path):
    home = tmp_path / "home"
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    key = tmp_path / "anchor.key"
    env = dict(os.environ, REPO=str(REPO), CHAIN=str(chain), CORVIN_HOME=str(home),
               CORVIN_TENANT_ID="_default", CORVIN_AUDIT_ANCHOR_KEY=str(key), PHASE="write")
    env.pop("VOICE_AUDIT_PATH", None)
    env.pop("FORGE_ROOT", None)
    w = subprocess.run([sys.executable, "-c", _KEYLESS], env=env, capture_output=True, text=True, timeout=120)
    assert w.returncode == 0 and json.loads(w.stdout.splitlines()[-1])["mac"], w.stderr[-1500:]
    # The anchor key is gone and cannot be re-created (a missing file would be
    # re-generated as a NEW key, which reads as mac_tampered): its directory is
    # a file. The service env sets the variable.
    key.unlink()
    blocker = tmp_path / "keydir"
    blocker.write_text("not a directory", encoding="utf-8")
    env.update(PHASE="verify", CORVIN_AUDIT_ANCHOR_KEY=str(blocker / "anchor.key"),
               CORVIN_AUDIT_VERIFY_NO_KEY_OK="1")
    v = subprocess.run([sys.executable, "-c", _KEYLESS], env=env, capture_output=True, text=True, timeout=120)
    assert v.returncode == 0, v.stderr[-1500:]
    out = json.loads(v.stdout.splitlines()[-1])
    assert out["tripwire_ok"] is False and "mac_unverifiable_key_absent" in out["issues"], out
    assert out["cli_mode_ok"] is True, out
    # The operator CLI still honours the variable for a cross-host verify.
    cli = REPO / "corvin_operator" / "voice" / "scripts" / "voice_audit.py"
    ok_run = subprocess.run([sys.executable, str(cli), "--path", str(chain), "verify"],
                            env=env, capture_output=True, text=True, timeout=120)
    env.pop("CORVIN_AUDIT_VERIFY_NO_KEY_OK")
    strict_run = subprocess.run([sys.executable, str(cli), "--path", str(chain), "verify"],
                                env=env, capture_output=True, text=True, timeout=120)
    assert strict_run.returncode == 1, strict_run.stdout[-800:] + strict_run.stderr[-800:]
    assert ok_run.returncode == 0, ok_run.stdout[-800:] + ok_run.stderr[-800:]


# ── SEC-13: the readers read THE chain ───────────────────────────────────────

def test_sec13_audit_view_reads_the_tenant_chain(home):
    import audit_view  # noqa: PLC0415
    from forge import security_events as se
    from forge.paths import tenant_audit_chain
    assert audit_view._audit_path() == tenant_audit_chain("_default")
    se.write_event(tenant_audit_chain("_default"), "test.sec13_marker",
                   details={"tenant_id": "_default"})
    types = [e.get("event_type") for e in audit_view._iter_all_events()]
    assert "test.sec13_marker" in types


def test_sec13_chain_dual_track_reads_host_chain_for_default_only(home):
    from corvin_console.routes import chain_dual_track as cdt
    from forge.paths import legacy_audit_chains, tenant_audit_chain
    assert cdt._chain_paths("acme")[0] == tenant_audit_chain("acme")
    assert legacy_audit_chains("acme")["host_global_forge"] not in cdt._chain_paths("acme")
    assert legacy_audit_chains("_default")["host_global_forge"] in cdt._chain_paths("_default")


# ══ Refutation round 2026-11-05 (RS-1..RS-16) ════════════════════════════════

# ── RS-2: a Python that resolves but cannot run the gate still fails CLOSED ──

def _stub_python(bindir: Path, rc: int) -> None:
    stub = bindir / "python3"
    stub.write_text(f"#!/bin/sh\necho 'Fatal Python error: init_fs_encoding' >&2\nexit {rc}\n")
    stub.chmod(0o755)


@pytest.mark.parametrize("gate", ["path_gate.py", "auth_elevation_gate.py", "web_trust_gate.py"])
@pytest.mark.parametrize("rc", [1, 126, 127, 139])
def test_rs2_gate_whose_python_cannot_start_exits_2(tmp_path, gate, rc):
    cmds = _hook_commands()
    bindir = _bare_bin(tmp_path)
    _stub_python(bindir, rc)
    env = {"PATH": str(bindir), "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg"),
           "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent)}
    proc = _run_hook(cmds[gate], env, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "did not run to a decision" in proc.stderr


def test_rs2_real_python_with_broken_pythonhome_exits_2(tmp_path):
    cmds = _hook_commands()
    bindir = _bare_bin(tmp_path)
    os.symlink(sys.executable, bindir / "python3")
    env = {"PATH": str(bindir), "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg"),
           "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent), "PYTHONHOME": str(tmp_path / "nohome")}
    proc = _run_hook(cmds["path_gate.py"], env,
                     {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert proc.returncode == 2, (proc.returncode, proc.stderr[-400:])


def test_rs2_best_effort_hook_crash_stays_non_blocking(tmp_path):
    cmds = _hook_commands()
    bindir = _bare_bin(tmp_path)
    _stub_python(bindir, 1)
    env = {"PATH": str(bindir), "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg"),
           "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent)}
    proc = _run_hook(cmds["artifact_register.py"], env, {})
    assert proc.returncode == 1, (proc.returncode, proc.stderr)


# ── RS-3: an IAC is accepted only for the instance bound to the signing key ──

@pytest.fixture
def rs3(home, tmp_path, monkeypatch):
    """A real receiver (HTTP server) with min_trust=community and a configured CA."""
    import test_adr0078_e2e as base  # noqa: PLC0415 - the ADR-0078 e2e helpers (bridges/shared)
    from corvin_operator.license import capability_api  # noqa: PLC0415

    monkeypatch.setattr(capability_api, "active_tier", lambda **_k: "member")
    ca_priv, ca_pub = base.att.generate_ca_keypair()
    monkeypatch.setenv("CORVIN_CA_PUBKEY_HEX", ca_pub)
    eps = tmp_path / "endpoints"
    eps.mkdir()
    monkeypatch.setenv("REMOTE_ENDPOINTS_DIR", str(eps))
    servers = []

    def start(**origin_extra):
        odir = tmp_path / f"origins{len(servers)}"
        odir.mkdir()
        base._write_origin(odir, min_trust="community", **origin_extra)
        srv = base.build_server(origins_dir=odir, nonce_store=base.NonceStore(),
                                force_m1_only=True, instance_id="e2e-recv")
        base.serve_in_thread(srv)
        servers.append(srv)
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1/a2a/receive"
        return odir, url

    def iac(iid, tier="verified"):
        return base.att.sign_attestation(instance_id=iid, tier=tier, ca_privkey_hex=ca_priv)

    yield base, start, iac
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("bound_key", ["_peer_instance_id", "sender_instance_id_pin", "_invite_issuer"])
def test_rs3_borrowed_attestation_under_the_victims_name_is_refused(rs3, bound_key):
    """The refutation repro, on a pairing that records its peer: the attacker signs with its OWN
    key, names the victim and presents the victim's genuine IAC -> refused."""
    base, start, iac = rs3
    _odir, url = start(**{bound_key: "inst-attacker"})
    resp = base._post(url, base._envelope(attestation=iac("inst-victim"),
                                          sender_instance_id="inst-victim"))
    assert resp["status"] == "rejected", resp
    # Positive control: the bound instance's own IAC passes.
    resp = base._post(url, base._envelope(attestation=iac("inst-attacker")))
    assert resp["status"] == "ok", resp


def test_rs3_self_named_replay_still_refused(rs3):
    base, start, iac = rs3
    _odir, url = start()
    resp = base._post(url, base._envelope(attestation=iac("inst-victim"),
                                          sender_instance_id="inst-attacker"))
    assert resp["status"] == "rejected", resp


def test_rs3_unbound_pairing_is_pinned_at_first_attested_contact(rs3, home):
    """TOFU: the first attested contact pins the key's instance (audited); a later envelope
    carrying another instance's IAC under that instance's name is refused."""
    base, start, iac = rs3
    odir, url = start()
    assert base._post(url, base._envelope(attestation=iac("inst-honest")))["status"] == "ok"
    pin = odir / f"{base.ORIGIN_ID}.attested_instance"
    assert pin.read_text() == "inst-honest" and (pin.stat().st_mode & 0o777) == 0o600
    resp = base._post(url, base._envelope(attestation=iac("inst-victim"),
                                          sender_instance_id="inst-victim"))
    assert resp["status"] == "rejected", resp
    assert base._post(url, base._envelope(attestation=iac("inst-honest")))["status"] == "ok"
    chain = home / "tenants" / "_default" / "global" / "forge" / ("audit" + ".jsonl")
    events = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    pinned = [e for e in events if e.get("event_type") == "A2A.instance_pinned"]
    assert len(pinned) == 1 and pinned[0]["details"]["reason"] == "tofu_first_attested_contact"
    failed = [e for e in events if e.get("event_type") == "a2a.attestation_failed"]
    assert [e["details"]["reason"] for e in failed] == ["attestation_pin_mismatch"]


def test_rs3_tofu_refuses_an_instance_bound_to_another_key(rs3):
    base, start, iac = rs3
    odir, url = start()
    other = dict(json.loads((odir / f"{base.ORIGIN_ID}.json").read_text()),
                 origin_id="victim-origin", _peer_instance_id="inst-victim")
    (odir / "victim-origin.json").write_text(json.dumps(other))
    resp = base._post(url, base._envelope(attestation=iac("inst-victim"),
                                          sender_instance_id="inst-victim"))
    assert resp["status"] == "rejected", resp
    assert not (odir / f"{base.ORIGIN_ID}.attested_instance").exists()


# ── RS-4: an incident for tenant X lands on X's chain from any process ──────

def _chain_events(home: Path, tenant: str) -> list[dict]:
    chain = home / "tenants" / tenant / "global" / "forge" / ("audit" + ".jsonl")
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


def test_rs4_library_incident_for_other_tenant_lands_on_its_chain(home, monkeypatch):
    import incident_tracker  # noqa: PLC0415
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    incident_tracker.open_incident(trigger_event="audit.chain_gap_detected", trigger_chain_hash="ab12",
                                   description="x", severity="serious",
                                   category="chain_integrity", tenant_id="acme")
    acme = [e["event_type"] for e in _chain_events(home, "acme")]
    assert "incident.opened" in acme, acme
    dflt = [e["event_type"] for e in _chain_events(home, "_default")]
    assert "audit.tenant_mismatch" not in dflt, dflt


def test_rs4_cli_open_runs_as_the_named_tenant(home, tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CORVIN_TENANT_ID"}
    proc = subprocess.run(
        [sys.executable, str(REPO / "corvin_operator" / "voice" / "scripts" / "corvin_incident.py"),
         "--tenant", "acme", "open", "--category", "chain_integrity", "--description", "x"],
        capture_output=True, text=True, env=env, timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    opened = [e for e in _chain_events(home, "acme") if e["event_type"] == "incident.opened"]
    assert len(opened) == 1 and opened[0]["details"].get("tenant_id") == "acme", opened
    assert not [e for e in _chain_events(home, "_default")
                if e["event_type"] == "audit.tenant_mismatch"]


def test_rs4_cli_refuses_an_invalid_tenant(home, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPO / "corvin_operator" / "voice" / "scripts" / "corvin_incident.py"),
         "--tenant", "../etc", "list"],
        capture_output=True, text=True, env=dict(os.environ), timeout=120, cwd=str(tmp_path))
    assert proc.returncode != 0 and "invalid --tenant" in proc.stderr


# ── RS-5: a group chat file is never deleted for one member ──────────────────

def test_rs5_group_chat_files_keep_other_members(home):
    import consent  # noqa: PLC0415
    import roles  # noqa: PLC0415
    subject, other = "123456789", "555"
    files = {
        consent._store_path("telegram", "-123456789", tenant_id="_default"):
            {subject: {"mode": "durable"}, other: {"mode": "durable"}},
        roles._store_path("telegram", "-123456789", tenant_id="_default"):
            {other: {"bundle": "admin"}},
        roles._store_path("whatsapp", "123456789-1600000000@g.us", tenant_id="_default"):
            {other: {"bundle": "member"}, subject: {"bundle": "member"}},
    }
    for p, body in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body))
    _result, layers = _console_erase("_default", subject)
    assert layers["L-uid-stores"]["status"] == "applied", layers["L-uid-stores"]
    for p in files:
        assert p.exists(), f"group file deleted: {p.name}"
        doc = json.loads(p.read_text())
        assert subject not in doc and other in doc, (p.name, doc)


def test_rs5_subjects_own_chat_file_goes_when_empty(home):
    import consent  # noqa: PLC0415
    subject = "123456789"
    own = consent._store_path("telegram", subject, tenant_id="_default")
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(json.dumps({subject: {"mode": "durable"}}))
    _console_erase("_default", subject)
    assert not own.exists()


# ── RS-7: the legacy root store is erased for EVERY tenant, never moved ──────

def test_rs7_root_disclosure_of_other_tenant_is_erased_for_that_tenant(home):
    uid = "888000888"
    root = home / "global" / "disclosure" / "discord__acmechan.json"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text(json.dumps({uid: {"action": "accepted"}, "999": {"action": "accepted"}}))
    _result, layers = _console_erase("acme", uid)
    assert layers["L-uid-stores"]["status"] == "applied", layers["L-uid-stores"]
    doc = json.loads(root.read_text())
    assert uid not in doc and "999" in doc


def test_rs7_default_access_never_moves_another_tenants_root_file(home, monkeypatch):
    import disclosure  # noqa: PLC0415
    import roles  # noqa: PLC0415
    for kind in ("disclosure", "roles"):
        f = home / "global" / kind / "discord__acmechan.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"u-acme": {"action": "accepted", "bundle": "member"}}))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    disclosure.get_state("discord", "acmechan", "u-acme")
    roles.list_roles("discord", "acmechan")
    for kind in ("disclosure", "roles"):
        assert (home / "global" / kind / "discord__acmechan.json").exists()
        assert not (home / "tenants" / "_default" / "global" / kind / "discord__acmechan.json").exists()


def test_rs7_tenant_reader_sees_the_legacy_layer_and_writes_its_own_file(home, monkeypatch):
    import roles  # noqa: PLC0415
    legacy = home / "global" / "roles" / "discord__acmechan.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"u-acme": {"bundle": "member", "granted_at": 1.0,
                                             "expires_at": None}}))
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    assert "u-acme" in roles.list_roles("discord", "acmechan")["granted"]
    assert legacy.read_text().count("u-acme") == 1  # read-only: untouched


# ── RS-8: the bg_monitor purge is locked; a torn store FAILS the layer ───────

def test_rs8_torn_bg_watch_fails_the_layer(home):
    import bg_monitor  # noqa: PLC0415
    p = bg_monitor._bg_watch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"k": {"from": "victim", "chat_id": "c1", "tenant_id": "_default"}')
    from corvin_console import chat_router  # noqa: PLC0415
    res = chat_router._execute_erasure("_default", "victim")
    layer = [l for l in res.per_layer if l.layer_id == "L-bg-monitor"][0]
    assert layer.status.value == "failed", (layer.status, layer.reason)
    assert res.overall_status.value != "completed"


def test_rs8_concurrent_touch_and_purge_never_resurrect(home):
    """Real writers in two processes: touch() in a loop while the purge runs. The erased
    entry must stay erased and the other entry must survive (no lost update)."""
    import bg_monitor  # noqa: PLC0415
    bg_monitor.touch("discord", "victim", "c1", tenant_id="_default")
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "import bg_monitor\n"
        "for i in range(150):\n"
        "    bg_monitor.touch('discord', 'other', 'c2', tenant_id='_default')\n"
    ) % str(SHARED)
    proc = subprocess.Popen([sys.executable, "-c", script], env=dict(os.environ))
    import time as _t  # noqa: PLC0415
    _t.sleep(0.05)
    assert bg_monitor.purge_user("victim", tenant_id="_default") == 1
    assert proc.wait(timeout=120) == 0
    state = bg_monitor._load_state()
    froms = {v.get("from") for v in state.values() if isinstance(v, dict)}
    assert "victim" not in froms and "other" in froms, state


def test_rs8_unlocked_stale_writer_cannot_resurrect(home):
    """A writer that skipped the lock and saves a copy read before the purge is refused."""
    import bg_monitor  # noqa: PLC0415
    bg_monitor.touch("discord", "victim", "c1", tenant_id="_default")
    stale = bg_monitor._load_state()
    assert bg_monitor.purge_user("victim", tenant_id="_default") == 1
    with pytest.raises(bg_monitor.StaleStateError):
        bg_monitor._save_state(stale)
    assert "victim" not in bg_monitor._bg_watch_path().read_text()


# ── RS-16: --use-stubs still purges the four root-level routing stores ───────

def test_rs16_use_stubs_still_runs_the_root_store_layers(home, tmp_path):
    subject = "discord:434343"
    paths = _plant_root_stores(home, subject)
    proc = subprocess.run(
        [sys.executable, str(REPO / "corvin_operator" / "voice" / "scripts" / "corvin_erasure.py"),
         "--home", str(home), "--tenant", "_default", "--format", "json",
         "run", subject, "--requester", "operator:test", "--use-stubs"],
        capture_output=True, text=True, env=dict(os.environ), timeout=300, cwd=str(tmp_path))
    assert proc.returncode in (0, 1), proc.stderr[-1500:]
    layers = {l["layer_id"]: l for l in json.loads(proc.stdout)["per_layer"]}
    for lid in ("L-bg-monitor", "L-completion-notify", "L-task-progress", "L-task-supervisor"):
        assert layers[lid]["status"] == "applied", (lid, layers.get(lid))
    assert not paths["completion_notify"].exists() and not paths["task_supervisor"].exists()


# ── RS-6: empty file, misspelt block, duplicated key -> strictest; one record per state ──

@pytest.mark.parametrize("body,code", [
    ("", "config_empty"),
    ("# only a comment\n", "config_empty"),
    ("spec:\n  engine-trust:\n    min_tier: high\n", "block_name_typo"),
    ("spec:\n  enginetrust:\n    min_tier: high\n", "block_name_typo"),
    ("spec:\n  engine_trsut:\n    min_tier: high\n", "block_name_typo"),
    ("spec:\n  engine_trust:\n    min_tier: high\nspec:\n  web_chat: {}\n", "duplicate_key"),
    ("spec:\n  engine_trust:\n    min_tier: medium\n    min_tier: low\n", "duplicate_key"),
])
def test_rs6_present_but_not_as_meant_is_strictest(home, body, code):
    import engine_trust  # noqa: PLC0415
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)
    cfg.chmod(0o600)
    assert engine_trust.load_min_tier_for_tenant("_default") == engine_trust.STRICTEST_TIER
    recs = [e for e in _chain_events(home, "_default")
            if e["event_type"] == "engine_trust.config_invalid"]
    assert [r["details"]["reason"] for r in recs] == [code]


def test_rs6_unrelated_spec_keys_stay_permissive(home):
    import engine_trust  # noqa: PLC0415
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("spec:\n  engine_models: {}\n  engine_pref: {}\n  web_chat: {}\n")
    assert engine_trust.load_min_tier_for_tenant("_default") == "low"


def test_rs6_critical_record_once_per_config_state(home):
    import engine_trust  # noqa: PLC0415
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("spec:\n  engine_trust:\n    min_tier: hihg\n")
    for _ in range(5):  # five spawns over one unchanged config
        assert engine_trust.load_min_tier_for_tenant("_default") == "high"

    def n():
        return len([e for e in _chain_events(home, "_default")
                    if e["event_type"] == "engine_trust.config_invalid"])

    assert n() == 1
    cfg.write_text("spec:\n  engine_trust:\n    min_tier: hgih\n")  # changed, still broken
    os.utime(cfg, ns=(1, 1))
    engine_trust.load_min_tier_for_tenant("_default")
    engine_trust.load_min_tier_for_tenant("_default")
    assert n() == 2


# ── RS-10: the ratchet derives helper emitters; L44 decisions are registered ──

def _verify_module():
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "verify_audit_event_completeness", REPO / "scripts" / "verify_audit_event_completeness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rs10_helper_emitters_are_derived():
    import ast  # noqa: PLC0415
    v = _verify_module()
    synthetic = ast.parse(
        "def _inner(event_type, details):\n    write_event(p, event_type, details=details)\n"
        "def outer(ev):\n    _inner(ev, {})\n"
        "def unrelated(x):\n    print(x)\n")
    callees = v.derive_writer_callees([synthetic])
    assert {"_inner", "outer"} <= callees and "unrelated" not in callees
    trees = [ast.parse(p.read_text(encoding="utf-8")) for p in (
        SHARED / "spawn_gates.py", SHARED / "social_http_server.py",
        REPO / "core" / "plugins" / "corvin_plugins" / "bootstrap.py",
        REPO / "core" / "gateway" / "corvin_gateway" / "plugin_cmd.py",
        REPO / "corvin_operator" / "license" / "session_refresh.py",
        # the chain-writing ``emit_audit_event``; plugin_cmd's same-named helper only logs
        REPO / "core" / "console" / "corvin_console" / "auth.py",
        REPO / "core" / "console" / "corvin_console" / "audit.py")]
    callees = v.derive_writer_callees(trees)
    for name in ("_hr_classifier_audit", "_audit_social", "_audit_degradation",
                 "emit_audit_event", "_audit_license_event"):
        assert name in callees, name
    assert {"audit_writer", "audit_callback", "audit_fn"} <= callees


def test_rs10_house_rules_decisions_are_registered_closed():
    from forge import security_events as se  # noqa: PLC0415
    expected = {"house_rules.denied": "CRITICAL", "house_rules.escalated": "WARNING",
                "house_rules.warned": "WARNING", "house_rules.allowed": "INFO"}
    for name, sev in expected.items():
        assert se.EVENT_SEVERITY[name] == sev, name
        assert "rule_id" in se._EVENT_ALLOWLIST[name] and "task" not in se._EVENT_ALLOWLIST[name]
    kept, _dropped = se.filter_audit_details(
        {"reason": "gate_construction_error", "error_type": "OSError", "prompt": "secret task"},
        event_type="house_rules.denied")
    assert kept.get("error_type") == "OSError" and "prompt" not in kept
    base = set(json.loads((REPO / "scripts" / "audit_unregistered_baseline.json").read_text()))
    assert not (set(expected) & base)
    # The names the derived callees made visible are tracked by the ratchet, not invisible.
    for name in ("social.signature_invalid", "plugin.load_refused",
                 "license.key_integrity_mismatch", "plugin.signature_verification_failed"):
        assert name in base or name in se.EVENT_SEVERITY, name


# ── RS-11: the installed-skill store is per tenant ───────────────────────────

def _rs11_install(root: Path, skill_id: str, version: str = "1.0.0") -> None:
    import hashlib  # noqa: PLC0415
    import io  # noqa: PLC0415
    import zipfile  # noqa: PLC0415
    from core.skills.skill_installer import SkillInstaller  # noqa: PLC0415

    root.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{skill_id}/SKILL.md", f"---\nname: {skill_id}\ndescription: d\n---\n# {skill_id}\n")
    zp = root.parent / f"{skill_id}-{version}.zip"
    zp.write_bytes(buf.getvalue())
    ok, msg = SkillInstaller(install_root=root).install_skill(
        zp, hashlib.sha256(zp.read_bytes()).hexdigest(),
        {"skill_id": skill_id, "version": version, "dependencies": []})
    assert ok, msg
    # Boot catch-up only considers VERIFIED entries (an operator-signed package).
    reg = root / "skills_registry.json"
    data = json.loads(reg.read_text())
    for entry in data[skill_id]:
        entry["verified"] = True
    reg.write_text(json.dumps(data))


def _rs11_client(tenant: str):
    from types import SimpleNamespace  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from corvin_console import deps as console_deps  # noqa: PLC0415
    from corvin_console.app import router as console_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")
    rec = SimpleNamespace(tenant_id=tenant, tier="owner", sid_fingerprint="fp")
    app.dependency_overrides[console_deps.require_session] = lambda: rec
    app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = lambda: rec
    return TestClient(app)


def test_rs11_installed_store_is_per_tenant(home):
    from core.skills.installed_prompt_skills import installed_store_root  # noqa: PLC0415
    assert installed_store_root("_default") == home / "skills_installed"
    assert installed_store_root("acme") == home / "tenants" / "acme" / "skills" / "installed"
    with pytest.raises(ValueError):
        installed_store_root("../x")


def test_rs11_listings_never_cross_tenants(home):
    from core.skills.installed_prompt_skills import installed_store_root  # noqa: PLC0415
    _rs11_install(installed_store_root("_default"), "host.skill")
    _rs11_install(installed_store_root("acme"), "acme.skill")
    for tenant, mine, other in (("_default", "host.skill", "acme.skill"),
                                ("acme", "acme.skill", "host.skill")):
        client = _rs11_client(tenant)
        for url in ("/v1/console/skills-manager/skills/installed",
                    "/v1/console/marketplace/skills/search"):
            r = client.get(url)
            assert r.status_code == 200, (url, r.text[:300])
            ids = {s["skill_id"] for s in r.json()["skills"]}
            assert mine in ids and other not in ids, (tenant, url, ids)


def test_rs11_boot_catch_up_never_activates_another_tenants_install(home, monkeypatch):
    from core.skills import installed_prompt_skills as ips  # noqa: PLC0415
    _rs11_install(ips.installed_store_root("_default"), "host.skill")
    seen = []
    monkeypatch.setattr(ips, "activate_installed_skill",
                        lambda sid, ver, body, *, tenant_id: seen.append((tenant_id, sid))
                        or ips.Activation(sid, ver, "activated", ips.forge_name_for(sid)))
    assert ips.activate_missing_installed_skills("acme") == [] and seen == []
    ips.activate_missing_installed_skills("_default")
    assert seen == [("_default", "host.skill")]


# ── RS-13: an existing workflow chat log is tightened to 0o600, audited once ──

@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_rs13_existing_chat_log_is_tightened_once(home):
    from corvin_console.routes import workflows as wf  # noqa: PLC0415
    path = wf._chat_path("_default", "wf_mode")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"role": "user", "text": "old"}\n')
    path.chmod(0o664)
    wf._append_chat_line("_default", "wf_mode", {"role": "user", "text": "new"})
    assert path.stat().st_mode & 0o777 == 0o600
    wf._append_chat_line("_default", "wf_mode", {"role": "user", "text": "again"})
    recs = [e for e in _chain_events(home, "_default")
            if e["event_type"] == "workflow.chat_log_mode_repaired"]
    assert len(recs) == 1
    assert {k: recs[0]["details"][k] for k in ("workflow_id", "prior_mode")} == {
        "workflow_id": "wf_mode", "prior_mode": "0o664"}
    assert path.read_text().count("\n") == 3


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_rs13_new_chat_log_is_created_owner_only(home):
    from corvin_console.routes import workflows as wf  # noqa: PLC0415
    old = os.umask(0o002)
    try:
        wf._append_chat_line("_default", "wf_new", {"role": "user", "text": "hi"})
    finally:
        os.umask(old)
    assert wf._chat_path("_default", "wf_new").stat().st_mode & 0o777 == 0o600
    assert not [e for e in _chain_events(home, "_default")
                if e["event_type"] == "workflow.chat_log_mode_repaired"]


# ── 8c5392cbd regression: secret-rotation backups never collide, never readable ──

@pytest.fixture
def rotation(tmp_path, monkeypatch):
    from core.security.secret_rotation import RotationDaemon  # noqa: PLC0415
    # create_backup reads <cwd>/.env and ~/.config/corvin-voice/*: never the live ones.
    fake_home = tmp_path / "fakehome"
    (fake_home / ".config" / "corvin-voice").mkdir(parents=True)
    (fake_home / ".config" / "corvin-voice" / "secrets.json").write_text('{"k": "v"}')
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("X=1\n")
    from unittest.mock import MagicMock  # noqa: PLC0415
    d = RotationDaemon(tenant_id="_default", audit_backend=MagicMock())
    d.tenant_home = tmp_path / "tenant_home"
    return d


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_secret_rotation_backups_in_one_instant_never_collide(rotation, monkeypatch):
    import core.security.secret_rotation as sr  # noqa: PLC0415
    from datetime import datetime as _dt  # noqa: PLC0415

    class _Frozen(_dt):
        @classmethod
        def utcnow(cls):
            return _dt(2026, 11, 5, 3, 0, 0, 123456)

    monkeypatch.setattr(sr, "datetime", _Frozen)
    paths = [rotation.create_backup() for _ in range(3)]
    assert all(paths) and len(set(paths)) == 3, paths
    assert sorted(Path(p).name for p in paths) == [
        "credentials-20261105030000123456-1.tar.gz",
        "credentials-20261105030000123456-2.tar.gz",
        "credentials-20261105030000123456.tar.gz"]
    for p in paths:
        assert Path(p).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_secret_rotation_backup_is_never_readable_while_written(rotation, monkeypatch):
    """The archive is owner-only from its creation - not chmod'ed after tarfile wrote it."""
    import tarfile  # noqa: PLC0415
    modes = []
    real_add = tarfile.TarFile.add

    def spy(self, *a, **k):
        for f in (rotation.tenant_home / "backups").iterdir():
            modes.append(f.stat().st_mode & 0o777)
        return real_add(self, *a, **k)

    monkeypatch.setattr(tarfile.TarFile, "add", spy)
    old = os.umask(0)
    try:
        assert rotation.create_backup()
    finally:
        os.umask(old)
    assert modes and set(modes) == {0o600}, [oct(m) for m in modes]
