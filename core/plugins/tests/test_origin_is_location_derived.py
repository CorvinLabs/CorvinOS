"""R2-A4: ``origin: builtin`` in registry.yaml must buy nothing.

``registry.yaml`` is per-tenant, operator-writable state — the same side of the
trust boundary as ``tenant.corvin.yaml``, which is why a privileged
``boot_layer`` claimed there is already downgraded. Its ``origin:`` line was
not: it was read straight into ``PluginRecord.origin`` and from there into

  * ``trust.evaluate`` (ADR-0249), where ``origin == "builtin"`` returns
    ``Verdict.BUILTIN`` — "ships with CorvinOS" — before any signature check;
  * ``tenant_scope.evaluate`` (ADR-0250), where ``origin == "builtin"`` is the
    ONE exemption from the multi-tenant provider-slot refusal.

So two lines of YAML naming any importable class took a process-wide provider
slot on a multi-tenant install — a slot that sees every tenant's data. The
reviewer demonstrated it with ``class_path: r2evil:EvilAudit``; this test uses
the same shape.

Round 2 derives the origin from where the class FILE lives
(``bootstrap._origin_for_class_path`` → ``origin_for_plugin_dir``, the same
function the discovery path already used) and downgrades the stored claim on
read (``state._downgrade_claimed_origin``).

The load runs in a SUBPROCESS with its own registry, module state and chain, so
this exercises the real lifecycle rather than a re-imported module in the
test's own interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]

_EVIL = '''
class EvilAudit:
    plugin_id = "r2evil"
    plugin_type = "audit_backend"
    version = "1.0.0"

    def on_load(self, ctx):
        self.ctx = ctx

    def on_unload(self):
        pass

    def health_check(self):
        from corvin_plugins.protocol import HealthStatus
        return HealthStatus(ok=True, message="evil")

    # audit_backend provider surface
    def write(self, record):
        return None
'''

_CHILD = r'''
import json, os, sys
repo = Path = None
from pathlib import Path
repo = Path(os.environ["REPO"]); home = Path(os.environ["CORVIN_HOME"])
for p in ("core/plugins", "operator/bridges/shared", "operator/forge", "operator",
          "operator/license"):
    sys.path.insert(0, str(repo / p))
sys.path.insert(0, os.environ["EVIL_DIR"])

from corvin_plugins import bootstrap
from corvin_plugins.registry import get_registry
from corvin_plugins.providers import audit_backend as ab
from corvin_plugins.state import TenantRegistry

loaded = bootstrap.bootstrap_all(
    tenant_id="_default", corvin_home=home,
    tenant_config={"spec": {"plugins": {}}}, lifecycle_enabled=True,
)
reg = TenantRegistry.load(tenant_id="_default")
rec = reg.records.get("r2evil")
print("RESULT " + json.dumps({
    "loaded": loaded,
    "registered": get_registry().discover(),
    "provider_slot_owner": ab.owner_plugin_id(),
    "stored_origin_after_read": rec.origin.value if rec else None,
}))
'''


def _write_registry(home: Path, origin: str) -> None:
    reg_dir = home / "tenants" / "_default" / "plugins"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "registry.yaml").write_text(textwrap.dedent(f"""\
        spec:
          schema_version: "1.0"
        plugins:
          r2evil:
            plugin_id: r2evil
            version: "1.0.0"
            display_name: r2evil
            plugin_type: audit_backend
            origin: {origin}
            boot_layer: installed
            enabled: true
            class_path: "r2evil:EvilAudit"
        """), encoding="utf-8")


def _run(tmp_path: Path, *, tenants: list[str], origin: str) -> dict:
    home = tmp_path / "home"
    for t in tenants:
        (home / "tenants" / t).mkdir(parents=True, exist_ok=True)
    _write_registry(home, origin)
    evil_dir = tmp_path / "evil"
    evil_dir.mkdir(exist_ok=True)
    (evil_dir / "r2evil.py").write_text(_EVIL, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "REPO": str(_REPO), "CORVIN_HOME": str(home), "EVIL_DIR": str(evil_dir),
        "CORVIN_TENANT_ID": "_default",
        "VOICE_AUDIT_PATH": str(home / "global" / "forge" / "audit.jsonl"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key"),
    })
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("FORGE_ROOT", None)
    proc = subprocess.run([sys.executable, "-c", _CHILD], env=env, cwd=str(_REPO),
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + "\n" + proc.stderr
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    assert line, out[-4000:]
    return json.loads(line[len("RESULT "):])


class TestClaimedBuiltinOriginBuysNothing:
    def test_multi_tenant_provider_slot_is_refused_despite_origin_builtin(self, tmp_path):
        """The finding, directly: multi-tenant + claimed builtin + provider type."""
        result = _run(tmp_path, tenants=["_default", "acme", "globex"], origin="builtin")
        # REGISTRATION is the discriminator, not the provider-slot handle: a
        # refused provider slot makes _register_instance return False, so the
        # plugin never enters the registry at all. (Asserting only on the slot
        # handle would pass vacuously — it is None whenever nothing claimed it.)
        assert "r2evil" not in result["registered"], result
        assert result["provider_slot_owner"] != "r2evil", result

    def test_the_stored_claim_is_downgraded_on_read(self, tmp_path):
        """…and the record does not keep carrying the claim afterwards, where
        every console listing and ``consent_required()`` would read it."""
        result = _run(tmp_path, tenants=["_default", "acme"], origin="builtin")
        assert result["stored_origin_after_read"] == "community", result

    def test_vetted_is_not_believed_by_the_loader_either(self, tmp_path):
        """``vetted`` claimed in registry.yaml buys no provider slot either.

        Note what is NOT asserted: that the STORED value is rewritten. It is
        deliberately left alone (see ``state._downgrade_claimed_origin``) —
        the install path derives ``vetted`` from location itself (ADR-0643), so
        rewriting it on read would turn every legitimately installed plugin
        into one that needs fresh operator consent and would break enable /
        disable for the whole install. A false ``vetted`` is caught where it
        actually matters: the ADR-0249 trust gate demands a valid signature
        from a pinned anchor, and the loader derives the origin from the class
        file regardless of what is on disk.
        """
        result = _run(tmp_path, tenants=["_default", "acme"], origin="vetted")
        assert "r2evil" not in result["registered"], result


class TestTheGateStillPassesWhatItShould:
    def test_single_tenant_install_still_loads_the_plugin(self, tmp_path):
        """The refusal is about the multi-tenant slot, not about the plugin: a
        gate that refuses everything is indistinguishable from a broken feature,
        and this one sits in the boot path of every install."""
        result = _run(tmp_path, tenants=["_default"], origin="community")
        assert "r2evil" in result["registered"], result


@pytest.mark.parametrize("class_path,expected", [
    ("corvin_plugins.buildin.does.not.exist:X", None),
    ("r2evil:EvilAudit", None),
])
def test_unlocatable_module_derives_no_origin(class_path, expected):
    """``None`` is the restrictive answer — never a fallback to the claim."""
    sys.path.insert(0, str(_REPO / "core" / "plugins"))
    from corvin_plugins.bootstrap import _origin_for_class_path

    assert _origin_for_class_path(class_path)[0] is expected
