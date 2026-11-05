"""Adversarial review 2026-11-05 (area DLG) — delegation fixes, driven through real boundaries.

Same shape as ``test_review_20261105_dx_e2e.py`` (whose fixtures and recording ``claude``
shim this file reuses): the ACS runtime, the A2A worker and the bridge delegation path
run for real and spawn a real subprocess; only the L44 classifier verdict and the
licence check are pinned (the documented test idioms).

DLG-1   ``check_l34`` judged a DECLARED class as-is on every tenant that has a policy, so
        a credential in an ACS task / manager-written subtask reached a us_cloud engine.
DLG-2   The worker env strip kept ``*_URL`` / ``*_DSN`` / ``*_PASSPHRASE`` bearer secrets.
DLG-12  The bridge recorded a FAILED ACS delegation as a native turn.
DLG-13  Bridge decision rows claimed ``acs`` on a bridge whose parity flag is off.
DLG-17  Inbound A2A L34 never looked at the attachments the worker is told to read.
DLG-22  Truncated manager turns were unpriced; the ACS budget counted words.
DLG-23  ``check_l34`` / the A2A gates fell back to the process environment's tenant.
DLG-25  An A2A worker driven by a REMOTE peer got the host's full platform credential chain.
"""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_review_20261105_dx_e2e import (  # noqa: E402  (shared fixtures + shim)
    SECRET_TEXT, TOKEN_FRAGMENT, _calls, _chain, _run_acs, _spec, sandbox,  # noqa: F401
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shim script")

POLICY = ("spec:\n  data_classification:\n    matrix:\n"
          "      INTERNAL: [local, eu_cloud, us_cloud]\n      SECRET: [local]\n")


def _write_policy(home: Path) -> None:
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(POLICY, encoding="utf-8")
    cfg.chmod(0o600)
    import spawn_gates
    spawn_gates._l34_cache.clear()


def _reset_a2a_quota() -> None:
    import a2a_worker
    home = Path(getattr(a2a_worker, "_CORVIN_HOME_SNAPSHOT_A2A", "/nonexistent"))
    for name in ("compute_quota.json", ".compute_quota.json.lock"):
        try:
            (home / "global" / "license" / name).unlink()
        except OSError:
            pass


def _spawn_a2a(**kw):
    import a2a_worker
    from agents.claude_code import ClaudeCodeEngine

    shim = os.environ["CORVIN_CLAUDE_BIN"]
    _reset_a2a_quota()
    args = dict(instruction="summarise the attached notes", origin_id="peer-dlg",
                task_id="t-dlg", persona="assistant", ttl_s=30,
                engine_factory=lambda: ClaudeCodeEngine(binary=shim))
    args.update(kw)
    return a2a_worker.spawn_a2a_worker(**args)


# ── DLG-1: a declared class may only RAISE the content's class (policy present) ──────


def test_dlg1_check_l34_policy_present_declared_class_cannot_hide_a_credential(sandbox):
    import spawn_gates

    _write_policy(sandbox)
    # Positive controls: the same policy lets the declared class through for benign text,
    # and refuses the credential when no class is declared.
    assert spawn_gates.check_l34("claude_code", "_default", classification="internal",
                                 prompt="summarise the release notes",
                                 persona="orchestrator") is None
    assert spawn_gates.check_l34("claude_code", "_default", prompt=SECRET_TEXT,
                                 persona="orchestrator") is not None
    refusal = spawn_gates.check_l34("claude_code", "_default", classification="internal",
                                    prompt=SECRET_TEXT, persona="orchestrator")
    assert refusal is not None and "SECRET" in refusal, refusal
    # A content marker can never LOWER a declared class either.
    assert spawn_gates.check_l34("claude_code", "_default", classification="secret",
                                 prompt="[class:public] hello", persona="orchestrator") is not None


def test_dlg1_check_l34_classifier_error_with_declared_class_fails_closed(sandbox, monkeypatch):
    import data_classification
    import spawn_gates

    _write_policy(sandbox)

    def _boom(*_a, **_k):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(data_classification, "classify_task", _boom)
    refusal = spawn_gates.check_l34("claude_code", "_default", classification="internal",
                                    prompt="summarise the release notes", persona="orchestrator")
    assert refusal is not None and "classification failed" in refusal, refusal


def test_dlg1_acs_manager_and_worker_refuse_a_credential_under_the_review_policy(
        sandbox, tmp_path, monkeypatch):
    """The ACS call shape (declared class + prompt) through a real run, with the policy the
    review measured: INTERNAL may go to us_cloud, SECRET only local."""
    _write_policy(sandbox)
    ok = _run_acs(_spec("summarise the release notes"))
    assert _calls(tmp_path), f"positive control: the manager never ran ({ok.status}: {ok.error})"
    (tmp_path / "shim.jsonl").unlink()

    res = _run_acs(_spec(SECRET_TEXT))
    assert res.status == "failed", res
    assert _calls(tmp_path) == [], "a credential-bearing task reached the manager"
    assert any(r.get("event_type") == "acs.manager_l34_blocked" for r in _chain(sandbox))

    monkeypatch.setenv("CORVIN_DX_SUBTASK", "use this: " + SECRET_TEXT)
    _run_acs(_spec("prepare the release"))
    workers = [c for c in _calls(tmp_path) if c["kind"] == "worker"]
    assert all(TOKEN_FRAGMENT not in c["stdin"] for c in workers)
    assert any(r.get("event_type") == "acs.worker_l34_blocked" for r in _chain(sandbox))


def test_dlg23_check_l34_without_a_tenant_refuses_instead_of_reading_the_env(sandbox):
    import spawn_gates

    assert spawn_gates.check_l34("claude_code", "_default", prompt="hello") is None  # control
    for missing in ("", None, "   "):
        refusal = spawn_gates.check_l34("claude_code", missing, prompt="hello")  # type: ignore[arg-type]
        assert refusal is not None and "no tenant" in refusal, (missing, refusal)


# ── DLG-2: URL / DSN / passphrase credentials never reach a worker ───────────────────


_URL_SECRETS = {
    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/AbCdEf-secret",
    "CORVIN_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/secret",
    "SENTRY_DSN": "https://publickey@o1.ingest.sentry.io/1",
    "DATABASE_URL": "postgres://user:hunter2@db:5432/app",
    "GPG_PASSPHRASE": "correct horse",
    # Not on any list — caught by the VALUE rule only.
    "BILLING_DB_URL": "mysql://svc:s3cr3t@billing:3306/x",
    "PARTNER_HOOK_URL": "https://partner.example/hook?token=abcdef123456",
    "ANALYTICS_DSN": "https://k3y@analytics.example/2",
}
# Refutation RD-1: deny-by-default — a plain location pointer survives only inside the
# worker base set or Corvin's own namespaces; `UPSTREAM_REPO_URL` is no longer inherited.
_URL_SAFE = {
    "CORVIN_DOCS_URL": "https://docs.example.com/guide",
    "CORVIN_UPSTREAM_REPO_URL": "https://github.com/org/repo.git",
    "HOME": "/home/x",
}


def test_dlg2_secret_env_overrides_clear_url_credentials_and_keep_plain_locations():
    import a2a_worker

    cleared = a2a_worker._secret_env_overrides({**_URL_SECRETS, **_URL_SAFE})
    assert set(_URL_SECRETS) <= set(cleared), sorted(set(_URL_SECRETS) - set(cleared))
    assert not set(_URL_SAFE) & set(cleared), sorted(set(_URL_SAFE) & set(cleared))


def test_dlg2_acs_worker_and_manager_spawn_env_has_no_url_credential(sandbox, tmp_path,
                                                                     monkeypatch):
    for k, v in {**_URL_SECRETS, **_URL_SAFE}.items():
        monkeypatch.setenv(k, v)
    _run_acs(_spec("summarise the release notes"))
    calls = _calls(tmp_path)
    kinds = {c["kind"] for c in calls}
    assert {"manager", "worker"} <= kinds, f"positive control: both kinds must run ({kinds})"
    for c in calls:
        leaked = set(_URL_SECRETS) & set(c["env_names"])
        assert not leaked, f"{c['kind']} spawn env carries {sorted(leaked)}"
        assert "CORVIN_DOCS_URL" in c["env_names"], "a plain location pointer was stripped"


def test_dlg2_a2a_worker_env_has_no_url_credential(sandbox, tmp_path, monkeypatch):
    for k, v in _URL_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    monkeypatch.setenv("CORVIN_DX_SHIM_PROBE", ",".join(_URL_SECRETS))
    res = _spawn_a2a()
    calls = _calls(tmp_path)
    assert calls, f"positive control: the engine never ran ({res.status}: {res.error})"
    leaked = {k for k, v in calls[-1]["env_probe"].items() if v}
    assert leaked == set(), f"URL credentials reached the remote-peer worker: {sorted(leaked)}"


# ── DLG-25: platform credentials only for an origin that cannot read the env ─────────


_AWS = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_ACCESS_KEY_ID": "AKIAEXAMPLEEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-access-key-value", "AWS_REGION": "eu-central-1"}
_ALL_ENV_READERS_DENIED = ["Bash", "PowerShell", "Monitor", "Read", "Grep", "NotebookRead",
                           "Task", "Agent", "Glob", "LS"]


def test_dlg25_platform_host_refuses_an_origin_that_can_read_the_environment(
        sandbox, tmp_path, monkeypatch):
    for k, v in _AWS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a(disallowed_tools=["Read", "Grep", "NotebookRead"])  # Bash still allowed
    assert res.status == "rejected" and res.reason_code == "gate", (res.status, res.error)
    assert _calls(tmp_path) == [], "the engine ran with the cloud credential chain"
    recs = [r for r in _chain(sandbox)
            if r.get("event_type") == "A2A.worker_platform_credentials_refused"]
    assert recs, "the refusal left no audit record"
    det = recs[-1]["details"]
    assert det.get("platform") == "bedrock" and det.get("origin_id") == "peer-dlg", det
    assert det.get("reason") == "origin_can_read_environment", det


def test_dlg25_platform_host_restores_the_chain_when_nothing_can_read_it(
        sandbox, tmp_path, monkeypatch):
    for k, v in _AWS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GITHUB_TOKEN", "gh-operator-secret-value")
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    monkeypatch.setenv("CORVIN_DX_SHIM_PROBE", "AWS_SECRET_ACCESS_KEY,AWS_ACCESS_KEY_ID,GITHUB_TOKEN")
    res = _spawn_a2a(disallowed_tools=list(_ALL_ENV_READERS_DENIED))
    calls = _calls(tmp_path)
    assert calls, f"the engine never ran ({res.status}: {res.error})"
    probe = calls[-1]["env_probe"]
    assert probe["AWS_SECRET_ACCESS_KEY"] == _AWS["AWS_SECRET_ACCESS_KEY"], probe
    assert probe["GITHUB_TOKEN"] == "", "an unrelated operator secret came back with the chain"


def test_dlg25_non_platform_host_is_unchanged_for_a_capable_origin(sandbox, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a()  # every tool allowed, no platform: runs as before
    assert _calls(tmp_path), f"positive control: the engine never ran ({res.status}: {res.error})"


# ── DLG-17: the inbound attachments are classified with the instruction ──────────────


def _att(name: str, mime: str, content: bytes):
    from a2a_attachments import Attachment
    return Attachment.from_bytes(name=name, mime=mime, content=content)


@pytest.mark.parametrize("policy", [False, True])
def test_dlg17_a_credential_in_an_attachment_is_refused(sandbox, tmp_path, monkeypatch, policy):
    if policy:
        _write_policy(sandbox)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    ok = _spawn_a2a(inbound_attachments=[_att("notes.txt", "text/plain", b"release notes v2")])
    assert _calls(tmp_path), f"positive control: a benign attachment runs ({ok.status}: {ok.error})"
    (tmp_path / "shim.jsonl").unlink()

    res = _spawn_a2a(inbound_attachments=[
        _att("notes.txt", "text/plain", b"release notes v2"),
        _att("deploy.env", "text/plain", SECRET_TEXT.encode()),
    ])
    assert res.status == "rejected" and res.reason_code == "data_flow", (res.status, res.error)
    assert _calls(tmp_path) == [], "an attachment carrying a credential reached the engine"


def test_dlg17_a_corrupt_attachment_fails_closed_before_the_engine(sandbox, tmp_path, monkeypatch):
    _write_policy(sandbox)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    bad = _att("notes.txt", "text/plain", b"release notes")
    bad.sha256 = "0" * 64
    res = _spawn_a2a(inbound_attachments=[bad])
    assert res.status == "rejected" and res.reason_code == "gate_error", (res.status, res.error)
    assert _calls(tmp_path) == []


def test_dlg17_a_binary_attachment_is_not_misread_as_text(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    res = _spawn_a2a(inbound_attachments=[_att("pic.png", "image/png", png)])
    assert _calls(tmp_path), f"a binary attachment must not block the run ({res.status}: {res.error})"


# ── DLG-22: manager usage survives truncation; the budget charges reported tokens ────


_BIG_MANAGER_SHIM = r'''#!__PY__
import json, sys
sys.stdin.read()
pad = "x" * int(__PAD__)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps({"decision": "COMPLETE", "reasoning": pad}),
                  "usage": {"input_tokens": 1234, "output_tokens": 567,
                            "cache_read_input_tokens": 89, "cache_creation_input_tokens": 10},
                  "modelUsage": {"claude-opus-5-5": {"inputTokens": 1234, "outputTokens": 567}}}))
'''


def _manager_call(tmp_path: Path, monkeypatch, pad: int):
    import stat as _stat
    import acs_runtime

    shim = tmp_path / "claude-big"
    shim.write_text(_BIG_MANAGER_SHIM.replace("__PY__", sys.executable)
                    .replace("__PAD__", str(pad)), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | _stat.S_IXUSR)
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(shim))
    holder = acs_runtime._WorkerProcessHolder()
    text, tok = acs_runtime._call_manager_sync("decide", "claude-sonnet-5", "_default", holder)
    return text, tok, holder


def test_dlg22_manager_budget_charges_the_reported_tokens(sandbox, tmp_path, monkeypatch):
    text, tok, holder = _manager_call(tmp_path, monkeypatch, pad=10)
    assert "COMPLETE" in text, "positive control: the envelope was parsed"
    assert tok == 1234 + 567, tok
    assert holder.envelope and holder.envelope["usage"]["input_tokens"] == 1234


def test_dlg22_a_truncated_manager_turn_is_still_priced(sandbox, tmp_path, monkeypatch):
    import acs_runtime

    text, tok, holder = _manager_call(tmp_path, monkeypatch, pad=acs_runtime._MANAGER_OUTPUT_CAP + 5000)
    assert len(text) <= acs_runtime._MANAGER_OUTPUT_CAP, "positive control: the head was cut"
    assert holder.envelope is not None, "the truncated turn's usage was thrown away"
    assert holder.envelope["usage"]["output_tokens"] == 567
    assert acs_runtime._reported_model(holder.envelope) == "claude-opus-5-5"
    assert tok == 1234 + 567, tok


# ── DLG-12 / DLG-13: the bridge's routing record names the engine that ran ───────────


def _bridge_adapter(monkeypatch):
    try:
        import license.session_refresh as _sr  # type: ignore
        monkeypatch.setattr(_sr, "_TEST_MODE_SNAPSHOT", "1")
    except Exception:  # noqa: BLE001
        pass
    import adapter  # type: ignore
    return adapter


def _ledger(tenant: str = "_default") -> list[dict]:
    from core.skills.os_skills.monitoring import routing_ledger
    return routing_ledger.read_records(tenant)


@pytest.mark.parametrize("kind", ["run_failed", "gate_refused"])
def test_dlg12_a_failed_bridge_acs_delegation_is_judged_as_acs(sandbox, monkeypatch, kind):
    import acs_runtime as rt
    import corvin_console.feature_flags as ff

    adapter = _bridge_adapter(monkeypatch)

    class _Failing:
        def __init__(self, **kw):
            pass

        async def run(self, spec, run_id=None, **kw):
            return rt.ACSResult(run_id=run_id or "x", workflow_id="w", status="failed",
                                error="manager call failed")

    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    meta = {"turn_id": adapter._new_route_turn_id()}
    gate = "[data-flow] refused" if kind == "gate_refused" else None
    saved = sys.modules.get("license.compute_quota")
    sys.modules["license.compute_quota"] = quota
    try:
        with patch.object(ff, "is_enabled",
                          side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
             patch.object(ff, "worker_engine_mode", return_value="native"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=gate), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.object(rt, "ACSRuntime", _Failing):
            answer, _ = adapter._maybe_delegate_worker(
                "/delegate compare two things", channel="sandbox-dlg", chat_key="c",
                persona="", tenant_id="_default", meta=meta)
    finally:
        if saved is None:
            sys.modules.pop("license.compute_quota", None)
        else:
            sys.modules["license.compute_quota"] = saved
    assert answer is None, "a failed run degrades to the direct turn"
    assert meta.get("routed_engine") == "acs", f"positive control: the rule picked acs ({meta})"
    # The native turn then answers successfully — the routing record must still judge ACS.
    adapter._TURN_OUTCOME.failed = None
    adapter._TURN_OUTCOME.refused = None
    adapter._LAST_OS_TURN_STATUS["c"] = "ok"
    adapter._report_route_outcome("_default", meta, "the native answer", "c", time.monotonic())
    outs = [r for r in _ledger() if r.get("turn_id") == meta["turn_id"]
            and r.get("kind") == "outcome"]
    assert outs, f"no outcome row for the turn: {_ledger()}"
    out = outs[-1]
    assert out.get("used") == "acs", out
    if kind == "gate_refused":
        # NOT-JUDGED (the ledger stores no verdict for an excluded turn) — never a success.
        assert out.get("excluded") == "no_engine" and out.get("ok") is not True, out
    else:
        assert out.get("ok") is False and not out.get("excluded"), out


def test_dlg13_bridge_without_acs_parity_never_records_an_acs_decision(sandbox, monkeypatch):
    import corvin_console.feature_flags as ff

    adapter = _bridge_adapter(monkeypatch)
    meta = {"turn_id": adapter._new_route_turn_id()}
    with patch.object(ff, "is_enabled",
                      side_effect=lambda fid, tid="_default": fid == "bridge_tde_execution"), \
         patch.object(ff, "worker_engine_mode", return_value="acs"):
        answer, _ = adapter._maybe_delegate_worker(
            "/delegate compare two things", channel="sandbox-dlg", chat_key="c13",
            persona="", tenant_id="_default", meta=meta)
    assert answer is None
    rows = [r for r in _ledger() if r.get("turn_id") == meta["turn_id"] and "bundled" in r]
    assert rows, f"positive control: the decision was recorded ({_ledger()})"
    assert rows[-1]["bundled"] == "native" and rows[-1]["used"] == "native", rows[-1]
    assert meta.get("routed_engine") == "native", meta


def test_dlg23_maybe_delegate_worker_requires_the_tenant(sandbox, monkeypatch):
    adapter = _bridge_adapter(monkeypatch)
    with pytest.raises(TypeError):
        adapter._maybe_delegate_worker("hi", channel="x", chat_key="c", persona="")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        adapter._maybe_delegate_worker("hi", channel="x", chat_key="c", persona="", tenant_id="")


# ── DLG-16: the A2A worker-session events are registered with closed field sets ──────


@pytest.mark.parametrize("event,fields", [
    ("worker_session.created", {"scope_label", "persona"}),
    ("worker_session.resumed", {"scope_label", "persona", "resume_count"}),
    ("worker_session.stale_evicted", {"scope_label", "persona"}),
    ("A2A.worker_platform_credentials_refused",
     {"task_id", "origin_id", "platform", "reason", "tenant_id"}),
])
def test_dlg16_worker_session_events_are_registered(event, fields):
    from forge import security_events as se
    assert event in se.EVENT_SEVERITY
    assert se._EVENT_ALLOWLIST.get(event) == frozenset(fields)


# ══ Refutation round (RD-1..RD-10, 2026-11-05) — copies of the refutation repros ═════════
# The repro files of that round (scratchpad refute_delegation/) are reproduced here through
# the same real boundaries: the ACS runtime and the A2A worker spawn the recording `claude`
# shim, the gates write the sandbox chain.

# Built from parts: this file must not itself carry a credential-shaped literal.
_PAT = "ghp_" + TOKEN_FRAGMENT
_RD1_LEAKS = {
    "REPO_URL": f"https://{_PAT}@github.com/org/repo.git",
    "PG_URL": "postgresql://app@db.internal:5432/prod?pass" + "word=hunter2-prod",
    "EXPORT_URL": ("https://bucket.s3.amazonaws.com/x?X-Amz-Algorithm=AWS4-HMAC-SHA256"
                   "&X-Amz-Cred" "ential=AKIAEXAMPLE%2F20261105&X-Amz-Signature=abc"
                   "&X-Amz-Security-Token=FwoGZXIvYXdzEJr"),
    "REDIS_HOST": "redis://:s3cr3tpass@redis.internal:6379/0",
    "MONGO_ENDPOINT": "mongodb://admin:s3cr3tpass@mongo.internal/admin",
    "AMQP_ADDR": "amqp://guest:s3cr3tpass@rabbit.internal/",
    "DATABASE_URL_RO": "postgres://ro:s3cr3tpass@db.internal/prod",
    "CELERY_BROKER": "redis://:s3cr3tpass@redis.internal/1",
    "TEAMS_WEBHOOK_URL": "https://contoso.webhook.office.com/webhookb2/abc@def/IncomingWebhook/1/2",
    "ALERT_HOOK_URL": "https://hooks.zapier.com/hooks/catch/123456/abcdef/",
    "SERVICE_URL": "https://api.example.com/v1?auth=eyJhbGciOiJIUzI1NiJ9.x.y",
    "GRAFANA_URL": "https://grafana.example.com/?apiKey=glsa_abcdef",
}
# The same shapes inside Corvin's own namespace — admitted by name, caught by the VALUE rule.
_RD1_NAMESPACED = {f"CORVIN_RD_{k}": v for k, v in _RD1_LEAKS.items()}


def test_rd1_strip_is_deny_by_default_and_the_value_rule_holds_inside_the_namespace():
    import acs_runtime

    env = {**_RD1_LEAKS, **_RD1_NAMESPACED, "HOME": "/home/x", "PATH": "/usr/bin",
           "LC_ALL": "C.UTF-8", "CORVIN_DOCS_URL": "https://docs.example.com/guide",
           "UNRELATED_SETTING": "plain"}
    acs_runtime._strip_worker_secrets(env)
    assert not set(_RD1_LEAKS) & set(env), sorted(set(_RD1_LEAKS) & set(env))
    assert not set(_RD1_NAMESPACED) & set(env), sorted(set(_RD1_NAMESPACED) & set(env))
    assert "UNRELATED_SETTING" not in env, "a name outside the base set was inherited"
    for kept in ("HOME", "PATH", "LC_ALL", "CORVIN_DOCS_URL"):
        assert kept in env, f"positive control: {kept} must survive"


def test_rd1_no_leak_shape_reaches_an_acs_worker_or_manager(sandbox, tmp_path, monkeypatch):
    for k, v in {**_RD1_LEAKS, **_RD1_NAMESPACED}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CORVIN_DOCS_URL", "https://docs.example.com/guide")
    _run_acs(_spec("summarise the release notes"))
    calls = _calls(tmp_path)
    assert {"manager", "worker"} <= {c["kind"] for c in calls}, "positive control: both ran"
    for c in calls:
        leaked = (set(_RD1_LEAKS) | set(_RD1_NAMESPACED)) & set(c["env_names"])
        assert not leaked, f"{c['kind']} spawn env carries {sorted(leaked)}"
        assert {"HOME", "PATH", "CORVIN_DOCS_URL"} <= set(c["env_names"]), c["env_names"]


def test_rd1_a2a_overlay_blanks_every_leak_shape():
    import a2a_worker

    over = a2a_worker._secret_env_overrides({**_RD1_LEAKS, "HOME": "/home/x"})
    assert all(over.get(k) == "" for k in _RD1_LEAKS), {k: over.get(k) for k in _RD1_LEAKS}
    assert "HOME" not in over


def _write_tenant_yaml(home: Path, text: str) -> None:
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text, encoding="utf-8")
    cfg.chmod(0o600)


def test_rd1_operator_keep_list_reaches_operator_workers_audited_never_a2a(
        sandbox, tmp_path, monkeypatch):
    import acs_runtime

    acs_runtime._WORKER_ENV_KEEP_AUDITED.clear()
    _write_tenant_yaml(sandbox, "spec:\n  worker_env:\n    keep: [MY_TOOL_HOME, 'not a name']\n")
    monkeypatch.setenv("MY_TOOL_HOME", "/opt/tool")
    monkeypatch.setenv("CORVIN_DX_SHIM_PROBE", "MY_TOOL_HOME")
    _run_acs(_spec("summarise the release notes"))
    calls = _calls(tmp_path)
    assert calls and all(c["env_probe"]["MY_TOOL_HOME"] == "/opt/tool" for c in calls), calls
    recs = [r for r in _chain(sandbox) if r.get("event_type") == "worker_env.keep_loaded"]
    assert len(recs) == 1, f"audited once per process+tenant, got {len(recs)}"
    assert recs[0]["details"].get("names") == "MY_TOOL_HOME", recs[0]
    assert recs[0]["details"].get("rejected") == 1, recs[0]

    (tmp_path / "shim.jsonl").unlink()
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a()
    a2a_calls = _calls(tmp_path)
    assert a2a_calls, f"positive control: the A2A engine ran ({res.status}: {res.error})"
    assert a2a_calls[-1]["env_probe"]["MY_TOOL_HOME"] == "", "a remote peer got the keep list"


# ── RD-4: an env-credential host (the documented Docker install) ──────────────────────


_APIKEY = "sk-ant-api03-" + "rdfour" * 6


@pytest.fixture()
def apikey_host(tmp_path, monkeypatch):
    """A host whose ONLY Claude credential is ANTHROPIC_API_KEY: an empty HOME (no login
    file), no platform flag, no CLAUDE_CONFIG_DIR."""
    home = tmp_path / "op-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", _APIKEY)
    for name in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        monkeypatch.delenv(name, raising=False)
    return home


def test_rd4_acs_worker_and_manager_keep_the_engine_key_on_an_apikey_host(
        sandbox, apikey_host, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-operator-secret-value")
    monkeypatch.setenv("CORVIN_DX_SHIM_PROBE", "ANTHROPIC_API_KEY,GITHUB_TOKEN")
    _run_acs(_spec("summarise the release notes"))
    calls = _calls(tmp_path)
    assert {"manager", "worker"} <= {c["kind"] for c in calls}, "positive control: both ran"
    for c in calls:
        assert c["env_probe"]["ANTHROPIC_API_KEY"] == _APIKEY, (c["kind"], "lost its credential")
        assert c["env_probe"]["GITHUB_TOKEN"] == "", "an unrelated secret came back with the key"


def test_rd4_subscription_host_still_strips_the_env_key(sandbox, apikey_host, tmp_path, monkeypatch):
    creds = apikey_host / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CORVIN_DX_SHIM_PROBE", "ANTHROPIC_API_KEY")
    _run_acs(_spec("summarise the release notes"))
    calls = _calls(tmp_path)
    assert calls and all(c["env_probe"]["ANTHROPIC_API_KEY"] == "" for c in calls)


def test_rd4_gateway_task_and_workflow_envs_keep_the_engine_key(sandbox, apikey_host):
    from core.console.corvin_console import task_worker_pool as twp
    from core.gateway.corvin_gateway import dispatcher
    from core.workflows.corvin_workflows import engines_claude

    base = {"PATH": "/usr/bin:/bin", "HOME": str(apikey_host), "ANTHROPIC_API_KEY": _APIKEY,
            "OPENAI_API_KEY": "sk-openai-operator"}
    overlay = dispatcher._worker_spawn_env("_default", persona="assistant", run_id="r1", base=base)
    assert "ANTHROPIC_API_KEY" not in overlay or overlay["ANTHROPIC_API_KEY"] == _APIKEY, overlay
    assert overlay.get("OPENAI_API_KEY") == "", overlay
    assert twp._worker_spawn_env("_default").get("ANTHROPIC_API_KEY") == _APIKEY
    assert engines_claude._worker_spawn_env("_default").get("ANTHROPIC_API_KEY") == _APIKEY


def test_rd4_a2a_refuses_a_capable_origin_and_audits(sandbox, apikey_host, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a()   # every tool allowed
    assert res.status == "rejected" and res.reason_code == "gate", (res.status, res.error)
    assert _calls(tmp_path) == [], "the engine ran with the host key for a capable origin"
    recs = [r for r in _chain(sandbox)
            if r.get("event_type") == "A2A.worker_engine_credential_refused"]
    assert recs, "the refusal left no audit record"
    det = recs[-1]["details"]
    assert det.get("auth_kind") == "api_key" and det.get("origin_id") == "peer-dlg", det
    assert det.get("reason") == "origin_can_read_environment", det


def test_rd4_rd10_a2a_restores_the_key_with_the_tool_allow_list(
        sandbox, apikey_host, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    monkeypatch.setenv("CORVIN_DX_SHIM_PROBE", "ANTHROPIC_API_KEY")
    res = _spawn_a2a(disallowed_tools=list(_ALL_ENV_READERS_DENIED) + ["WebSearch"])
    calls = _calls(tmp_path)
    assert calls, f"the engine never ran ({res.status}: {res.error})"
    assert calls[-1]["env_probe"]["ANTHROPIC_API_KEY"] == _APIKEY
    tools = [a for a in calls[-1]["argv"] if a.startswith("--tools=")]
    assert tools == ["--tools=WebFetch,TodoWrite"], calls[-1]["argv"]


# ── RD-10: the restore decision is an ALLOW list ──────────────────────────────────────


def test_rd10_an_origin_granted_a_tool_outside_the_allow_list_is_refused(
        sandbox, tmp_path, monkeypatch):
    for k, v in _AWS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a(allowed_tools=["WebFetch", "Skill"],
                     disallowed_tools=list(_ALL_ENV_READERS_DENIED))
    assert res.status == "rejected" and res.reason_code == "gate", (res.status, res.error)
    assert _calls(tmp_path) == []


def test_rd10_platform_restore_runs_with_the_allow_list_as_the_whole_tool_set(
        sandbox, tmp_path, monkeypatch):
    for k, v in _AWS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a(disallowed_tools=list(_ALL_ENV_READERS_DENIED))
    calls = _calls(tmp_path)
    assert calls, f"the engine never ran ({res.status}: {res.error})"
    assert "--tools=WebFetch,WebSearch,TodoWrite" in calls[-1]["argv"], calls[-1]["argv"]


# ── RD-2 / RD-9: a namespace-bound `forge` must not let a gate approve unaudited ──────


_RD2_SCRIPT = r'''
import json, os, sys
REPO = sys.argv[1]
sys.path.insert(0, REPO + "/corvin_operator")
import forge                                     # the namespace package, cached
assert getattr(forge, "__file__", None) is None, "precondition: forge is the namespace"
sys.path.insert(0, REPO + "/corvin_operator/forge")
sys.path.insert(0, REPO + "/corvin_operator/bridges/shared")
sys.path.insert(0, REPO)
import house_rules
house_rules._house_rules_classifier = lambda task, rules, auth, **kw: ("", 0.0, "test clear")
import a2a_worker, spawn_gates
out = {"tenant": a2a_worker._receiver_tenant("_default")}
out["l34"] = spawn_gates.check_l34("claude_code", "_default", prompt="summarise the notes",
                                   persona="assistant")
out["l44"] = spawn_gates.check_l44("summarise the notes", "_default", persona="assistant")
out["forge_file"] = getattr(sys.modules["forge"], "__file__", None)
chain = os.path.join(os.environ["CORVIN_HOME"], "tenants", "_default", "global", "forge",
                     "audit.jsonl")
out["events"] = [json.loads(l).get("event_type") for l in open(chain)] if os.path.exists(chain) else []
print(json.dumps(out))
'''


def test_rd2_rd9_gates_audit_in_a_process_whose_forge_is_the_namespace_package(tmp_path):
    import json
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    home = tmp_path / "home"
    _write_tenant_yaml(home, POLICY)
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_ROOT", "PYTHONPATH", "VOICE_AUDIT_PATH")}
    env.update(CORVIN_HOME=str(home), XDG_CONFIG_HOME=str(tmp_path / "xdg"),
               CORVIN_AUDIT_ANCHOR_KEY=str(tmp_path / "anchor.key"), CORVIN_TENANT_ID="_default")
    p = subprocess.run([sys.executable, "-c", _RD2_SCRIPT, str(repo)], env=env,
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=180)
    assert p.returncode == 0, p.stderr[-3000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["tenant"] == "_default"
    assert out["l34"] is None and out["l44"] is None, out
    assert out["forge_file"] and out["forge_file"].endswith("forge/forge/__init__.py"), out
    assert any(e and e.startswith("data_flow.") for e in out["events"]), out
    assert any(e and e.startswith("house_rules.") for e in out["events"]), out


def test_rd2_a_gate_whose_audit_writer_cannot_load_refuses(sandbox, monkeypatch):
    import spawn_gates

    real = spawn_gates._forge_module

    def _boom(name):
        if name == "security_events":
            raise ModuleNotFoundError(f"No module named 'forge.{name}'")
        return real(name)

    monkeypatch.setattr(spawn_gates, "_forge_module", _boom)
    for refusal in (spawn_gates.check_l34("claude_code", "_default", prompt="hello"),
                    spawn_gates.check_l35("claude_code", "_default"),
                    spawn_gates.check_l44("hello", "_default")):
        assert refusal is not None and "audit writer" in refusal, refusal


# ── RD-3: attachments the worker can read are judged in every encoding ───────────────


def _gzip(data: bytes) -> bytes:
    import gzip
    return gzip.compress(data)


def _zip(data: bytes) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("deploy.env", data)
    return buf.getvalue()


def _bz2(data: bytes) -> bytes:
    import bz2
    return bz2.compress(data)


@pytest.mark.parametrize("name,mime,blob", [
    ("deploy.env", "text/plain; charset=utf-16", lambda s: s.encode("utf-16")),
    ("deploy.env", "application/octet-stream", lambda s: s.encode("utf-16-le")),
    ("deploy.env", "application/octet-stream", lambda s: s.encode("utf-32")),
    ("deploy.env.gz", "application/gzip", lambda s: _gzip(s.encode())),
    ("deploy.zip", "application/zip", lambda s: _zip(s.encode())),
    ("deploy.env.bz2", "application/x-bzip2", lambda s: _bz2(s.encode())),
])
def test_rd3_an_encoded_or_compressed_credential_attachment_is_refused(
        sandbox, tmp_path, monkeypatch, name, mime, blob):
    _write_policy(sandbox)
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a(inbound_attachments=[_att(name, mime, blob(SECRET_TEXT))])
    assert res.status == "rejected" and res.reason_code == "data_flow", (res.status, res.error)
    assert _calls(tmp_path) == [], "an attachment carrying a credential reached the engine"


def test_rd3_a_benign_compressed_attachment_runs(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    res = _spawn_a2a(inbound_attachments=[
        _att("notes.txt.gz", "application/gzip", _gzip(b"release notes v2")),
        _att("notes16.txt", "text/plain; charset=utf-16", "release notes".encode("utf-16"))])
    assert _calls(tmp_path), f"benign attachments must run ({res.status}: {res.error})"


def test_rd3_a_compressed_attachment_past_the_size_cap_is_refused(sandbox, tmp_path, monkeypatch):
    from a2a_attachments import MAX_ATTACHMENTS_TOTAL_BYTES

    monkeypatch.setenv("CORVIN_DX_SHIM_MODE", "stream")
    bomb = _gzip(b"a" * (MAX_ATTACHMENTS_TOTAL_BYTES + 10))
    res = _spawn_a2a(inbound_attachments=[_att("big.gz", "application/gzip", bomb)])
    assert res.status == "rejected" and res.reason_code == "data_flow", (res.status, res.error)
    assert _calls(tmp_path) == []


# ── RD-5: no tenant, or a padded one, refuses at every gate entry ─────────────────────


@pytest.mark.parametrize("tenant", [None, "", "_default ", " _default", "_DEFAULT"])
def test_rd5_every_gate_entry_refuses_a_missing_or_malformed_tenant(sandbox, tenant):
    import spawn_gates

    _write_policy(sandbox)
    assert spawn_gates.check_l34("claude_code", "_default", prompt="hello") is None  # control
    for refusal in (
        spawn_gates.check_l34("claude_code", tenant, prompt="hello"),   # type: ignore[arg-type]
        spawn_gates.check_l35("claude_code", tenant),                   # type: ignore[arg-type]
        spawn_gates.check_l44("hello", tenant),                         # type: ignore[arg-type]
        spawn_gates.check_l44_floor("hello", tenant),                   # type: ignore[arg-type]
        spawn_gates.resolve_l34_route("claude_code", tenant, prompt="hello").refusal,  # type: ignore[arg-type]
    ):
        assert refusal is not None and "no tenant" in refusal, (tenant, refusal)


def test_rd5_the_bridge_gate_tenant_is_validated(monkeypatch, sandbox):
    adapter = _bridge_adapter(monkeypatch)
    assert adapter._bridge_gate_tenant(None) == "_default"
    assert adapter._bridge_gate_tenant("_default ") is None
    monkeypatch.setenv("CORVIN_TENANT_ID", "Bad Tenant")
    assert adapter._bridge_gate_tenant(None) is None

    class _Eng:
        name = "claude_code"

    msg = adapter._check_compliance_or_fail(_Eng(), prompt="hi", persona=None, channel="x",
                                            chat_key="c", tenant_id=None)
    assert msg is not None and "tenant" in msg, msg


# ── RD-6: an ACS run ended by an INNER gate is not judged as an engine failure ───────


def test_rd6_acs_result_names_the_inner_gate_refusal(sandbox, tmp_path):
    res = _run_acs(_spec(SECRET_TEXT))
    assert res.status == "failed" and res.gate_refusal == "manager_l34_blocked", res


def test_rd6_bridge_records_an_inner_gate_refusal_as_not_judged(sandbox, monkeypatch):
    import acs_runtime as rt
    import corvin_console.feature_flags as ff

    adapter = _bridge_adapter(monkeypatch)

    class _InnerGate:
        def __init__(self, **kw):
            pass

        async def run(self, spec, run_id=None, **kw):
            return rt.ACSResult(run_id=run_id or "x", workflow_id="w", status="failed",
                                error="L34 data classification gate blocked the manager prompt",
                                gate_refusal="manager_l34_blocked")

    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    meta = {"turn_id": adapter._new_route_turn_id()}
    saved = sys.modules.get("license.compute_quota")
    sys.modules["license.compute_quota"] = quota
    try:
        with patch.object(ff, "is_enabled",
                          side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
             patch.object(ff, "worker_engine_mode", return_value="native"), \
             patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
             patch.object(adapter, "_check_egress_or_fail", return_value=None), \
             patch.object(rt, "ACSRuntime", _InnerGate):
            answer, _ = adapter._maybe_delegate_worker(
                "/delegate compare two things", channel="sandbox-dlg", chat_key="c6",
                persona="", tenant_id="_default", meta=meta)
    finally:
        if saved is None:
            sys.modules.pop("license.compute_quota", None)
        else:
            sys.modules["license.compute_quota"] = saved
    assert answer is None and meta.get("routed_engine") == "acs", meta
    adapter._TURN_OUTCOME.failed = None
    adapter._TURN_OUTCOME.refused = None
    adapter._LAST_OS_TURN_STATUS["c6"] = "ok"
    adapter._report_route_outcome("_default", meta, "the native answer", "c6", time.monotonic())
    outs = [r for r in _ledger() if r.get("turn_id") == meta["turn_id"]
            and r.get("kind") == "outcome"]
    assert outs, _ledger()
    assert outs[-1].get("excluded") == "no_engine" and outs[-1].get("ok") is not True, outs[-1]


# ── RD-8: the workflow records the orchestration MCP server writes are registered ─────


def test_rd8_workflow_records_are_registered_carry_run_id_and_state_severity(sandbox):
    from core.orchestration.corvin_orchestration import mcp_server as ms
    from forge import security_events as se

    assert ms._FORGE_AVAILABLE
    sink = ms._audit_sink_for("_default", "workflow", run_id="run-rd8")
    for ev, det in [("run.level", {"level": 0, "nodes": ["a"]}),
                    ("node.started", {"node_id": "a", "node_type": "agent"}),
                    ("run.terminal", {"state": "failed", "node_id": "a"}),
                    ("run.terminal", {"state": "complete", "nodes": 1, "failed_branches": 0,
                                      "skipped": 0})]:
        sink({"event": ev, **det})
    rows = [r for r in _chain(sandbox) if str(r.get("event_type", "")).startswith("workflow.")]
    assert len(rows) == 4, rows
    for r in rows:
        assert r["event_type"] in se.EVENT_SEVERITY and r["event_type"] in se._EVENT_ALLOWLIST
        assert r.get("run_id") == "run-rd8", r
        assert not r.get("details", {}).get("_dropped_fields"), r
    terminal = [r for r in rows if r["event_type"] == "workflow.run.terminal"]
    assert [t["severity"] for t in terminal] == ["ERROR", "INFO"], terminal
