"""E2E: a genuinely FRESH install auto-loads Corvin-Marketplace plugins (ADR-2228).

Before this, ``bootstrap_builtin()`` — the real function every boot calls via
``bootstrap_all`` → ``boot_platform()`` — only ever scanned a LOCAL path for the
marketplace's ``buildin/`` tree (an operator-set ``CORVIN_MARKETPLACE_ROOT``, a
sibling git checkout, or a previously-synced GitHub cache). A host with none of
those — i.e. every genuinely fresh install — scanned a path that did not exist and
silently loaded zero marketplace plugins, forever, with no manual step able to fix
it short of cloning Corvin-Marketplace by hand next to CorvinOS.
``test_boot_marketplace_e2e_subprocess.py`` (the one E2E that boots the real
sequence) explicitly ``pytest.skip()``s exactly this case instead of catching it.

These tests force that exact condition — no sibling checkout, no
``CORVIN_MARKETPLACE_ROOT``, a throwaway ``CORVIN_HOME`` — and mock only the network
boundary (``urllib.request.urlopen``), the same technique
``test_marketplace_sync.py`` already uses to pin ``ensure_marketplace_source`` in
isolation. Here it is driven through the REAL boot entry point,
``bootstrap.bootstrap_builtin``, to prove the two are actually wired together (they
were not, before ADR-2228).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
_CONSOLE = _REPO / "core" / "console"

for _p in (
    str(_PKG),
    str(_CONSOLE),
    str(_REPO / "core" / "compliance"),
    str(_REPO / "corvin_operator" / "forge"),
    str(_REPO / "corvin_operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import bootstrap, registry  # noqa: E402

try:
    import audit as _audit  # type: ignore[import-not-found]  # noqa: E402
except ImportError:  # pragma: no cover - layout-dependent
    _audit = None

_TOP = "CorvinLabs-Corvin-Marketplace-deadbeef"

_CANARY_MANIFEST = b"""\
plugin_id: fresh-install-canary
plugin_type: data_connector
version: 0.1.0
display_name: Fresh Install Canary
origin: builtin
boot_layer: installed
locality: local
network_egress: none
egress_hosts: []
pii_risk: none
requires_consent: false
audit_required: true
"""

_CANARY_PROVIDER = b'''\
from __future__ import annotations
from corvin_plugins.protocol import HealthStatus, PluginContext


class FreshInstallCanaryPlugin:
    plugin_id = "fresh-install-canary"
    plugin_type = "data_connector"
    version = "0.1.0"
    display_name = "Fresh Install Canary"
    is_infrastructure = False

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    def on_load(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)
'''

# A second, independently-rooted plugin with the SAME plugin_id — simulates a
# marketplace conflict (two directories claiming one identity) rather than a
# fabricated dependency-declaration mechanism, which does not exist in this
# discovery path (see ADR-2228 Consequences).
_DUP_MANIFEST = _CANARY_MANIFEST.replace(b"Fresh Install Canary", b"Fresh Install Canary (duplicate)")

# A manifest missing the required ``plugin_type`` field — must be skipped by the
# ADR-0247 validation gate without taking the rest of the boot down with it.
_INVALID_MANIFEST = b"""\
plugin_id: fresh-install-invalid
display_name: Missing plugin_type
origin: builtin
boot_layer: installed
"""


def _tarball(entries: dict[str, bytes]) -> bytes:
    """A minimal GitHub-tarball-shaped archive: everything nested under one
    ``"<owner>-<repo>-<sha>/"`` directory, the way the real tarball API answers."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for relpath, content in entries.items():
            info = tarfile.TarInfo(name=f"{_TOP}/{relpath}")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class _FakeResponse:
    """``shutil.copyfileobj`` keeps calling ``read(bufsize)`` until it gets back
    an empty bytes object, so a fixed-data fake must hand out *data* exactly once
    and ``b""`` after — returning *data* every call loops forever."""

    def __init__(self, data: bytes):
        self._data = data
        self._served = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *_a, **_k):
        if self._served:
            return b""
        self._served = True
        return self._data


