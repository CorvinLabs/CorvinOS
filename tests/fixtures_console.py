"""Shared console test fixtures: a real app, a real transport.

107 test errors in the 2026-09-20 full run were a single shape — a test asked
for a fixture nobody had written:

    fixture 'client' not found            35
    fixture 'page' not found              28
    fixture 'real_haiku_client' not found 20
    fixture 'app' not found               10
    fixture 'real_opus_client' not found   9

Those tests were never skipped and never red for a reason anyone could act on;
they were collection errors, i.e. invisible. This module supplies the two that
can be served locally (``app`` / ``client``) against the REAL console
application over the REAL ASGI transport, so a test using them satisfies the
e2e-wiring-proof bar rather than importing the handler and calling it.

Authentication is overridden, not bypassed in production code: every console
route depends on ``require_session``/``require_csrf``, and the override injects
a synthetic owner-tier ``SessionRecord`` for tenant ``_default``.
"""
from __future__ import annotations

import dataclasses
import os

import pytest

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps


def fake_session_record(tenant_id: str = "_default") -> "session_auth.SessionRecord":
    """A synthetic authenticated session for `tenant_id`.

    Built by reflection over the dataclass so a new required field on
    SessionRecord does not silently break every console test.
    """
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


@pytest.fixture
def app():
    """The real console router, mounted where the live host mounts it.

    ``corvin_console.app.app`` includes its router at the ROOT, so a request to
    it lands on ``/forge/tools``. The shipped host does not: ``corvin_gateway``
    mounts the same router at ``/v1/console`` (app.py:605), which is the path
    the SPA and every operator actually call. A fixture serving the bare
    console app would make tests green on URLs that 404 in production, so this
    reproduces the gateway's mount instead.

    Authentication: We override ALL auth-related dependencies, both at the FastAPI
    level AND by patching the functions themselves, because some code paths call
    these functions directly instead of through dependency injection.
    """
    from fastapi import FastAPI

    from corvin_console import app as console_module

    fake_rec = fake_session_record("_default")
    test_app = FastAPI()
    test_app.include_router(console_module.router, prefix="/v1/console")

    # Override dependencies at the FastAPI level
    test_app.dependency_overrides[console_deps.require_session] = lambda: fake_rec
    test_app.dependency_overrides[console_deps.require_csrf] = lambda: fake_rec
    test_app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = lambda: fake_rec

    # ALSO patch the functions themselves in the deps module, because
    # require_session_csrf_on_mutation calls require_session/require_csrf directly
    import core.console.corvin_console.deps as deps_module
    original_require_session = deps_module.require_session
    original_require_csrf = deps_module.require_csrf
    original_require_session_csrf_on_mutation = deps_module.require_session_csrf_on_mutation

    deps_module.require_session = lambda corvin_console_sid=None: fake_rec
    deps_module.require_csrf = lambda corvin_console_sid=None, x_csrf_token=None: fake_rec
    deps_module.require_session_csrf_on_mutation = lambda request, corvin_console_sid=None, x_csrf_token=None: fake_rec

    try:
        yield test_app
    finally:
        test_app.dependency_overrides.clear()
        # Restore original functions
        deps_module.require_session = original_require_session
        deps_module.require_csrf = original_require_csrf
        deps_module.require_session_csrf_on_mutation = original_require_session_csrf_on_mutation


@pytest.fixture
def client(app):
    """Synchronous TestClient over the real console app."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
async def async_client(app):
    """httpx.AsyncClient bound to the real console app via ASGI transport."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://console.test") as c:
        yield c


def _llm_client_or_skip(env_var: str, model_id: str):
    """A real Anthropic client, or a skip when no key is configured.

    These suites are deliberately NOT mocked — they exist to catch drift
    against the live API. Without a key they must SKIP (visible, explained),
    not error out as a missing fixture.
    """
    api_key = os.environ.get(env_var) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip(
            f"no API key in {env_var} or ANTHROPIC_API_KEY — "
            f"this suite calls the live {model_id} endpoint on purpose"
        )
    try:
        import anthropic
    except ImportError:  # pragma: no cover - anthropic is a base dependency
        pytest.skip("anthropic SDK not installed")
    return anthropic.Anthropic(api_key=api_key)


@pytest.fixture
def real_haiku_client():
    """Live Anthropic client pinned to Haiku, or skip."""
    return _llm_client_or_skip("CORVIN_TEST_ANTHROPIC_KEY", "claude-haiku-4-5")


@pytest.fixture
def real_opus_client():
    """Live Anthropic client pinned to Opus, or skip."""
    return _llm_client_or_skip("CORVIN_TEST_ANTHROPIC_KEY", "claude-opus-5")
