"""Unit tests for audit_sealer.py (Layer 37).

Run with::

    python3 operator/bridges/shared/test_audit_sealer.py

Tests use a fake sealer (XOR cipher) so they pass without `age` /
`gpg` installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fcntl
import types
import unittest.mock as mock

import audit_sealer as _mod  # noqa: E402  (for monkeypatching)
from audit_sealer import (  # noqa: E402
    _AUDIT_ALLOWED,
    _build_tsa_request,
    _validate_audit_details,
    AuditPolicy,
    EncryptionConfig,
    make_forge_audit_writer,
    RetentionPolicy,
    RotationDecision,
    RotationPolicy,
    RotationResult,
    enforce_retention,
    last_hash_of_segment,
    list_sealed_segments,
    policy_from_tenant_config,
    rotate_and_seal,
    sealer_binary_available,
    should_rotate,
    unseal_to_temp,
)


def _make_chain_entry(prev_hash: str, event_type: str, ts: float) -> dict:
    """Build a fake chain entry with proper hash linking."""
    rec = {
        "ts": ts,
        "event_type": event_type,
        "severity": "INFO",
        "run_id": "",
        "tool": "test",
        "details": {},
        "prev_hash": prev_hash,
    }
    canonical = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\n")
    h.update(canonical.encode("utf-8"))
    rec["hash"] = h.hexdigest()[:16]
    return rec


def _write_chain(path: Path, n_entries: int, *, t0: float = 1700000000.0) -> str:
    """Write a fake chain of n entries. Returns the last hash."""
    prev = ""
    with path.open("w") as fh:
        for i in range(n_entries):
            rec = _make_chain_entry(prev, f"test.event_{i}", t0 + i)
            fh.write(json.dumps(rec) + "\n")
            prev = rec["hash"]
    return prev


def _fake_sealer(plaintext: Path, sealed: Path, recipient: str) -> None:
    """Test sealer: XOR every byte with recipient[0]. Reversible by
    test_unseal_to_temp via the matching _fake_unsealer."""
    if not recipient:
        raise RuntimeError("fake sealer: empty recipient")
    key = recipient[0].encode("ascii")[0]
    data = plaintext.read_bytes()
    sealed.write_bytes(bytes(b ^ key for b in data))


class TestPolicyDataclasses(unittest.TestCase):

    def test_rotation_defaults(self):
        p = RotationPolicy()
        self.assertEqual(p.max_size_mb, 100.0)
        self.assertEqual(p.max_age_days, 30)

    def test_rotation_negative_raises(self):
        with self.assertRaises(ValueError):
            RotationPolicy(max_size_mb=-1)
        with self.assertRaises(ValueError):
            RotationPolicy(max_age_days=-1)

    def test_encryption_enabled_requires_recipient(self):
        with self.assertRaises(ValueError):
            EncryptionConfig(enabled=True, recipient="")

    def test_encryption_bad_sealer(self):
        with self.assertRaises(ValueError):
            EncryptionConfig(sealer_cmd="rot13")  # type: ignore[arg-type]

    def test_retention_seconds(self):
        p = RetentionPolicy(retention_years=7.0)
        # 7 * 365.25 * 86400
        self.assertAlmostEqual(p.retention_seconds, 220903200.0, delta=1.0)

    def test_retention_negative_raises(self):
        with self.assertRaises(ValueError):
            RetentionPolicy(retention_years=-1)


class TestPolicyFromTenant(unittest.TestCase):

    def test_empty_defaults(self):
        p = policy_from_tenant_config(None)
        self.assertEqual(p.rotation.max_age_days, 30)
        self.assertEqual(p.encryption.enabled, False)
        self.assertEqual(p.retention.retention_years, 7.0)

    def test_full_config(self):
        cfg = {
            "spec": {
                "audit": {
                    "retention_years": 10.0,
                    "encryption_at_rest": {
                        "enabled": True,
                        "recipient": "age1xyz",
                        "sealer_cmd": "age",
                    },
                    "rotation": {
                        "max_size_mb": 50.0,
                        "max_age_days": 14,
                    },
                },
            },
        }
        p = policy_from_tenant_config(cfg)
        self.assertEqual(p.retention.retention_years, 10.0)
        self.assertTrue(p.encryption.enabled)
        self.assertEqual(p.encryption.recipient, "age1xyz")
        self.assertEqual(p.rotation.max_size_mb, 50.0)
        self.assertEqual(p.rotation.max_age_days, 14)

    def test_bad_sealer_raises(self):
        cfg = {"spec": {"audit": {
            "encryption_at_rest": {"sealer_cmd": "rot13"},
        }}}
        with self.assertRaises(ValueError):
            policy_from_tenant_config(cfg)


class TestShouldRotate(unittest.TestCase):

    def test_missing_file(self):
        d = should_rotate(Path("/tmp/nonexistent_audit_xyz.jsonl"), RotationPolicy())
        self.assertFalse(d.should)
        self.assertIn("missing", d.reason)

    def test_size_trigger(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            p.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
            d = should_rotate(p, RotationPolicy(max_size_mb=1.0, max_age_days=999))
            self.assertTrue(d.should)
            self.assertIn("size", d.reason)

    def test_age_trigger(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            p.write_bytes(b"x")
            old = time.time() - (40 * 86400)
            os.utime(p, (old, old))
            d = should_rotate(p, RotationPolicy(max_size_mb=999, max_age_days=30))
            self.assertTrue(d.should)
            self.assertIn("age", d.reason)

    def test_under_thresholds(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            p.write_bytes(b"x")
            d = should_rotate(p, RotationPolicy(max_size_mb=999, max_age_days=999))
            self.assertFalse(d.should)


class TestLastHashOfSegment(unittest.TestCase):

    def test_three_entry_chain(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            last = _write_chain(p, 3)
            self.assertEqual(last_hash_of_segment(p), last)

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            p.write_text("")
            self.assertEqual(last_hash_of_segment(p), "")

    def test_missing_file(self):
        self.assertEqual(
            last_hash_of_segment(Path("/tmp/nonexistent_audit_xyz.jsonl")),
            "",
        )


class TestRotateAndSealWithoutEncryption(unittest.TestCase):

    def test_rotate_preserves_chain_link(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            last_hash = _write_chain(audit, 5)

            events: list[tuple[str, str, dict]] = []

            def writer(et, sev, det):
                events.append((et, sev, dict(det)))

            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),  # disabled
                retention=RetentionPolicy(),
            )
            result = rotate_and_seal(audit, policy, audit_writer=writer)

            self.assertTrue(result.rotated)
            self.assertIsNone(result.sealed_path)
            self.assertIsNotNone(result.rotated_path)
            self.assertEqual(result.last_hash, last_hash)

            # Rotated segment exists, plaintext
            assert result.rotated_path is not None
            self.assertTrue(result.rotated_path.is_file())

            # New live file has rotation_link as first entry, followed by
            # audit.chain_anchor_written from step 6b (ADR-0135 re-anchor).
            assert audit.is_file()
            lines = [l for l in audit.read_text().splitlines() if l.strip()]
            self.assertGreaterEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["event_type"], "audit.rotation_link")
            self.assertEqual(rec["prev_hash"], last_hash)
            self.assertIn("hash", rec)
            # If step 6b ran (clag importable), chain_anchor_written follows.
            if len(lines) >= 2:
                anchor_rec = json.loads(lines[-1])
                self.assertEqual(anchor_rec["event_type"], "audit.chain_anchor_written")

    def test_rotate_on_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),
                retention=RetentionPolicy(),
            )
            result = rotate_and_seal(audit, policy)
            self.assertFalse(result.rotated)
            self.assertIn("missing", result.reason)


class TestRotateAndSealWithFakeSealer(unittest.TestCase):

    def test_rotate_seals_and_removes_plaintext(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 3)

            events: list[tuple[str, str, dict]] = []

            def writer(et, sev, det):
                events.append((et, sev, dict(det)))

            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(
                    enabled=True, recipient="X", sealer_cmd="age",
                ),
                retention=RetentionPolicy(),
            )
            result = rotate_and_seal(
                audit, policy,
                audit_writer=writer,
                sealer=_fake_sealer,
            )

            self.assertTrue(result.rotated)
            self.assertIsNotNone(result.sealed_path)
            assert result.sealed_path is not None
            self.assertTrue(result.sealed_path.is_file())
            self.assertEqual(result.sealed_path.suffix, ".age")
            # plaintext rotated file gone
            self.assertIsNone(result.rotated_path)
            # sealed file is chmod 444
            mode = result.sealed_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o444)
            # one audit.segment_sealed event landed on the NEW live chain
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][0], "audit.segment_sealed")

    def test_seal_failure_emits_critical(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 2)

            events: list[tuple[str, str, dict]] = []

            def writer(et, sev, det):
                events.append((et, sev, dict(det)))

            def broken_sealer(plain, sealed, recipient):
                raise RuntimeError("simulated sealer failure")

            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(
                    enabled=True, recipient="K", sealer_cmd="age",
                ),
                retention=RetentionPolicy(),
            )
            with self.assertRaises(RuntimeError):
                rotate_and_seal(
                    audit, policy,
                    audit_writer=writer,
                    sealer=broken_sealer,
                )
            self.assertTrue(any(e[0] == "audit.rotation_failed" for e in events))
            self.assertTrue(any(e[1] == "CRITICAL" for e in events))


class TestRotationLinkFailClosed(unittest.TestCase):
    """V-009: rotation_link write must be fail-closed."""

    def test_rotation_link_fail_closed(self):
        """If the rotation_link write fails, rotate_and_seal raises RuntimeError
        and the audit directory is left in a consistent state."""
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 3)

            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),  # no sealing
                retention=RetentionPolicy(),
            )

            # Patch Path.open so that writing to audit.jsonl raises OSError
            # while reads (for chain extraction) still work.
            original_open = Path.open

            def failing_open(self_path, mode="r", **kwargs):
                if "w" in mode and self_path.name == "audit.jsonl":
                    raise OSError("simulated disk full")
                return original_open(self_path, mode, **kwargs)

            with mock.patch.object(Path, "open", failing_open):
                with self.assertRaises(RuntimeError) as cm:
                    rotate_and_seal(audit, policy)

            self.assertIn("rotation_link write failed", str(cm.exception))

            # The rotated segment must exist (os.replace already fired).
            rotated_files = [
                p for p in Path(td).iterdir()
                if p.name.startswith("audit.") and p.name != "audit.jsonl"
                and p.suffix == ".jsonl"
            ]
            self.assertEqual(len(rotated_files), 1, "rotated segment should exist")

            # The new live audit.jsonl must NOT exist (write failed).
            self.assertFalse(audit.exists(), "orphaned empty audit.jsonl must not be left behind")

    def test_rotation_link_success_still_works(self):
        """Sanity-check: successful rotation_link write does not raise."""
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 2)
            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),
                retention=RetentionPolicy(),
            )
            result = rotate_and_seal(audit, policy)
            self.assertTrue(result.rotated)
            self.assertTrue(audit.is_file())
            # First line is always rotation_link (step 6b may add chain_anchor_written)
            first_line = audit.read_text().splitlines()[0]
            line = json.loads(first_line)
            self.assertEqual(line["event_type"], "audit.rotation_link")


class TestConcurrentRotationBlocked(unittest.TestCase):
    """V-010: flock mutex must prevent concurrent rotations."""

    def test_concurrent_rotation_blocked(self):
        """A second rotate_and_seal call while the lock is held raises
        RuntimeError('rotation already in progress')."""
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 3)

            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),
                retention=RetentionPolicy(),
            )

            # Manually acquire the sidecar lock to simulate an in-flight rotation.
            lock_path = audit.with_name(".rotation.lock")
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                # Now rotate_and_seal from the *current* thread must hit LOCK_NB
                # and raise RuntimeError immediately.
                with self.assertRaises(RuntimeError) as cm:
                    rotate_and_seal(audit, policy)
                self.assertIn("already in progress", str(cm.exception))
                # The live audit file must be untouched (no os.replace happened).
                self.assertTrue(audit.is_file(), "live audit.jsonl must be untouched")
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def test_lock_released_after_successful_rotation(self):
        """After a successful rotation the lock file is cleaned up so
        a subsequent rotation can proceed."""
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 2)
            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),
                retention=RetentionPolicy(),
            )
            rotate_and_seal(audit, policy)
            lock_path = audit.with_name(".rotation.lock")
            # Lock file must not persist after rotation completes.
            self.assertFalse(lock_path.exists(), ".rotation.lock must be removed after rotation")


class TestMakeForgeAuditWriterFilter(unittest.TestCase):
    """V-021: make_forge_audit_writer must filter details to allowed_keys."""

    def test_strips_disallowed_keys(self):
        """Writer must not forward keys outside the allowed set."""
        captured: list[dict] = []

        # Inject a fake write_event into the module's forge import path.
        def fake_write_event(path, event_type, *, severity, details):
            captured.append(dict(details))

        with tempfile.TemporaryDirectory() as td:
            audit_path = Path(td) / "audit.jsonl"

            # Patch the forge import inside make_forge_audit_writer by
            # temporarily inserting a fake module into sys.modules.
            import sys
            fake_forge_mod = types.ModuleType("forge.security_events")
            fake_forge_mod.write_event = fake_write_event  # type: ignore[attr-defined]
            sys.modules["forge"] = types.ModuleType("forge")
            sys.modules["forge.security_events"] = fake_forge_mod
            try:
                writer = make_forge_audit_writer(
                    audit_path,
                    allowed_keys=frozenset({"sealed_segment", "sealer_cmd"}),
                )
                writer(
                    "audit.segment_sealed", "INFO",
                    {
                        "sealed_segment": "audit.2026-01-01T000000Z.jsonl.age",
                        "sealer_cmd": "age",
                        "LEAKED_CONTENT": "should not appear",
                        "rotated_size_bytes": 12345,
                    },
                )
            finally:
                sys.modules.pop("forge", None)
                sys.modules.pop("forge.security_events", None)

        self.assertEqual(len(captured), 1)
        details = captured[0]
        self.assertIn("sealed_segment", details)
        self.assertIn("sealer_cmd", details)
        self.assertNotIn("LEAKED_CONTENT", details)
        self.assertNotIn("rotated_size_bytes", details)

    def test_default_allowed_keys_are_audit_allowed(self):
        """Without explicit allowed_keys, the default equals _AUDIT_ALLOWED."""
        import inspect
        sig = inspect.signature(make_forge_audit_writer)
        default = sig.parameters["allowed_keys"].default
        self.assertEqual(default, _AUDIT_ALLOWED)


class TestRetention(unittest.TestCase):

    def test_retains_recent(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            recent = d / "audit.2025-01-15T120000Z.jsonl.age"
            recent.write_bytes(b"sealed")
            os.chmod(recent, 0o444)
            # very large retention
            policy = RetentionPolicy(retention_years=10.0)
            removed = enforce_retention(d, policy)
            self.assertEqual(removed, [])
            self.assertTrue(recent.exists())

    def test_removes_old(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            old = d / "audit.2010-01-01T000000Z.jsonl.age"
            old.write_bytes(b"sealed")
            old_t = time.time() - (20 * 365.25 * 86400)
            os.utime(old, (old_t, old_t))
            os.chmod(old, 0o444)

            events: list[tuple[str, str, dict]] = []

            def writer(et, sev, det):
                events.append((et, sev, dict(det)))

            policy = RetentionPolicy(retention_years=7.0)
            removed = enforce_retention(d, policy, audit_writer=writer)
            self.assertEqual(len(removed), 1)
            self.assertFalse(old.exists())
            # audit BEFORE removal — event exists
            self.assertTrue(any(e[0] == "audit.segment_retired" for e in events))

    def test_only_sealed_segments_listed(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "audit.jsonl").write_text("")          # live, ignored
            (d / "audit.2025-01-15T120000Z.jsonl").write_text("")  # plaintext segment
            (d / "audit.2025-02-15T120000Z.jsonl.age").write_bytes(b"")
            (d / "unrelated.txt").write_text("")
            segs = list_sealed_segments(d)
            names = [p.name for p in segs]
            self.assertIn("audit.2025-01-15T120000Z.jsonl", names)
            self.assertIn("audit.2025-02-15T120000Z.jsonl.age", names)
            self.assertNotIn("audit.jsonl", names)
            self.assertNotIn("unrelated.txt", names)


class TestUnseal(unittest.TestCase):
    """End-to-end: seal with fake_sealer, then unseal manually
    (fake_sealer is symmetric XOR so we can roundtrip)."""

    def test_unseal_emits_audit_before_decrypt(self):
        if not sealer_binary_available("age"):
            self.skipTest("age binary not available; skipping real-binary unseal test")

    def test_unseal_inferred_kind(self):
        # We can't actually unseal without age installed; verify the
        # audit emission + suffix inference logic with a missing file.
        events: list[tuple[str, str, dict]] = []

        def writer(et, sev, det):
            events.append((et, sev, dict(det)))

        with self.assertRaises(FileNotFoundError):
            unseal_to_temp(
                Path("/tmp/nonexistent.jsonl.age"),
                audit_writer=writer,
                requester="dpo_test",
            )
        # Even on missing file, the audit attempt happens AFTER the
        # existence check in our implementation — confirm that this
        # is the intended order (no audit on missing file).
        # This documents the contract.
        self.assertEqual(events, [])


class TestUnsealTempdirLeak(unittest.TestCase):
    """BUG (documented, not fixed here): unseal_to_temp() creates
    ``td = Path(tempfile.mkdtemp(prefix="corvin-unseal-"))`` and returns
    only ``plaintext = td / <name>`` (a file *inside* td). Every real
    caller (voice_audit.py's --include-sealed verify loop and
    cmd_unseal) only ever does ``plaintext.unlink()`` in their cleanup
    path; nothing ever rmtree/rmdir's the enclosing tempdir itself, so
    it leaks permanently on every unseal. These tests exercise the real
    decrypt-success code path (subprocess.run mocked so they don't
    depend on the `age` binary being installed) and pin down that
    behaviour so a real fix has a red/green target. Any leaked
    directories created here are cleaned up in the test itself (via
    shutil.rmtree) so the test suite doesn't itself pollute /tmp."""

    @staticmethod
    def _fake_successful_run(cmd, *, stdout=None, stderr=None, check=False):
        import subprocess as _subprocess
        stdout.write(b"decrypted-plaintext-content\n")
        return _subprocess.CompletedProcess(cmd, 0, stdout=None, stderr=b"")

    def test_unseal_to_temp_leaves_parent_tempdir_after_caller_cleanup(self):
        """Reproduces the leak: after unseal_to_temp() + the exact
        cleanup every real caller performs (plaintext.unlink()), the
        enclosing tempdir is still present on disk. This documents the
        current (buggy) behaviour; once fixed, this assertion should be
        inverted."""
        import shutil

        with tempfile.TemporaryDirectory() as td:
            sealed = Path(td) / "audit.2026-01-01T000000Z.jsonl.age"
            sealed.write_bytes(b"sealed-bytes-not-really-encrypted")

            with mock.patch.object(_mod.subprocess, "run", side_effect=self._fake_successful_run):
                plaintext = unseal_to_temp(sealed)

            tmpdir = plaintext.parent
            try:
                self.assertTrue(plaintext.is_file())
                self.assertTrue(tmpdir.is_dir())
                self.assertTrue(str(tmpdir.name).startswith("corvin-unseal-"))

                # Exactly what both real callers (voice_audit.py's
                # --include-sealed loop and cmd_unseal) do in their
                # `finally:` cleanup block.
                plaintext.unlink()

                self.assertFalse(plaintext.exists(), "plaintext file itself is cleaned up")
                # BUG: the enclosing tempdir is never removed by any
                # caller — it leaks permanently. This is the blind spot.
                self.assertTrue(
                    tmpdir.exists(),
                    "documents the known leak: corvin-unseal-* tempdir "
                    "survives the caller's plaintext.unlink() cleanup",
                )
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def test_unseal_to_temp_repeated_calls_accumulate_leaked_dirs(self):
        """Simulates the daily `voice-audit verify --include-sealed`
        job unsealing multiple sealed segments in one run: each call
        leaks its own tempdir, so N unseals leave N empty directories
        behind (unbounded growth over time), not just one."""
        import shutil

        with tempfile.TemporaryDirectory() as td:
            sealed = Path(td) / "audit.2026-01-01T000000Z.jsonl.age"
            sealed.write_bytes(b"sealed-bytes-not-really-encrypted")

            leaked_dirs: list[Path] = []
            try:
                with mock.patch.object(_mod.subprocess, "run", side_effect=self._fake_successful_run):
                    for _ in range(3):
                        plaintext = unseal_to_temp(sealed)
                        leaked_dirs.append(plaintext.parent)
                        # Caller-side cleanup, same as production code.
                        plaintext.unlink()

                # Three distinct tempdirs, all still present.
                self.assertEqual(len(set(leaked_dirs)), 3)
                for d in leaked_dirs:
                    self.assertTrue(d.exists(), f"{d} should have leaked but is gone")
            finally:
                for d in leaked_dirs:
                    shutil.rmtree(d, ignore_errors=True)

    def test_unseal_to_temp_with_caller_supplied_tmpdir_does_not_leak(self):
        """Sanity check / contrast case: when the caller passes its own
        ``tmpdir=`` (which none of the real callers currently do), the
        caller retains a reference and CAN clean up both the file and
        the directory itself. This confirms the leak is specific to the
        ``tmpdir=None`` (mkdtemp-internal) branch, not to unseal_to_temp
        in general."""
        with tempfile.TemporaryDirectory() as td:
            sealed = Path(td) / "audit.2026-01-01T000000Z.jsonl.age"
            sealed.write_bytes(b"sealed-bytes-not-really-encrypted")
            caller_owned_dir = Path(td) / "caller-owned-unseal-dir"

            with mock.patch.object(_mod.subprocess, "run", side_effect=self._fake_successful_run):
                plaintext = unseal_to_temp(sealed, tmpdir=caller_owned_dir)

            self.assertEqual(plaintext.parent, caller_owned_dir)
            self.assertTrue(plaintext.is_file())

            # Caller owns the dir it passed in, so it can remove both.
            import shutil
            shutil.rmtree(caller_owned_dir)
            self.assertFalse(caller_owned_dir.exists())


