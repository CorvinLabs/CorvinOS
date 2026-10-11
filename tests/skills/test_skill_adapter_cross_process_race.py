"""The router config's read-modify-write is safe ACROSS PROCESSES (bridge + console share it).

The HTTP-level concurrency test cannot show this: every request runs on one event loop, and the
handler has no ``await`` between read and write, so requests are effectively serialised and the
``flock`` is never contended. Here real OS processes each apply deltas to the SAME config file.
``SkillAdapter._locked`` must serialise them AND reload inside the lock; otherwise two writers that
both loaded version N both persist N+1 and an update (and its version id) is lost.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path

import pytest

SKILL = "os.delegation_router"
TENANT = "_default"
PROCS, PER_PROC, STEP = 4, 6, 0.005


def _worker(home: str, anchor: str, n: int) -> int:
    os.environ["CORVIN_HOME"] = home
    os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = anchor
    os.environ.setdefault("FORGE_ROOT", str(Path(home) / "forge"))
    from core.skills.os_skills.skill_adapter import SkillAdapter

    done = 0
    for _ in range(n):
        SkillAdapter(SKILL, TENANT).apply_config_delta({"confidence_threshold": STEP}, confidence_delta=0.0)
        done += 1
    return done


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin-home"
    (home / "tenants" / TENANT / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    anchor = str(tmp_path / "anchor.key")
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", anchor)
    monkeypatch.setenv("FORGE_ROOT", str(home / "forge"))
    return home, anchor


def test_processes_sharing_one_config_lose_no_update_and_no_version(sandbox):
    home, anchor = sandbox
    from core.skills.os_skills.skill_adapter import load_skill_config, skill_config_path

    ctx = mp.get_context("spawn")
    with ctx.Pool(PROCS) as pool:
        done = pool.starmap(_worker, [(str(home), anchor, PER_PROC)] * PROCS)
    assert sum(done) == PROCS * PER_PROC

    data = json.loads(skill_config_path(SKILL, TENANT).read_text())
    ids = [v["version_id"] for v in data["versions"]]
    total = PROCS * PER_PROC
    assert len(ids) == len(set(ids)), f"two writers produced the same version id: {sorted(ids)}"
    assert sorted(ids, key=lambda v: int(v[1:])) == [f"v{i}" for i in range(1, total + 1)], (
        f"{len(ids)} versions recorded for {total} applied deltas: an update was lost")

    cfg, version = load_skill_config(SKILL, TENANT)
    assert version == f"v{total}"
    assert round(cfg.confidence_threshold, 3) == round(0.70 + STEP * total, 3)
