"""Tests for instance_identity.py — Layer 38 stable instance UUID."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import instance_identity  # type: ignore[import-not-found]

try:
    import jwt as _pyjwt
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    _M3_DEPS_OK = True
except ImportError:
    _M3_DEPS_OK = False


def _make_rsa_test_keypair() -> tuple[str, str]:
    """Generate a throwaway RSA keypair for RS256 IBC signing in tests."""
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=_ser.Encoding.PEM,
        format=_ser.PrivateFormat.PKCS8,
        encryption_algorithm=_ser.NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        encoding=_ser.Encoding.PEM,
        format=_ser.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def _sign_test_ibc(priv_pem: str, claims: dict) -> str:
    return _pyjwt.encode(claims, priv_pem, algorithm="RS256")


class _TempHomeMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.id_path = self.tmpdir / "instance_id.json"
        self._prev_env = os.environ.get("CORVIN_INSTANCE_ID_PATH")
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(self.id_path)

    def tearDown(self) -> None:  # type: ignore[override]
        if self._prev_env is not None:
            os.environ["CORVIN_INSTANCE_ID_PATH"] = self._prev_env
        else:
            os.environ.pop("CORVIN_INSTANCE_ID_PATH", None)
        self._tmp.cleanup()


class TestFirstCall(_TempHomeMixin, unittest.TestCase):
    def test_first_call_creates_file(self) -> None:
        self.assertFalse(self.id_path.exists())
        iid = instance_identity.get_instance_id()
        self.assertTrue(self.id_path.exists())
        # Validate UUID4 shape
        uuid.UUID(iid, version=4)

    def test_file_mode_is_0600(self) -> None:
        instance_identity.get_instance_id()
        mode = self.id_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_file_not_group_or_world_readable(self) -> None:
        instance_identity.get_instance_id()
        mode = self.id_path.stat().st_mode
        self.assertFalse(mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_metadata_includes_created_at(self) -> None:
        meta = instance_identity.instance_id_metadata()
        self.assertIn("created_at", meta)
        # ISO 8601 with timezone
        self.assertIn("T", meta["created_at"])

    def test_metadata_label_defaults_empty(self) -> None:
        meta = instance_identity.instance_id_metadata()
        self.assertEqual(meta.get("label"), "")


class TestStability(_TempHomeMixin, unittest.TestCase):
    def test_subsequent_call_returns_same_id(self) -> None:
        iid1 = instance_identity.get_instance_id()
        iid2 = instance_identity.get_instance_id()
        self.assertEqual(iid1, iid2)

    def test_id_survives_module_reload(self) -> None:
        iid1 = instance_identity.get_instance_id()
        # Simulate restart by reloading via fresh metadata read
        with self.id_path.open() as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["instance_id"], iid1)


class TestThreadSafety(_TempHomeMixin, unittest.TestCase):
    def test_concurrent_first_call_yields_single_uuid(self) -> None:
        results: list[str] = []
        ev = threading.Event()

        def worker() -> None:
            ev.wait()
            results.append(instance_identity.get_instance_id())

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        ev.set()
        for t in threads:
            t.join()
        self.assertEqual(len(set(results)), 1)


class TestLabel(_TempHomeMixin, unittest.TestCase):
    def test_set_label_creates_and_updates(self) -> None:
        meta = instance_identity.set_label("test-instance")
        self.assertEqual(meta["label"], "test-instance")
        # And the file reflects it
        with self.id_path.open() as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["label"], "test-instance")

    def test_set_label_preserves_uuid(self) -> None:
        iid = instance_identity.get_instance_id()
        instance_identity.set_label("renamed")
        self.assertEqual(instance_identity.get_instance_id(), iid)

    def test_set_label_rejects_control_chars(self) -> None:
        with self.assertRaises(ValueError):
            instance_identity.set_label("bad\x00label")
        with self.assertRaises(ValueError):
            instance_identity.set_label("with\nnewline")

    def test_set_label_rejects_too_long(self) -> None:
        with self.assertRaises(ValueError):
            instance_identity.set_label("x" * 65)

    def test_set_label_accepts_max_length(self) -> None:
        meta = instance_identity.set_label("x" * 64)
        self.assertEqual(len(meta["label"]), 64)

    def test_set_label_requires_str(self) -> None:
        with self.assertRaises(TypeError):
            instance_identity.set_label(123)  # type: ignore[arg-type]


class TestSelfHeal(_TempHomeMixin, unittest.TestCase):
    def test_world_readable_file_gets_tightened(self) -> None:
        instance_identity.get_instance_id()
        os.chmod(self.id_path, 0o644)
        # Re-read; the loader should silently tighten back to 0600.
        instance_identity.get_instance_id()
        mode = self.id_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_corrupt_file_regenerates(self) -> None:
        with self.id_path.open("w") as fh:
            fh.write("not valid json {{{")
        os.chmod(self.id_path, 0o600)
        iid = instance_identity.get_instance_id()
        uuid.UUID(iid, version=4)

    def test_missing_uuid_field_regenerates(self) -> None:
        with self.id_path.open("w") as fh:
            json.dump({"created_at": "2026-01-01T00:00:00+00:00"}, fh)
        os.chmod(self.id_path, 0o600)
        iid = instance_identity.get_instance_id()
        uuid.UUID(iid, version=4)


class TestEnvLabel(_TempHomeMixin, unittest.TestCase):
    def test_env_label_used_on_first_create(self) -> None:
        os.environ["CORVIN_INSTANCE_LABEL"] = "from-env"
        try:
            meta = instance_identity.instance_id_metadata()
            self.assertEqual(meta["label"], "from-env")
        finally:
            os.environ.pop("CORVIN_INSTANCE_LABEL", None)


class _TempIBCHomeMixin(_TempHomeMixin):
    """Adds key/cert/CRL-cache path isolation and a throwaway RS256 trust anchor."""

    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()
        self.key_path = self.tmpdir / "instance_key.pem"
        self.cert_path = self.tmpdir / "instance_cert.jwt"
        self.crl_cache_path = self.tmpdir / "ibc_crl_cache.json"
        self._env_overrides = {
            "CORVIN_INSTANCE_KEY_PATH": str(self.key_path),
            "CORVIN_INSTANCE_CERT_PATH": str(self.cert_path),
            "CORVIN_CRL_CACHE_PATH": str(self.crl_cache_path),
        }
        self._prev_overrides = {k: os.environ.get(k) for k in self._env_overrides}
        os.environ.update(self._env_overrides)

        if _M3_DEPS_OK:
            self.rsa_priv_pem, self.rsa_pub_pem = _make_rsa_test_keypair()
            self._prev_pubkey_env = os.environ.get("CORVIN_IBC_PUBKEY_PEM")
            os.environ["CORVIN_IBC_PUBKEY_PEM"] = self.rsa_pub_pem

    def tearDown(self) -> None:  # type: ignore[override]
        for k, v in self._prev_overrides.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        if _M3_DEPS_OK:
            if self._prev_pubkey_env is not None:
                os.environ["CORVIN_IBC_PUBKEY_PEM"] = self._prev_pubkey_env
            else:
                os.environ.pop("CORVIN_IBC_PUBKEY_PEM", None)
        super().tearDown()

    def _write_local_ibc(self, **extra_claims) -> dict:
        """Write a locally-valid, RS256-signed IBC to self.cert_path and return its claims."""
        instance_id = instance_identity.get_instance_id()
        claims = {
            "iss": "Corvin Labs",
            "sub": instance_id,
            "email": "test@example.com",
            "license_id": "lic_test",
            "plan": "member",
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "jti": "test-jti-0001",
        }
        claims.update(extra_claims)
        token = _sign_test_ibc(self.rsa_priv_pem, claims)
        self.cert_path.write_text(token, encoding="utf-8")
        os.chmod(self.cert_path, 0o600)
        return claims


@unittest.skipUnless(_M3_DEPS_OK, "pyjwt/cryptography not installed")
class TestHardwareFingerprint(_TempHomeMixin, unittest.TestCase):
    def test_tpm_absence_does_not_break_fingerprint(self) -> None:
        with mock.patch.object(instance_identity, "_read_tpm_pcr0", return_value=""):
            fp = instance_identity.compute_hardware_fp()
        # CPU/MAC alone should still be enough on any real machine.
        self.assertIsInstance(fp, str)

    def test_fingerprint_changes_when_tpm_pcr0_changes(self) -> None:
        with mock.patch.object(instance_identity, "_read_tpm_pcr0", return_value="aaa"):
            fp_a = instance_identity.compute_hardware_fp()
        with mock.patch.object(instance_identity, "_read_tpm_pcr0", return_value="bbb"):
            fp_b = instance_identity.compute_hardware_fp()
        self.assertNotEqual(fp_a, fp_b)


@unittest.skipUnless(_M3_DEPS_OK, "pyjwt/cryptography not installed")
class TestBindInstance(_TempIBCHomeMixin, unittest.TestCase):
    """bind_instance() (M1) had no direct test coverage before this session."""

    def _fake_resp(self, body: bytes):
        fake_resp = mock.Mock()
        fake_resp.read.return_value = body
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)
        return fake_resp

    def test_success_stores_cert(self) -> None:
        instance_id = instance_identity.get_instance_id()
        claims = {
            "sub": instance_id, "email": "a@b.com", "license_id": "lic",
            "plan": "member", "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "iat": int(time.time()), "exp": int(time.time()) + 3600, "jti": "bind-jti",
        }
        token = _sign_test_ibc(self.rsa_priv_pem, claims)
        resp = self._fake_resp(json.dumps({"ibc": token}).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with mock.patch.object(instance_identity, "_verify_ibc_signature", return_value=None):
                decoded = instance_identity.bind_instance("sest-tok", "fp-123")
        self.assertEqual(decoded["sub"], instance_id)
        self.assertTrue(self.cert_path.exists())

    def test_malformed_json_response_raises_ibcerror(self) -> None:
        resp = self._fake_resp(b"not json {{{")
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance("sest-tok", "fp-123")

    def test_non_object_json_response_raises_ibcerror(self) -> None:
        resp = self._fake_resp(json.dumps(["not", "a", "dict"]).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance("sest-tok", "fp-123")

    def test_missing_ibc_field_raises_ibcerror(self) -> None:
        resp = self._fake_resp(json.dumps({"not_ibc": "x"}).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance("sest-tok", "fp-123")

    def test_http_error_raises_ibcerror(self) -> None:
        import urllib.error
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 403, "Forbidden", {}, None),
        ):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance("sest-tok", "fp-123")

    def test_sub_mismatch_raises_ibcerror(self) -> None:
        claims = {
            "sub": "someone-elses-instance-id",
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "iat": int(time.time()), "exp": int(time.time()) + 3600, "jti": "x",
        }
        token = _sign_test_ibc(self.rsa_priv_pem, claims)
        resp = self._fake_resp(json.dumps({"ibc": token}).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with mock.patch.object(instance_identity, "_verify_ibc_signature", return_value=None):
                with self.assertRaises(instance_identity.IBCError):
                    instance_identity.bind_instance("sest-tok", "fp-123")

    def test_pubkey_mismatch_raises_ibcerror(self) -> None:
        """R1 finding: an IBC signed for a DIFFERENT pubkey must be rejected."""
        instance_id = instance_identity.get_instance_id()
        claims = {
            "sub": instance_id, "instance_pubkey": "not-our-pubkey-b64",
            "iat": int(time.time()), "exp": int(time.time()) + 3600, "jti": "x",
        }
        token = _sign_test_ibc(self.rsa_priv_pem, claims)
        resp = self._fake_resp(json.dumps({"ibc": token}).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with mock.patch.object(instance_identity, "_verify_ibc_signature", return_value=None):
                with self.assertRaises(instance_identity.IBCError):
                    instance_identity.bind_instance("sest-tok", "fp-123")


@unittest.skipUnless(_M3_DEPS_OK, "pyjwt/cryptography not installed")
class TestBindHardware(_TempIBCHomeMixin, unittest.TestCase):
    def test_bind_hardware_without_existing_ibc_raises(self) -> None:
        with self.assertRaises(instance_identity.IBCError):
            instance_identity.bind_hardware()

    def test_bind_hardware_success_stores_reissued_cert(self) -> None:
        self._write_local_ibc()
        instance_id = instance_identity.get_instance_id()

        reissued_claims = {
            "iss": "Corvin Labs", "sub": instance_id, "email": "test@example.com",
            "license_id": "lic_test", "plan": "member",
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "hardware_fp": "deadbeef" * 8,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "jti": "test-jti-0002",
        }
        reissued_jwt = _sign_test_ibc(self.rsa_priv_pem, reissued_claims)

        fake_resp = mock.Mock()
        fake_resp.read.return_value = json.dumps({"ibc": reissued_jwt}).encode()
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="deadbeef" * 8):
            with mock.patch("urllib.request.urlopen", return_value=fake_resp):
                # The real repo checkout ships a2a_network_pubkey.pem, which takes
                # precedence over CORVIN_IBC_PUBKEY_PEM — bypass real-signature
                # verification here since this test only exercises the
                # store/decode/mismatch-guard logic, not RS256 verification itself.
                with mock.patch.object(instance_identity, "_verify_ibc_signature", return_value=None):
                    decoded = instance_identity.bind_hardware()

        self.assertEqual(decoded["hardware_fp"], "deadbeef" * 8)
        on_disk = self.cert_path.read_text(encoding="utf-8").strip()
        self.assertEqual(on_disk, reissued_jwt)

    def test_bind_hardware_rejects_malformed_json_response(self) -> None:
        self._write_local_ibc()
        fake_resp = mock.Mock()
        fake_resp.read.return_value = b"not valid json {{{"
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="fp"):
            with mock.patch("urllib.request.urlopen", return_value=fake_resp):
                with self.assertRaises(instance_identity.IBCError):
                    instance_identity.bind_hardware()

    def test_bind_hardware_rejects_mismatched_fp_in_response(self) -> None:
        self._write_local_ibc()
        instance_id = instance_identity.get_instance_id()
        reissued_claims = {
            "sub": instance_id, "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "hardware_fp": "wrong-fp", "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "jti": "test-jti-0003",
        }
        reissued_jwt = _sign_test_ibc(self.rsa_priv_pem, reissued_claims)
        fake_resp = mock.Mock()
        fake_resp.read.return_value = json.dumps({"ibc": reissued_jwt}).encode()
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="expected-fp"):
            with mock.patch("urllib.request.urlopen", return_value=fake_resp):
                with mock.patch.object(instance_identity, "_verify_ibc_signature", return_value=None):
                    with self.assertRaises(instance_identity.IBCError):
                        instance_identity.bind_hardware()


@unittest.skipUnless(_M3_DEPS_OK, "pyjwt/cryptography not installed")
class TestCheckHardwareBinding(_TempIBCHomeMixin, unittest.TestCase):
    def test_no_ibc_reports_not_bound(self) -> None:
        result = instance_identity.check_hardware_binding()
        self.assertFalse(result["bound"])
        self.assertIsNone(result["matches"])

    def test_ibc_without_hardware_claim_reports_not_bound(self) -> None:
        self._write_local_ibc()
        result = instance_identity.check_hardware_binding()
        self.assertFalse(result["bound"])
        self.assertIsNone(result["matches"])

    def test_matching_fingerprint_reports_match(self) -> None:
        self._write_local_ibc(hardware_fp="fp-match")
        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="fp-match"):
            result = instance_identity.check_hardware_binding()
        self.assertTrue(result["bound"])
        self.assertTrue(result["matches"])

    def test_mismatched_fingerprint_reports_mismatch(self) -> None:
        self._write_local_ibc(hardware_fp="fp-original")
        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="fp-changed"):
            result = instance_identity.check_hardware_binding()
        self.assertTrue(result["bound"])
        self.assertFalse(result["matches"])


@unittest.skipUnless(_M3_DEPS_OK, "pyjwt/cryptography not installed")
class TestRevocationList(_TempIBCHomeMixin, unittest.TestCase):
    def _fake_crl_response(self, revoked_jti: list[str]):
        fake_resp = mock.Mock()
        fake_resp.read.return_value = json.dumps({"revoked_jti": revoked_jti}).encode()
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)
        return fake_resp

    def test_fetch_writes_cache_and_reuses_within_ttl(self) -> None:
        with mock.patch(
            "urllib.request.urlopen", return_value=self._fake_crl_response(["jti-a"])
        ) as mock_urlopen:
            first = instance_identity.fetch_revocation_list()
            second = instance_identity.fetch_revocation_list()
        self.assertEqual(first, ["jti-a"])
        self.assertEqual(second, ["jti-a"])
        mock_urlopen.assert_called_once()
        self.assertTrue(self.crl_cache_path.exists())

    def test_offline_falls_back_to_stale_cache_within_grace(self) -> None:
        stale_payload = {
            "fetched_at": time.time() - (2 * 24 * 3600),  # 2 days old: past TTL, within 7d grace
            "revoked_jti": ["jti-stale"],
        }
        self.crl_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.crl_cache_path.write_text(json.dumps(stale_payload), encoding="utf-8")

        with mock.patch(
            "urllib.request.urlopen", side_effect=OSError("network unreachable")
        ):
            result = instance_identity.fetch_revocation_list()
        self.assertEqual(result, ["jti-stale"])

    def test_offline_no_cache_returns_empty_not_revoked(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=OSError("network unreachable")):
            result = instance_identity.fetch_revocation_list()
        self.assertEqual(result, [])

    def test_is_ibc_revoked_true_when_jti_listed(self) -> None:
        self._write_local_ibc(jti="revoked-jti")
        with mock.patch.object(
            instance_identity, "fetch_revocation_list", return_value=["revoked-jti"]
        ):
            self.assertTrue(instance_identity.is_ibc_revoked())

    def test_is_ibc_revoked_false_when_not_listed(self) -> None:
        self._write_local_ibc(jti="clean-jti")
        with mock.patch.object(
            instance_identity, "fetch_revocation_list", return_value=["some-other-jti"]
        ):
            self.assertFalse(instance_identity.is_ibc_revoked())

    def test_is_ibc_revoked_false_when_no_ibc(self) -> None:
        self.assertFalse(instance_identity.is_ibc_revoked())

    def test_get_ibc_jwt_returns_none_when_revoked(self) -> None:
        # get_ibc_jwt() is the hot path for every outbound A2A send — it must
        # answer from the local CRL cache only, never trigger a live fetch.
        self._write_local_ibc(jti="revoked-jti")
        self.crl_cache_path.write_text(
            json.dumps({"fetched_at": time.time(), "revoked_jti": ["revoked-jti"]}),
            encoding="utf-8",
        )
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("must not hit network")):
            self.assertIsNone(instance_identity.get_ibc_jwt())

    def test_get_ibc_jwt_returns_token_when_not_revoked(self) -> None:
        self._write_local_ibc(jti="clean-jti")
        self.crl_cache_path.write_text(
            json.dumps({"fetched_at": time.time(), "revoked_jti": []}),
            encoding="utf-8",
        )
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("must not hit network")):
            self.assertIsNotNone(instance_identity.get_ibc_jwt())

    def test_get_ibc_jwt_never_hits_network_even_with_stale_cache(self) -> None:
        """The hot path must not fetch even when the cache is past its TTL —
        that refresh belongs to an out-of-band job, never to a send call."""
        self._write_local_ibc(jti="clean-jti")
        self.crl_cache_path.write_text(
            json.dumps({
                "fetched_at": time.time() - (2 * instance_identity._CRL_TTL_SECONDS),
                "revoked_jti": [],
            }),
            encoding="utf-8",
        )
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("must not hit network")):
            self.assertIsNotNone(instance_identity.get_ibc_jwt())


@unittest.skipUnless(_M3_DEPS_OK, "pyjwt/cryptography not installed")
class TestRevocationStatusCached(_TempIBCHomeMixin, unittest.TestCase):
    def test_no_ibc_is_unknown(self) -> None:
        self.assertEqual(instance_identity.revocation_status_cached(), "unknown")

    def test_no_cache_file_is_unknown(self) -> None:
        self._write_local_ibc(jti="jti-x")
        self.assertEqual(instance_identity.revocation_status_cached(), "unknown")

    def test_never_makes_network_call(self) -> None:
        self._write_local_ibc(jti="jti-x")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("must not hit network")):
            self.assertEqual(instance_identity.revocation_status_cached(), "unknown")

    def test_clean_when_jti_not_in_fresh_cache(self) -> None:
        self._write_local_ibc(jti="jti-clean")
        self.crl_cache_path.write_text(
            json.dumps({"fetched_at": time.time(), "revoked_jti": ["other-jti"]}),
            encoding="utf-8",
        )
        self.assertEqual(instance_identity.revocation_status_cached(), "clean")

    def test_revoked_when_jti_in_fresh_cache(self) -> None:
        self._write_local_ibc(jti="jti-revoked")
        self.crl_cache_path.write_text(
            json.dumps({"fetched_at": time.time(), "revoked_jti": ["jti-revoked"]}),
            encoding="utf-8",
        )
        self.assertEqual(instance_identity.revocation_status_cached(), "revoked")

    def test_unknown_when_cache_older_than_grace(self) -> None:
        self._write_local_ibc(jti="jti-revoked")
        self.crl_cache_path.write_text(
            json.dumps({
                "fetched_at": time.time() - (8 * 24 * 3600),
                "revoked_jti": ["jti-revoked"],
            }),
            encoding="utf-8",
        )
        self.assertEqual(instance_identity.revocation_status_cached(), "unknown")


class TestCILint(unittest.TestCase):
    def test_no_anthropic_import(self) -> None:
        import ast
        src = (_here / "instance_identity.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
