"""``scripts/token_savings_receipt.py`` re-prints the README's token-savings table from the audit chain (T-0246).

The figure in README.md / docs/overview/token-savings.md ("-60 % vs Opus 5, $344 vs $863, 222 priced
turns") was read from the chain by hand on 2026-10-04 and nothing in the repo reproduced it. The script
folds the chain exactly like the console (``compute_cost_efficiency``: same reader, same rate card, same
cache multipliers) for a window and a reference model of the operator's choice, and prints the table.

Fixture: three OS turns - two priced (Haiku, Sonnet with cache reads) and one on a priced model but
without token data - plus one turn before the window. Expected dollars are computed by hand from the
rate card in ``model_selection_learner``: Haiku 4.5 $1/$5 per MTok, Sonnet 5 $2/$10, Opus 5 $5/$25,
cache reads 0.1x the input rate (Opus 5.5: $4/$20, cache read $0.20/MTok).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "token_savings_receipt.py"
sys.path.insert(0, str(_REPO))

from core.learning.model_selection_learner import compute_cost_efficiency  # noqa: E402


def _ts(day: str, hour: int = 12) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00").timestamp()


def _turn(fh, turn_id: str, model: str, ts: float, **tokens) -> None:
    fh.write(json.dumps({"ts": ts, "event_type": "os_turn.started",
                         "details": {"turn_id": turn_id, "model": model, "persona": "assistant"}}) + "\n")
    fh.write(json.dumps({"ts": ts + 1.0, "event_type": "os_turn.completed",
                         "details": {"turn_id": turn_id, "model": model, "tools_called": 1, "exit_code": 0,
                                     "timed_out": False, **tokens}}) + "\n")


@pytest.fixture
def chain(tmp_path: Path) -> Path:
    p = tmp_path / "audit.jsonl"
    with p.open("w") as fh:
        # before the window (2026-09-28): must not count with --since 2026-09-28
        _turn(fh, "t0", "claude-opus-5", _ts("2026-09-20"), input_tokens=1000, output_tokens=1000)
        # priced: Haiku 10K in / 2K out -> actual $0.02, on Opus 5 $0.10, on Opus 5.5 $0.08
        _turn(fh, "t1", "claude-haiku-4-5-20251001", _ts("2026-09-29"), input_tokens=10_000, output_tokens=2_000)
        # priced: Sonnet 5K in / 1K out / 100K cache reads -> actual $0.04, on Opus 5 $0.10, on Opus 5.5 $0.06
        _turn(fh, "t2", "claude-sonnet-5", _ts("2026-09-30"), input_tokens=5_000, output_tokens=1_000,
              cache_read_input_tokens=100_000)
        # a priced model but no token data: counted as a turn, never priced
        _turn(fh, "t3", "claude-opus-5", _ts("2026-10-01"))
    return p


class TestComputeCostEfficiencyWindowAndReference:
    def test_since_until_and_reference_keywords(self, chain):
        r = compute_cost_efficiency("_default", chain_path=chain, since=_ts("2026-09-28", 0))
        assert (sum(d.total_turns for d in r.daily), sum(d.counted_turns for d in r.daily)) == (3, 2)
        assert (r.total_actual_usd, r.total_baseline_usd) == (pytest.approx(0.06), pytest.approx(0.20))
        assert r.reference_model == "claude-opus-5"
        r = compute_cost_efficiency("_default", chain_path=chain, since=_ts("2026-09-28", 0),
                                    until=_ts("2026-09-30", 0))
        assert (sum(d.total_turns for d in r.daily), r.total_actual_usd) == (1, pytest.approx(0.02))
        r = compute_cost_efficiency("_default", chain_path=chain, since=_ts("2026-09-28", 0),
                                    reference="claude-opus-5-5")
        assert (r.total_actual_usd, r.total_baseline_usd) == (pytest.approx(0.06), pytest.approx(0.14))
        assert r.reference_model == "claude-opus-5-5"
        with pytest.raises(ValueError):
            compute_cost_efficiency("_default", chain_path=chain, reference="not-a-model")

    def test_an_explicit_since_ignores_the_counting_epoch(self, chain, monkeypatch):
        from core.console.corvin_console import usage_epoch as UE

        monkeypatch.setattr(UE, "epoch_ts", lambda tid: _ts("2026-10-01", 0))
        assert sum(d.total_turns for d in compute_cost_efficiency("_default", chain_path=chain).daily) == 1
        assert sum(d.total_turns for d in compute_cost_efficiency(
            "_default", chain_path=chain, since=_ts("2026-09-01", 0)).daily) == 4


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(_SCRIPT), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=120)


class TestReceiptCli:
    def test_json_numbers_are_exact(self, chain):
        r = _run("--chain", str(chain), "--since", "2026-09-28", "--reference", "claude-opus-5", "--json")
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        os_turns = out["os_turns"]
        assert (os_turns["turns"], os_turns["priced_turns"]) == (3, 2)
        assert os_turns["actual_usd"] == pytest.approx(0.06)
        assert os_turns["reference_usd"] == pytest.approx(0.20)
        assert os_turns["saving_percent"] == pytest.approx(70.0)
        assert out["reference_model"] == "claude-opus-5"
        assert out["window"] == {"since": "2026-09-28T00:00:00+00:00", "until": None, "source": "cli"}
        assert out["worker_runs"] is None, "a --chain run reads one file; no tenant worker series"
        assert set(os_turns["model_mix"]) == {"claude-haiku-4-5-20251001", "claude-sonnet-5"}

    def test_the_table_and_the_window(self, chain):
        r = _run("--chain", str(chain), "--since", "2026-09-28", "--until", "2026-09-30",
                 "--reference", "claude-opus-5-5")
        assert r.returncode == 0, r.stderr
        assert "claude-opus-5-5" in r.stdout
        assert "2026-09-28" in r.stdout and "2026-09-30" in r.stdout
        row = next(line for line in r.stdout.splitlines() if line.lstrip().startswith("OS turns"))
        assert "1" in row and "$0.02" in row and "$0.08" in row and "75.0 %" in row

    def test_an_unreadable_chain_exits_2(self, tmp_path):
        r = _run("--chain", str(tmp_path / "missing.jsonl"), "--since", "2026-09-28")
        assert r.returncode == 2, r.stdout + r.stderr
        assert "missing.jsonl" in r.stderr
        r = _run("--chain", str(tmp_path), "--since", "2026-09-28")  # a directory
        assert r.returncode == 2

    def test_a_bad_reference_model_exits_2(self, chain):
        r = _run("--chain", str(chain), "--since", "2026-09-28", "--reference", "gpt-9")
        assert r.returncode == 2
        assert "gpt-9" in r.stderr

    def test_without_since_the_tenant_chain_and_its_counting_epoch_are_used(self, chain, tmp_path, monkeypatch):
        """No ``--chain``: the chain comes from ``tenant_audit_chain()`` under CORVIN_HOME; no ``--since``:
        the tenant's usage epoch applies (ADR-0760); ``--since`` overrides it."""
        from core.console.corvin_console import usage_epoch as UE
        from core.paths.tenant import tenant_audit_chain

        home = tmp_path / "home"
        monkeypatch.setenv("CORVIN_HOME", str(home))
        dest = tenant_audit_chain("_default")
        dest.parent.mkdir(parents=True)
        dest.write_bytes(chain.read_bytes())
        UE.set_epoch("_default", at=_ts("2026-10-01", 0), reason="test")
        env = {"CORVIN_HOME": str(home)}
        r = _run("--reference", "claude-opus-5", "--json", env=env)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["os_turns"]["turns"] == 1
        assert out["window"]["source"] == "usage_epoch"
        assert out["window"]["since"] == "2026-10-01T00:00:00+00:00"
        assert out["chain"] == str(dest)
        assert "worker_runs" in out and out["worker_runs"] is not None
        r = _run("--since", "2026-09-01", "--reference", "claude-opus-5", "--json", env=env)
        assert json.loads(r.stdout)["os_turns"]["turns"] == 4

    def test_the_script_never_writes_the_chain(self, chain):
        before = (chain.stat().st_mtime_ns, chain.read_bytes())
        chain.chmod(0o444)
        try:
            r = _run("--chain", str(chain), "--since", "2026-09-01", "--json")
        finally:
            chain.chmod(0o644)
        assert r.returncode == 0, r.stderr
        assert (chain.stat().st_mtime_ns, chain.read_bytes()) == before
