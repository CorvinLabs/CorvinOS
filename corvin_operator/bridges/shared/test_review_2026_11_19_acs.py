"""Review 2026-11-19, ACS findings A2A-8 / A2A-9 / A2A-10.

* A2A-10 — a timed-out or cancelled engine subprocess is killed WITH its
  children (own session + ``killpg``), driven through the real spawn functions
  with a fake CLI that backgrounds a child (``sleep & wait``).
* A2A-9  — maintainer signing material and the pseudonymisation seed never
  reach a worker environment (ACS worker and the A2A worker overlay).
* A2A-8  — ``acs.engine_completed`` carries no priceable token split; the
  worker ``engine.span.end`` is the one carrier.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import acs_runtime  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


def _fake_cli(tmp_path: Path, seconds: str) -> Path:
    """A stand-in ``claude`` that starts a background child and waits on it —
    the shape of a CLI whose tool shell is still running at the deadline."""
    script = tmp_path / "fake-claude"
    script.write_text(f'#!/bin/bash\nexec bash -c "sleep {seconds} & wait"\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _alive(marker: str) -> list[str]:
    out = subprocess.run(["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True)
    return [p for p in out.stdout.split() if p]


def _wait_gone(marker: str, within_s: float = 5.0) -> list[str]:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        left = _alive(marker)
        if not left:
            return []
        time.sleep(0.1)
    return _alive(marker)


@pytest.fixture
def _no_gates(monkeypatch):
    monkeypatch.setattr(acs_runtime, "_resolve_worker_engine", lambda m, t: ("claude_code", ""))
    monkeypatch.setattr(acs_runtime, "_assert_engine_licensed", lambda e: None)


@posix_only
def test_worker_timeout_kills_the_whole_process_group(tmp_path, monkeypatch, _no_gates):
    marker = "47.1119"
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(_fake_cli(tmp_path, marker)))
    monkeypatch.setattr(acs_runtime, "_effective_worker_timeout", lambda b: 1)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        acs_runtime._call_worker_sync("do it", "", "claude-haiku-4-5", {}, tenant_id="_default")
    # Returned at the deadline, not when the orphan's pipe finally closed.
    assert time.monotonic() - started < 15
    assert _wait_gone(marker) == [], "the background child survived the timeout kill"


@posix_only
def test_manager_timeout_kills_the_whole_process_group(tmp_path, monkeypatch, _no_gates):
    marker = "47.1120"
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(_fake_cli(tmp_path, marker)))
    monkeypatch.setattr(acs_runtime, "_MANAGER_TIMEOUT", 1)
    with pytest.raises(subprocess.TimeoutExpired):
        acs_runtime._call_manager_sync("decide", "claude-haiku-4-5", "_default")
    assert _wait_gone(marker) == [], "the background child survived the manager timeout"


@posix_only
def test_cancel_through_the_holder_kills_the_whole_process_group(tmp_path):
    marker = "47.1121"
    proc = subprocess.Popen(
        ["bash", "-c", f"sleep {marker} & wait"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **acs_runtime._new_session_kwargs())
    try:
        time.sleep(0.3)
        assert _alive(marker), "positive control: the child must be running first"
        holder = acs_runtime._WorkerProcessHolder()
        holder.popen = proc
        holder.kill()
        proc.wait(timeout=5)
        assert _wait_gone(marker) == []
    finally:
        acs_runtime._kill_process_tree(proc)


@posix_only
def test_kill_never_signals_our_own_process_group():
    # A child that shares OUR group (no new session): only the child may die.
    proc = subprocess.Popen(["sleep", "30"])
    try:
        assert os.getpgid(proc.pid) == os.getpgrp()
        acs_runtime._kill_process_tree(proc)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
    # Still here: the test process was not signalled.
    assert proc.returncode is not None


_SIGNING_NAMES = ("CORVIN_MAINTAINER_PRIV", "CORVIN_MAINTAINER_CAP", "CORVIN_PSEUDO_SEED")


def test_signing_material_never_reaches_an_acs_worker_env():
    src = {"PATH": "/usr/bin", "HOME": "/tmp", "CORVIN_TENANT_ID": "_default",
           "CORVIN_MAINTAINER_PUBKEY": "pub", **{n: "x" * 40 for n in _SIGNING_NAMES}}
    env = acs_runtime.build_worker_env(
        "_default", source_env=src, operator_keep=False, platform=False,
        redirect=False, engine_credential=False)
    for name in _SIGNING_NAMES:
        assert name not in env, name
    # Positive control: the reduction is not simply dropping everything.
    assert env.get("CORVIN_TENANT_ID") == "_default"
    assert env.get("CORVIN_MAINTAINER_PUBKEY") == "pub"
    # A caller's declared extra cannot bring them back either.
    env2 = acs_runtime.build_worker_env(
        "_default", source_env={"PATH": "/usr/bin"}, extra={n: "v" for n in _SIGNING_NAMES},
        operator_keep=False, platform=False, redirect=False, engine_credential=False)
    assert not set(_SIGNING_NAMES) & set(env2)


def test_the_secret_name_rule_was_not_widened():
    # ADR-0759: the three names are listed explicitly; the NAME regex stays narrow.
    for name in _SIGNING_NAMES:
        assert not acs_runtime._SECRET_NAME_RE.search(name), name
        assert name in acs_runtime._WORKER_SECRET_ENV_STRIP


def test_signing_material_is_blanked_for_an_a2a_worker_even_when_absent_from_base():
    import a2a_worker
    overlay = a2a_worker._secret_env_overrides({"PATH": "/usr/bin"}, tenant_id="_default")
    for name in _SIGNING_NAMES:
        assert overlay.get(name) == "", name
    overlay2 = a2a_worker._secret_env_overrides(
        {"PATH": "/usr/bin", **{n: "s3" for n in _SIGNING_NAMES}}, tenant_id="_default")
    for name in _SIGNING_NAMES:
        assert overlay2.get(name) == "", name


# ── A2A-8 / R2-A2A-1..3: one priced entry per worker run, behavioural ─────────
#
# A stub ``claude`` is driven through the real ``_dispatch_workers`` ->
# ``_call_worker_sync`` -> subprocess path and the REAL audit writer; the chain
# file is then read by ``compute_cost_efficiency``, the function the console's
# cost panel calls. No source text is inspected.

_SPLIT = {"input_tokens": 1000, "output_tokens": 500,
          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
_MODEL = "claude-haiku-4-5"


def _sandbox_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    key = tmp_path / "anchor.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(key))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    chain = Path(acs_runtime._audit_path("_default"))
    assert str(chain).startswith(str(home)), chain
    return chain


def _records(chain: Path, event_type: str) -> list[dict]:
    import json
    out = []
    for line in chain.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("event_type") == event_type:
            out.append(rec)
    return out


def _run_stub_worker(tmp_path: Path, monkeypatch, run_id: str) -> None:
    import asyncio
    import json
    from test_acs_runtime import _minimal_spec  # type: ignore[import-not-found]

    script = tmp_path / f"claude-stub-{run_id}"
    envelope = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": json.dumps({"status": "success", "confidence": 0.9, "result": {"ok": True}}),
        "model": _MODEL, "usage": dict(_SPLIT),
    }
    script.write_text("#!/bin/sh\ncat >/dev/null\ncat <<'__E__'\n" + json.dumps(envelope)
                      + "\n__E__\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    binary = str(script)
    monkeypatch.setattr(acs_runtime, "_resolve_worker_engine",
                        lambda *a, **k: ("claude_code", "configured-model"))
    monkeypatch.setattr(acs_runtime, "_assert_engine_licensed", lambda *a, **k: None)
    monkeypatch.setattr(acs_runtime, "_claude_binary", lambda *a, **k: binary)
    monkeypatch.setattr(acs_runtime, "_apply_provider_redirect", lambda *a, **k: None)
    monkeypatch.setattr(acs_runtime.shutil, "which", lambda *a, **k: binary)
    spec = _minimal_spec()
    root_budget = acs_runtime._budget_from_spec(spec)
    root_budget.max_total_workers = 100
    root_budget.max_workers_per_iteration = 5
    run_dir = tmp_path / f"run-{run_id}"
    run_dir.mkdir()
    ctx = acs_runtime.RunContext(
        run_id=run_id, workflow_id="wf", workflow_spec=spec,
        budget=root_budget.fraction(1.0), run_dir=run_dir, root_budget=root_budget,
    )
    asyncio.run(acs_runtime._dispatch_workers(
        [{"id": "t0", "instructions": "do the thing"}], ctx, depth=1,
        manager_model="m", worker_model="configured-model",
    ))


def _cost(chain: Path):
    from core.learning import model_selection_learner as msl
    return msl, msl.compute_cost_efficiency("_default", chain, since=0.0)


def _one_run_usd(msl) -> float:
    return msl._price_and_baseline(_MODEL, 1000, 500, 0, 0, reference=None)[0]


@posix_only
def test_a_real_acs_worker_run_is_one_priced_entry(tmp_path, monkeypatch):
    chain = _sandbox_home(tmp_path, monkeypatch)
    _run_stub_worker(tmp_path, monkeypatch, "run-new")

    completed = _records(chain, "acs.engine_completed")
    ends = [r for r in _records(chain, "engine.span.end") if r["details"].get("role") == "worker"]
    assert len(completed) == 1 and len(ends) == 1, (completed, ends)
    # The split travels on the span only; the completion carries none of it.
    assert ends[0]["details"]["input_tokens"] == 1000
    assert ends[0]["details"]["output_tokens"] == 500
    for key in _SPLIT:
        assert not completed[0]["details"].get(key), key
    assert ends[0]["details"]["span_id"] == "spn-run-new-" + completed[0]["details"]["worker_id"]

    msl, res = _cost(chain)
    total = sum(p.total_turns for p in res.acs_daily)
    counted = sum(p.counted_turns for p in res.acs_daily)
    assert (total, counted) == (1, 1), (total, counted)
    assert res.acs_total_actual_usd == pytest.approx(_one_run_usd(msl), abs=1e-4)
    assert res.acs_model_mix == {_MODEL: 1}


@posix_only
def test_engine_completed_names_its_span_in_the_chain_once_allowlisted(tmp_path, monkeypatch):
    """R2-A2A-2: ``span_id`` is emitted on acs.engine_completed. Whether it
    REACHES the chain is decided by the allowlist in security_events.py, which
    this cluster does not own - so the expectation is read from the live
    allowlist: listed -> the field must be in the record; not listed -> it must
    be named in ``_dropped_fields`` (the emitter sent it, the floor cut it)."""
    from forge import security_events as sec
    chain = _sandbox_home(tmp_path, monkeypatch)
    _run_stub_worker(tmp_path, monkeypatch, "run-span")
    rec = _records(chain, "acs.engine_completed")[0]
    det = rec["details"]
    expected = "spn-run-span-" + det["worker_id"]
    if "span_id" in sec._EVENT_ALLOWLIST["acs.engine_completed"]:
        assert det.get("span_id") == expected, det
    else:
        assert "span_id" in (det.get("_dropped_fields") or []), det


def _write_old_form_run(chain: Path, run_id: str, *, span_tokens: bool = True) -> None:
    """The append-only history: completion AND span both carry the split."""
    from forge import security_events as sec
    import engine_span as espan
    sec.write_event(chain, "acs.engine_completed", details={
        "run_id": run_id, "worker_id": "w0", "engine_id": "claude_code",
        "model_id": _MODEL, "locality": "us_cloud", "duration_ms": 5,
        "tokens_used": 1500, "exit_code": 0, **_SPLIT,
    })
    sec.write_event(chain, espan.ENGINE_SPAN_END, details=espan.end_details(
        span_id=f"spn-{run_id}-w0", role="worker", engine_id="claude_code",
        model_id=_MODEL, run_id=run_id, status="ok", duration_ms=5,
        tokens_used=1500, **(espan.usage_split(_SPLIT) if span_tokens else {})))


def test_old_double_carrying_history_is_priced_once(tmp_path, monkeypatch):
    chain = _sandbox_home(tmp_path, monkeypatch)
    chain.parent.mkdir(parents=True, exist_ok=True)
    _write_old_form_run(chain, "run-old")
    # Positive control: both chain records really carry the split.
    assert _records(chain, "acs.engine_completed")[0]["details"]["input_tokens"] == 1000
    assert _records(chain, "engine.span.end")[0]["details"]["input_tokens"] == 1000

    msl, res = _cost(chain)
    assert sum(p.total_turns for p in res.acs_daily) == 1
    assert sum(p.counted_turns for p in res.acs_daily) == 1
    assert res.acs_total_actual_usd == pytest.approx(_one_run_usd(msl), abs=1e-4)


def test_a_span_without_tokens_does_not_displace_a_measured_completion(tmp_path, monkeypatch):
    # 2026-09-18 shape: the floor dropped the span's token fields, the
    # completion still has them. The measured record stands - one priced turn.
    chain = _sandbox_home(tmp_path, monkeypatch)
    chain.parent.mkdir(parents=True, exist_ok=True)
    _write_old_form_run(chain, "run-floor", span_tokens=False)
    msl, res = _cost(chain)
    assert sum(p.total_turns for p in res.acs_daily) == 1
    assert sum(p.counted_turns for p in res.acs_daily) == 1
    assert res.acs_total_actual_usd == pytest.approx(_one_run_usd(msl), abs=1e-4)


def test_a_completion_without_any_span_is_still_counted(tmp_path, monkeypatch):
    # Pre-ADR-0171 history: no span exists - nothing to reconcile against.
    from forge import security_events as sec
    chain = _sandbox_home(tmp_path, monkeypatch)
    chain.parent.mkdir(parents=True, exist_ok=True)
    sec.write_event(chain, "acs.engine_completed", details={
        "run_id": "run-lone", "worker_id": "w0", "engine_id": "claude_code",
        "model_id": _MODEL, "duration_ms": 5, "tokens_used": 1500, "exit_code": 0, **_SPLIT})
    msl, res = _cost(chain)
    assert sum(p.total_turns for p in res.acs_daily) == 1
    assert sum(p.counted_turns for p in res.acs_daily) == 1


def test_old_and_new_forms_together_count_each_run_once(tmp_path, monkeypatch):
    chain = _sandbox_home(tmp_path, monkeypatch)
    chain.parent.mkdir(parents=True, exist_ok=True)
    _write_old_form_run(chain, "run-old")
    if os.name == "posix":
        _run_stub_worker(tmp_path, monkeypatch, "run-new")
        expected = 2
    else:
        expected = 1
    msl, res = _cost(chain)
    assert sum(p.total_turns for p in res.acs_daily) == expected
    assert sum(p.counted_turns for p in res.acs_daily) == expected
    assert res.acs_total_actual_usd == pytest.approx(expected * _one_run_usd(msl), abs=1e-4)


# ── R2-A2A-11: a worker killed at its deadline still closes a priceable span ─


def _run_timing_out_worker(tmp_path, monkeypatch, run_id: str, printed: str) -> Path:
    """A stub engine that prints *printed* (what it reported so far) and then
    hangs past the worker deadline; real spawn path, real audit writer."""
    import asyncio
    from test_acs_runtime import _minimal_spec  # type: ignore[import-not-found]

    chain = _sandbox_home(tmp_path, monkeypatch)
    script = tmp_path / f"claude-hang-{run_id}"
    body = "#!/bin/bash\ncat >/dev/null\n"
    if printed:
        body += "cat <<'__E__'\n" + printed + "\n__E__\n"
    body += "sleep 30\n"
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    binary = str(script)
    monkeypatch.setattr(acs_runtime, "_resolve_worker_engine",
                        lambda *a, **k: ("claude_code", "configured-model"))
    monkeypatch.setattr(acs_runtime, "_assert_engine_licensed", lambda *a, **k: None)
    monkeypatch.setattr(acs_runtime, "_claude_binary", lambda *a, **k: binary)
    monkeypatch.setattr(acs_runtime, "_apply_provider_redirect", lambda *a, **k: None)
    monkeypatch.setattr(acs_runtime.shutil, "which", lambda *a, **k: binary)
    monkeypatch.setattr(acs_runtime, "_effective_worker_timeout", lambda budget: 1)
    spec = _minimal_spec()
    root_budget = acs_runtime._budget_from_spec(spec)
    root_budget.max_total_workers = 100
    root_budget.max_workers_per_iteration = 5
    run_dir = tmp_path / f"run-{run_id}"
    run_dir.mkdir()
    ctx = acs_runtime.RunContext(
        run_id=run_id, workflow_id="wf", workflow_spec=spec,
        budget=root_budget.fraction(1.0), run_dir=run_dir, root_budget=root_budget,
    )
    results = asyncio.run(acs_runtime._dispatch_workers(
        [{"id": "t0", "instructions": "do the thing"}], ctx, depth=1,
        manager_model="m", worker_model="configured-model",
    ))
    assert [r.status for r in results] == ["failed"], results
    return chain


@posix_only
def test_a_timed_out_worker_span_carries_what_the_engine_reported(tmp_path, monkeypatch):
    import json
    frames = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": _MODEL}),
        json.dumps({"type": "assistant", "message": {"model": _MODEL, "usage": dict(_SPLIT)}}),
        '{"type": "assistant", "message": {"cut in ha',     # the frame the kill cut
    ])
    chain = _run_timing_out_worker(tmp_path, monkeypatch, "run-to", frames)
    ends = [r["details"] for r in _records(chain, "engine.span.end")
            if r["details"].get("role") == "worker"]
    assert len(ends) == 1 and ends[0]["status"] == "error", ends
    assert ends[0]["model_id"] == _MODEL
    assert ends[0]["input_tokens"] == 1000 and ends[0]["output_tokens"] == 500
    # No completion record for a failed run - the span is the one carrier.
    assert _records(chain, "acs.engine_completed") == []
    msl, res = _cost(chain)
    assert sum(p.total_turns for p in res.acs_daily) == 1
    assert sum(p.counted_turns for p in res.acs_daily) == 1
    assert res.acs_total_actual_usd == pytest.approx(_one_run_usd(msl), abs=1e-4)


@posix_only
def test_a_timed_out_worker_that_reported_nothing_stays_unpriced(tmp_path, monkeypatch):
    chain = _run_timing_out_worker(tmp_path, monkeypatch, "run-silent", "")
    ends = [r["details"] for r in _records(chain, "engine.span.end")
            if r["details"].get("role") == "worker"]
    assert len(ends) == 1 and ends[0]["status"] == "error"
    assert ends[0]["model_id"] == "", "nothing reported: never a configured guess"
    assert not any(ends[0].get(k) for k in (
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))


def test_partial_attestation_reads_an_envelope_frames_or_nothing():
    import json
    f = acs_runtime._partial_attestation
    assert f("") == {} and f(None) == {} and f("not json at all") == {}
    env = json.dumps({"type": "result", "model": _MODEL, "usage": {"input_tokens": 7}})
    assert f(env) == {"reported_model": _MODEL, "input_tokens": 7}
    # A usage object of zeros is not "reported usage".
    assert "input_tokens" not in f(json.dumps({"model": _MODEL, "usage": {"input_tokens": 0}}))
    # Newest frame wins for the model.
    two = "\n".join([json.dumps({"model": "claude-old"}), json.dumps({"model": _MODEL})])
    assert f(two)["reported_model"] == _MODEL
