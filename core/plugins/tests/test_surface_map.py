"""The surface map must describe the tree, not a memory of it (ADR-0245).

Two classes of assertion live here, and the second is the one that earns its keep.

**Structural** — every column of every row names something real: the type is in
``KNOWN_PLUGIN_TYPES``, the ctx handle is a ``PluginContext`` field, the template
file exists, the provider module imports.

**Call-site** — the ``consumed_by`` claim is checked against the actual tree by
searching for a real invocation, and the ``consumed_by=None`` claim is checked the
same way in reverse. That second direction is the point: a unit test proves a
mechanism works *when called*, never that anything calls it. Six of the eleven
plugin types currently register successfully and are never invoked, and no test in
this repo noticed. If someone wires one up, the "still dead" assertion fails and
forces the map to be corrected — a red test on GOOD news, which is the only way
the map stays true without anyone remembering to update it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from corvin_plugins.protocol import KNOWN_PLUGIN_TYPES, PluginContext
from corvin_plugins.surface_map import (
    SURFACES,
    all_types,
    buildable_types,
    consumed_types,
    surface_for,
    unconsumed_types,
)

_REPO = Path(__file__).resolve().parents[3]
_TEMPLATES = _REPO / "core" / "plugins" / "templates"
_PROVIDERS = _REPO / "core" / "plugins" / "corvin_plugins" / "providers"


# ── Structural: the map names real things ────────────────────────────────────

def test_map_covers_exactly_the_known_plugin_types():
    """No type may be in one list and missing from the other.

    Both directions matter. A type in KNOWN_PLUGIN_TYPES but not the map is a
    surface nobody documented; a type in the map but not KNOWN_PLUGIN_TYPES is a
    surface that cannot be declared in a manifest.
    """
    assert set(all_types()) == set(KNOWN_PLUGIN_TYPES)


def test_no_duplicate_rows():
    types = [s.plugin_type for s in SURFACES]
    assert len(types) == len(set(types)), f"duplicate rows: {types}"


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_ctx_handle_is_a_real_plugin_context_field(surface):
    """A handle that does not exist means on_load() would raise AttributeError."""
    assert hasattr(PluginContext, "__dataclass_fields__")
    assert surface.ctx_handle in PluginContext.__dataclass_fields__, (
        f"{surface.plugin_type}: ctx_handle {surface.ctx_handle!r} is not a "
        f"PluginContext field"
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_template_file_exists_when_claimed(surface):
    if surface.template is None:
        return
    assert (_TEMPLATES / surface.template).is_file(), (
        f"{surface.plugin_type}: template {surface.template!r} does not exist"
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_provider_module_exists_when_claimed(surface):
    if surface.provider_module is None:
        return
    assert (_PROVIDERS / f"{surface.provider_module}.py").is_file(), (
        f"{surface.plugin_type}: provider module "
        f"{surface.provider_module!r} does not exist"
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_every_surface_states_an_invariant(surface):
    """An empty invariant means an author has nothing to meet — refuse it."""
    assert surface.invariant.strip(), f"{surface.plugin_type}: empty invariant"


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_dead_reason_present_iff_unconsumed(surface):
    """A dead surface without a reason is an assertion the reader cannot check."""
    if surface.consumed:
        assert surface.dead_reason is None, (
            f"{surface.plugin_type}: consumed but carries a dead_reason"
        )
    else:
        assert surface.dead_reason, (
            f"{surface.plugin_type}: unconsumed but gives no dead_reason"
        )


#: Phrasings that make one row's cause depend on another row's.
#:
#: This is not a style rule. Three rows — worker_engine, bridge_channel and
#: (in effect) user_backend — carried a cause that was only ever true of
#: compute_engine, because "Same as compute_engine: the handle is never passed"
#: was cheaper to write than checking. It was wrong for both: engine_factory has
#: no ``register()`` at all, and channel_registry does not exist. A reader
#: following that cause would pass the handle, watch nothing change, and have no
#: idea why.
_DEFERRING_PHRASES = (
    "same as",
    "see above",
    "as above",
    "ditto",
    "likewise",
    "same reason",
)


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_dead_reason_stands_on_its_own(surface):
    """A cause may not be delegated to another row.

    ``surface_map`` is what ``corvin plugin types`` prints, so a wrong cause is
    not an internal note — it is the instruction a plugin author acts on. A
    deferring cause is additionally the one shape that goes stale invisibly: fix
    the row it points at and this row still reads as explained.
    """
    if surface.consumed:
        return
    lowered = surface.dead_reason.lower()
    hit = next((p for p in _DEFERRING_PHRASES if p in lowered), None)
    assert hit is None, (
        f"{surface.plugin_type}: dead_reason defers to another row via {hit!r}. "
        f"State this surface's own cause — the three rows that did this named a "
        f"cause that was false for them.\n  {surface.dead_reason}"
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.plugin_type)
def test_dead_reason_names_something_in_the_tree(surface):
    """The cause must point at an identifier a reader can go and look at.

    Weaker than it sounds, and deliberately so: the assertion is only that the
    reason cites *something* — a module, a function, a config key — rather than
    describing a feeling about the code. A cause with no referent cannot be
    checked, and an unfalsifiable cause is how the three wrong ones survived
    review.
    """
    if surface.consumed:
        return
    assert re.search(r"[\w/]+\.py\b|\w+\(\)|\bspec\.[\w.]+", surface.dead_reason), (
        f"{surface.plugin_type}: dead_reason cites no file, call or config key, "
        f"so a reader cannot verify it:\n  {surface.dead_reason}"
    )


def test_every_template_on_disk_is_claimed_by_the_map():
    """A template nobody references is one an author will never be pointed at."""
    on_disk = {p.name for p in _TEMPLATES.glob("*_plugin.py")}
    claimed = {s.template for s in SURFACES if s.template}
    assert on_disk == claimed, (
        f"templates on disk but not in the map: {sorted(on_disk - claimed)}; "
        f"in the map but not on disk: {sorted(claimed - on_disk)}"
    )


# ── Call-site: does anything actually invoke this? ───────────────────────────

def _grep_repo(pattern: str, *, exclude: tuple[str, ...]) -> list[str]:
    """Search tracked .py files, returning 'path:line' hits.

    Uses git grep so the search matches what is committed rather than whatever
    stray files exist in the worktree.
    """
    try:
        out = subprocess.run(
            ["git", "grep", "-n", "-E", pattern, "--", "*.py"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git grep unavailable: {exc}")
    hits = []
    for line in out.stdout.splitlines():
        if any(frag in line for frag in exclude):
            continue
        hits.append(line)
    return hits


#: Paths that describe the mechanism rather than using it. A match inside the
#: providers package, the tests, or a template is not evidence of a consumer.
_NOT_A_CONSUMER = (
    "core/plugins/corvin_plugins/",
    "core/plugins/tests/",
    "core/plugins/templates/",
    "core/console/tests/",
    "tests/",
)


@pytest.mark.parametrize(
    "surface", [s for s in SURFACES if s.consumed], ids=lambda s: s.plugin_type
)
def test_claimed_consumer_file_exists_and_invokes_the_registry(surface):
    """`consumed_by` must name a file that really calls into the registry."""
    consumer = _REPO / surface.consumed_by
    assert consumer.is_file(), (
        f"{surface.plugin_type}: consumed_by names {surface.consumed_by!r}, "
        f"which does not exist"
    )
    if surface.provider_module is None:
        # The registry belongs to another subsystem (compute / engine / bridge),
        # so there is no providers/ module to resolve an alias against. What the
        # consumer must show instead is that it hands that subsystem's registry
        # in through the plugin context handle — for compute_engine,
        # `bootstrap_declared(..., compute_registry=get_registry())` in the
        # COMPUTE WORKER, which is the process that dispatches.
        text = consumer.read_text(encoding="utf-8", errors="replace")
        assert surface.ctx_handle in text, (
            f"{surface.plugin_type}: {surface.consumed_by} never mentions the "
            f"context handle {surface.ctx_handle!r}, so it cannot be handing the "
            f"registry to a plugin"
        )
        return

    module = surface.provider_module
    called = _provider_calls(consumer, module)
    # A CALL, not a mention. The rule used to be `module in text` — the bare
    # string "audit_backend" appearing anywhere in the file — which accepts a
    # file that only imports the module and never touches it. A fuzzy alias
    # regex was tried as the replacement and was worse: `_\w*audit\w*\.\w+\(`
    # matched `_audit_metrics.render(`, an unrelated module. So the binding is
    # RESOLVED rather than guessed — find what this file imported the provider
    # AS, then require a call on that exact name.
    #
    # KNOWN LIMIT, stated rather than hidden. This does NOT distinguish a real
    # consumer from an incidental one. audit_backend's row pointed at
    # core/gateway/corvin_gateway/app.py until 2026-07-27, and that file does
    # call the provider — `drain_now()`, once, in a shutdown flush — so this
    # guard would have passed it just as the old one did. The real fan-out is
    # `fanout()` in operator/bridges/shared/audit.py, after the core write
    # commits. No cheap static rule separates "calls it on the hot path" from
    # "calls it while shutting down"; that one needed an audit, exactly like
    # user_backend's wrong dead_reason did. What this guard now buys is the
    # narrower, still-worthwhile pair: an import with no call at all, and a
    # same-prefix module mistaken for the provider.
    assert called, (
        f"{surface.plugin_type}: {surface.consumed_by} imports {module!r} but "
        f"never calls anything on it — a consumed_by row must name the file "
        f"that CALLS the provider, not one that merely imports it"
    )


def _provider_calls(path, module: str) -> list[str]:
    """Attributes invoked on ``module`` in ``path``, resolving the local alias.

    Handles the two import shapes this tree uses::

        from corvin_plugins.providers import audit_backend as _audit_sink
        from corvin_plugins.providers import summary_provider as _summary_prov

    and counts ``<alias>.<attr>(...)`` calls on the resolved name only.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
            "providers"
        ):
            for a in node.names:
                if a.name == module:
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith(f"providers.{module}"):
                    aliases.add(a.asname or a.name.rsplit(".", 1)[-1])
    return [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in aliases
    ]