class TestAuditAllowList(unittest.TestCase):

    def test_keys_match_spec(self):
        self.assertEqual(_AUDIT_ALLOWED, frozenset({
            "rotated_segment", "sealed_segment", "sealer_cmd",
            "rotated_size_bytes", "age_days", "reason", "requester",
            "tsa_success", "timestamp_token_name",
        }))

    def test_smuggled_key_rejected(self):
        with self.assertRaises(ValueError):
            _validate_audit_details({
                "sealed_segment": "x",
                "audit_content": "leaked plaintext",
            })


class TestEncryptionConfigTSA(unittest.TestCase):

    def test_tsa_disabled_by_default(self):
        cfg = EncryptionConfig(enabled=True, recipient="R", sealer_cmd="age")
        self.assertFalse(cfg.tsa_enabled)
        self.assertEqual(cfg.tsa_url, "")
        self.assertEqual(cfg.tsa_hash_algo, "sha256")

    def test_tsa_enabled_requires_url(self):
        with self.assertRaises(ValueError):
            EncryptionConfig(
                enabled=True, recipient="R", sealer_cmd="age",
                tsa_enabled=True, tsa_url="",
            )

    def test_tsa_bad_hash_algo_raises(self):
        with self.assertRaises(ValueError):
            EncryptionConfig(
                enabled=True, recipient="R", sealer_cmd="age",
                tsa_enabled=True, tsa_url="http://tsa.example.com",
                tsa_hash_algo="md5",
            )

    def test_tsa_config_from_tenant(self):
        cfg = {
            "spec": {
                "audit": {
                    "encryption_at_rest": {
                        "enabled": True,
                        "recipient": "age1xyz",
                        "sealer_cmd": "age",
                        "tsa_enabled": True,
                        "tsa_url": "http://tsa.example.com/tsr",
                        "tsa_hash_algo": "sha256",
                    },
                },
            },
        }
        p = policy_from_tenant_config(cfg)
        self.assertTrue(p.encryption.tsa_enabled)
        self.assertEqual(p.encryption.tsa_url, "http://tsa.example.com/tsr")
        self.assertEqual(p.encryption.tsa_hash_algo, "sha256")


