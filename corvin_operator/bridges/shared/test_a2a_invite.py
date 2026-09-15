"""Test suite for ADR-0063 — A2A Invite-Token Protocol.

Covers: token generation, parse/round-trip, expiry, single-use,
revocation, registry, origin/endpoint dict helpers, sig verification,
and CI lint (no anthropic import).
"""
import ast
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import a2a_invite as inv
import a2a_invite_registry as reg


def _isolated_invite(**kwargs):
    """Generate an invite with an isolated master-key tempdir."""
    with tempfile.TemporaryDirectory() as d:
        key_path = Path(d) / "invite_master_key"
        with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
            return inv.generate_invite(
                iid="test-iid-1234",
                origin_id=kwargs.get("origin_id", "peer-b"),
                url=kwargs.get("url", "https://server-a.example.com"),
                allowed_personas=kwargs.get("allowed_personas", ["assistant"]),
                ttl_seconds=kwargs.get("ttl_seconds", 86400),
                single_use=kwargs.get("single_use", False),
                label=kwargs.get("label"),
                spawn_worker=kwargs.get("spawn_worker", False),
            ), key_path, d


class TokenGenerationTests(unittest.TestCase):
    def test_token_starts_with_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                token, token_str = inv.generate_invite(
                    iid="iid1", origin_id="peer", url="https://a.b",
                    ttl_seconds=3600,
                )
        self.assertTrue(token_str.startswith(inv.TOKEN_PREFIX))

    def test_token_has_dot_separator(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                _, token_str = inv.generate_invite(
                    iid="iid1", origin_id="peer", url="https://a.b",
                )
        rest = token_str[len(inv.TOKEN_PREFIX):]
        self.assertIn(".", rest)

    def test_fresh_keys_per_invite(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            env = {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}
            with mock.patch.dict(os.environ, env):
                t1, _ = inv.generate_invite(iid="i", origin_id="p", url="https://a.b")
                t2, _ = inv.generate_invite(iid="i", origin_id="p", url="https://a.b")
        self.assertNotEqual(t1.hk, t2.hk)
        self.assertNotEqual(t1.rk, t2.rk)

    def test_invalid_origin_id_raises(self):
        with self.assertRaises(inv.InviteError):
            inv.generate_invite(iid="i", origin_id="bad/path", url="https://a.b")

    def test_default_ttl_is_7_days(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                now_before = time.time()
                token, _ = inv.generate_invite(iid="i", origin_id="p", url="https://a.b")
        self.assertIsNotNone(token.exp)
        self.assertAlmostEqual(token.exp, now_before + 7 * 86400, delta=5)

    def test_no_expiry_when_ttl_none(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                token, _ = inv.generate_invite(iid="i", origin_id="p", url="https://a.b", ttl_seconds=None)
        self.assertIsNone(token.exp)

    def test_ikey_is_16_hex_chars(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                token, _ = inv.generate_invite(iid="i", origin_id="p", url="https://a.b")
        self.assertEqual(len(token.ikey), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in token.ikey))


class ParseRoundTripTests(unittest.TestCase):
    def _gen_and_parse(self, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                token, token_str = inv.generate_invite(
                    iid="iid-abc", origin_id="peer-x", url="https://server.example",
                    allowed_personas=["assistant", "coder"],
                    ttl_seconds=3600,
                    single_use=True,
                    label="For Klaus",
                    **kwargs,
                )
                parsed, payload_bytes, sig_bytes = inv.parse_invite(token_str)
                sig_ok = inv.verify_invite_sig(payload_bytes, sig_bytes)
        return token, parsed, sig_ok

    def test_parse_round_trip_fields(self):
        token, parsed, _ = self._gen_and_parse()
        self.assertEqual(parsed.iid, "iid-abc")
        self.assertEqual(parsed.oid, "peer-x")
        self.assertEqual(parsed.url, "https://server.example")
        self.assertEqual(sorted(parsed.pa), sorted(["assistant", "coder"]))
        self.assertTrue(parsed.su)
        self.assertEqual(parsed.lbl, "For Klaus")

    def test_sig_verification_passes_for_issuer(self):
        _, _, sig_ok = self._gen_and_parse()
        self.assertTrue(sig_ok)

    def test_sig_verification_fails_with_wrong_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            env = {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}
            with mock.patch.dict(os.environ, env):
                _, token_str = inv.generate_invite(iid="i", origin_id="p", url="https://a.b")
            # Tamper key
            key_path.write_text(inv.secrets.token_hex(32))
            _, payload_bytes, sig_bytes = inv.parse_invite(token_str)
            with mock.patch.dict(os.environ, env):
                sig_ok = inv.verify_invite_sig(payload_bytes, sig_bytes)
        self.assertFalse(sig_ok)

    def test_bit_flip_in_payload_fails_parse_or_sig(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            env = {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}
            with mock.patch.dict(os.environ, env):
                _, token_str = inv.generate_invite(iid="i", origin_id="p", url="https://a.b")
            rest = token_str[len(inv.TOKEN_PREFIX):]
            dot = rest.rfind(".")
            payload_b64 = list(rest[:dot])
            payload_b64[5] = "A" if payload_b64[5] != "A" else "B"
            tampered = inv.TOKEN_PREFIX + "".join(payload_b64) + rest[dot:]
            try:
                _, payload_bytes, sig_bytes = inv.parse_invite(tampered)
                with mock.patch.dict(os.environ, env):
                    sig_ok = inv.verify_invite_sig(payload_bytes, sig_bytes)
                self.assertFalse(sig_ok)
            except inv.InviteError:
                pass  # parse failed — also acceptable

    def test_missing_prefix_raises(self):
        with self.assertRaises(inv.InviteError):
            inv.parse_invite("not-a-token")

    def test_missing_dot_separator_raises(self):
        with self.assertRaises(inv.InviteError):
            inv.parse_invite(inv.TOKEN_PREFIX + "nodot")

    def test_invalid_base64_raises(self):
        with self.assertRaises(inv.InviteError):
            inv.parse_invite(inv.TOKEN_PREFIX + "!!!.!!!")


class ExpiryAndValidationTests(unittest.TestCase):
    def _token_with_exp(self, exp: float | None, su: bool = False):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                ttl = (exp - time.time()) if exp else None
                token, _ = inv.generate_invite(
                    iid="i", origin_id="p", url="https://a.b",
                    ttl_seconds=ttl, single_use=su,
                )
        return token

    def test_valid_non_expired_token(self):
        token = self._token_with_exp(time.time() + 9999)
        result = inv.validate_invite(token)
        self.assertTrue(result.ok)

    def test_expired_token_rejected(self):
        token = self._token_with_exp(time.time() + 1)
        result = inv.validate_invite(token, now=time.time() + 999)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "expired")

    def test_no_expiry_token_always_valid(self):
        token = self._token_with_exp(None)
        result = inv.validate_invite(token, now=time.time() + 1_000_000)
        self.assertTrue(result.ok)

    def test_revoked_token_rejected(self):
        token = self._token_with_exp(time.time() + 9999)
        with tempfile.TemporaryDirectory() as d:
            r = reg.InviteRegistry(Path(d) / "invites.json")
            r.create(reg.InviteEntry(
                ikey=token.ikey, oid="p", lbl="", iat=token.iat, exp=token.exp, su=False,
                revoked=True,
            ))
            result = inv.validate_invite(token, registry=r)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "revoked")

    def test_single_use_already_accepted_rejected(self):
        token = self._token_with_exp(time.time() + 9999, su=True)
        with tempfile.TemporaryDirectory() as d:
            r = reg.InviteRegistry(Path(d) / "invites.json")
            r.create(reg.InviteEntry(
                ikey=token.ikey, oid="p", lbl="", iat=token.iat, exp=token.exp, su=True,
                accepted=True,
            ))
            result = inv.validate_invite(token, registry=r)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "already_accepted")


class RegistryTests(unittest.TestCase):
    def _registry(self, d):
        return reg.InviteRegistry(Path(d) / "invites.json")

    def test_create_and_get(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            e = reg.InviteEntry(ikey="abcd1234abcd1234", oid="p", lbl="lbl",
                                iat=1.0, exp=99999.0, su=False)
            r.create(e)
            got = r.get("abcd1234abcd1234")
        self.assertIsNotNone(got)
        self.assertEqual(got["oid"], "p")

    def test_get_unknown_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            self.assertIsNone(r.get("unknown"))

    def test_mark_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            r.create(reg.InviteEntry(ikey="abc", oid="p", lbl="", iat=1.0, exp=None, su=True))
            ok = r.mark_accepted("abc")
            got = r.get("abc")
        self.assertTrue(ok)
        self.assertTrue(got["accepted"])

    def test_revoke(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            r.create(reg.InviteEntry(ikey="abc", oid="p", lbl="", iat=1.0, exp=None, su=False))
            ok = r.revoke("abc")
            got = r.get("abc")
        self.assertTrue(ok)
        self.assertTrue(got["revoked"])

    def test_revoke_unknown_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            self.assertFalse(r.revoke("nope"))

    def test_list_all_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            r.create(reg.InviteEntry(ikey="aaa", oid="p", lbl="", iat=1.0, exp=None, su=False))
            r.create(reg.InviteEntry(ikey="bbb", oid="p", lbl="", iat=2.0, exp=None, su=False))
            entries = r.list_all()
        self.assertEqual(entries[0].ikey, "bbb")

    def test_cleanup_removes_expired_entries(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            old_exp = time.time() - 200_000  # expired long ago
            r.create(reg.InviteEntry(ikey="old", oid="p", lbl="", iat=1.0, exp=old_exp, su=False))
            r.create(reg.InviteEntry(ikey="new", oid="p", lbl="", iat=2.0, exp=time.time() + 9999, su=False))
            removed = r.cleanup(max_age_s=86400)
            remaining = r.list_all()
        self.assertEqual(removed, 1)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].ikey, "new")

    def test_find_by_label(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            r.create(reg.InviteEntry(ikey="abc", oid="p", lbl="For Klaus", iat=1.0, exp=None, su=False))
            found = r.find_by_label("for Klaus")
        self.assertIsNotNone(found)
        self.assertEqual(found.ikey, "abc")

    def test_file_mode_is_0600(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._registry(d)
            r.create(reg.InviteEntry(ikey="abc", oid="p", lbl="", iat=1.0, exp=None, su=False))
            mode = Path(d, "invites.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class DictHelperTests(unittest.TestCase):
    def _make_token(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = Path(d) / "mk"
            with mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(key_path)}):
                token, _ = inv.generate_invite(
                    iid="iid-abc", origin_id="peer-x", url="https://server.example",
                    ttl_seconds=3600, allowed_personas=["assistant"],
                )
        return token

    def test_origin_dict_fields(self):
        token = self._make_token()
        d = inv.invite_to_origin_dict(token)
        self.assertEqual(d["origin_id"], "peer-x")
        self.assertEqual(d["hmac_key"], token.hk)
        self.assertEqual(d["recv_key"], token.rk)
        self.assertIn("allowed_personas", d)
        self.assertFalse(d["spawn_worker"])

    def test_endpoint_dict_url_is_base_url_plus_path(self):
        token = self._make_token()
        d = inv.invite_to_endpoint_dict(token, local_instance_id="local-iid")
        self.assertTrue(d["url"].startswith("https://server.example"))
        self.assertIn("/v1/a2a/receive", d["url"])

    def test_endpoint_dict_instance_id_is_issuer(self):
        token = self._make_token()
        d = inv.invite_to_endpoint_dict(token, local_instance_id="local-iid")
        self.assertEqual(d["instance_id"], "iid-abc")


class CILintTests(unittest.TestCase):
    def _check_no_anthropic(self, path: Path):
        src = path.read_text("utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertFalse(
                        name.startswith("anthropic"),
                        f"{path.name} imports 'anthropic' — CI lint violation",
                    )

    def test_a2a_invite_no_anthropic(self):
        self._check_no_anthropic(_HERE / "a2a_invite.py")

    def test_a2a_invite_registry_no_anthropic(self):
        self._check_no_anthropic(_HERE / "a2a_invite_registry.py")


if __name__ == "__main__":
    unittest.main()
