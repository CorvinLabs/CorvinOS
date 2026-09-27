"""Override routes never expose the raw session id (adversarial review 2026-09-27).

``rec.sid`` is the ``corvin_console_sid`` cookie — a bearer credential. It was
stored as ``requestor_id`` and returned by ``GET /control-plane/overrides`` to
every session of the tenant. Also covered: the consent dependency reads the
SESSION, not a ``?rec=`` query parameter (which used to 403 every request, or
500 when supplied).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

BASE = "/v1/console/control-plane/overrides"


def _grant(home: Path, sid: str) -> None:
    from core.compliance import consent_store

    from corvin_console import auth as _auth

    consent_store._stores.clear()
    # The consent subject is the session FINGERPRINT, never the raw sid.
    consent_store.get_consent_store("_default", corvin_home=home).grant_consent(
        user_id=_auth.load_session(sid).sid_fingerprint,
        scope="control_plane_override_operations")


def _session_sid(client) -> str:
    return client.cookies.get("corvin_console_sid")


def test_raw_sid_never_returned_or_audited():
    with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
        from corvin_console.routes import control_plane_overrides as ov

        ov._authority = None
        sid = _session_sid(client)
        _grant(home, sid)
        r = client.post(BASE, headers={"X-CSRF-Token": csrf},
                        json={"override_type": "force_enable", "target_id": "plugin-x",
                              "reason": "maintenance window"})
        assert r.status_code == 200, r.text
        assert sid not in r.text

        r = client.get(BASE)
        assert r.status_code == 200, r.text
        assert sid not in r.text
        from corvin_console import auth as _auth
        fp = _auth.load_session(sid).sid_fingerprint
        assert [o["requestor_id"] for o in r.json()["overrides"]] == [fp]

        for chain in home.rglob("audit.jsonl"):
            assert sid not in chain.read_text(), chain
        ov._authority = None


def test_consent_gate_reads_the_session_not_a_query_param():
    with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, home, _):
        from core.compliance import consent_store

        consent_store._stores.clear()
        # no consent → 403, and a crafted ?rec= neither bypasses nor crashes it
        assert client.get(BASE).status_code == 403
        assert client.get(BASE + "?rec=x").status_code == 403
        client.cookies.clear()
        assert client.get(BASE).status_code == 401
