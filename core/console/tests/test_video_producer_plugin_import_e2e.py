"""F3 - the console never executes Video Producer plugin code for a tenant that did not enable it
(ADR-2247, PLAN-0946 R4, T-0093).

Until T-0093 ``video_producer_api`` ran ``_load_plugin()`` at module import: importing the console executed
third-party-style plugin code before any tenant had enabled it, and ``POST /video/jobs/{id}/feedback``
(``video_learning_api``, no router gate) loaded it for a tenant that never had. Now nothing loads at
import and the one loader, ``_ensure_plugin(tenant_id)``, loads only for a tenant that enabled the plugin.

Three proofs:
* a fresh interpreter imports the console with the plugin directory ABSENT (an empty directory is
  bind-mounted over it in a private mount namespace) - no error, nothing loaded, nothing written;
* a fresh interpreter imports the console with the plugin directory PRESENT and opens none of its files;
* over HTTP (real router, session cookie + CSRF, two tenants): a tenant that has the plugin installed but
  not enabled gets 404 on the gated router AND on the ungated learning route, and the plugin stays
  unloaded; the boot audit does not load it for that tenant's store either; the first request of the
  enabling tenant loads it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import _install_plugin, _route_module, _sandbox  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_CONSOLE = _REPO / "core" / "console"
_PKG = "corvin_video_producer_plugin"
_SIBLING = _REPO.parent / "Corvin-Marketplace" / "plugins" / "contributor" / "media" / "video_producer"

# Run inside the fresh interpreter: import the console as a host does, then report what happened.
_PROBE = textwrap.dedent(r"""
    import json, os, sys
    from pathlib import Path

    home = Path(os.environ["CORVIN_HOME"])
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    opened = []
    sys.addaudithook(lambda ev, args: opened.append(str(args[0])) if ev == "open" and args and
                     isinstance(args[0], (str, bytes, os.PathLike)) and "video_producer" in str(args[0]) and
                     "/routes/" not in str(args[0]) else None)
    import corvin_console.app  # noqa: F401  - every console route module, as both hosts import it
    from corvin_console.routes import video_producer_api as vpa
    after = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    locs = [str(src) for src, _ in vpa._plugin_locations()]
    print("PROBE" + json.dumps({
        "loaded_modules": sorted(n for n in sys.modules if n.split(".")[0] == "corvin_video_producer_plugin"),
        "globals": [x is None for x in (vpa.VideoJob, vpa._get_storage, vpa.get_runner, vpa.PLUGIN_SOURCE)],
        "plugin_files_opened": [p for p in opened if any(p.startswith(l.rsplit("/src", 1)[0]) for l in locs)],
        "present": {l: (Path(l) / "__init__.py").is_file() for l in locs},
        # other console modules create their own state at import (learning, token metrics); the claim is
        # about this plugin: nothing of it - store, cache, sidecar - may appear
        "home_written": sorted(p for p in set(after) - set(before) if "video" in p.lower()),
        "home_written_other": len(set(after) - set(before)),
    }))
""")


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CORVIN_MARKETPLACE_ROOT"}
    # every root the probe could write to stays inside its own temp CORVIN_HOME (FORGE_ROOT included)
    env.update(CORVIN_HOME=str(home), FORGE_ROOT=str(home / "forge"),
               PYTHONPATH=os.pathsep.join([str(_CONSOLE), str(_REPO)]),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def _probe(argv_prefix: list, home: Path) -> dict:
    proc = subprocess.run([*argv_prefix, sys.executable, "-c", _PROBE], env=_env(home), cwd=str(home),
                          capture_output=True, text=True, timeout=180)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE")), None)
    assert proc.returncode == 0 and line, f"rc={proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    return json.loads(line[len("PROBE"):])


def _unshare_ok() -> bool:
    if not shutil.which("unshare") or not _SIBLING.is_dir():
        return False
    r = subprocess.run(["unshare", "-r", "-m", "true"], capture_output=True, timeout=10)
    return r.returncode == 0


class FreshInterpreterImport(unittest.TestCase):
    @unittest.skipUnless(_unshare_ok(), "needs an unprivileged mount namespace and the sibling plugin checkout")
    def test_import_with_the_plugin_directory_absent_has_no_side_effect(self):
        home, empty = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(shutil.rmtree, empty, True)
        # the only plugin directory on this host is the sibling checkout; CORVIN_HOME is empty, so the
        # installed/cached locations do not exist either. Hide the sibling for this one process only.
        script = (f'mount --bind "{empty}" "{_SIBLING}" && exec "$@"')
        out = _probe(["unshare", "-r", "-m", "sh", "-c", script, "sh"], home)
        print(f"\n[F3 absent] {out}")
        self.assertFalse(any(out["present"].values()), f"positive control: a plugin directory is visible {out}")
        self.assertEqual(out["loaded_modules"], [])
        self.assertEqual(out["globals"], [True, True, True, True])
        self.assertEqual(out["plugin_files_opened"], [])
        self.assertEqual(out["home_written"], [])

    @unittest.skipUnless((_SIBLING / "src" / "__init__.py").is_file(), "needs the sibling plugin checkout")
    def test_import_with_the_plugin_directory_present_executes_none_of_it(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        out = _probe([], home)
        print(f"\n[F3 present] {out}")
        self.assertTrue(any(out["present"].values()), f"positive control: the plugin is not visible {out}")
        self.assertEqual(out["loaded_modules"], [])
        self.assertEqual(out["globals"], [True, True, True, True])
        self.assertEqual(out["plugin_files_opened"], [])
        self.assertEqual(out["home_written"], [])


class TenantGatedLoadHttp(unittest.TestCase):
    def test_only_an_enabling_tenant_causes_the_plugin_to_load(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in [n for n in sys.modules if n.split(".")[0] == _PKG]:
            sys.modules.pop(name)
        with _sandbox(tmp, tenants=("acme", "other"), plugin="absent") as (_c, _t, home, clients):
            _install_plugin("acme", enable=True)
            _install_plugin("other", enable=False)  # installed, consent not given
            mod = _route_module()
            loaded = lambda: any(n.split(".")[0] == _PKG for n in sys.modules)  # noqa: E731
            self.assertFalse(loaded(), "the plugin was loaded by importing the console")
            self.assertIsNone(mod._get_storage)

            oc, ocsrf = clients["other"]
            self.assertEqual(oc.get("/v1/console/video/jobs").status_code, 404)
            # the learning route has no router gate; it used to load the plugin on its way to a 404
            r = oc.post("/v1/console/video/jobs/job_0000abcd/feedback", headers={"X-CSRF-Token": ocsrf},
                        json={"scene_id": "s1", "rating": 4})
            self.assertEqual(r.status_code, 404, r.text)
            # boot audit over a store of the non-enabling tenant
            (home / "tenants" / "other" / "video_producer" / "jobs").mkdir(parents=True)
            self.assertEqual(mod.audit_interrupted_jobs(), 0)
            self.assertFalse(loaded(), "a tenant that did not enable the plugin caused it to load")
            self.assertIsNone(mod._get_storage)

            ac, _ = clients["acme"]
            r = ac.get("/v1/console/video/jobs")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(loaded() and mod._get_storage is not None, "the enabling tenant did not load it")
            # loaded for acme is not access for other
            self.assertEqual(oc.get("/v1/console/video/jobs").status_code, 404)
            self.assertEqual(oc.post("/v1/console/video/jobs/job_0000abcd/feedback", headers={"X-CSRF-Token": ocsrf},
                                     json={"scene_id": "s1", "rating": 4}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
