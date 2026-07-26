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
    text = consumer.read_text(encoding="utf-8", errors="replace")
    # The consumer either calls get_active() on the provider module, or (for
    # audit_backend) imports the module to bind a sink. Both are real use.
    module = surface.provider_module or surface.plugin_type
    assert re.search(r"get_active\(\)", text) or module in text, (
        f"{surface.plugin_type}: {surface.consumed_by} does not appear to "
        f"invoke the registry"
    )


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
        "user_backend",
        "stt_provider",
        "data_connector",
        "compute_engine",
        "worker_engine",
        "bridge_channel",
    }
    assert set(consumed_types()) == {
        "router_backend",
        "summary_provider",
        "notification_backend",
        "recall_backend",
        "audit_backend",
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
