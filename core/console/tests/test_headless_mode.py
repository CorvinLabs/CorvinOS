"""Headless API-only mode (ADR-0241/0243, Phase 6).

What is under test is narrow on purpose: `headless_api_mode` decides whether
this process serves a browser surface. It does NOT decide whether bridges run —
that is `bridge_supervisor_plugins`, and coupling the two would make
"core + CLI + bridges, no browser UI" (deployment model D) unreachable.

The property that matters most is the one that is easy to get wrong: with the
flag on there must be no `/console` surface *at all*, including the friendly
"SPA not built" placeholder. A placeholder is a browser surface.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (str(_REPO / "core" / "console"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@contextmanager
def _sandbox(tmp: Path, *, headless: bool | None):
    """A tenant home with the flag written through the real overlay file."""
    home = tmp / "corvin_home"
    global_dir = home / "tenants" / "_default" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    if headless is not None:
        # features.json is what the Settings UI writes — go through the real
        # resolution path rather than patching is_enabled, so this test would
        # notice if the flag stopped being readable that way. Note the nesting
        # under "flags": a flat {id: bool} is silently ignored by is_enabled,
        # which is exactly the kind of thing a mocked flag would have hidden.
        (global_dir / "features.json").write_text(
            json.dumps({"flags": {"headless_api_mode": headless}}), encoding="utf-8"
        )

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = "_default"
    dropped = {
        name: mod for name, mod in list(sys.modules.items())
        if name.startswith(("corvin_console", "forge"))
    }
    for name in dropped:
        sys.modules.pop(name, None)
    try:
        yield home
    finally:
        for name in list(sys.modules):
            if name.startswith(("corvin_console", "forge")):
                sys.modules.pop(name, None)
        sys.modules.update(dropped)
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestHeadlessFlagResolution(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ships_dark(self):
        with _sandbox(self.tmp, headless=None):
            from corvin_console.app import headless_enabled

            self.assertFalse(headless_enabled())

    def test_flag_on_is_read(self):
        with _sandbox(self.tmp, headless=True):
            from corvin_console.app import headless_enabled

            self.assertTrue(headless_enabled())

    def test_flag_off_is_read(self):
        with _sandbox(self.tmp, headless=False):
            from corvin_console.app import headless_enabled

            self.assertFalse(headless_enabled())

    def test_unreadable_flag_resolves_to_serving_the_ui(self):
        # An install that cannot answer the question must behave as it did
        # before the feature existed — which is "UI mounted".
        with _sandbox(self.tmp, headless=None) as home:
            (home / "tenants" / "_default" / "global" / "features.json").write_text(
                "{ this is not json", encoding="utf-8"
            )
            from corvin_console.app import headless_enabled

            self.assertFalse(headless_enabled())


class TestMountBehaviour(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _routes_after_mount(self, headless: bool | None) -> list[str]:
        with _sandbox(self.tmp, headless=headless):
            from corvin_console.app import mount_static
            from fastapi import FastAPI

            app = FastAPI()
            mount_static(app)
            return [getattr(r, "path", "") for r in app.routes]

    def test_headless_mounts_nothing_not_even_a_placeholder(self):
        paths = self._routes_after_mount(True)
        self.assertEqual(
            [p for p in paths if p.startswith("/console")], [],
            "headless mode must not serve /console at all — the "
            "'SPA not built' placeholder is still a browser surface",
        )

    def test_non_headless_serves_something_under_console(self):
        # Counter-test: a guard that removes the surface in BOTH states would
        # pass the test above while breaking every normal install.
        paths = self._routes_after_mount(False)
        self.assertTrue(
            any(p.startswith("/console") for p in paths)
            or any(getattr(r, "path", "") == "/console" for r in []),
            f"expected a /console surface when headless is off, got {paths}",
        )


class TestEveryBrowserSurfaceIsCovered(unittest.TestCase):
    """The SPA is not the only browser surface on the gateway app.

    The mount test above builds a bare ``FastAPI()`` and calls ``mount_static``
    on it, which proves what that ONE function does and nothing about the
    process. The gateway also serves ``/local-stats`` (a full HTML dashboard)
    and redirects ``/`` into ``/console/``. Both were untouched by headless mode
    — so "no browser surface" was true of the function under test and false of
    the deployment. This pins the other two at their registration site.
    """

    @staticmethod
    def _gateway_source() -> str:
        return (
            _REPO / "core" / "gateway" / "corvin_gateway" / "app.py"
        ).read_text(encoding="utf-8")

    def test_local_stats_dashboard_is_gated(self):
        src = self._gateway_source()
        idx = src.find('"/local-stats"')
        self.assertGreater(idx, 0, "/local-stats route not found — did it move?")
        # The gate has to be established BEFORE the route is registered.
        self.assertIn(
            "headless_enabled", src[:idx],
            "/local-stats serves a full HTML dashboard and is registered "
            "without consulting headless mode",
        )

    def test_root_redirect_does_not_point_into_a_404(self):
        src = self._gateway_source()
        idx = src.find("async def root_redirect")
        self.assertGreater(idx, 0)
        body = src[idx:idx + 1200]
        self.assertIn(
            "headless_enabled", body,
            "GET / redirects to /console/, which does not exist in headless "
            "mode — a 302 into a 404",
        )


class TestNoHiddenCoupling(unittest.TestCase):
    def test_headless_does_not_gate_the_bridge_supervisor(self):
        # Deployment model D is "core + CLI + bridges, no browser UI". If the
        # supervisor ever starts reading headless_api_mode, that model becomes
        # unreachable and two independent switches become one.
        src = (
            _REPO / "core" / "plugins" / "corvin_plugins" / "bridges" / "supervisor.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "headless_api_mode", src,
            "the bridge supervisor must not read headless_api_mode — the two "
            "flags are independent by design (see docs/HEADLESS_CORE_ARCHITECTURE.md)",
        )


if __name__ == "__main__":
    unittest.main()
