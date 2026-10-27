"""ADR-2287 review findings (2026-10-27) on the holder's object store, ``a2a_objects``.

Store-level properties (classification of the whole object and of evasive encodings, the replay
ledger, the quota reservation, the staging open, the start sweep, erasure errors and the erase/serve
race). The transport-level findings (default send, CLI duplicate names, the pull audit records) are
in ``test_a2a_large_object_reference_e2e.py``.

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_objects_review.py
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
import zipfile
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_objects as o  # noqa: E402

W = o.CLASSIFY_SCAN_BYTES
PII = ("name,email,iban\n" + "".join(
    f"Max Mustermann{i},max{i}@example.com,DE89370400440532013{i:03d}\n" for i in range(200))).encode()
PEM = b"-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890abcdef\n-----END RSA PRIVATE KEY-----\n"
PAD = b"lorem ipsum dolor sit amet\n" * (W // 27 + 1)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64


def _noop(*_a, **_k):
    return None


class _Store(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CORVIN_A2A_FEED_DIR": str(self.root / "g" / "a2a_feed"),
                                                 "CORVIN_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)
        getattr(o, "_reset_for_tests", o._last_sweep.clear)()

    def stage(self, data: bytes, name: str = "out.txt"):
        p = self.root / "work" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
        return o.stage_file(p)

    def issue(self, data: bytes, cfg: dict, name: str = "out.txt"):
        staged, why = self.stage(data, name)
        self.assertIsNotNone(staged, why)
        return o.issue(staged, origin_id="peerA", origin_cfg=cfg, task_id="t",
                       audit_strict=_noop, audit=_noop)


class TestWholeObjectClassification(_Store):
    """Finding 1 (HIGH) + 2: the whole object is classified, in overlapping windows."""

    def test_pem_after_one_mib_is_secret_and_refused_for_a_public_origin(self):
        ref, why = self.issue(b"a" * (W + 10) + PEM, {"max_data_classification": "PUBLIC"})
        self.assertIsNone(ref)
        self.assertEqual(why, "object_classification_exceeded")
        self.assertEqual(o.classify_bytes(PAD + PEM), "SECRET")

    def test_secret_straddling_a_window_edge_is_found(self):
        cut = W - 10   # the PEM header starts 10 bytes before the first window ends
        self.assertEqual(o.classify_bytes(b"a" * cut + PEM), "SECRET")

    def test_marker_and_personal_data_after_the_first_window(self):
        self.assertEqual(o.classify_bytes(PAD + b"\n[class:confidential]\n"), "CONFIDENTIAL")
        self.assertEqual(o.classify_bytes(PAD + PII), "CONFIDENTIAL")

    def test_a_multibyte_character_cut_by_a_window_edge_is_still_text(self):
        head = PEM + b"x" * (W - len(PEM) - 1)
        self.assertEqual(o.classify_bytes(head + "€".encode() + b"tail" * 300000), "SECRET")
        plain = b"x" * (W - 1) + "€".encode() + b"tail" * 1000
        self.assertEqual(o.classify_bytes(plain), "PUBLIC")
        # the file path agrees with the bytes path
        ref, why = self.issue(head + "€".encode() + b"tail" * 300000, {"max_data_classification": "INTERNAL"})
        self.assertEqual((ref, why), (None, "object_classification_exceeded"))


class TestEvasiveEncodings(_Store):
    """Finding 3: one NUL byte, UTF-16, compression/archives and undecodable text do not make
    personal data or a key INTERNAL."""

    def test_nul_bytes_are_stripped(self):
        self.assertEqual(o.classify_bytes(PII + b"\x00"), "CONFIDENTIAL")
        self.assertEqual(o.classify_bytes(PEM + b"\x00"), "SECRET")

    def test_utf16_with_and_without_bom(self):
        self.assertEqual(o.classify_bytes(PII.decode().encode("utf-16")), "CONFIDENTIAL")
        self.assertEqual(o.classify_bytes(PII.decode().encode("utf-16-le")), "CONFIDENTIAL")
        self.assertEqual(o.classify_bytes(PEM.decode().encode("utf-16-be")), "SECRET")

    def test_archives_and_compression_are_confidential(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("customers.csv", PII)
        for label, data in (("gzip", gzip.compress(PII)), ("zip", z.getvalue()), ("xz", lzma.compress(PII)),
                            ("bzip2", bz2.compress(PII)), ("7z", b"7z\xbc\xaf\x27\x1c" + b"\x00\x04" + PII)):
            with self.subTest(label):
                self.assertEqual(o.classify_bytes(data), "CONFIDENTIAL")

    def test_undecodable_text_is_confidential(self):
        latin1 = PII.decode().replace("Max", "Jürgen").encode("latin-1")
        self.assertEqual(o.classify_bytes(latin1), "CONFIDENTIAL")

    def test_known_media_stays_internal_and_an_embedded_key_is_still_found(self):
        self.assertEqual(o.classify_bytes(PNG), "INTERNAL")
        self.assertEqual(o.classify_bytes(PNG + PEM), "SECRET")

    def test_inline_gate_uses_the_same_classifier(self):
        self.assertEqual(o.classify_bytes(gzip.compress(PII)), "CONFIDENTIAL")


class TestReplayLedgerIds(_Store):
    """Finding 4: a ping id longer than the ledger keeps is refused, never replayable."""

    def test_long_ping_id_is_refused_and_cannot_replay(self):
        ref, why = self.issue(PNG + os.urandom(2 * 1024 * 1024), {}, "big.bin")
        self.assertIsNotNone(ref, why)
        long_id = "x" * 200
        kw = dict(origin_id="peerA", origin_cfg={}, ref_id=ref["ref_id"], offset=0, length=1024,
                  audit_strict=_noop, audit=_noop)
        first = o.serve(pull_id=long_id, **kw)
        second = o.serve(pull_id=long_id, **kw)
        self.assertNotIn(None, (first[1], second[1]), "a long id is never served, so it can never replay")
        self.assertEqual(first[1], "object_bad_request")
        ok = o.serve(pull_id="y" * 36, **kw)
        self.assertIsNone(ok[1])
        self.assertEqual(o.serve(pull_id="y" * 36, **kw)[1], "object_pull_replayed")


class TestStartSweep(_Store):
    """Finding 6: orphans (bytes without a readable record) and expired objects are swept at
    receiver start, not only on traffic."""

    def test_old_orphans_go_young_ones_stay(self):
        root = o.store_dir()
        o._ensure(root)
        old_bin, young_bin = root / ("A" * 32 + ".bin"), root / ("B" * 32 + ".bin")
        bad_bin, bad_meta = root / ("C" * 32 + ".bin"), root / ("C" * 32 + ".json")
        for p in (old_bin, young_bin, bad_bin):
            p.write_bytes(b"x" * 100)
        bad_meta.write_text("{not json")
        past = time.time() - o.MAX_TTL_S - 60
        for p in (old_bin, bad_bin, bad_meta):
            os.utime(p, (past, past))
        import remote_trigger_receiver as rtr

        rtr.RemoteTriggerReceiver(origins_dir=self.root / "origins", nonce_store=rtr.NonceStore(),
                                  instance_id="iid", force_m1_only=True, forge_se=mock.MagicMock())
        self.assertFalse(old_bin.exists())
        self.assertFalse(bad_bin.exists())
        self.assertFalse(bad_meta.exists())
        self.assertTrue(young_bin.exists(), "a young orphan may be an issue in flight")

    def test_expired_object_is_swept_at_start(self):
        ref, _ = self.issue(PNG + os.urandom(2000), {}, "e.bin")
        meta = o._read_meta(o.store_dir(), ref["ref_id"])
        meta["expires_at"] = time.time() - 1
        o._write_meta(o.store_dir(), meta)
        o._last_sweep.clear()
        import remote_trigger_receiver as rtr

        rtr.RemoteTriggerReceiver(origins_dir=self.root / "origins", nonce_store=rtr.NonceStore(),
                                  instance_id="iid", force_m1_only=True, forge_se=mock.MagicMock())
        self.assertFalse((o.store_dir() / f"{ref['ref_id']}.bin").exists())


class TestErasure(_Store):
    """Finding 7: an erasure failure reaches L36, and a pull racing an erasure recreates nothing."""

    def test_erase_failure_is_reported_as_failed(self):
        import a2a_feed
        import erasure_handlers as eh

        self.issue(PNG + os.urandom(2000), {}, "e.bin")
        a2a_feed.feed_dir().mkdir(parents=True, exist_ok=True)
        with mock.patch.object(o, "erase_origin", side_effect=OSError("disk")):
            with mock.patch.object(eh, "_tenant_global", lambda tid: a2a_feed.feed_dir().parent):
                res = eh.A2AFeedHandler(tenant_id="_default").purge("peerA", "req")
        self.assertEqual(getattr(res.status, "value", res.status), "failed", res)

    def test_a_pull_racing_an_erasure_does_not_recreate_the_record(self):
        ref, _ = self.issue(PNG + os.urandom(2000), {}, "race.bin")
        root = o.store_dir()

        def erased_elsewhere_meanwhile(*_a, **_k):
            # another process (the L36 erasure) removes bytes and record after the chunk was read,
            # while this process writes its pull record
            (root / f"{ref['ref_id']}.bin").unlink(missing_ok=True)
            (root / f"{ref['ref_id']}.json").unlink(missing_ok=True)

        chunk, why = o.serve(origin_id="peerA", origin_cfg={}, ref_id=ref["ref_id"], offset=0, length=100,
                             pull_id="p1", audit_strict=erased_elsewhere_meanwhile, audit=_noop)
        self.assertIsNone(chunk)
        self.assertEqual(why, "object_unavailable")
        self.assertFalse((root / f"{ref['ref_id']}.json").exists(), "the erased record was recreated")


class TestQuotaReservation(_Store):
    """Finding 8: concurrent stagers cannot overrun the quota; a disk-space floor applies."""

    def test_concurrent_stagers_respect_the_quota(self):
        size = 1024 * 1024
        srcs = []
        for i in range(4):
            p = self.root / "work" / f"s{i}.bin"
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(os.urandom(size))
            srcs.append(p)
        real_open = os.open

        def slow_open(path, flags, *a, **k):
            if str(path).endswith(".part"):
                time.sleep(0.3)   # every stager passes its quota check before any copy lands
            return real_open(path, flags, *a, **k)

        results: list = []
        barrier = threading.Barrier(4)

        def run(p):
            barrier.wait()
            results.append(o.stage_file(p))

        with mock.patch.object(o, "STORE_QUOTA_BYTES", 2 * size + 10), mock.patch.object(o.os, "open", slow_open):
            ts = [threading.Thread(target=run, args=(p,)) for p in srcs]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
        staged = [s for s, _ in results if s is not None]
        self.assertEqual(len(staged), 2, results)
        self.assertEqual(sorted(w for _, w in results if w), ["object_store_full", "object_store_full"])

    def test_disk_floor(self):
        fake = mock.Mock(free=o.MIN_FREE_DISK_BYTES + 10, total=1, used=0)
        with mock.patch.object(o.shutil, "disk_usage", return_value=fake):
            staged, why = self.stage(b"x" * 100)
        self.assertEqual((staged, why), (None, "object_store_full"))


class TestStagingOpen(_Store):
    """Finding 9: what harvest checked is what staging reads (no symlink/hardlink swap)."""

    def test_a_symlink_swapped_in_after_the_harvest_check_is_not_staged(self):
        import a2a_worker

        secret = self.root / "secret.txt"
        secret.write_bytes(b"operator secret " * 100000)
        out = self.root / "out"
        out.mkdir()
        target = out / "result.bin"
        target.write_bytes(os.urandom(2 * 1024 * 1024))
        real_stage = o.stage_file

        def swap_then_stage(src, *a, **k):
            src.unlink()
            src.symlink_to(secret)
            return real_stage(src, *a, **k)

        staged: list = []
        with mock.patch.object(o, "stage_file", swap_then_stage):
            atts, dropped = a2a_worker._harvest_report(out, {"attachments_by_reference": True}, staged_out=staged)
        self.assertEqual(staged, [], "the swapped-in symlink target was staged")
        self.assertEqual(dropped, {"unreadable": 1})

    def test_a_hardlink_is_refused(self):
        p = self.root / "work" / "h.bin"
        p.parent.mkdir()
        p.write_bytes(os.urandom(1000))
        os.link(p, self.root / "work" / "alias.bin")
        self.assertEqual(o.stage_file(p), (None, "unreadable"))


if __name__ == "__main__":
    unittest.main()
