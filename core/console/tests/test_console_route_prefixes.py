"""Console routers are mounted once under /v1/console — no doubled prefix.

The console app includes every route module into ``app.router``, which the host
mounts at ``/v1/console``. A module that ALSO declares ``prefix="/v1/console/..."``
lands at ``/v1/console/v1/console/...``: the documented path 404s and the UI,
which calls the single-prefix path, reaches nothing (found 2026-09-26 for the
intent router and all four control-plane routers).

Through the real console router with a real session (``_sandbox``):

* the intent and control-plane paths the UI calls are served (not 404), and the
  doubled path is gone;
* ``/audit-log`` and ``/audit`` are not captured by the ``/{id}`` routes that used
  to be declared before them;
* a ratchet over the whole route table: no NEW ``/v1/console/v1/...`` path may
  appear beyond the ones already listed in ``_KNOWN_DOUBLED`` (to be burned down).
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

# Route groups still mounted under a doubled prefix on 2026-09-26. Shrink this
# set when a group is fixed; never grow it — a new entry is a new bug.
_KNOWN_DOUBLED = {
    "/v1/console/v1/console/cost", "/v1/console/v1/console/datahub",
    "/v1/console/v1/console/discovery", "/v1/console/v1/console/feedback",
    "/v1/console/v1/console/learning", "/v1/console/v1/console/skills",
    "/v1/console/v1/engine", "/v1/console/v1/licensing", "/v1/console/v1/models",
    "/v1/console/v1/monitoring", "/v1/console/v1/skill-forge",
}


async def _noop() -> None:
    return None


def _group(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:6]) if parts[4] == "console" else "/".join(parts[:5])


class RoutePrefixTest(unittest.TestCase):
    def test_ui_paths_are_served_and_doubled_paths_are_gone(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            for path in ("/v1/console/intents/recent", "/v1/console/intents/health",
                         "/v1/console/control-plane/plugins", "/v1/console/control-plane/subsystems",
                         "/v1/console/control-plane/overrides", "/v1/console/control-plane/snapshots"):
                self.assertNotEqual(client.get(path).status_code, 404, path)
                self.assertEqual(client.get(path.replace("/v1/console/", "/v1/console/v1/console/", 1))
                                 .status_code, 404, "doubled " + path)
            client.cookies.clear()
            self.assertEqual(client.get("/v1/console/control-plane/plugins").status_code, 401)

    def test_classify_answers_and_audits_without_user_text(self):
        """POST /intents/classify used to call a non-existent audit_backend.write_event
        (500 on every request) and put the raw user text into the audit payload."""
        secret = "please rename my private file salary-2026.xlsx"
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            from core.compliance import consent as _consent
            orig = _consent.consent_required
            _consent.consent_required = lambda scope: (lambda rec: _noop())
            try:
                import corvin_console.routes.intents as intents_mod
                intents_mod.consent_required = _consent.consent_required
                r = client.post("/v1/console/intents/classify", headers={"X-CSRF-Token": csrf},
                                json={"text": secret})
            finally:
                _consent.consent_required = orig
            self.assertEqual(r.status_code, 200, r.text)
            blob = repr(_audit_events(home))
            self.assertIn("intent_", blob)
            self.assertNotIn("salary-2026", blob)

    def test_static_audit_paths_are_not_captured_as_ids(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            from corvin_console.app import router
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            paths = list(app.openapi()["paths"])
            for base, static in (("/v1/console/control-plane/plugins", "audit-log"),
                                 ("/v1/console/control-plane/subsystems", "audit-log"),
                                 ("/v1/console/control-plane/overrides", "audit")):
                static_path = f"{base}/{static}"
                param = next(p for p in paths if p.startswith(base + "/{") and p.count("/") == static_path.count("/"))
                self.assertLess(paths.index(static_path), paths.index(param),
                                f"{static_path} is declared after {param} and is unreachable")

    def test_no_new_doubled_prefix(self):
        with _sandbox(Path(tempfile.mkdtemp())):
            from corvin_console.app import router
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            doubled = {_group(p) for p in app.openapi()["paths"] if re.match(r"^/v1/console/v1/", p)}
            self.assertEqual(doubled - _KNOWN_DOUBLED, set(), "new doubled /v1/console prefix")
            self.assertNotIn("/v1/console/v1/console/intents", doubled)
            self.assertNotIn("/v1/console/v1/console/control-plane", doubled)


if __name__ == "__main__":
    unittest.main()
