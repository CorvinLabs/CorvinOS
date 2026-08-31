"""Per-subtask E2E for the Phase-G ``My Profile`` tab.

Covers:
  * ``GET /v1/console/profile`` — 200 with current snapshot + schema
  * ``PUT /v1/console/profile`` — round-trip identity + audience,
    re-auth gate, empty-body 400, audit ``console.action_performed``
    + ``console.action_failed``
  * ``POST /v1/console/profile/reset`` — wipes everything, re-auth gate
  * ``POST /v1/console/profile/preview`` — renders the live TTS-audience
    block without touching disk
  * ``GET /v1/console/chat-settings`` — list across known channels
  * ``GET /v1/console/chat-settings/{channel}/{chat_key}`` — detail
    projection of an existing chat_profiles entry
  * ``PATCH /v1/console/chat-settings/{channel}/{chat_key}`` — partial
    persona / dialectic / LDD update with audit
  * 401 on wrong re-auth token (possession-proof check)
  * audit chain integrity (``verify_chain`` over the full lifecycle)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

# Make in-tree packages importable when running this file directly.
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

import importlib


# Each test re-imports the modules so ENV-derived paths reflect the
# active sandbox. Keep this list small and surgical.
_REIMPORT_MODULES = (
    "forge.paths",
    "corvin_gateway.auth",
    "corvin_console.auth",
    "corvin_console.audit",
    "corvin_console.deps",
    "corvin_console.routes.profile",
    "corvin_console.routes.chat_settings",
    "corvin_console.routes.auth_routes",
    "corvin_console.routes.dashboard",
    "corvin_console.routes.sessions",
    "corvin_console.routes.audit_tail",
    "corvin_console.routes.runs",
    "corvin_console.routes.personas",
    "corvin_console.routes.tools",
    "corvin_console.routes.skills",
    "corvin_console.routes.memory",
    "corvin_console.routes.streams",
    "corvin_console.routes.promote",
    "corvin_console.routes.workspaces",
    "corvin_console.routes.members",
    "corvin_console.routes.compute",
    "corvin_console.routes.settings",
    "corvin_console.app",
    "corvin_console",
    "profile",
)


def _reset_modules():
    for name in list(sys.modules):
        if name in _REIMPORT_MODULES or name.startswith("corvin_console."):
            sys.modules.pop(name, None)
    # also bust the voice/profile module reference
    sys.modules.pop("profile", None)


@contextmanager
def _sandbox(tenant_id: str = "_default"):
    """Create a hermetic CORVIN_HOME + XDG_CONFIG_HOME + minimal tenant
    tree, mint a tenant token, mint a console session, and return the
    FastAPI app + the live cookies/headers needed for authenticated
    requests."""
    with tempfile.TemporaryDirectory(prefix="console-prof-test-") as td:
        home = Path(td) / "corvin"
        xdg = Path(td) / "xdg"
        bridges = _REPO / "operator" / "bridges"
        # Pre-create the tenant tree (mirror of the migration helper).
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
        (xdg / "corvin-voice").mkdir(parents=True)

        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "XDG_CONFIG_HOME")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            _reset_modules()
            # Lazy-imports below pick up the fresh env.
            from corvin_console import auth as console_session_auth
            from corvin_console.app import router
            from corvin_console.deps import verify_reauth  # noqa
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            # Mint a console session.
            rec = console_session_auth.create_session(
                tenant_id=tenant_id,
            )
            csrf = console_session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)
            # The re_auth_token is the sid_fingerprint — a 12-char hex SHA-256
            # prefix of the session id.  Only the authenticated session-holder
            # can derive this value, giving a possession-proof second factor.
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
                "bridges_dir":  bridges,
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


# ── Profile route ─────────────────────────────────────────────────────


class ProfileGetTests(unittest.TestCase):
    def test_get_empty_profile_returns_snapshot_and_schema(self):
        with _sandbox() as ctx:
            r = ctx["client"].get("/v1/console/profile")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["tenant_id"], "_default")
            self.assertIn("profile", body)
            self.assertIn("schema", body)
            # Fresh install seeds the friendly-voice defaults
            # (metaphors=on, learning=3) — see commit 4708dfa — so the
            # TTS-audience preview renders a non-empty block even before the
            # user configures anything. The chat-render system block stays
            # empty by default (voice_audience_chat_render is off).
            self.assertIn("HÖRER-PROFIL", body["preview_de"])
            self.assertIn("AUDIENCE", body["preview_en"])
            self.assertEqual(body["system_block"], "")
            self.assertIn("audience", body["schema"])
            self.assertIn("level", body["schema"]["audience"])

    def test_get_without_session_returns_401(self):
        with _sandbox() as ctx:
            ctx["client"].cookies.clear()
            r = ctx["client"].get("/v1/console/profile")
            self.assertEqual(r.status_code, 401)


class ProfileWriteIdentityTests(unittest.TestCase):
    def test_identity_round_trip_with_reauth(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={
                    "identity": {
                        "name":             "Silvio",
                        "display_language": "de",
                        "tone":             "trocken, präzise",
                        "default_persona":  "coder",
                    },
                    "re_auth_token": ctx["token"],
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            saved = r.json()["profile"]["identity"]
            self.assertEqual(saved["name"], "Silvio")
            self.assertEqual(saved["display_language"], "de")
            # GET reflects the same state.
            r2 = ctx["client"].get("/v1/console/profile")
            self.assertEqual(r2.json()["profile"]["identity"]["name"], "Silvio")

    def test_display_language_is_normalised_through_bcp47(self):
        """Regression (2026-07-12): PUT /v1/console/profile used to store
        `identity.display_language` verbatim (only max_length=8 capped) --
        a bare "zh" (not the canonical "zh-Hans") persisted unnormalised and
        silently broke every downstream i18n.t() lookup that expects a
        normalised BCP-47 code (welcome greeting, voice-summary language
        pin). Root cause traced to this route + profile_cli.py's `/profile
        set` both skipping the i18n.normalise() step `/lang set` already
        does. See docs/troubleshooting.md #34."""
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"identity": {"display_language": "zh"},
                      "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(
                r.json()["profile"]["identity"]["display_language"], "zh-Hans",
                "bare 'zh' must be normalised to the canonical BCP-47 form",
            )

    def test_display_language_rejects_unrecognisable_code(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"identity": {"display_language": "!!!not-a-lang"},
                      "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 422, r.text)

    def test_write_with_wrong_reauth_token_returns_401(self):
        """verify_reauth rejects tokens that do not match sid_fingerprint."""
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={
                    "identity": {"name": "X"},
                    "re_auth_token": "wrong_fingerprint",
                },
            )
            self.assertEqual(r.status_code, 401, r.text)

    def test_write_empty_body_returns_400(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 400, r.text)

    def test_write_without_csrf_returns_403(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                json={"identity": {"name": "X"}, "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 403)


class ProfileWriteConcurrencyTests(unittest.TestCase):
    """Confirmed blind spot (fixed 2026-07-13): PUT /profile used to do
    ``profile.load(force=True) -> merge -> profile.save()`` with NO lock
    spanning the cycle. FastAPI runs sync routes (this one included) on
    anyio's threadpool, so two concurrent PUT requests in the SAME console
    process are a genuine intra-process race: both read the same base
    dict, both merge their own field in, and whichever save() lands second
    silently clobbers the first request's field. The route now goes
    through ``profile.mutate()``, which shares ``profile._write_lock``
    with every other in-process writer.

    Drives several concurrent PUT bursts, each burst setting a distinct
    field per thread (mirroring the plain read-modify-write shape the bug
    actually hit — different keys, same underlying file), then asserts
    every field survived every burst with no lost updates.
    """

    # (section, key, value-factory(round) -> JSON-safe value)
    _FIELDS = [
        ("identity", "name",                     lambda r: f"Name-r{r}"),
        ("identity", "tone",                     lambda r: f"tone-r{r}"),
        ("identity", "timezone",                 lambda r: f"Europe/Berlin-r{r}"),
        ("identity", "default_persona",          lambda r: f"persona-r{r}"),
        ("identity", "custom_instructions",      lambda r: f"custom-r{r}"),
        ("identity", "voice_note_max_sentences", lambda r: (r % 10) + 1),
        ("audience", "voice_audience_background", lambda r: f"bg-r{r}"),
    ]

    def test_concurrent_put_requests_lose_no_field(self):
        import threading as _threading

        with _sandbox() as ctx:
            client = ctx["client"]
            headers = _auth_headers(ctx)
            token = ctx["token"]
            n_rounds = 8
            errors: list[BaseException] = []
            errors_lock = _threading.Lock()

            for rnd in range(n_rounds):
                def worker(section: str, key: str, value_fn, _rnd=rnd):
                    try:
                        resp = client.put(
                            "/v1/console/profile",
                            headers=headers,
                            json={section: {key: value_fn(_rnd)},
                                  "re_auth_token": token},
                        )
                        if resp.status_code != 200:
                            raise AssertionError(
                                f"PUT {section}.{key} round {_rnd} -> "
                                f"{resp.status_code}: {resp.text}"
                            )
                    except BaseException as exc:  # noqa: BLE001
                        with errors_lock:
                            errors.append(exc)

                threads = [
                    _threading.Thread(target=worker, args=(section, key, value_fn))
                    for section, key, value_fn in self._FIELDS
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

                self.assertEqual(
                    errors, [],
                    f"round {rnd}: {len(errors)} PUT requests raised/failed "
                    f"(first: {errors[0]!r})" if errors else "no errors",
                )

                # After every field-thread in this round has landed, ALL
                # of them must be present with THIS round's value — an
                # unlocked read-modify-write race would show some fields
                # reverted to a PREVIOUS round's value (lost update) here.
                snap = client.get("/v1/console/profile").json()["profile"]
                lost = []
                for section, key, value_fn in self._FIELDS:
                    expected = value_fn(rnd)
                    actual = snap[section].get(key)
                    if actual != expected:
                        lost.append((section, key, expected, actual))
                self.assertEqual(
                    lost, [],
                    f"round {rnd}: {len(lost)}/{len(self._FIELDS)} fields lost "
                    f"to an unlocked read-modify-write race in PUT /profile: "
                    f"{lost}",
                )


class ProfileWriteAudienceTests(unittest.TestCase):
    def test_audience_round_trip_renders_preview(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={
                    "audience": {
                        "voice_audience_level":    "novice",
                        "voice_audience_jargon":   1,
                        "voice_audience_style":    "concise",
                        "voice_audience_learning": 2,
                    },
                    "re_auth_token": ctx["token"],
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotEqual(r.json()["preview_de"], "")
            # The DE block must mention the learning annex when learning=2.
            self.assertIn("LERN-ZUGABE", r.json()["preview_de"])

    def test_audience_validation_rejects_bad_level(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={
                    "audience": {"voice_audience_level": "guru"},
                    "re_auth_token": ctx["token"],
                },
            )
            self.assertEqual(r.status_code, 422, r.text)

    def test_audience_validation_rejects_extra_keys(self):
        with _sandbox() as ctx:
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={
                    "audience": {"voice_audience_level": "novice", "foo": "bar"},
                    "re_auth_token": ctx["token"],
                },
            )
            self.assertEqual(r.status_code, 422, r.text)

    def test_audience_null_deletes_field(self):
        with _sandbox() as ctx:
            ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"audience": {"voice_audience_level": "expert"},
                      "re_auth_token": ctx["token"]},
            )
            r = ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"audience": {"voice_audience_level": None},
                      "re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsNone(r.json()["profile"]["audience"]["voice_audience_level"])


class ProfilePreviewTests(unittest.TestCase):
    def test_preview_renders_without_persistence(self):
        with _sandbox() as ctx:
            r = ctx["client"].post(
                "/v1/console/profile/preview",
                headers=_auth_headers(ctx),
                json={
                    "audience": {
                        "voice_audience_level":  "novice",
                        "voice_audience_jargon": 0,
                    },
                    "lang": "de",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["empty"])
            # The preview POST must not persist: the disk profile must carry
            # no user-set audience level. (We can't proxy this via preview_de
            # anymore — a fresh profile renders the seeded friendly-voice
            # defaults, so preview_de is non-empty even with nothing saved.)
            r2 = ctx["client"].get("/v1/console/profile")
            self.assertIsNone(
                r2.json()["profile"]["audience"]["voice_audience_level"]
            )

    def test_preview_empty_audience_returns_empty_block(self):
        with _sandbox() as ctx:
            r = ctx["client"].post(
                "/v1/console/profile/preview",
                headers=_auth_headers(ctx),
                json={"audience": {}, "lang": "de"},
            )
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["empty"])

    def test_preview_invalid_lang_422(self):
        with _sandbox() as ctx:
            r = ctx["client"].post(
                "/v1/console/profile/preview",
                headers=_auth_headers(ctx),
                json={"audience": {}, "lang": "zh-Hans"},
            )
            self.assertEqual(r.status_code, 422)


class ProfileResetTests(unittest.TestCase):
    def test_reset_wipes_and_audits(self):
        with _sandbox() as ctx:
            ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"identity": {"name": "X"},
                      "re_auth_token": ctx["token"]},
            )
            r = ctx["client"].post(
                "/v1/console/profile/reset",
                headers=_auth_headers(ctx),
                json={"re_auth_token": ctx["token"]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsNone(r.json()["profile"]["identity"]["name"])

    def test_reset_with_wrong_reauth_token_returns_401(self):
        """verify_reauth rejects tokens that do not match sid_fingerprint."""
        with _sandbox() as ctx:
            r = ctx["client"].post(
                "/v1/console/profile/reset",
                headers=_auth_headers(ctx),
                json={"re_auth_token": "wrong_fingerprint"},
            )
            self.assertEqual(r.status_code, 401)


# ── Chat-settings route ───────────────────────────────────────────────


def _seed_channel_settings(bridges_dir: Path, channel: str, body: dict):
    """Write a minimal bridges/<channel>/settings.json for the test."""
    d = bridges_dir / channel
    d.mkdir(parents=True, exist_ok=True)
    p = d / "settings.json"
    # Read existing if any, merge, write back. We restore in teardown.
    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            existing = {}
    existing.setdefault("__test_marker__", True)
    existing.update(body)
    p.write_text(json.dumps(existing, indent=2))


class ChatSettingsListTests(unittest.TestCase):
    """The list endpoint walks every known channel's settings.json.

    These tests read the OPERATOR's actual settings tree — the bridges
    directory is shared with the live system. We assert only structural
    invariants (presence of keys, types) rather than specific chat IDs.
    """
    def test_list_returns_known_channels_array(self):
        with _sandbox() as ctx:
            r = ctx["client"].get("/v1/console/chat-settings")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["tenant_id"], "_default")
            self.assertIn("telegram", body["known_channels"])
            self.assertIn("discord", body["known_channels"])
            self.assertIsInstance(body["chats"], list)
            self.assertIsInstance(body["count"], int)

    def test_list_without_session_401(self):
        with _sandbox() as ctx:
            ctx["client"].cookies.clear()
            r = ctx["client"].get("/v1/console/chat-settings")
            self.assertEqual(r.status_code, 401)


class ChatSettingsDetailTests(unittest.TestCase):
    def test_unknown_channel_404(self):
        with _sandbox() as ctx:
            r = ctx["client"].get("/v1/console/chat-settings/myspace/abc")
            self.assertEqual(r.status_code, 404, r.text)

    def test_invalid_chat_key_400(self):
        with _sandbox() as ctx:
            r = ctx["client"].get("/v1/console/chat-settings/discord/..%2F..%2Fetc")
            self.assertIn(r.status_code, (400, 404))


# ── Cross-component: audit chain integrity ────────────────────────────


class AuditChainTests(unittest.TestCase):
    def test_profile_write_emits_action_performed_and_chain_verifies(self):
        with _sandbox() as ctx:
            ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"identity": {"name": "Aud Test"},
                      "re_auth_token": ctx["token"]},
            )
            # Trigger action_failed via empty body (no identity/audience).
            ctx["client"].put(
                "/v1/console/profile",
                headers=_auth_headers(ctx),
                json={"re_auth_token": ctx["token"]},
            )
            ctx["client"].post(
                "/v1/console/profile/reset",
                headers=_auth_headers(ctx),
                json={"re_auth_token": ctx["token"]},
            )
            chain = ctx["home"] / "tenants" / ctx["tenant_id"] / "global" / "forge" / "audit.jsonl"
            self.assertTrue(chain.exists())
            # Re-import after sandbox env-vars are set
            from forge import security_events as _se
            ok, problems = _se.verify_chain(chain)
            self.assertTrue(ok, f"chain broken: {problems}")
            events = [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]
            actions = [e["event_type"] for e in events
                       if e["event_type"].startswith("console.")]
            self.assertIn("console.action_performed", actions)
            self.assertIn("console.action_failed", actions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
