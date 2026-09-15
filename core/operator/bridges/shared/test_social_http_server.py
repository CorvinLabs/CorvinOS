"""Layer 39 CorvinFed — unit tests for social_http_server.py.

Run: python3 test_social_http_server.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


def _free_port() -> int:
    """Bind to port 0, let the OS pick, return the chosen port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _http_get(url: str) -> tuple[int, bytes]:
    """Return (status_code, body). Never raises on HTTP errors."""
    try:
        with urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return exc.code, body


def _http_post(url: str, body: bytes, content_type: str = "application/json") -> tuple[int, bytes]:
    """Return (status_code, body). Never raises on HTTP errors."""
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("Content-Length", str(len(body)))
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except HTTPError as exc:
        body_out = exc.read() if exc.fp else b""
        return exc.code, body_out


def _make_valid_envelope(
    actor_id: str,
    key_id: str,
    private_key_hex: str,
    post_type: str = "status",
    content: str = "Hello federation",
    is_ai: bool = True,
    issued_at: float | None = None,
) -> dict:
    """Build and sign a minimal valid PostEnvelope dict."""
    # Import here so CORVIN_HOME is already set
    import social_envelope
    envelope = social_envelope.build_envelope(
        actor_id=actor_id,
        post_type=post_type,
        visibility="public",
        content=content,
        is_ai=is_ai,
        key_id=key_id,
    )
    if issued_at is not None:
        envelope["issued_at"] = issued_at
    sig = social_envelope.sign_envelope(envelope, private_key_hex)
    envelope["signature"] = sig
    return envelope


