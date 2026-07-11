"""Tests for instance_identity.py — Layer 38 stable instance UUID."""
from __future__ import annotations

import base64
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
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    _M3_DEPS_OK = True
except ImportError:
    _M3_DEPS_OK = False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign_test_ibc(priv_key: "Ed25519PrivateKey", claims: dict, kid: str = "ibc-v1") -> str:
    """Build a CORVIN-<header>.<payload>.<sig> Ed25519 test token, mirroring
    Corvin-Features signing.py's issue_ibc_jwt() wire format."""
    header_b64 = _b64url(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": kid}).encode())
    payload_b64 = _b64url(json.dumps(claims).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = priv_key.sign(signing_input)
    return f"CORVIN-{header_b64}.{payload_b64}.{_b64url(sig)}"


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
    """Adds key/cert/CRL-cache path isolation and a throwaway Ed25519 trust
    anchor, patched into instance_identity._IBC_TRUST_KEY_RING under
    "sess-v1" — the same kid the real client resolves "ibc-v1" tokens
    against (see _verify_ibc_signature's ibc- -> sess- kid mapping)."""

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
            self.ibc_signing_key = Ed25519PrivateKey.generate()
            pub_der = self.ibc_signing_key.public_key().public_bytes(
                _ser.Encoding.DER, _ser.PublicFormat.SubjectPublicKeyInfo
            )
            self.ibc_trust_pubkey_b64 = base64.b64encode(pub_der).decode()
            self._trust_ring_patch = mock.patch.object(
                instance_identity, "_IBC_TRUST_KEY_RING", {"sess-v1": self.ibc_trust_pubkey_b64}
            )
            self._trust_ring_patch.start()

    def tearDown(self) -> None:  # type: ignore[override]
        for k, v in self._prev_overrides.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        if _M3_DEPS_OK:
            self._trust_ring_patch.stop()
        super().tearDown()

    def _write_local_ibc(self, **extra_claims) -> dict:
        """Write a locally-valid, Ed25519-signed IBC to self.cert_path and
        return its claims."""
        instance_id = instance_identity.get_instance_id()
        claims = {
            "iss": "corvinlabs.io",
            "type": "instance_binding",
            "sub": instance_id,
            "email": "test@example.com",
            "customer_fp": "test-customer-fp",
            "plan": "member",
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "jti": "test-jti-0001",
        }
        claims.update(extra_claims)
        token = _sign_test_ibc(self.ibc_signing_key, claims)
        self.cert_path.write_text(token, encoding="utf-8")
        os.chmod(self.cert_path, 0o600)
        return claims


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
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


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
class TestVerifyIbcSignature(_TempIBCHomeMixin, unittest.TestCase):
    """Direct coverage of the Ed25519 CORVIN-token verifier."""

    def _claims(self, **extra) -> dict:
        base = {
            "sub": instance_identity.get_instance_id(),
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "iat": int(time.time()), "exp": int(time.time()) + 3600, "jti": "x",
        }
        base.update(extra)
        return base

    def test_valid_token_verifies(self) -> None:
        token = _sign_test_ibc(self.ibc_signing_key, self._claims())
        claims = instance_identity._verify_ibc_signature(token)
        self.assertEqual(claims["sub"], instance_identity.get_instance_id())

    def test_wrong_key_rejected(self) -> None:
        other_key = Ed25519PrivateKey.generate()
        token = _sign_test_ibc(other_key, self._claims())
        with self.assertRaises(instance_identity.IBCError):
            instance_identity._verify_ibc_signature(token)

    def test_expired_rejected(self) -> None:
        token = _sign_test_ibc(self.ibc_signing_key, self._claims(exp=int(time.time()) - 10))
        with self.assertRaises(instance_identity.IBCError):
            instance_identity._verify_ibc_signature(token)

    def test_malformed_prefix_rejected(self) -> None:
        with self.assertRaises(instance_identity.IBCError):
            instance_identity._verify_ibc_signature("not-a-corvin-token")

    def test_malformed_segments_rejected(self) -> None:
        with self.assertRaises(instance_identity.IBCError):
            instance_identity._verify_ibc_signature("CORVIN-onlyoneseg")

    def test_unknown_kid_rejected(self) -> None:
        token = _sign_test_ibc(self.ibc_signing_key, self._claims(), kid="ibc-v99")
        with self.assertRaises(instance_identity.IBCError):
            instance_identity._verify_ibc_signature(token)

    def test_lic_and_sess_kid_share_same_trust_entry(self) -> None:
        # The trust ring is keyed by "sess-v1"; both "sess-" and bare kids
        # resolve directly, "ibc-" strips its prefix onto "sess-".
        token_sess = _sign_test_ibc(self.ibc_signing_key, self._claims(), kid="sess-v1")
        instance_identity._verify_ibc_signature(token_sess)  # must not raise


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
class TestAuthenticatedFeaturesRequest(_TempIBCHomeMixin, unittest.TestCase):
    """The license+HMAC auth glue shared by bind_instance()/bind_hardware()."""

    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()
        here = Path(__file__).resolve()
        lic_dir = None
        for parent in here.parents:
            candidate = parent / "operator" / "license"
            if candidate.is_dir():
                lic_dir = candidate
                break
        assert lic_dir is not None, "operator/license not found relative to test file"
        if str(lic_dir) not in sys.path:
            sys.path.insert(0, str(lic_dir))
        import session_refresh as _sr  # type: ignore[import-not-found]
        self._sr = _sr

    def test_raises_when_not_activated(self) -> None:
        with mock.patch.object(self._sr, "load_features", return_value=None):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity._authenticated_features_request("/v1/instance/bind", {"a": 1})

    def test_raises_when_api_key_missing(self) -> None:
        with mock.patch.object(self._sr, "load_features", return_value={"token_fp": "x", "api_key": ""}):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity._authenticated_features_request("/v1/instance/bind", {"a": 1})

    def test_raises_when_no_license_token(self) -> None:
        with mock.patch.object(self._sr, "load_features", return_value={"token_fp": "x", "api_key": "k"}):
            with mock.patch.object(self._sr, "_find_license_token", return_value=None):
                with self.assertRaises(instance_identity.IBCError):
                    instance_identity._authenticated_features_request("/v1/instance/bind", {"a": 1})

    def test_success_returns_parsed_json_and_sends_auth_header(self) -> None:
        fake_resp = mock.Mock()
        fake_resp.read.return_value = json.dumps({"ok": True}).encode()
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)

        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return fake_resp

        with mock.patch.object(self._sr, "load_features", return_value={"token_fp": "x", "api_key": "k"}):
            with mock.patch.object(self._sr, "_find_license_token", return_value="CORVIN-fake.lic.tok"):
                with mock.patch.object(self._sr, "_sign_request", return_value=("123", "deadbeef")):
                    with mock.patch.object(self._sr, "_get_device_fp", return_value="devfp"):
                        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                            result = instance_identity._authenticated_features_request(
                                "/v1/instance/bind", {"a": 1}
                            )
        self.assertEqual(result, {"ok": True})
        req = captured["req"]
        self.assertEqual(req.get_header("Authorization"), "Bearer CORVIN-fake.lic.tok")
        self.assertEqual(json.loads(req.data), {"a": 1})
        self.assertTrue(req.full_url.endswith("/v1/instance/bind"))

    def test_http_error_raises_ibcerror(self) -> None:
        import urllib.error
        with mock.patch.object(self._sr, "load_features", return_value={"token_fp": "x", "api_key": "k"}):
            with mock.patch.object(self._sr, "_find_license_token", return_value="CORVIN-fake.lic.tok"):
                with mock.patch.object(self._sr, "_sign_request", return_value=("123", "deadbeef")):
                    with mock.patch.object(self._sr, "_get_device_fp", return_value=""):
                        with mock.patch(
                            "urllib.request.urlopen",
                            side_effect=urllib.error.HTTPError("url", 403, "Forbidden", {}, None),
                        ):
                            with self.assertRaises(instance_identity.IBCError):
                                instance_identity._authenticated_features_request(
                                    "/v1/instance/bind", {"a": 1}
                                )

    def test_malformed_json_response_raises_ibcerror(self) -> None:
        fake_resp = mock.Mock()
        fake_resp.read.return_value = b"not json {{{"
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(self._sr, "load_features", return_value={"token_fp": "x", "api_key": "k"}):
            with mock.patch.object(self._sr, "_find_license_token", return_value="CORVIN-fake.lic.tok"):
                with mock.patch.object(self._sr, "_sign_request", return_value=("123", "deadbeef")):
                    with mock.patch.object(self._sr, "_get_device_fp", return_value=""):
                        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
                            with self.assertRaises(instance_identity.IBCError):
                                instance_identity._authenticated_features_request(
                                    "/v1/instance/bind", {"a": 1}
                                )


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
class TestBindInstance(_TempIBCHomeMixin, unittest.TestCase):
    """bind_instance() (M1) — rewritten for the real Ed25519/CORVIN-token
    server contract; no more sest_token/license_fp (never a real concept)."""

    def _claims(self, **extra) -> dict:
        base = {
            "sub": instance_identity.get_instance_id(),
            "type": "instance_binding",
            "email": "a@b.com", "customer_fp": "fp1", "plan": "member",
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "iat": int(time.time()), "exp": int(time.time()) + 3600, "jti": "bind-jti",
        }
        base.update(extra)
        return base

    def test_success_stores_cert(self) -> None:
        instance_id = instance_identity.get_instance_id()
        token = _sign_test_ibc(self.ibc_signing_key, self._claims())
        with mock.patch.object(
            instance_identity, "_authenticated_features_request", return_value={"ibc": token}
        ) as mock_req:
            decoded = instance_identity.bind_instance()
        self.assertEqual(decoded["sub"], instance_id)
        self.assertTrue(self.cert_path.exists())
        self.assertEqual(self.cert_path.read_text(encoding="utf-8").strip(), token)
        mock_req.assert_called_once()
        self.assertEqual(mock_req.call_args[0][0], "/v1/instance/bind")

    def test_missing_ibc_field_raises_ibcerror(self) -> None:
        with mock.patch.object(
            instance_identity, "_authenticated_features_request", return_value={"not_ibc": "x"}
        ):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance()

    def test_auth_failure_propagates(self) -> None:
        with mock.patch.object(
            instance_identity, "_authenticated_features_request",
            side_effect=instance_identity.IBCError("not activated"),
        ):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance()

    def test_sub_mismatch_raises_ibcerror(self) -> None:
        token = _sign_test_ibc(self.ibc_signing_key, self._claims(sub="someone-elses-instance-id"))
        with mock.patch.object(
            instance_identity, "_authenticated_features_request", return_value={"ibc": token}
        ):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance()

    def test_pubkey_mismatch_raises_ibcerror(self) -> None:
        """R1 finding: an IBC signed for a DIFFERENT pubkey must be rejected."""
        token = _sign_test_ibc(self.ibc_signing_key, self._claims(instance_pubkey="not-our-pubkey-b64"))
        with mock.patch.object(
            instance_identity, "_authenticated_features_request", return_value={"ibc": token}
        ):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance()

    def test_wrong_signing_key_rejected(self) -> None:
        other_key = Ed25519PrivateKey.generate()
        token = _sign_test_ibc(other_key, self._claims())
        with mock.patch.object(
            instance_identity, "_authenticated_features_request", return_value={"ibc": token}
        ):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_instance()


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
class TestBindHardware(_TempIBCHomeMixin, unittest.TestCase):
    def test_bind_hardware_without_existing_ibc_raises(self) -> None:
        with self.assertRaises(instance_identity.IBCError):
            instance_identity.bind_hardware()

    def test_bind_hardware_success_stores_reissued_cert(self) -> None:
        self._write_local_ibc()
        instance_id = instance_identity.get_instance_id()

        reissued_claims = {
            "iss": "corvinlabs.io", "type": "instance_binding", "sub": instance_id,
            "email": "test@example.com", "customer_fp": "test-customer-fp", "plan": "member",
            "instance_pubkey": instance_identity.get_instance_pubkey_b64(),
            "hardware_fp": "deadbeef" * 8,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "jti": "test-jti-0002",
        }
        reissued_token = _sign_test_ibc(self.ibc_signing_key, reissued_claims)

        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="deadbeef" * 8):
            with mock.patch.object(
                instance_identity, "_authenticated_features_request",
                return_value={"ibc": reissued_token},
            ) as mock_req:
                decoded = instance_identity.bind_hardware()

        self.assertEqual(decoded["hardware_fp"], "deadbeef" * 8)
        on_disk = self.cert_path.read_text(encoding="utf-8").strip()
        self.assertEqual(on_disk, reissued_token)
        self.assertEqual(mock_req.call_args[0][0], "/v1/instance/bind-hardware")
        sent_body = mock_req.call_args[0][1]
        self.assertEqual(sent_body["instance_id"], instance_id)
        self.assertEqual(sent_body["hardware_fp"], "deadbeef" * 8)
        # Signature must verify against OUR local instance pubkey.
        self.assertTrue(
            instance_identity.verify_instance_sig(
                sent_body["hardware_fp_sig"],
                sent_body["hardware_fp"].encode("utf-8"),
                instance_identity.get_instance_pubkey_b64(),
            )
        )

    def test_bind_hardware_rejects_missing_ibc_field(self) -> None:
        self._write_local_ibc()
        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="fp"):
            with mock.patch.object(
                instance_identity, "_authenticated_features_request", return_value={}
            ):
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
        reissued_token = _sign_test_ibc(self.ibc_signing_key, reissued_claims)

        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value="expected-fp"):
            with mock.patch.object(
                instance_identity, "_authenticated_features_request",
                return_value={"ibc": reissued_token},
            ):
                with self.assertRaises(instance_identity.IBCError):
                    instance_identity.bind_hardware()

    def test_bind_hardware_no_fingerprint_available(self) -> None:
        self._write_local_ibc()
        with mock.patch.object(instance_identity, "compute_hardware_fp", return_value=""):
            with self.assertRaises(instance_identity.IBCError):
                instance_identity.bind_hardware()


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
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


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
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


@unittest.skipUnless(_M3_DEPS_OK, "cryptography not installed")
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