def _consumer_pattern(module: str) -> str:
    """Regex matching a real import-or-call of a provider module.

    Must cover the shape actually used in this tree:
        from corvin_plugins.providers import router_backend as _router_prov
    An earlier version used ``providers[. ]*<module>``, which cannot match that
    line — ` import ` sits between the two halves — so the search returned zero
    hits for every module and the "still dead" assertion below was vacuously
    green forever. ``test_grep_finds_a_known_live_consumer`` exists to make that
    class of breakage impossible to reintroduce silently.
    """
    return (
        rf"providers import [^#]*\b{module}\b"
        rf"|providers\.{module}\b"
        rf"|\b{module}\.get_active"
    )


def test_grep_finds_a_known_live_consumer():
    """Positive control for the search itself.

    Without this, a broken pattern makes every "is still unconsumed" assertion
    pass by finding nothing, and the suite reports health it never checked. This
    pins one case we know is true — adapter.py imports router_backend — so the
    search failing silently is itself a failure.
    """
    hits = _grep_repo(_consumer_pattern("router_backend"), exclude=_NOT_A_CONSUMER)
    assert any("adapter.py" in h for h in hits), (
        "the consumer search found no live consumer for router_backend — the "
        f"grep pattern is broken, so every unconsumed assertion is vacuous. "
        f"hits={hits}"
    )