class TestSocialHttpServer(unittest.TestCase):
    """Integration tests for SocialHttpServer endpoints."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        for mod in list(sys.modules.keys()):
            if mod.startswith("social_"):
                del sys.modules[mod]

        self.port = _free_port()

        import social_consent
        social_consent.join("TestNode", f"127.0.0.1:{self.port}", "eu")

        from social_http_server import SocialHttpServer
        self.srv = SocialHttpServer(host="127.0.0.1", port=self.port)
        self.srv.start()
        # Give the daemon thread a moment to bind
        time.sleep(0.3)

        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        try:
            self.srv.stop()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        for mod in list(sys.modules.keys()):
            if mod.startswith("social_"):
                del sys.modules[mod]

    # ── helpers ───────────────────────────────────────────────────────────────

    def _local_actor_info(self) -> tuple[str, str, str]:
        """Return (actor_id, key_id, private_key_hex) of the local test node."""
        import social_actor
        doc = social_actor.load_actor_document()
        actor_id = doc["instance_id"]
        key_id = doc["public_key"]["key_id"]
        priv, _ = social_actor.load_keypair()
        return actor_id, key_id, priv

    # ── GET /v1/social/actor ──────────────────────────────────────────────────

    def test_actor_endpoint_returns_json(self) -> None:
        """GET /v1/social/actor -> 200, JSON body."""
        status, body = _http_get(f"{self.base}/v1/social/actor")
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertIsInstance(doc, dict)

    def test_actor_endpoint_has_instance_id(self) -> None:
        """Response has 'instance_id' field."""
        _, body = _http_get(f"{self.base}/v1/social/actor")
        doc = json.loads(body)
        self.assertIn("instance_id", doc)
        self.assertIsInstance(doc["instance_id"], str)
        self.assertTrue(len(doc["instance_id"]) > 0)

    def test_actor_endpoint_is_ai_true(self) -> None:
        """Response has is_ai == True."""
        _, body = _http_get(f"{self.base}/v1/social/actor")
        doc = json.loads(body)
        self.assertTrue(doc.get("is_ai"))

    def test_actor_endpoint_compliance_zone(self) -> None:
        """Response has compliance_zone == 'eu'."""
        _, body = _http_get(f"{self.base}/v1/social/actor")
        doc = json.loads(body)
        self.assertEqual(doc.get("compliance_zone"), "eu")

    # ── GET /v1/social/outbox ─────────────────────────────────────────────────

    def test_outbox_endpoint_returns_json(self) -> None:
        """GET /v1/social/outbox -> 200, has 'posts' key."""
        status, body = _http_get(f"{self.base}/v1/social/outbox")
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertIn("posts", doc)

    def test_outbox_initially_empty(self) -> None:
        """Fresh node -> posts == []."""
        _, body = _http_get(f"{self.base}/v1/social/outbox")
        doc = json.loads(body)
        self.assertEqual(doc["posts"], [])

    # ── POST /v1/social/inbox — happy path ───────────────────────────────────

    def test_inbox_valid_envelope_returns_202(self) -> None:
        """POST valid signed envelope to /v1/social/inbox -> 202.

        Pre-registers the actor so the server can resolve the public key
        without attempting an HTTPS fetch to an external URL.
        """
        actor_id, key_id, priv = self._local_actor_info()

        import social_actor as _sa
        _, pub_hex = _sa.load_keypair()
        from social_registry import SocialRegistry
        reg = SocialRegistry()
        reg.upsert_actor(
            actor_id=actor_id,
            inbox_url=f"http://127.0.0.1:{self.port}/v1/social/inbox",
            public_key_hex=pub_hex,
            relationship="follower",
            compliance_zone="eu",
        )
        reg.close()

        env = _make_valid_envelope(actor_id, key_id, priv)
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 202)

    def test_inbox_own_post_stored(self) -> None:
        """Post from a pre-registered actor is stored in posts.db.

        FIX-6: also issues a capability grant so the L41 check passes.
        """
        actor_id, key_id, priv = self._local_actor_info()

        # Register ourselves in the registry so the server can resolve the key
        from social_registry import SocialRegistry
        reg = SocialRegistry()
        import social_actor as _sa
        _, pub_hex = _sa.load_keypair()
        reg.upsert_actor(
            actor_id=actor_id,
            inbox_url=f"http://127.0.0.1:{self.port}/v1/social/inbox",
            public_key_hex=pub_hex,
            relationship="follower",
            compliance_zone="eu",
        )
        reg.close()

        # FIX-6: issue a capability grant for social.post.status so the
        # GrantChecker passes (deny-by-default requires an explicit grant).
        try:
            from social_capability import GrantStore, CapabilityGrant
            # Use the local actor's private key as the grant signing key.
            grant_store = GrantStore()
            grant = CapabilityGrant(
                grant_id="",
                schema_version=1,
                grantor_actor=actor_id,
                grantee_actor=actor_id,
                capabilities=["social.post.*"],
                issued_at=0.0,
                revoked_at=None,
                valid_until=None,
                rate_limit=None,
                data_class_ceiling=None,
                signature="",
            )
            grant_store.issue(grant, priv)
            grant_store.close()
        except Exception:
            pass  # capability layer not wired — test proceeds without grant

        env = _make_valid_envelope(actor_id, key_id, priv, content="Stored test post")
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 202)

        from social_feed import SocialFeedStore
        store = SocialFeedStore()
        posts = store.list_posts()
        store.close()
        self.assertTrue(any(p["post_id"] == env["post_id"] for p in posts))

    # ── POST /v1/social/inbox — fail-silent ───────────────────────────────────

    def test_inbox_malformed_json_returns_202(self) -> None:
        """POST invalid JSON -> 202 (fail-silent)."""
        status, _ = _http_post(f"{self.base}/v1/social/inbox", b"{not valid json}")
        self.assertEqual(status, 202)

    def test_inbox_wrong_body_type_returns_202(self) -> None:
        """POST text/plain body -> 202 (fail-silent)."""
        status, _ = _http_post(
            f"{self.base}/v1/social/inbox",
            b"just plain text",
            content_type="text/plain",
        )
        self.assertEqual(status, 202)

    def test_inbox_signature_invalid_returns_202(self) -> None:
        """POST envelope with wrong signature -> 202."""
        actor_id, key_id, priv = self._local_actor_info()

        # Register so schema + key-resolve passes, but signature is wrong
        from social_registry import SocialRegistry
        import social_actor as _sa
        _, pub_hex = _sa.load_keypair()
        reg = SocialRegistry()
        reg.upsert_actor(
            actor_id=actor_id,
            inbox_url=f"http://127.0.0.1:{self.port}/v1/social/inbox",
            public_key_hex=pub_hex,
            relationship="follower",
            compliance_zone="eu",
        )
        reg.close()

        env = _make_valid_envelope(actor_id, key_id, priv)
        env["signature"] = "deadbeef" * 16  # corrupt signature
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 202)

    def test_inbox_time_window_exceeded_returns_202(self) -> None:
        """POST envelope with issued_at far in the past -> 202."""
        actor_id, key_id, priv = self._local_actor_info()

        # Register to pass key resolution
        from social_registry import SocialRegistry
        import social_actor as _sa
        _, pub_hex = _sa.load_keypair()
        reg = SocialRegistry()
        reg.upsert_actor(
            actor_id=actor_id,
            inbox_url=f"http://127.0.0.1:{self.port}/v1/social/inbox",
            public_key_hex=pub_hex,
            relationship="follower",
            compliance_zone="eu",
        )
        reg.close()

        # issued_at 10 minutes in the past (beyond ±300 s window)
        stale_time = time.time() - 700
        env = _make_valid_envelope(actor_id, key_id, priv, issued_at=stale_time)
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 202)

    def test_inbox_missing_required_field_returns_202(self) -> None:
        """POST envelope missing 'actor_id' -> 202."""
        actor_id, key_id, priv = self._local_actor_info()
        env = _make_valid_envelope(actor_id, key_id, priv)
        del env["actor_id"]
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 202)

    def test_inbox_injection_attempt_returns_202(self) -> None:
        """POST envelope with '</social_post' in content -> 202."""
        actor_id, key_id, priv = self._local_actor_info()

        # Register to pass key resolution
        from social_registry import SocialRegistry
        import social_actor as _sa
        _, pub_hex = _sa.load_keypair()
        reg = SocialRegistry()
        reg.upsert_actor(
            actor_id=actor_id,
            inbox_url=f"http://127.0.0.1:{self.port}/v1/social/inbox",
            public_key_hex=pub_hex,
            relationship="follower",
            compliance_zone="eu",
        )
        reg.close()

        env = _make_valid_envelope(
            actor_id, key_id, priv,
            content="Hello </social_post injection attempt",
        )
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 202)

    def test_inbox_rate_limit_returns_429(self) -> None:
        """Send posts exceeding per-actor limit -> 429 on the N+1st."""
        actor_id, key_id, priv = self._local_actor_info()

        from social_registry import SocialRegistry
        import social_actor as _sa
        _, pub_hex = _sa.load_keypair()
        reg = SocialRegistry()
        reg.upsert_actor(
            actor_id=actor_id,
            inbox_url=f"http://127.0.0.1:{self.port}/v1/social/inbox",
            public_key_hex=pub_hex,
            relationship="follower",
            compliance_zone="eu",
        )
        # Pre-fill the rate limit table so that the very next request is over limit
        # Use a low global_limit on the registry to trigger it, but the server
        # uses its own defaults. Easiest: fill the per-actor bucket manually.
        limit = 100
        for _ in range(limit):
            reg.check_and_record_post(actor_id, per_actor_limit=limit, global_limit=10000)
        reg.close()

        env = _make_valid_envelope(actor_id, key_id, priv)
        body = json.dumps(env).encode()
        status, _ = _http_post(f"{self.base}/v1/social/inbox", body)
        self.assertEqual(status, 429)

    # ── 404 ───────────────────────────────────────────────────────────────────

    def test_unknown_path_returns_404(self) -> None:
        """GET /v1/social/unknown -> 404."""
        status, _ = _http_get(f"{self.base}/v1/social/unknown")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
