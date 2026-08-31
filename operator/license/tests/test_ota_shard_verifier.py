"""ADR-0154 M6 (MSLI shard verifier) + corvin-license-debug E2E.

Free-tier-safety property: on a clean no-license install every shard agrees and
the aggregate is OK or WARN (never FAIL on a benign install). A paid-root +
free-audit-DNA state is flagged as cross-shard divergence (WARN).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_LIC_DIR = Path(__file__).resolve().parents[1]
# operator/ on path enables `import license.feature_lattice` — the SAME module
# object the validator wires the OTA root key into (shared state, not a 2nd copy).
for _p in (
    str(_LIC_DIR.parent),
    str(_LIC_DIR),
    str(_LIC_DIR.parent / "bridges" / "shared"),
    str(_LIC_DIR.parent / "forge" / "forge"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib  # noqa: E402

# Resolve via importlib so we bind the sys.modules entry (the instance the
# validator / shard_verifier wire the root key into), not a possibly-stale
# `license` package attribute left by another test's module surgery.
fl = importlib.import_module("license.feature_lattice")  # type: ignore
sv = importlib.import_module("license.shard_verifier")  # type: ignore


@pytest.fixture(autouse=True)
def _reset_root():
    fl.set_feature_root_key(None)
    yield
    fl.set_feature_root_key(None)


def _make_instance_id(home: Path, iid: str = "abcd1234-0000-0000-0000-000000000000") -> None:
    d = home / "global"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "instance_id.json"
    p.write_text(json.dumps({"instance_id": iid}), encoding="utf-8")
    os.chmod(p, 0o600)


def test_shard_d_free_consistent():
    fl.set_feature_root_key(None)
    r = sv._check_shard_d(tier_is_free=True)
    assert r["status"] == sv.OK


def test_shard_d_divergence_free_tier_paid_root():
    fl.set_feature_root_key("CORVIN-x.y.z")  # paid root installed
    r = sv._check_shard_d(tier_is_free=True)  # but tier says free
    assert r["status"] == sv.WARN
    assert "divergence" in r["detail"]


def test_shard_b_ok_with_good_instance_file(tmp_path):
    _make_instance_id(tmp_path)
    r = sv._check_shard_b(tmp_path)
    assert r["status"] == sv.OK


def test_shard_b_fail_on_permissive_mode(tmp_path):
    _make_instance_id(tmp_path)
    os.chmod(tmp_path / "global" / "instance_id.json", 0o644)
    r = sv._check_shard_b(tmp_path)
    assert r["status"] == sv.FAIL


def test_shard_b_warn_when_absent(tmp_path):
    r = sv._check_shard_b(tmp_path)
    assert r["status"] == sv.WARN


def test_shard_c_ok_when_no_chain(tmp_path):
    r = sv._check_shard_c(tmp_path, paid_root_active=False)
    assert r["status"] == sv.OK


def test_verify_shards_clean_free_install(tmp_path):
    _make_instance_id(tmp_path)
    fl.set_feature_root_key(None)
    report = sv.verify_shards(corvin_home=tmp_path)
    assert report["aggregate"] in (sv.OK, sv.WARN)
    assert {s["shard"] for s in report["shards"]} == {"A", "B", "C", "D"}


def test_selftest_warn_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    sv.selftest_warn()  # must not raise


def test_cli_human_and_json(tmp_path, monkeypatch, capsys):
    _make_instance_id(tmp_path)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import license_debug_cli as cli  # type: ignore

    rc = cli.main(["--no-load"])
    out = capsys.readouterr().out
    assert "license diagnosis" in out
    assert rc in (0, 1)

    rc2 = cli.main(["--no-load", "--json"])
    out2 = capsys.readouterr().out
    parsed = json.loads(out2)
    assert "aggregate" in parsed and "shards" in parsed
    assert rc2 in (0, 1)
