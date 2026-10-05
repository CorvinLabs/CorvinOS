"""Tests for the fresh-install GitHub fallback for Corvin-Marketplace plugin
SOURCE (as opposed to the index JSON, which ``routes/marketplace.py``'s
``_IndexManager`` already fell back to GitHub for).

Before this, a fresh install with no sibling ``../Corvin-Marketplace`` checkout
could still LIST plugins (index fallback) but could never INSTALL one: every
resolve hit "no local source for ... (expected .../plugin.yaml under the
marketplace checkout)" because ``_marketplace_root()`` only ever looked at a
local filesystem path nobody had cloned yet. These tests pin the two new
pieces: ``bootstrap.ensure_marketplace_source`` (download+extract+cache,
with its guard rails against overwriting an operator-managed checkout and
against re-downloading on every miss) and ``marketplace_resolve``'s one retry
through it before giving up.
"""
from __future__ import annotations

import io
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

for _p in (str(_PKG), str(_CONSOLE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import bootstrap  # noqa: E402


def _fake_marketplace_tarball(plugin_relpath: str) -> bytes:
    """A minimal GitHub-tarball-shaped archive: everything nested under one
    "<owner>-<repo>-<sha>/" directory, the way the real tarball API answers."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        top = "CorvinLabs-Corvin-Marketplace-deadbeef"
        content = b"plugin_id: test-plugin\nplugin_type: generic\n"
        info = tarfile.TarInfo(name=f"{top}/{plugin_relpath}")
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class _FakeResponse:
    """``shutil.copyfileobj`` keeps calling ``read(bufsize)`` until it gets back
    an empty bytes object, so a fixed-data fake must hand out *data* exactly
    once and ``b""`` after — returning *data* every call loops forever."""

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


class EnsureMarketplaceSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self._env_patch = mock.patch.dict(
            os.environ, {"CORVIN_HOME": str(self.home)}, clear=False
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        os.environ.pop("CORVIN_MARKETPLACE_ROOT", None)
        # Never let a real test machine's sibling checkout (if any) short-circuit
        # the fallback path these tests exist to exercise.
        self._sibling_patch = mock.patch.object(
            bootstrap, "_marketplace_sibling_dir",
            return_value=Path(self._tmp.name) / "no-such-sibling" / "plugins" / "buildin",
        )
        self._sibling_patch.start()
        self.addCleanup(self._sibling_patch.stop)

    def test_skips_when_operator_root_is_set(self):
        os.environ["CORVIN_MARKETPLACE_ROOT"] = str(self.home / "operator-managed")
        self.addCleanup(lambda: os.environ.pop("CORVIN_MARKETPLACE_ROOT", None))
        with mock.patch.object(bootstrap, "_download_and_extract_marketplace") as dl:
            bootstrap.ensure_marketplace_source()
        dl.assert_not_called()

    def test_skips_when_sibling_checkout_already_exists(self):
        sibling = Path(self._tmp.name) / "sibling" / "plugins" / "buildin"
        sibling.mkdir(parents=True)
        self._sibling_patch.stop()
        with mock.patch.object(bootstrap, "_marketplace_sibling_dir", return_value=sibling):
            with mock.patch.object(bootstrap, "_download_and_extract_marketplace") as dl:
                bootstrap.ensure_marketplace_source()
        dl.assert_not_called()
        self._sibling_patch.start()  # keep tearDown symmetric with setUp

    def test_downloads_and_extracts_into_cache_then_root_resolves_it(self):
        tarball = _fake_marketplace_tarball("plugins/buildin/memory/foo/plugin.yaml")
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(tarball)):
            bootstrap.ensure_marketplace_source()
        cache_root = bootstrap._marketplace_root()
        self.assertTrue((cache_root / "memory" / "foo" / "plugin.yaml").is_file())

    def test_does_not_redownload_within_the_ttl(self):
        tarball = _fake_marketplace_tarball("plugins/buildin/memory/foo/plugin.yaml")
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(tarball)) as urlopen:
            bootstrap.ensure_marketplace_source()
            bootstrap.ensure_marketplace_source()
        self.assertEqual(urlopen.call_count, 1)

    def test_network_failure_is_swallowed_not_raised(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            bootstrap.ensure_marketplace_source()  # must not raise

    def test_rejects_path_traversal_in_tarball_member(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="top/../../etc/passwd")
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(buf.getvalue())):
            bootstrap.ensure_marketplace_source()  # best-effort: logs, does not raise
        # No cache directory should have been populated from the malicious tarball.
        self.assertFalse((bootstrap._marketplace_cache_dir() / "plugins").exists())


class ResolveBuiltinDirSyncRetryTests(unittest.TestCase):
    """marketplace_resolve.resolve_builtin_dir() retries once through
    bootstrap.ensure_marketplace_source() before raising "no local source"."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for key in list(sys.modules):
            if key.startswith("corvin_console"):
                del sys.modules[key]
        from corvin_console.routes import marketplace_resolve as mr  # noqa: PLC0415
        self.mr = mr

    def test_sync_is_attempted_and_resolution_retried(self):
        synced_root = Path(self._tmp.name) / "synced" / "plugins"
        plugin_dir = synced_root / "buildin" / "memory" / "foo"
        calls = {"n": 0}

        def fake_roots():
            calls["n"] += 1
            # First call (pre-sync): nothing exists yet. Second call
            # (post-sync): ensure_marketplace_source "found" the plugin.
            return [synced_root] if calls["n"] > 1 else []

        def fake_sync(**_kw):
            plugin_dir.mkdir(parents=True, exist_ok=True)
            (plugin_dir / "plugin.yaml").write_text("plugin_id: foo\n")

        with mock.patch.object(self.mr, "_plugins_roots", side_effect=fake_roots), \
             mock.patch.object(self.mr._bootstrap, "ensure_marketplace_source", side_effect=fake_sync) as sync:
            resolved = self.mr.resolve_builtin_dir("plugin:buildin-memory-foo")
        sync.assert_called_once()
        self.assertEqual(resolved, plugin_dir.resolve())

    def test_still_raises_with_original_message_when_sync_does_not_help(self):
        with mock.patch.object(self.mr, "_plugins_roots", return_value=[]), \
             mock.patch.object(self.mr._bootstrap, "ensure_marketplace_source") as sync:
            with self.assertRaises(self.mr.MarketplaceResolveError) as ctx:
                self.mr.resolve_builtin_dir("plugin:buildin-memory-foo")
        sync.assert_called_once()
        self.assertIn("no local source for 'plugin:buildin-memory-foo'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
