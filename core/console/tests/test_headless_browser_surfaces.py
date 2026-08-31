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

A fourth hole sits one level deeper and is why this file grew an authenticated
section: ``GET /v1/console/compute/experiments/{id}/report`` renders a full
``<!DOCTYPE html>`` page with its own stylesheet.  Sweeping *bare* paths could
never see it — it is behind ``require_session``, and headless mode keeps login
working on purpose.  "Every browser path answers correctly" is therefore not the
same question as "no HTML page is served"; both have to be asked.

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


#: An experiment id the report route can actually render, seeded on disk.
_EXPERIMENT_ID = "exp_headless_probe"
_REPORT_PATH = f"/v1/console/compute/experiments/{_EXPERIMENT_ID}/report"


def _seed_experiment(home: Path) -> None:
    """Write the one artifact ``/report`` needs to produce a page.

    Without it the route 404s and would "pass" the headless assertion for the
    wrong reason — the test has to reach the HTML-rendering path.
    """
    exp_dir = home / "tenants" / "_default" / "compute" / "experiments" / _EXPERIMENT_ID
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "experiment.json").write_text(
        json.dumps(
            {
                "experiment_id": _EXPERIMENT_ID,
                "name": "Headless probe",
                "hypothesis": "an API-only process hands out no styled page",
                "session_label": "probe",
                "run_ids": [],
            }
        ),
        encoding="utf-8",
    )


def _session_record():
    """A minimal owner session, built from the dataclass so field drift shows up."""
    import dataclasses
    import time

    from corvin_console import auth as session_auth

    now = time.time()
    values = {}
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
            values[f.name] = "_default"
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)


def _authed_standalone_client():
    """``corvin serve``'s app with the session dependency already satisfied.

    Overriding ``require_session`` rather than minting a real cookie keeps the
    subject of the test the *response*, not the login flow — and headless mode
    deliberately leaves login working, so a real session would answer here too.
    """
    from corvin_console.deps import require_session
    from corvin_console.standalone import create_app

    app = create_app()
    app.dependency_overrides[require_session] = _session_record
    return _client(app)


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


# ── Authenticated HTML: the page a bare sweep cannot see ─────────────────────


class TestAuthenticatedReportIsNotAPage(_SurfaceAssertions):
    """``/compute/experiments/{id}/report`` is a full HTML document.

    ``Depends(require_session)`` is not a headless guard — headless mode keeps
    the API, and the API includes logging in.
    """

    def test_report_serves_no_html_when_headless(self):
        with _sandbox(self.tmp, headless=True) as home:
            _seed_experiment(home)
            client = _authed_standalone_client()
            resp = client.get(_REPORT_PATH)
            self.assertLess(resp.status_code, 500, resp.text)
            self.assertFalse(
                resp.headers.get("content-type", "").startswith("text/html"),
                "the experiment report served an HTML page with "
                "headless_api_mode on",
            )
            self.assertNotIn("<!DOCTYPE html>", resp.text)

    def test_report_keeps_its_content_as_json_when_headless(self):
        """Headless removes the presentation, not the report.

        Without this the guard could 404 the route and still pass the test
        above — an API-only install would silently lose a product feature.
        """
        with _sandbox(self.tmp, headless=True) as home:
            _seed_experiment(home)
            resp = _authed_standalone_client().get(_REPORT_PATH)
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["experiment_id"], _EXPERIMENT_ID)
            self.assertEqual(body["experiment"]["name"], "Headless probe")
            self.assertIn("runs", body)
            self.assertIn("improvement_pct", body)

    def test_report_is_still_a_page_with_the_flag_off(self):
        with _sandbox(self.tmp, headless=False) as home:
            _seed_experiment(home)
            resp = _authed_standalone_client().get(_REPORT_PATH)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(
                resp.headers.get("content-type", "").startswith("text/html"),
                "the report is an HTML page on a normal install",
            )
            self.assertIn("<!DOCTYPE html>", resp.text)
            self.assertIn("Headless probe", resp.text)

    def test_a_missing_experiment_still_404s_in_both_states(self):
        """The headless branch must not swallow the not-found path."""
        for headless in (True, False):
            with self.subTest(headless=headless), _sandbox(self.tmp, headless=headless):
                resp = _authed_standalone_client().get(
                    "/v1/console/compute/experiments/nope_not_here/report"
                )
                self.assertEqual(resp.status_code, 404, resp.text)


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
