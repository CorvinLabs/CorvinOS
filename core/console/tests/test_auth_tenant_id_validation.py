"""ADR-0007 — tenant_id boundary at ``corvin_console.auth.create_session``.

Confirmed test blind spot: ``create_session()`` only checks
``if not tenant_id: raise SessionError(...)`` (auth.py:304) — it never runs
``tenant_id`` through the ``_TENANT_ID_RE`` / ``validate_tenant_id`` charset
contract (``[a-z0-9_][a-z0-9_-]{0,62}``, no path-traversal / uppercase /
whitespace / unicode) that ``operator/forge/forge/tenants.py`` defines and
that ``core/console/corvin_console/routes/license.py`` has to defensively
re-check before it will build a filesystem path from ``rec.tenant_id``.

The only current caller (``routes/auth_routes.py::local_login``) hardcodes
``tenant_id="_default"``, so this is not reachable today. These tests pin
down the *actual* current behaviour of the boundary (accepts and persists
any non-empty string verbatim, no charset/shape check) so that:

  * the gap is documented as a real, reproducible test rather than a
    read-only code-review claim, and
  * a future fix that adds validation at this boundary will show up as an
    intentional, visible test change here (not a silent regression).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    str(_REPO / "core" / "console"),
    str(_REPO / "operator"),
    str(_REPO / "operator" / "forge"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The canonical ADR-0007 charset contract (mirrored in forge/paths.py and
# core/console/corvin_console/routes/license.py). Used here only to prove
# our malicious fixtures actually violate it — not imported into auth.py.
_TENANT_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")

_MALFORMED_TENANT_IDS = [
    "../../etc",             # path traversal
    "../../../etc/passwd",   # deeper path traversal
    "_default/../evil",      # traversal appended to an otherwise-valid id
    "UPPERCASE",              # uppercase rejected by the charset rule
    "tenant with spaces",     # whitespace rejected
    "tenant/slash",           # path separator
    "tenant\\backslash",      # Windows path separator
    "tenant\x00null",         # embedded NUL
    "ünïcödé",                # unicode rejected
    "__reserved",             # double-underscore reserved prefix
    " ",                      # whitespace-only — truthy, so `if not tenant_id` lets it through
]


def _assert_actually_malformed(tenant_id: str) -> None:
    """Sanity-check our fixtures against the real ADR-0007 contract."""
    with pytest.raises(Exception):
        from forge.tenants import validate_tenant_id  # type: ignore

        validate_tenant_id(tenant_id)
    assert not _TENANT_ID_RE.match(tenant_id) or tenant_id.startswith("__")


@pytest.fixture()
def console_home(tmp_path, monkeypatch):
    tenant = "_default"
    (tmp_path / "global" / "console" / "sessions").mkdir(parents=True)
    (tmp_path / "tenants" / tenant / "global" / "console" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)
    # Fresh import so corvin_home() picks up the env.
    for m in [k for k in list(sys.modules) if k.startswith("corvin_console.auth")]:
        del sys.modules[m]
    from corvin_console import auth  # type: ignore

    yield auth, tmp_path


@pytest.mark.parametrize("bad_tenant_id", _MALFORMED_TENANT_IDS)
def test_malformed_tenant_ids_violate_the_adr0007_contract(bad_tenant_id):
    """Guard the test fixtures themselves: every value here really is invalid
    per the canonical charset rule, so the assertions below are meaningful."""
    _assert_actually_malformed(bad_tenant_id)


@pytest.mark.parametrize("bad_tenant_id", _MALFORMED_TENANT_IDS)
def test_create_session_does_not_reject_malformed_tenant_id(console_home, bad_tenant_id):
    """Documents the confirmed blind spot: create_session() has NO charset/shape
    guard for tenant_id — it happily mints and persists a session for a
    path-traversal / uppercase / whitespace / unicode tenant_id that the
    ADR-0007 contract (forge.tenants.validate_tenant_id) would reject outright.

    This is not (yet) exploitable in production because the only caller
    hardcodes tenant_id="_default" — but the public API itself provides no
    defense-in-depth, matching the review's finding.
    """
    auth, _ = console_home
    rec = auth.create_session(tenant_id=bad_tenant_id)
    # No SessionError, no ValueError, no InvalidTenantID — accepted verbatim.
    assert rec.tenant_id == bad_tenant_id


@pytest.mark.parametrize("bad_tenant_id", _MALFORMED_TENANT_IDS)
def test_malformed_tenant_id_is_persisted_unsanitized_to_disk(console_home, bad_tenant_id):
    """The malformed tenant_id is not just accepted in-memory — it is written
    verbatim into the on-disk SessionRecord JSON via _write_record(), with no
    sanitization/escaping. Any future reader of this file that does raw string
    interpolation (rather than re-validating, as routes/license.py has to)
    would be building a path/query from attacker-shaped input.
    """
    auth, tmp_path = console_home
    rec = auth.create_session(tenant_id=bad_tenant_id)

    session_file = tmp_path / "global" / "console" / "sessions" / f"{rec.sid}.json"
    assert session_file.exists()
    on_disk = json.loads(session_file.read_text(encoding="utf-8"))
    assert on_disk["tenant_id"] == bad_tenant_id

    # load_session() round-trips the same unsanitized value back out.
    loaded = auth.load_session(rec.sid)
    assert loaded is not None
    assert loaded.tenant_id == bad_tenant_id


def test_create_session_still_rejects_empty_tenant_id(console_home):
    """Control case: the ONE guard that does exist (`if not tenant_id`) still
    works for a plain empty string — this is the one path-shape the current
    code correctly blocks."""
    auth, _ = console_home
    with pytest.raises(auth.SessionError):
        auth.create_session(tenant_id="")


def test_create_session_accepts_well_formed_tenant_id(console_home):
    """Control case: a well-formed tenant_id (the only shape any current
    production caller ever passes) round-trips normally."""
    auth, _ = console_home
    rec = auth.create_session(tenant_id="_default")
    assert rec.tenant_id == "_default"
    loaded = auth.load_session(rec.sid)
    assert loaded is not None
    assert loaded.tenant_id == "_default"
