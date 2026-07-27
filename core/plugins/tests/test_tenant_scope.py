"""A stranger may not take a process-wide provider slot (ADR-0250 D1).

The rule under test: a provider plugin that is not `origin=builtin` may not
occupy a provider slot on an install with more than one tenant, because the slot
is process-wide and the plugin would therefore see every tenant's data.

Two directions matter equally and the second is the one that rots:

* the refusal fires when it should — multi-tenant, non-builtin, provider type;
* it does **not** fire on the default single-tenant install, on a bundled
  passthrough provider, or on a plugin type that takes no slot at all. A gate
  that refuses everything is indistinguishable from a broken feature, and this
  one sits in the boot path of every install.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The real hash-chained writer lives in `operator/bridges/shared/audit.py`, which
# is not on the path of a bare pytest run. Without this the audit assertion below
# passes through `_default_audit_emit`'s ImportError branch and the refusal looks
# unaudited — a test failure that says nothing about the code under test. Same
# extension `test_bootstrap.py` performs, for the same reason.
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (
    str(_HERE.parents[1]),
    str(_REPO / "operator" / "forge"),
    str(_REPO / "operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import bootstrap, tenant_scope  # noqa: E402
from corvin_plugins.protocol import HealthStatus, KNOWN_PLUGIN_TYPES  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


def _home(tmp_path: Path, tenants: list[str]) -> Path:
    home = tmp_path / "corvin"
    for name in tenants:
        (home / "tenants" / name).mkdir(parents=True)
    return home


# ── count_tenants ────────────────────────────────────────────────────────────

def test_no_tenants_dir_counts_as_one(tmp_path):
    """A fresh install has one tenant by construction.

    This is the one absent-means-permissive case, and it is safe because the
    alternative refuses every provider plugin on a first boot — a gate nobody
    could ever satisfy is a gate everyone routes around.
    """
    assert tenant_scope.count_tenants(tmp_path / "nothing-here") == 1


def test_counts_tenant_directories(tmp_path):
    home = _home(tmp_path, ["_default", "acme", "globex"])
    assert tenant_scope.count_tenants(home) == 3


def test_files_in_the_tenants_dir_are_not_tenants(tmp_path):
    home = _home(tmp_path, ["_default"])
    (home / "tenants" / "README.md").write_text("not a tenant")
    assert tenant_scope.count_tenants(home) == 1


def test_unreadable_returns_none_not_a_permissive_number(tmp_path, monkeypatch):
    """The load-bearing failure direction.

    `None` is not `0` and not `1`. Both of those are permissive answers, and an
    unanswerable question must never produce a permissive answer — ADR-0238's
    "could not check is not nothing is running", applied to enumeration.
    """
    home = _home(tmp_path, ["_default", "acme"])

    def _boom(self):
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "iterdir", _boom)
    assert tenant_scope.count_tenants(home) is None


# ── evaluate ─────────────────────────────────────────────────────────────────

def test_non_provider_type_is_never_refused(tmp_path):
    """`compute_engine` and friends register elsewhere and take no slot."""
    home = _home(tmp_path, ["_default", "acme", "globex"])
    for plugin_type in KNOWN_PLUGIN_TYPES - tenant_scope.PROVIDER_PLUGIN_TYPES:
        decision = tenant_scope.evaluate(
            plugin_type=plugin_type, origin="community", corvin_home=home
        )
        assert decision.allowed, plugin_type
        assert decision.reason == "not_a_provider_type"


@pytest.mark.parametrize("plugin_type", sorted(tenant_scope.PROVIDER_PLUGIN_TYPES))
def test_single_tenant_is_unaffected(tmp_path, plugin_type):
    """The default install must behave exactly as before this gate existed."""
    home = _home(tmp_path, ["_default"])
    decision = tenant_scope.evaluate(
        plugin_type=plugin_type, origin="community", corvin_home=home
    )
    assert decision.allowed
    assert decision.reason == "single_tenant"


@pytest.mark.parametrize("plugin_type", sorted(tenant_scope.PROVIDER_PLUGIN_TYPES))
def test_multi_tenant_refuses_a_community_provider(tmp_path, plugin_type):
    home = _home(tmp_path, ["_default", "acme"])
    decision = tenant_scope.evaluate(
        plugin_type=plugin_type, origin="community", corvin_home=home
    )
    assert not decision.allowed
    assert decision.reason == "multi_tenant_provider_slot"
    assert decision.tenant_count == 2


def test_builtin_is_exempt_on_a_multi_tenant_install(tmp_path):
    """The bundled passthrough providers must keep working everywhere."""
    home = _home(tmp_path, ["_default", "acme", "globex"])
    decision = tenant_scope.evaluate(
        plugin_type="audit_backend", origin="builtin", corvin_home=home
    )
    assert decision.allowed
    assert decision.reason == "origin_builtin"


def test_vetted_is_NOT_exempt(tmp_path):
    """The exemption that would quietly widen if nobody pinned it.

    A maintainer signature (ADR-0249) attests who wrote the code. It does not
    attest that the plugin's storage path, cache key or auth decision is
    tenant-aware. Those are different claims, and treating `vetted` as "safe
    enough" would let a signed plugin read every tenant's audit events.
    """
    home = _home(tmp_path, ["_default", "acme"])
    decision = tenant_scope.evaluate(
        plugin_type="user_backend", origin="vetted", corvin_home=home
    )
    assert not decision.allowed
    assert decision.reason == "multi_tenant_provider_slot"


def test_unknown_origin_is_treated_as_not_builtin(tmp_path):
    """The declarative path supplies no origin, and must not thereby be exempt.

    `spec.plugins.installed` entries carry no `origin` field. An operator writing
    a class path into a tenant config is an explicit opt-in for THAT tenant and
    says nothing about the others whose data the slot reaches.
    """
    home = _home(tmp_path, ["_default", "acme"])
    decision = tenant_scope.evaluate(
        plugin_type="recall_backend", origin=None, corvin_home=home
    )
    assert not decision.allowed


def test_enumeration_failure_refuses(tmp_path, monkeypatch):
    home = _home(tmp_path, ["_default"])
    monkeypatch.setattr(tenant_scope, "count_tenants", lambda _home: None)
    decision = tenant_scope.evaluate(
        plugin_type="audit_backend", origin="community", corvin_home=home
    )
    assert not decision.allowed
    assert decision.reason == "tenant_enumeration_failed"
    assert decision.tenant_count is None


# ── the set itself ───────────────────────────────────────────────────────────

def test_provider_types_are_all_real_plugin_types():
    unknown = tenant_scope.PROVIDER_PLUGIN_TYPES - KNOWN_PLUGIN_TYPES
    assert not unknown, (
        f"PROVIDER_PLUGIN_TYPES names types that do not exist: {sorted(unknown)}"
    )


# ── The call site: is the gate REACHED? ──────────────────────────────────────
#
# Everything above proves the rule is correct. None of it proves the load path
# consults it — which is the defect class this whole plan exists to close. A
# mechanism that is green in its own tests and never called is exactly what
# ADR-0233 found twice in its own implementation.

class _SlotTaker:
    """A declared provider plugin. Records whether on_load ever ran."""

    plugin_id = "test.slot-taker"
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Slot Taker"

    loaded = False

    def on_load(self, ctx):
        type(self).loaded = True

    def on_unload(self):
        type(self).loaded = False

    def health_check(self):
        return HealthStatus(ok=True)

    def notify(self, event, payload, *, tenant_id="_default", severity="info"):
        pass


@pytest.fixture
def declared_install(tmp_path, monkeypatch):
    """A corvin_home declaring `_SlotTaker`, parameterised by tenant count."""

    def _build(tenants: list[str]) -> Path:
        home = tmp_path / "corvin"
        for name in tenants:
            (home / "tenants" / name).mkdir(parents=True)
        cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "spec:\n"
            "  plugins:\n"
            "    installed:\n"
            "      - id: test.slot-taker\n"
            "        class_path: test_tenant_scope:_SlotTaker\n",
            encoding="utf-8",
        )
        return home

    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    _SlotTaker.loaded = False
    for pid in list(get_registry().discover()):
        get_registry().unregister(pid)
    yield _build
    for pid in list(get_registry().discover()):
        try:
            get_registry().unregister(pid)
        except Exception:
            pass
    _SlotTaker.loaded = False


def test_call_site_single_tenant_still_loads(declared_install):
    """The pre-feature path, unchanged. If this breaks, the gate is too wide."""
    home = declared_install(["_default"])
    loaded = bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=home)
    assert loaded == ["test.slot-taker"]
    assert _SlotTaker.loaded, "on_load did not run on a single-tenant install"


def test_call_site_multi_tenant_refuses_before_on_load(declared_install):
    """The assertion that proves the gate is wired, not merely correct.

    `on_load()` is where a provider hands itself to `set_active()`. Asserting it
    never ran is stronger than asserting the id is missing from the registry:
    a gate that refused *after* on_load would leave the slot taken while the
    registry looked clean.
    """
    home = declared_install(["_default", "acme"])
    loaded = bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=home)
    assert loaded == []
    assert not _SlotTaker.loaded, (
        "the plugin's on_load ran on a multi-tenant install — the ADR-0250 gate "
        "is either not reached or runs too late to stop the slot being taken"
    )
    assert "test.slot-taker" not in get_registry().discover()


def test_call_site_refusal_is_audited(declared_install):
    """A silent refusal reproduces the failure mode this area keeps producing."""
    home = declared_install(["_default", "acme"])
    audit = Path(os.environ["VOICE_AUDIT_PATH"])
    bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=home)
    assert audit.is_file(), "the refusal wrote no audit record at all"
    body = audit.read_text(encoding="utf-8")
    assert "plugin.provider_slot_refused" in body
    # The count, never the ids. Recording which other tenants exist to close a
    # tenant-isolation gap would be a net loss.
    assert "acme" not in body, (
        "the refusal event leaked another tenant's id — it must carry the count "
        "only"
    )


def test_provider_types_match_the_provider_modules():
    """The list is explicit, so it must be checked against the tree.

    Deriving it from `KNOWN_PLUGIN_TYPES` would have silently included the three
    types that register into other subsystems. Listing it by hand means it can
    silently fall behind instead — hence this test, which is the price of the
    explicit list.
    """
    providers = Path(__file__).resolve().parents[1] / "corvin_plugins" / "providers"
    on_disk = {
        p.stem for p in providers.glob("*.py") if not p.stem.startswith("_")
    }
    assert tenant_scope.PROVIDER_PLUGIN_TYPES == on_disk, (
        f"in the set but no provider module: "
        f"{sorted(tenant_scope.PROVIDER_PLUGIN_TYPES - on_disk)}; "
        f"module on disk but not in the set: "
        f"{sorted(on_disk - tenant_scope.PROVIDER_PLUGIN_TYPES)}"
    )