@pytest.mark.parametrize(
    "surface",
    [s for s in SURFACES if not s.consumed and s.provider_module],
    ids=lambda s: s.plugin_type,
)
def test_unconsumed_surface_is_still_unconsumed(surface):
    """Fails when someone wires up a dead provider without updating the map.

    This is deliberately a test that breaks on an IMPROVEMENT. Wiring
    `user_backend` into the auth path is exactly what should happen — and when it
    does, this assertion turns red and the map must be corrected to say so.
    Without it, the map would quietly keep calling a live mechanism dead, which is
    the same drift in the opposite direction.
    """
    hits = _grep_repo(
        _consumer_pattern(surface.provider_module), exclude=_NOT_A_CONSUMER
    )
    assert not hits, (
        f"{surface.plugin_type} is marked unconsumed but these look like "
        f"consumers now — update surface_map.consumed_by:\n  "
        + "\n  ".join(hits[:5])
    )


def test_the_unconsumed_set_is_recorded_not_incidental():
    """Pin the known-dead set so a change is a deliberate edit, not a drift.

    If this fails, do not just update the numbers — work out which direction the
    change went. A surface leaving this set is good news; one joining it means a
    consumer was deleted and a plugin type went dark.
    """
    assert set(unconsumed_types()) == {
        "user_backend",     # no credential auth path exists to call it
        "stt_provider",     # L23 resolves its own chain
        "data_connector",   # L24 resolves its own DSI adapters
        "worker_engine",    # L22 engine_registry has no register()
        "bridge_channel",   # no channel_registry class exists
    }
    assert set(consumed_types()) == {
        "router_backend",
        "summary_provider",
        "notification_backend",
        "recall_backend",
        "audit_backend",
        # Moved out of the dead set 2026-07-27 (Stage 4). NOT by passing the
        # handle in the gateway, which is what the plan asked for and could
        # never have worked — by loading compute_engine plugins in the COMPUTE
        # WORKER, the process where WorkerServer actually dispatches.
        "compute_engine",
    }


