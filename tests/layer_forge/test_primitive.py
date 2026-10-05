"""LayerPrimitive — the lock every registry read-modify-write runs under (ADR-2222 D4)."""
import concurrent.futures
import json
import threading

import pytest

from core.orchestration.layer_forge.primitive import (
    LayerPrimitive,
    LockTimeoutError,
    atomic_write_json,
)


def test_atomic_write_roundtrip_leaves_no_temp_files(tmp_path):
    target = tmp_path / "x.json"
    atomic_write_json(target, {"value": 42})
    assert json.loads(target.read_text()) == {"value": 42}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_read_modify_write_under_lock_loses_no_update(tmp_path):
    """40 concurrent increments of one counter file: the final value is exactly 40.
    Without mutual exclusion two writers read the same value and one increment is lost."""
    counter = tmp_path / "counter.json"
    atomic_write_json(counter, {"n": 0})
    prim = LayerPrimitive("entry", tmp_path / "locks")

    def bump(_):
        with prim.locked():
            n = json.loads(counter.read_text())["n"]
            atomic_write_json(counter, {"n": n + 1})

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(bump, range(40)))

    assert json.loads(counter.read_text())["n"] == 40


def test_lock_is_exclusive_while_held(tmp_path):
    prim = LayerPrimitive("entry", tmp_path)
    held, release = threading.Event(), threading.Event()

    def holder():
        with prim.locked():
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    with pytest.raises(LockTimeoutError):
        with LayerPrimitive("entry", tmp_path).locked(timeout_s=0.2):
            pass
    release.set()
    t.join()
    with prim.locked(timeout_s=1):
        pass  # released again


def test_different_entry_ids_do_not_block_each_other(tmp_path):
    with LayerPrimitive("a", tmp_path).locked():
        with LayerPrimitive("b", tmp_path).locked(timeout_s=0.2):
            pass
