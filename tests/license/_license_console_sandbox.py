"""Shared in-process harness for the licence tests in this directory.

Every console test here drives the REAL console router (``corvin_console.app.router``)
through FastAPI's TestClient, mounted under ``/v1/console`` exactly like both
shipped hosts do (``corvin_gateway.app`` and ``corvin_console.standalone``), with
a real session cookie and a scratch ``CORVIN_HOME``. Nothing talks to a live
service.

The session/cookie/CSRF plumbing is the shared ``_sandbox`` from
``core/console/tests/test_admin_route.py`` — reused, not copied.

Tier control
------------
The paid tier is resolved by ``corvin_operator.license.validator.active_tier()``
from a signed licence token. Tests cannot mint a production-signed token, so the
member tier is simulated by replacing the RESOLVER that ``capability_api`` reads
(``capability_api.active_tier``). The gate itself — ``require_capability``, the
FastAPI dependency, the route — runs unmodified. Free tier needs no patch at all:
a scratch ``CORVIN_HOME`` with no licence key IS the free tier.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
_CONSOLE_TESTS = REPO / "core" / "console" / "tests"
_SKILL_FORGE = REPO / "corvin_operator" / "skill-forge"

for _p in (str(_CONSOLE_TESTS), str(_SKILL_FORGE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_admin_route import _sandbox  # noqa: E402

TENANT = "_default"


@dataclasses.dataclass
class Console:
    client: object       # TestClient with a valid owner session cookie
    csrf: str            # matching X-CSRF-Token value
    home: Path           # scratch CORVIN_HOME

    @property
    def h(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def anonymous(self):
        """A client on the same app with NO session cookie."""
        from fastapi.testclient import TestClient

        return TestClient(self.client.app, raise_server_exceptions=False)

    def session_with_tier(self, tier: str):
        """A real persisted session whose console tier is ``tier``.

        ``create_session`` only mints ``owner`` sessions and ``SessionRecord`` is
        frozen, so the record is re-written through the store's own writer —
        ``require_session`` then loads it from disk like any other session.
        Returns ``(client, csrf)``.
        """
        from corvin_console import auth
        from fastapi.testclient import TestClient

        rec = auth.create_session(tenant_id=TENANT, token_fingerprint="test-fp")
        rec = dataclasses.replace(rec, tier=tier)
        auth._write_record(rec)
        client = TestClient(self.client.app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        return client, auth.derive_csrf_token(rec.csrf_secret, rec.sid)

    def audit_chain(self) -> Path:
        return self.home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"

    def audit_events(self) -> list[dict]:
        path = self.audit_chain()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@contextmanager
def console(tmp_path: Path) -> Iterator[Console]:
    with _sandbox(Path(tmp_path)) as (client, csrf, home, _clients):
        yield Console(client=client, csrf=csrf, home=home)


@contextmanager
def member_tier() -> Iterator[None]:
    """Make the licence resolver report ``member`` for the duration."""
    import corvin_operator.license.capability_api as capability_api

    with mock.patch.object(capability_api, "active_tier", lambda: "member"):
        yield


def assert_402_forge(resp) -> None:
    assert resp.status_code == 402, (resp.status_code, resp.text)
    detail = resp.json()["detail"]
    assert detail["error"] == "license_required"
    assert detail["capability"] == "forge.create"
    assert detail["reason"] == "not_available_in_tier"
    assert detail["upgrade_url"].startswith("https://")