# ── Lookup helpers ───────────────────────────────────────────────────────────

def test_surface_for_returns_the_row():
    assert surface_for("router_backend").ctx_handle == "router_registry"


def test_surface_for_unknown_type_lists_the_known_ones():
    with pytest.raises(KeyError) as exc:
        surface_for("nonsense_backend")
    assert "router_backend" in str(exc.value)


def test_buildable_types_are_exactly_those_with_templates():
    assert set(buildable_types()) == {
        s.plugin_type for s in SURFACES if s.template
    }


# ── The compute_engine handle: do not "fix" it by passing it ─────────────────


def test_the_compute_worker_still_loads_compute_engine_plugins():
    """The reverse guard: fails when Stage 4's call site is DELETED.

    An earlier version of this asserted the opposite — that the compute registry
    had no reader — and it was correct when written. Stage 4 gave it one:
    `corvin_compute/cli.py::_load_compute_engine_plugins` loads the tenant's
    `compute_engine` plugins IN THE WORKER PROCESS and hands what landed in the
    registry to `WorkerServer(extra_engines=...)`.

    Note what the old guard would NOT have caught, which is why it is replaced
    rather than kept: it matched `get_by_job_id(` and an absolute import, and the
    reader that arrived uses `discover()` / `get()` behind a relative import. A
    guard whose pattern is narrower than the thing it guards reports health it
    never checked — the same vacuous-green shape this suite exists to prevent.
    So this one anchors on the function name, which is the call site itself.
    """
    hits = _grep_repo(
        r"_load_compute_engine_plugins",
        exclude=("core/plugins/tests/",),
    )
    assert any("corvin_compute/cli.py" in h for h in hits), (
        "the compute worker no longer loads compute_engine plugins — a plugin "
        "would register into a registry nothing reads again. Correct "
        "surface_map's compute_engine row in the same commit.\n  "
        + "\n  ".join(hits[:5])
    )


def test_the_worker_only_loads_compute_engines(): 
    """The type filter is a safety property, not a tidiness one.

    Without `only_types` the compute worker would load the tenant's bridge
    supervisors too and start messenger daemons from the compute process — a
    second set racing the real ones, which ADR-0238 names as its load-bearing
    duplicate-start invariant. Pinned here as well as in
    `test_compute_engine_call_site.py` because deleting it is a one-word edit in
    a file that has nothing to do with bridges.
    """
    hits = _grep_repo(r'only_types=frozenset\(\{"compute_engine"\}\)', exclude=())
    assert any("corvin_compute/cli.py" in h for h in hits), (
        "the compute worker's plugin load is no longer type-filtered — it will "
        "start bridge daemons from the compute process"
    )


def test_the_worker_registration_surface_is_still_unreachable_from_a_plugin():
    """`WorkerServer.register_engine()` exists and nothing production calls it.

    The second half of the same finding, pinned separately because the two can
    be fixed independently: a reader for the standalone registry, or a
    cross-process path to the worker's own dict. Either one changes what the
    `compute_engine` row should say.
    """
    hits = _grep_repo(
        r"\.register_engine\(",
        exclude=(
            "core/compute/corvin_compute/worker.py",
            "core/compute/tests/",
            "operator/bridges/shared/test_",
            "operator/bridges/shared/adapter.py",  # _register_engine, different API
            "core/plugins/tests/",
        ),
    )
    assert not hits, (
        "WorkerServer.register_engine() has a caller now — a compute_engine "
        "plugin may finally have a route to the worker. Re-check the "
        "compute_engine row:\n  " + "\n  ".join(hits[:5])
    )
