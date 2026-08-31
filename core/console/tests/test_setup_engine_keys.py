"""Tests for ``PUT /setup/engines/{engine_id}`` — the possession-proof
re-auth gate on the secret-bearing engine-key write endpoint.

Concept: ``update_engine_key`` (routes/setup.py) calls
``verify_reauth(rec, body.re_auth_token)`` (deps.py) exactly like
``routes/profile.py``'s ``PUT /profile`` does, but writes directly into
``service.env`` (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / etc. — see
``_ENGINE_KEYS``) via ``_write_env_key``. That call-site is completely
unexercised by any existing test (grep across core/console/tests for
``setup/engines`` / ``update_engine_key`` / ``_write_env_key`` /
``_ENGINE_KEYS`` returns zero hits before this file), even though the
equivalent gate on ``profile.py`` has a dedicated wrong-token/empty-token/
correct-token matrix in test_profile_routes.py.

Harness mirrors test_profile_routes.py's ``_sandbox`` (hermetic CORVIN_HOME +
XDG_CONFIG_HOME, a *real* minted console session + CSRF token + the
session's actual ``sid_fingerprint`` as the re-auth token) rather than the
dependency_override/mock-session style used by test_setup_welcome_check.py,
since the whole point here is to exercise ``verify_reauth``'s real
constant-time comparison against a real session record, not a stub.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


_REIMPORT_MODULES = (
    "forge.paths",
    "corvin_gateway.auth",
    "corvin_console.auth",
    "corvin_console.audit",
    "corvin_console.deps",
    "corvin_console.routes.setup",
    "corvin_console.app",
    "corvin_console",
    "profile",
)


def _reset_modules():
    for name in list(sys.modules):
        if name in _REIMPORT_MODULES or name.startswith("corvin_console."):
            sys.modules.pop(name, None)
    sys.modules.pop("profile", None)


@contextmanager
def _sandbox(tenant_id: str = "_default"):
    """Hermetic CORVIN_HOME + XDG_CONFIG_HOME, a minted console session,
    and a live FastAPI TestClient — same recipe as test_profile_routes.py."""
    with tempfile.TemporaryDirectory(prefix="console-setup-engines-test-") as td:
        home = Path(td) / "corvin"
        xdg = Path(td) / "xdg"
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
        (xdg / "corvin-voice").mkdir(parents=True)

        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "XDG_CONFIG_HOME")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            _reset_modules()
            from corvin_console import auth as console_session_auth
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            rec = console_session_auth.create_session(tenant_id=tenant_id)
            csrf = console_session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)
            # sid_fingerprint is the only value a possessor of the live
            # session can derive -- the intended "second factor".
            token_plain = rec.sid_fingerprint

            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", rec.sid)

            yield {
                "home":         home,
                "xdg":          xdg,
                "tenant_id":    tenant_id,
                "token":        token_plain,
                "rec":          rec,
                "csrf":         csrf,
                "client":       client,
                "service_env":  xdg / "corvin-voice" / "service.env",
                "audit_chain":  home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl",
            }
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _reset_modules()


def _auth_headers(ctx, *, mutate: bool = True) -> dict:
    h = {}
    if mutate:
        h["X-CSRF-Token"] = ctx["csrf"]
    return h


def _read_service_env(ctx) -> dict[str, str]:
    path = ctx["service_env"]
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _audit_events(ctx) -> list[dict]:
    chain = ctx["audit_chain"]
    if not chain.exists():
        return []
    return [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]


class UpdateEngineKeyReauthTests(unittest.TestCase):
    """The wrong-token / empty-token / correct-token matrix for
    PUT /setup/engines/{engine_id} — the same gate class already tested
    for profile.py's PUT /profile, applied here to a secret-writing route."""

    def test_correct_reauth_token_accepts_and_persists_key(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/setup/engines/anthropic",
                headers=_auth_headers(ctx),
                json={"value": "sk-ant-correct-token-test", "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["ok"], True)
            self.assertEqual(body["engine_id"], "anthropic")
            self.assertEqual(body["key"], "ANTHROPIC_API_KEY")

            env = _read_service_env(ctx)
            self.assertEqual(env.get("ANTHROPIC_API_KEY"), "sk-ant-correct-token-test")

            actions = [e["event_type"] for e in _audit_events(ctx)]
            self.assertIn("console.action_performed", actions)

    def test_wrong_reauth_token_rejected_401_and_key_not_written(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/setup/engines/anthropic",
                headers=_auth_headers(ctx),
                json={"value": "sk-ant-should-not-persist", "re_auth_token": "wrong_fingerprint"},
            )
            self.assertEqual(r.status_code, 401, r.text)

            # The secret must NOT have been written to service.env.
            env = _read_service_env(ctx)
            self.assertNotIn("ANTHROPIC_API_KEY", env)

            events = _audit_events(ctx)
            failed = [e for e in events if e["event_type"] == "console.action_failed"]
            self.assertTrue(failed, "expected a console.action_failed audit event")
            self.assertEqual(failed[-1]["details"]["action"], "engine.key_update")
            self.assertEqual(failed[-1]["details"]["target_id"], "anthropic")
            self.assertEqual(failed[-1]["details"]["reason"], "reauth-failed")
            # No action_performed must have been emitted for this rejected write.
            performed = [e for e in events if e["event_type"] == "console.action_performed"]
            self.assertFalse(performed)

    def test_absent_reauth_token_passes_on_csrf_alone_documented_fail_open(self):
        """Documents the CURRENT contract: verify_reauth() treats a missing
        re_auth_token as automatically satisfied, relying on the CSRF check
        (already enforced by require_csrf upstream) as the sole possession
        proof. If EngineKeyUpdate's default ever changes such that this
        silently starts requiring/ignoring a token differently, this test
        pins today's behavior so the change is visible."""
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/setup/engines/anthropic",
                headers=_auth_headers(ctx),
                json={"value": "sk-ant-no-token-fail-open"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            env = _read_service_env(ctx)
            self.assertEqual(env.get("ANTHROPIC_API_KEY"), "sk-ant-no-token-fail-open")

    def test_write_without_csrf_returns_403(self):
        """The CSRF gate (require_csrf) sits upstream of verify_reauth --
        without it, no possession proof is checked at all."""
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/setup/engines/anthropic",
                json={"value": "sk-ant-no-csrf", "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 403)
            env = _read_service_env(ctx)
            self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_unknown_engine_id_returns_404_before_reauth_check(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/setup/engines/not-a-real-engine",
                headers=_auth_headers(ctx),
                json={"value": "irrelevant", "re_auth_token": "wrong_fingerprint"},
            )
            self.assertEqual(r.status_code, 404, r.text)

    def test_oauth_engine_has_no_key_and_returns_404(self):
        """claude_code is kind=oauth with key=None -- not writable via this
        route regardless of re-auth outcome."""
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/setup/engines/claude_code",
                headers=_auth_headers(ctx),
                json={"value": "irrelevant", "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 404, r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
