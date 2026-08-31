"""Unit tests for ``operator/forge/forge/artifacts.py`` (Layer 33).

Every test runs against a sandboxed CORVIN_HOME so the host's real
artifact tree is never touched. Audit emission is mocked so we can
assert on events without polluting the production chain.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # operator/forge

from forge import artifacts  # noqa: E402


class _Sandbox:
    """Sandbox a fresh CORVIN_HOME and capture audit events in-memory.

    Phase 7 of paths.py removed CORVIN_HOME env-var reading; the resolver
    now uses the repo root's .corvin/ directory unconditionally. To isolate
    tests from real on-disk data we patch ``forge.paths.corvin_home`` and
    the already-bound references in ``forge.artifacts`` (``tenant_global_dir``
    and ``tenant_sessions_dir``) so every path call returns a temp tree.
    """

    def __enter__(self) -> "_Sandbox":
        self.tmp = tempfile.TemporaryDirectory(prefix="corvin-art-")
        self.root = Path(self.tmp.name)
        self.events: list[tuple[str, str, dict]] = []

        # Patch corvin_home() at the source so that all derived helpers
        # (tenant_home, tenant_global_dir, tenant_sessions_dir …) pick up
        # the temp root.  We also need to patch the already-imported names
        # inside forge.artifacts because Python binds them at import time.
        from forge import paths as _paths

        def _fake_corvin_home() -> Path:
            return self.root

        self.home_patch = mock.patch.object(
            _paths, "corvin_home", side_effect=_fake_corvin_home)
        self.home_patch.start()

        # Patch the bound references inside forge.artifacts.
        self.global_dir_patch = mock.patch.object(
            artifacts, "tenant_global_dir",
            side_effect=lambda tid=None: self.root / "tenants" / "_default" / "global")
        self.global_dir_patch.start()

        self.sessions_dir_patch = mock.patch.object(
            artifacts, "tenant_sessions_dir",
            side_effect=lambda tid=None: self.root / "tenants" / "_default" / "sessions")
        self.sessions_dir_patch.start()

        self.env_patch = mock.patch.dict(os.environ, {
            "CORVIN_TENANT_ID": "_default",
        })
        self.env_patch.start()

        def fake_emit(event_type, *, severity, details, tenant_id=None):
            self.events.append((event_type, severity, details))

        self.audit_patch = mock.patch.object(
            artifacts, "_emit_audit", side_effect=fake_emit)
        self.audit_patch.start()

        self.tenant_home = self.root / "tenants" / "_default"
        # Use a session key that is unlikely to collide with real sessions.
        self.session_key = "discord:test-unit-sandbox"
        self.session_root = artifacts.session_artifacts_dir(
            self.session_key, "_default")
        self.global_root = artifacts.global_artifacts_dir("_default")
        return self

    def __exit__(self, *exc) -> None:
        self.audit_patch.stop()
        self.env_patch.stop()
        self.sessions_dir_patch.stop()
        self.global_dir_patch.stop()
        self.home_patch.stop()
        self.tmp.cleanup()

    def make_source(self, name: str = "src.pdf",
                    content: bytes = b"%PDF-1.4 stub") -> Path:
        p = self.root / name
        p.write_bytes(content)
        return p


# ── Path resolution & validation ───────────────────────────────────────────


class PathResolutionTests(unittest.TestCase):
    def test_session_key_must_be_safe(self) -> None:
        with _Sandbox():
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.session_artifacts_dir("../escape", "_default")
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.session_artifacts_dir("discord/bad", "_default")
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.session_artifacts_dir("", "_default")

    def test_session_root_layout(self) -> None:
        with _Sandbox() as sbx:
            # session_key changed to "discord:test-unit-sandbox" to avoid
            # collisions with real session data.
            self.assertTrue(str(sbx.session_root).endswith(
                f"tenants/_default/sessions/{sbx.session_key}/artifacts"))


# ── Register ───────────────────────────────────────────────────────────────


class RegisterTests(unittest.TestCase):
    def test_register_copies_into_sharded_path(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("invoice.pdf", b"%PDF-1.4 content")
            entry = artifacts.register(
                source_path=src, artifacts_root=sbx.session_root,
                description="invoice from Acme", by_tool="t.pdf")
            self.assertEqual(entry.name, "invoice.pdf")
            self.assertEqual(entry.mime, "application/pdf")
            self.assertEqual(entry.size, len(b"%PDF-1.4 content"))
            self.assertEqual(len(entry.sha256), 64)
            # Sharded by first 2 chars of sha.
            self.assertEqual(entry.path_rel.split("/")[0],
                             entry.sha256[:2])
            # File present on disk.
            self.assertTrue((sbx.session_root / entry.path_rel).is_file())
            # Source still exists (copy, not move).
            self.assertTrue(src.exists())

    def test_register_move_consumes_source(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("img.png", b"\x89PNG\r\n\x1a\nstub")
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root, move=True)
            self.assertFalse(src.exists())

    def test_register_size_cap(self) -> None:
        with _Sandbox() as sbx:
            # Force cap below the size of the source.
            cfg_path = artifacts._config_path("_default")
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps({"max_artifact_size_bytes": 5}))
            src = sbx.make_source("big.bin", b"toolarge-content")
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.register(source_path=src,
                                   artifacts_root=sbx.session_root)

    def test_register_sanitises_name(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("clean.pdf", b"%PDF-1.4 a")
            entry = artifacts.register(
                source_path=src, artifacts_root=sbx.session_root,
                name="../etc/passwd")
            self.assertNotIn("/", entry.name)
            self.assertNotIn("..", entry.name)

    def test_register_emits_audit(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("x.pdf", b"%PDF-1.4 d")
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root, by_tool="t")
            kinds = [ev[0] for ev in sbx.events]
            self.assertIn("artifact.registered", kinds)
            event = next(ev for ev in sbx.events
                         if ev[0] == "artifact.registered")
            # Privacy: details must NOT contain description / paths-outside-root.
            self.assertNotIn("description", event[2])
            self.assertIn("sha256", event[2])
            self.assertIn("mime", event[2])

    def test_mime_detect_png(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("a.png", b"\x89PNG\r\n\x1a\nrest")
            entry = artifacts.register(source_path=src,
                                       artifacts_root=sbx.session_root)
            self.assertEqual(entry.mime, "image/png")


# ── Listing ────────────────────────────────────────────────────────────────


class ListingTests(unittest.TestCase):
    def _seed(self, sbx: _Sandbox, n: int = 3) -> None:
        for i in range(n):
            src = sbx.make_source(f"f{i}.pdf", f"%PDF-1.4 #{i}".encode())
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root)
            time.sleep(0.001)

    def test_list_sorts_descending_by_ts(self) -> None:
        with _Sandbox() as sbx:
            self._seed(sbx, n=3)
            items = artifacts.list_active(sbx.session_root)
            self.assertEqual(len(items), 3)
            ts = [e.ts for e in items]
            self.assertEqual(ts, sorted(ts, reverse=True))

    def test_list_limit(self) -> None:
        with _Sandbox() as sbx:
            self._seed(sbx, n=5)
            self.assertEqual(len(artifacts.list_active(
                sbx.session_root, limit=2)), 2)

    def test_list_mime_filter(self) -> None:
        with _Sandbox() as sbx:
            self._seed(sbx, n=2)
            png = sbx.make_source("x.png", b"\x89PNG\r\n\x1a\nrest")
            artifacts.register(source_path=png,
                               artifacts_root=sbx.session_root)
            pdfs = artifacts.list_active(sbx.session_root,
                                         mime="application/pdf")
            self.assertEqual(len(pdfs), 2)
            self.assertTrue(all(e.mime == "application/pdf" for e in pdfs))

    def test_list_tombstone_hidden(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("a.pdf", b"%PDF-1.4 a")
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root)
            artifacts.purge_one(sbx.session_root, "a.pdf")
            self.assertEqual(artifacts.list_active(sbx.session_root), [])
            self.assertIsNone(artifacts.find_by_name(sbx.session_root, "a.pdf"))


# ── Pin (session → global) ─────────────────────────────────────────────────


class PinTests(unittest.TestCase):
    def test_pin_copies_to_global_and_writes_pinned_entry(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("keepme.pdf", b"%PDF-1.4 keep")
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root)
            pinned = artifacts.pin(session_root=sbx.session_root,
                                   global_root=sbx.global_root,
                                   name="keepme.pdf")
            self.assertTrue(pinned.pinned)
            # File now lives under global root.
            self.assertTrue((sbx.global_root / pinned.path_rel).is_file())
            # Audit emitted.
            self.assertIn("artifact.pinned",
                          [ev[0] for ev in sbx.events])

    def test_pin_missing_artifact_raises(self) -> None:
        with _Sandbox() as sbx:
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.pin(session_root=sbx.session_root,
                              global_root=sbx.global_root,
                              name="nope.pdf")


# ── Session purge (Layer 8 hook) ───────────────────────────────────────────


class PurgeSessionTests(unittest.TestCase):
    def test_purge_emits_critical_event_before_rmtree(self) -> None:
        with _Sandbox() as sbx:
            for i in range(2):
                src = sbx.make_source(f"x{i}.pdf", f"%PDF-1.4 {i}".encode())
                artifacts.register(source_path=src,
                                   artifacts_root=sbx.session_root)
            count = artifacts.purge_session(sbx.session_root)
            self.assertEqual(count, 2)
            self.assertFalse(sbx.session_root.exists())
            ev = next(e for e in sbx.events
                      if e[0] == "artifact.session_purged")
            self.assertEqual(ev[1], "CRITICAL")
            # Detail policy: never includes artifact names.
            self.assertNotIn("names", ev[2])
            self.assertNotIn("artifacts", ev[2])
            self.assertEqual(ev[2]["count"], 2)

    def test_purge_pinned_survives(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("keep.pdf", b"%PDF-1.4 k")
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root)
            artifacts.pin(session_root=sbx.session_root,
                          global_root=sbx.global_root,
                          name="keep.pdf")
            artifacts.purge_session(sbx.session_root)
            # Pinned copy still in global root.
            pinned_files = list(sbx.global_root.rglob("*"))
            files = [p for p in pinned_files if p.is_file()
                     and p.name != ".manifest.jsonl"
                     and p.name != ".manifest.lock"]
            self.assertEqual(len(files), 1)


# ── Reconcile (FS → manifest) ──────────────────────────────────────────────


class ReconcileTests(unittest.TestCase):
    def test_reconcile_rebuilds_manifest_from_files(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.make_source("x.pdf", b"%PDF-1.4 x")
            artifacts.register(source_path=src,
                               artifacts_root=sbx.session_root)
            # Delete the manifest; the on-disk file stays.
            manifest = artifacts._manifest_path(sbx.session_root)
            manifest.unlink()
            count = artifacts.reconcile_manifest(sbx.session_root)
            self.assertEqual(count, 1)
            items = artifacts.list_active(sbx.session_root)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].description, "(reconciled)")


# ── Locking ────────────────────────────────────────────────────────────────


class LockTests(unittest.TestCase):
    def test_lock_timeout_raises(self) -> None:
        with _Sandbox() as sbx:
            sbx.session_root.mkdir(parents=True, exist_ok=True)
            lock_path = artifacts._lock_path(sbx.session_root)
            # Hold an external lock, then attempt to acquire with 50 ms budget.
            with lock_path.open("a+") as fh:
                import fcntl as _f
                _f.flock(fh.fileno(), _f.LOCK_EX)
                with self.assertRaises(artifacts.ManifestLockTimeout):
                    with artifacts._ManifestLock(lock_path, timeout_ms=50):
                        pass


# ── Config ─────────────────────────────────────────────────────────────────


class ConfigTests(unittest.TestCase):
    def test_default_when_missing(self) -> None:
        with _Sandbox():
            cfg = artifacts.load_config()
            self.assertEqual(cfg["storage_backend"], "jsonl")
            self.assertIn("application/pdf", cfg["auto_register_mimes"])

    def test_override_merges(self) -> None:
        with _Sandbox():
            cfg_path = artifacts._config_path()
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(
                {"description_language": "de",
                 "max_artifact_size_bytes": 1024}))
            cfg = artifacts.load_config()
            self.assertEqual(cfg["description_language"], "de")
            self.assertEqual(cfg["max_artifact_size_bytes"], 1024)
            # Defaults still present for un-overridden keys.
            self.assertEqual(cfg["storage_backend"], "jsonl")


if __name__ == "__main__":
    unittest.main()