class _FreshInstallTestCase(unittest.TestCase):
    """Common fresh-install plumbing: throwaway CORVIN_HOME, no sibling checkout."""

    _CANARY_ID = "fresh-install-canary"
    _DUP_DIR_ID = "fresh-install-canary-dup"
    _INVALID_ID = "fresh-install-invalid"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        (self.home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)

        self._prev_env = {
            k: os.environ.get(k)
            for k in ("CORVIN_HOME", "CORVIN_MARKETPLACE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID")
        }
        self.addCleanup(self._restore_env)
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ.pop("CORVIN_MARKETPLACE_ROOT", None)
        os.environ.pop("VOICE_AUDIT_PATH", None)
        os.environ.pop("CORVIN_TENANT_ID", None)

        # Never let a real dev machine's sibling checkout short-circuit the
        # fallback path these tests exist to exercise (mirrors test_marketplace_sync.py).
        self._sibling_patch = mock.patch.object(
            bootstrap, "_marketplace_sibling_dir",
            return_value=Path(self._tmp.name) / "no-such-sibling" / "plugins" / "buildin",
        )
        self._sibling_patch.start()
        self.addCleanup(self._sibling_patch.stop)

        self.addCleanup(self._unregister_canaries)

    def _restore_env(self) -> None:
        for k, v in self._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _unregister_canaries(self) -> None:
        reg = registry.get_registry()
        for pid in (self._CANARY_ID, self._INVALID_ID):
            try:
                reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass
        for name in list(sys.modules):
            if name.startswith("corvin_builtin_fresh_install"):
                del sys.modules[name]

    def _audit_records(self, event_type: str) -> list[dict]:
        if _audit is None:
            self.skipTest("forge.security_events not importable in this layout")
        p = Path(_audit.audit_path())
        self.assertTrue(str(p).startswith(str(self.home)), p)
        if not p.exists():
            return []
        rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        return [r for r in rows if r.get("event_type") == event_type]


class FreshInstallAutoSyncTests(_FreshInstallTestCase):
    def test_zero_config_fresh_install_downloads_and_loads_the_marketplace_plugin(self):
        """The actual requirement: no clone, no flag, no Console click — the real
        boot-path function alone turns a bare GitHub tarball into a loaded plugin."""
        tarball = _tarball({
            f"plugins/buildin/canary/{self._CANARY_ID}/plugin.yaml": _CANARY_MANIFEST,
            f"plugins/buildin/canary/{self._CANARY_ID}/provider.py": _CANARY_PROVIDER,
        })
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(tarball)) as urlopen:
            loaded = bootstrap.bootstrap_builtin(
                tenant_id="_default", corvin_home=self.home,
                tenant_config={"spec": {"plugins": {}}},
            )
        urlopen.assert_called_once()  # the sync was actually attempted at boot
        self.assertIn(self._CANARY_ID, loaded)
        self.assertIn(self._CANARY_ID, registry.get_registry().discover())

    def test_does_not_redownload_on_the_very_next_boot_within_the_ttl(self):
        """Mirrors ensure_marketplace_source's own TTL contract (test_marketplace_sync.py)
        through the boot entry point: two boots in quick succession must not double-fetch."""
        tarball = _tarball({
            f"plugins/buildin/canary/{self._CANARY_ID}/plugin.yaml": _CANARY_MANIFEST,
            f"plugins/buildin/canary/{self._CANARY_ID}/provider.py": _CANARY_PROVIDER,
        })
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(tarball)) as urlopen:
            bootstrap.bootstrap_builtin(tenant_id="_default", corvin_home=self.home,
                                         tenant_config={"spec": {"plugins": {}}})
            bootstrap.bootstrap_builtin(tenant_id="_default", corvin_home=self.home,
                                         tenant_config={"spec": {"plugins": {}}})
        self.assertEqual(urlopen.call_count, 1)


