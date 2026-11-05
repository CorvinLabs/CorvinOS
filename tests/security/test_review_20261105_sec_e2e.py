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


def test_sec6_legacy_root_file_is_migrated_once(home):
    import disclosure  # noqa: PLC0415
    legacy = home / "global" / "disclosure" / "discord__chan1.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"u1": {"action": "joined", "first_seen": 1.0}}), encoding="utf-8")
    p = disclosure._store_path("discord", "chan1")
    assert p.is_relative_to(home / "tenants" / "_default"), p
    assert not legacy.exists() and json.loads(p.read_text())["u1"]["action"] == "joined"
    assert disclosure.get_state("discord", "chan1", "u1")["seen"] is True


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
