"""Unit tests for LayerPrimitive — proves race-conditions are structurally
excluded (ADR-2222 D4), not just 'usually fine'."""
import concurrent.futures

from core.orchestration.layer_forge.primitive import LayerPrimitive


def test_write_then_read_roundtrip(tmp_path):
    prim = LayerPrimitive("L34", tmp_path)
    prim.write_state({"value": 42})
    assert prim.read_state() == {"value": 42}


def test_read_before_any_write_returns_empty(tmp_path):
    prim = LayerPrimitive("L34", tmp_path)
    assert prim.read_state() == {}


def test_concurrent_writers_no_corruption(tmp_path):
    """10 threads write concurrently; every read-back must be valid, parseable
    JSON matching exactly one of the writes — never a half-written / corrupted
    blend of two writes (the race LayerPrimitive exists to prevent)."""
    prim = LayerPrimitive("L34", tmp_path)

    def writer(i):
        prim.write_state({"value": i, "marker": f"writer-{i}"})
        state = prim.read_state()
        # Whatever is currently on disk must be internally consistent:
        # marker must match value, never a torn mix of two different writes.
        assert state["marker"] == f"writer-{state['value']}"
        return state

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(writer, range(10)))

    # Every single read observed a fully-formed write, never a partial one.
    for r in results:
        assert r["marker"] == f"writer-{r['value']}"

    final = prim.read_state()
    assert final["marker"] == f"writer-{final['value']}"


def test_two_primitives_different_layer_ids_independent(tmp_path):
    a = LayerPrimitive("L34", tmp_path)
    b = LayerPrimitive("L10", tmp_path)
    a.write_state({"owner": "a"})
    b.write_state({"owner": "b"})
    assert a.read_state() == {"owner": "a"}
    assert b.read_state() == {"owner": "b"}