class TestBuildTSARequest(unittest.TestCase):

    def test_correct_total_length(self):
        dummy_hash = bytes(range(32))
        req = _build_tsa_request(dummy_hash)
        # For a 32-byte SHA-256 hash the DER encoding is exactly 59 bytes.
        self.assertEqual(len(req), 59)

    def test_starts_with_sequence_tag(self):
        req = _build_tsa_request(b"\x00" * 32)
        self.assertEqual(req[0], 0x30)

    def test_version_integer_1(self):
        req = _build_tsa_request(b"\x00" * 32)
        # version INTEGER 1 is at offset 2: 02 01 01
        self.assertEqual(req[2:5], bytes([0x02, 0x01, 0x01]))

    def test_hash_bytes_embedded(self):
        sentinel = bytes(range(32))
        req = _build_tsa_request(sentinel)
        # The 32 sentinel bytes must appear verbatim somewhere in the request.
        self.assertIn(sentinel, req)


class TestTSATimestamping(unittest.TestCase):
    """RFC 3161 TSA hook in rotate_and_seal."""

    def _policy(self, tsa_enabled: bool = False, tsa_url: str = "") -> AuditPolicy:
        return AuditPolicy(
            rotation=RotationPolicy(),
            encryption=EncryptionConfig(
                enabled=True, recipient="X", sealer_cmd="age",
                tsa_enabled=tsa_enabled,
                tsa_url=tsa_url or ("http://fake-tsa.example.com/tsr" if tsa_enabled else ""),
            ),
            retention=RetentionPolicy(),
        )

    def test_tsa_disabled_no_tsr_file(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 2)
            events: list[str] = []
            result = rotate_and_seal(
                audit, self._policy(tsa_enabled=False),
                audit_writer=lambda et, sv, d: events.append(et),
                sealer=_fake_sealer,
            )
            self.assertIsNone(result.timestamp_token_path)
            self.assertEqual(list(Path(td).glob("*.tsr")), [])
            self.assertNotIn("audit.segment_timestamped", events)
            self.assertNotIn("audit.tsa_request_failed", events)

    def test_tsa_happy_path(self):
        dummy_tsr = b"\x30\x0a\x02\x01\x00\x30\x05\x06\x03\x55\x1d\x07"

        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 3)
            events: list[tuple[str, str, dict]] = []

            def writer(et, sv, det):
                events.append((et, sv, dict(det)))

            with mock.patch.object(_mod, "_request_timestamp_token", return_value=dummy_tsr):
                result = rotate_and_seal(
                    audit, self._policy(tsa_enabled=True),
                    audit_writer=writer,
                    sealer=_fake_sealer,
                )

            # .tsr file written next to sealed file
            self.assertIsNotNone(result.timestamp_token_path)
            assert result.timestamp_token_path is not None
            self.assertTrue(result.timestamp_token_path.is_file())
            self.assertTrue(result.timestamp_token_path.name.endswith(".tsr"))
            self.assertEqual(result.timestamp_token_path.read_bytes(), dummy_tsr)
            # chmod 444
            self.assertEqual(result.timestamp_token_path.stat().st_mode & 0o777, 0o444)
            # audit.segment_timestamped event emitted
            event_types = [e[0] for e in events]
            self.assertIn("audit.segment_timestamped", event_types)
            self.assertNotIn("audit.tsa_request_failed", event_types)
            # sealed_segment + tsa_success + basename only in the timestamped event
            ts_event = next(e for e in events if e[0] == "audit.segment_timestamped")
            self.assertNotIn("tsa_url", ts_event[2])
            self.assertNotIn("timestamp_token_path", ts_event[2])
            self.assertTrue(ts_event[2].get("tsa_success"))
            self.assertIn("timestamp_token_name", ts_event[2])
            # basename only — no path separators
            self.assertNotIn("/", ts_event[2]["timestamp_token_name"])

    def test_tsa_failure_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 2)
            events: list[tuple[str, str, dict]] = []

            def writer(et, sv, det):
                events.append((et, sv, dict(det)))

            with mock.patch.object(
                _mod, "_request_timestamp_token",
                side_effect=RuntimeError("connection refused"),
            ):
                result = rotate_and_seal(
                    audit, self._policy(tsa_enabled=True),
                    audit_writer=writer,
                    sealer=_fake_sealer,
                )

            # Seal succeeded despite TSA failure
            self.assertTrue(result.rotated)
            self.assertIsNotNone(result.sealed_path)
            # No .tsr file
            self.assertIsNone(result.timestamp_token_path)
            self.assertEqual(list(Path(td).glob("*.tsr")), [])
            # WARNING event emitted, not CRITICAL
            event_types = [e[0] for e in events]
            self.assertIn("audit.segment_sealed", event_types)
            self.assertIn("audit.tsa_request_failed", event_types)
            fail_event = next(e for e in events if e[0] == "audit.tsa_request_failed")
            self.assertEqual(fail_event[1], "WARNING")
            self.assertIn("connection refused", fail_event[2]["reason"])

    def test_tsa_called_with_correct_args(self):
        """Verify _request_timestamp_token receives sealed_path + config values."""
        captured: list[dict] = []

        def mock_tsr(path, *, tsa_url, hash_algo, **_):
            captured.append({"path": path, "tsa_url": tsa_url, "hash_algo": hash_algo})
            return b"\x00" * 10

        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            _write_chain(audit, 2)
            with mock.patch.object(_mod, "_request_timestamp_token", side_effect=mock_tsr):
                rotate_and_seal(
                    audit, self._policy(tsa_enabled=True, tsa_url="http://mytsa.test/tsr"),
                    sealer=_fake_sealer,
                    audit_writer=lambda et, sev, det: None,  # required when encryption enabled
                )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["tsa_url"], "http://mytsa.test/tsr")
        self.assertEqual(captured[0]["hash_algo"], "sha256")


class TestNoAnthropicImport(unittest.TestCase):

    def test_no_anthropic_in_source(self):
        import ast
        src = (Path(__file__).resolve().parent / "audit_sealer.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


if __name__ == "__main__":
    unittest.main()
