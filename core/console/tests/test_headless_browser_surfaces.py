"""Headless mode is a property of the PROCESS, not of one function (ADR-0241/0243).

The question this file asks is deliberately the crude one: *build the app the
operator actually runs, ask it for every browser path, and see what comes back.*

That is the question the previous guard did not ask.  It read the source of one
file and looked for the string ``headless_enabled`` in it — a check that passes
for a route that imports the flag and ignores it, and that says nothing at all
about a second app in a second package.  Three real holes were open underneath a
green version of it:

* ``corvin serve`` does not run the gateway.  ``ops/launcher/corvin/serve_backend.py``
  pins ``corvin_console.standalone:create_app``, and *that* factory registered
  ``/local-stats`` (a full HTML dashboard) and ``GET /`` with no headless check —
  so the pip-install path served a browser UI with the flag on;
* the gateway's ``/op.html`` / ``/analytics.html`` / ``/telemetry.html`` sat at the
  same indentation as the guarded ``/local-stats`` but *outside* its ``if``, gated
  only by the presence of a sibling ``Corvin-Website`` checkout;
* the gateway's ``/favicon.ico`` redirected into ``/console/favicon.svg``, which
  does not exist in headless mode — a 302 into a 404.

Both flag states are covered (CLAUDE.md: a flag only ever tested in one state
rots).  The flag-OFF direction is not decoration: a guard that deletes the
surface unconditionally would satisfy every headless assertion here while
breaking every normal install.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]

for _p in (
    str(_REPO / "core" / "console"),
    str(_REPO / "core" / "gateway"),
    str(_REPO / "core" / "plugins"),
    str(_REPO / "operator"),
    str(_REPO / "operator" / "license"),
    str(_REPO / "operator" / "forge"),
    str(_REPO / "operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: Every bare browser path either app can answer.  Not "every route" — API routes
#: are supposed to keep working in headless mode; these are the ones that hand a
#: human a page (or point at one).
_BROWSER_PATHS = (
    "/",
    "/local-stats",
    "/console/",
    "/op.html",
    "/analytics.html",
    "/telemetry.html",
    "/favicon.ico",
)

#: The dev-machine sibling checkout the gateway's operator dashboards come from.
#: Absent on a normal install (and in CI), so the flag-OFF direction can only
#: assert those three paths where the files are actually there.
_WEBSITE_ROOT = _REPO.parent / "Corvin-Website"

#: corvin_plugins is NEVER purged (test_admin_route.py convention): a second copy
#: forks every enum and steals the audit fan-out sink bound at import time.
_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge")

_REDIRECTS = (301, 302, 303, 307, 308)


@contextmanager
def _sandbox(tmp: Path, *, headless: bool | None):
    """A tenant home with ``headless_api_mode`` written the way the UI writes it.

    Through ``features.json`` rather than a patched ``is_enabled``, so this would
    notice if the flag stopped being readable that way — including the nesting
    under ``"flags"``, which a flat ``{id: bool}`` silently loses.
    """
    home = tmp / "corvin_home"
    global_dir = home / "tenants" / "_default" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    if headless is not None:
        (global_dir / "features.json").write_text(
            json.dumps({"flags": {"headless_api_mode": headless}}), encoding="utf-8"
        )

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = "_default"
    # Keep the real GDPR chain out of the test run (tests/conftest.py convention).
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

    preloaded = {
        name: mod for name, mod in sys.modules.items()
        if name.startswith(_PURGED_PREFIXES)
    }
    for name in list(sys.modules):
        if name.startswith(_PURGED_PREFIXES):
            del sys.modules[name]
    try:
        yield home
    finally:
        for name in list(sys.modules):
            if name.startswith(_PURGED_PREFIXES):
                del sys.modules[name]
        sys.modules.update(preloaded)
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _client(app):
    from fastapi.testclient import TestClient

    # No `with` block on purpose: entering the context runs the lifespan, which
    # boots tripwires, plugins and a heartbeat thread. Route registration is what
    # is under test here and it happens at construction time.
    #
    # raise_server_exceptions=False so a route that exists but explodes shows up
    # as a 500 to assert on rather than aborting the test — that is exactly how
    # /op.html failed (NameError on _HTMLResponse, imported inside the `if`).
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def _standalone_client():
    """The app ``corvin serve`` runs (ops/launcher/corvin/serve_backend.py)."""
    from corvin_console.standalone import create_app

    return _client(create_app())


def _gateway_client():
    """The app an operator wires into uvicorn as ``corvin_gateway.app:app``."""
    import corvin_gateway.app as gateway_app

    return _client(gateway_app.app)


def _chase(client, path: str, *, hops: int = 5):
    """Follow same-origin redirects by hand and return (final_path, response)."""
    seen = [path]
    resp = client.get(path)
    while resp.status_code in _REDIRECTS and len(seen) <= hops:
        location = resp.headers.get("location", "")
        if not location:
            break
        target = urlsplit(location).path or "/"
        if target in seen:
            break
        seen.append(target)
        resp = client.get(target)
    return seen[-1], resp


class _SurfaceAssertions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.tmp = Path(self._tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def assert_no_browser_surface(self, client, path: str, label: str) -> None:
        """404 or JSON is fine.  HTML, a 5xx, or a dangling redirect is not."""
        resp = client.get(path)
        ctype = resp.headers.get("content-type", "")

        self.assertLess(
            resp.status_code, 500,
            f"{label} {path}: answered {resp.status_code} — a route that is "
            f"registered and then fails is still a registered browser surface "
            f"({ctype})",
        )
        self.assertFalse(
            resp.status_code == 200 and ctype.startswith("text/html"),
            f"{label} {path}: served an HTML page with headless_api_mode on",
        )

        if resp.status_code in _REDIRECTS:
            final_path, final = _chase(client, path)
            self.assertNotEqual(
                final.status_code, 404,
                f"{label} {path}: {resp.status_code} → {final_path}, which 404s — "
                f"a redirect into a hole is not an API-only answer",
            )
            self.assertFalse(
                final.status_code == 200
                and final.headers.get("content-type", "").startswith("text/html"),
                f"{label} {path}: redirects to {final_path}, which serves HTML",
            )

    def assert_surface_present(self, client, path: str, label: str) -> None:
        """The flag-OFF direction: the page (or the hop to it) still answers."""
        final_path, resp = _chase(client, path)
        self.assertNotEqual(
            resp.status_code, 404,
            f"{label} {path} → {final_path}: gone with headless_api_mode OFF — "
            f"the guard removed the surface in BOTH states",
        )
        self.assertLess(resp.status_code, 500, f"{label} {path}: {resp.status_code}")


# ── Flag ON ───────────────────────────────────────────────────────────────────


class TestStandaloneServesNoBrowserSurface(_SurfaceAssertions):
    """``corvin serve`` — the path a pip-install actually takes."""

    def test_every_browser_path_is_gone(self):
        with _sandbox(self.tmp, headless=True):
            client = _standalone_client()
            for path in _BROWSER_PATHS:
                with self.subTest(path=path):
                    self.assert_no_browser_surface(client, path, "standalone")

    def test_the_api_still_answers(self):
        """Headless removes the UI, not the product.

        Without this, "delete every route" would pass the test above.
        """
        with _sandbox(self.tmp, headless=True):
            client = _standalone_client()
            paths = {getattr(r, "path", "") for r in client.app.routes}
            self.assertTrue(
                any(p.startswith("/v1/console/") for p in paths),
                "headless mode must keep the REST API — it is API-ONLY mode, "
                "not off mode",
            )


class TestGatewayServesNoBrowserSurface(_SurfaceAssertions):
    def test_every_browser_path_is_gone(self):
        with _sandbox(self.tmp, headless=True):
            client = _gateway_client()
            for path in _BROWSER_PATHS:
                with self.subTest(path=path):
                    self.assert_no_browser_surface(client, path, "gateway")

    def test_healthz_still_answers(self):
        with _sandbox(self.tmp, headless=True):
            resp = _gateway_client().get("/healthz")
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["status"], "ok")


# ── Flag OFF — the counter-direction ──────────────────────────────────────────


class TestFlagOffKeepsTheUI(_SurfaceAssertions):
    def test_standalone_still_serves_its_pages(self):
        with _sandbox(self.tmp, headless=False):
            client = _standalone_client()
            for path in ("/", "/local-stats", "/console/"):
                with self.subTest(path=path):
                    self.assert_surface_present(client, path, "standalone")
            self.assertTrue(
                client.get("/local-stats")
                .headers.get("content-type", "")
                .startswith("text/html"),
                "/local-stats is an HTML dashboard when the flag is off",
            )

    def test_gateway_still_serves_its_pages(self):
        with _sandbox(self.tmp, headless=False):
            client = _gateway_client()
            for path in ("/", "/local-stats", "/console/", "/favicon.ico"):
                with self.subTest(path=path):
                    self.assert_surface_present(client, path, "gateway")

    def test_gateway_operator_dashboards_survive_where_they_exist(self):
        """Only assertable on a checkout that has the sibling website repo.

        Moving these three registrations inside the headless guard must not
        change what a dev machine sees with the flag off.
        """
        available = [
            f"/{name}"
            for name in ("op.html", "analytics.html", "telemetry.html")
            if (_WEBSITE_ROOT / name).exists()
        ]
        if not available:
            self.skipTest(f"no sibling Corvin-Website checkout at {_WEBSITE_ROOT}")
        with _sandbox(self.tmp, headless=False):
            client = _gateway_client()
            for path in available:
                with self.subTest(path=path):
                    self.assert_surface_present(client, path, "gateway")


# ── Ships dark ────────────────────────────────────────────────────────────────


class TestFreshInstallIsUnaffected(_SurfaceAssertions):
    def test_no_features_file_means_the_ui_is_served(self):
        """Absent key = off (CLAUDE.md), and off is the pre-feature code path."""
        with _sandbox(self.tmp, headless=None):
            self.assert_surface_present(_standalone_client(), "/local-stats", "standalone")

    def test_an_unreadable_flag_file_means_the_ui_is_served(self):
        """Every failure path in the flag read resolves to "serve as before"."""
        with _sandbox(self.tmp, headless=None) as home:
            (home / "tenants" / "_default" / "global" / "features.json").write_text(
                "{ this is not json", encoding="utf-8"
            )
            self.assert_surface_present(_standalone_client(), "/local-stats", "standalone")


if __name__ == "__main__":
    unittest.main()