class FreshInstallNetworkFailureTests(_FreshInstallTestCase):
    def test_offline_fresh_install_boots_with_zero_marketplace_plugins_not_a_crash(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("network unreachable")):
            loaded = bootstrap.bootstrap_builtin(  # must not raise
                tenant_id="_default", corvin_home=self.home,
                tenant_config={"spec": {"plugins": {}}},
            )
        self.assertNotIn(self._CANARY_ID, loaded)

    def test_sync_failure_is_audited_not_only_logged(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("network unreachable")):
            bootstrap.bootstrap_builtin(
                tenant_id="_default", corvin_home=self.home,
                tenant_config={"spec": {"plugins": {}}},
            )
        recs = self._audit_records("plugin.marketplace_sync_failed")
        self.assertEqual(len(recs), 1, "exactly one degradation record for the failed sync")
        details = recs[0]["details"]
        self.assertEqual(details.get("tenant_id"), "_default")
        self.assertEqual(details.get("reason"), "no_local_source_after_sync")
        self.assertEqual(recs[0].get("severity"), "WARNING")


class FreshInstallRobustnessTests(_FreshInstallTestCase):
    def test_one_invalid_manifest_is_skipped_valid_ones_still_load(self):
        """ADR-0247 manifest validation must keep failing closed for a plugin
        synced straight from the marketplace tarball, exactly as it does for a
        locally-cloned checkout — a bad manifest degrades one plugin, not the boot."""
        tarball = _tarball({
            f"plugins/buildin/canary/{self._CANARY_ID}/plugin.yaml": _CANARY_MANIFEST,
            f"plugins/buildin/canary/{self._CANARY_ID}/provider.py": _CANARY_PROVIDER,
            f"plugins/buildin/broken/{self._INVALID_ID}/plugin.yaml": _INVALID_MANIFEST,
        })
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(tarball)):
            loaded = bootstrap.bootstrap_builtin(
                tenant_id="_default", corvin_home=self.home,
                tenant_config={"spec": {"plugins": {}}},
            )
        self.assertIn(self._CANARY_ID, loaded)
        self.assertNotIn(self._INVALID_ID, loaded)
        recs = self._audit_records("plugin.load_failed")
        self.assertTrue(
            any(r["details"].get("plugin_id") == self._INVALID_ID for r in recs),
            "invalid manifest must be audited as a load failure, not only skipped silently",
        )

    def test_duplicate_plugin_id_across_two_marketplace_dirs_does_not_crash_boot(self):
        """Two directories claiming the same identity — the realistic shape of a
        marketplace 'conflict' in a discovery path with no dependency graph
        (ADR-2228 Consequences) — must resolve deterministically, never raise."""
        tarball = _tarball({
            f"plugins/buildin/canary/{self._CANARY_ID}/plugin.yaml": _CANARY_MANIFEST,
            f"plugins/buildin/canary/{self._CANARY_ID}/provider.py": _CANARY_PROVIDER,
            f"plugins/buildin/canary2/{self._DUP_DIR_ID}/plugin.yaml": _DUP_MANIFEST,
            f"plugins/buildin/canary2/{self._DUP_DIR_ID}/provider.py": _CANARY_PROVIDER,
        })
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(tarball)):
            loaded = bootstrap.bootstrap_builtin(  # must not raise
                tenant_id="_default", corvin_home=self.home,
                tenant_config={"spec": {"plugins": {}}},
            )
        self.assertIn(self._CANARY_ID, loaded)
        # Registered exactly once — the second directory's identical plugin_id
        # was recognised as already-loaded, never double-registered.
        discovered = list(registry.get_registry().discover())
        self.assertEqual(discovered.count(self._CANARY_ID), 1)


if __name__ == "__main__":
    unittest.main()
