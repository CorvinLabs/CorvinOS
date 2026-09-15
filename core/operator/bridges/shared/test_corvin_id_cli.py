"""Tests for corvin_id_cli.py — ADR-0145 M3 bind-hardware/check-hardware/check-revocation."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import corvin_id_cli  # type: ignore[import-not-found]
import instance_identity  # type: ignore[import-not-found]


class _TempHomeMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self._env_keys = (
            "CORVIN_INSTANCE_ID_PATH",
            "CORVIN_INSTANCE_KEY_PATH",
            "CORVIN_INSTANCE_CERT_PATH",
            "CORVIN_CRL_CACHE_PATH",
        )
        import os
        self._prev = {k: os.environ.get(k) for k in self._env_keys}
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(self.tmpdir / "instance_id.json")
        os.environ["CORVIN_INSTANCE_KEY_PATH"] = str(self.tmpdir / "instance_key.pem")
        os.environ["CORVIN_INSTANCE_CERT_PATH"] = str(self.tmpdir / "instance_cert.jwt")
        os.environ["CORVIN_CRL_CACHE_PATH"] = str(self.tmpdir / "ibc_crl_cache.json")

    def tearDown(self) -> None:  # type: ignore[override]
        import os
        for k, v in self._prev.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        self._tmp.cleanup()


class TestBindHardwareCommand(_TempHomeMixin, unittest.TestCase):
    def test_no_ibc_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            corvin_id_cli.main(["bind-hardware"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_delegates_to_instance_identity_bind_hardware(self) -> None:
        with mock.patch(
            "instance_identity.bind_hardware",
            return_value={"hardware_fp": "abc123", "jti": "jti-1"},
        ):
            rc = corvin_id_cli.main(["bind-hardware"])
        self.assertEqual(rc, 0)


class TestCheckHardwareCommand(_TempHomeMixin, unittest.TestCase):
    def test_not_bound_returns_zero(self) -> None:
        rc = corvin_id_cli.main(["check-hardware"])
        self.assertEqual(rc, 0)

    def test_mismatch_returns_nonzero(self) -> None:
        with mock.patch(
            "instance_identity.check_hardware_binding",
            return_value={
                "bound": True, "matches": False,
                "current_fp": "aaaa", "claimed_fp": "bbbb",
            },
        ):
            rc = corvin_id_cli.main(["check-hardware"])
        self.assertEqual(rc, 1)

    def test_match_returns_zero(self) -> None:
        with mock.patch(
            "instance_identity.check_hardware_binding",
            return_value={
                "bound": True, "matches": True,
                "current_fp": "aaaa", "claimed_fp": "aaaa",
            },
        ):
            rc = corvin_id_cli.main(["check-hardware"])
        self.assertEqual(rc, 0)


class TestCheckRevocationCommand(_TempHomeMixin, unittest.TestCase):
    def test_no_ibc_returns_zero(self) -> None:
        rc = corvin_id_cli.main(["check-revocation"])
        self.assertEqual(rc, 0)

    def test_revoked_returns_nonzero(self) -> None:
        with mock.patch("instance_identity.get_ibc", return_value={"jti": "x"}):
            with mock.patch("instance_identity.is_ibc_revoked", return_value=True):
                rc = corvin_id_cli.main(["check-revocation"])
        self.assertEqual(rc, 1)

    def test_not_revoked_returns_zero(self) -> None:
        with mock.patch("instance_identity.get_ibc", return_value={"jti": "x"}):
            with mock.patch("instance_identity.is_ibc_revoked", return_value=False):
                rc = corvin_id_cli.main(["check-revocation"])
        self.assertEqual(rc, 0)

    def test_refresh_flag_forwarded(self) -> None:
        with mock.patch("instance_identity.get_ibc", return_value={"jti": "x"}):
            with mock.patch("instance_identity.is_ibc_revoked", return_value=False) as mock_check:
                corvin_id_cli.main(["check-revocation", "--refresh"])
        mock_check.assert_called_once_with(force_refresh=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
