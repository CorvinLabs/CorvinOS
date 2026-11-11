"""REVIEW-0064 #1: the hint guard (core/projects/hints.py) loads the path gate in the WHEEL layout too.

In a wheel ``core/projects/`` lands at ``site-packages/core/projects/`` (pyproject ``packages``) while hatch_build.py vendors the
hook to ``corvin_core/_vendor/corvin_operator/voice/hooks/``. Before the fix ``hints.available()`` failed there, and with
``managed`` the default every managed create/move/import was refused 422 ``hint_word``.

Two proofs:

* ``WheelShapedLayout`` (fast, always runs): a site-packages tree laid out from the build's OWN mapping (pyproject
  ``packages``/``sources``, hatch_build.py ``_VENDOR_MAP``), loaded in an isolated interpreter (``python -I``, no repo path).
* ``RealWheel`` (builds the wheel with ``uv build --wheel`` - or takes ``CORVIN_TEST_WHEEL`` - and installs it offline into a
  fresh venv; skipped when uv, the SPA build or the dependency cache is missing): ``_wheel_projects_driver.py`` creates a managed
  project over real HTTP (the projects router the console mounts, a real session) and reads the doctor's ``hint_source``.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _projects_fixtures import small_catalogue, write_template  # noqa: E402


def _vendor_dest(src: str) -> str:
    """The wheel destination hatch_build.py's ``_VENDOR_MAP`` gives *src* (read from the file, never restated)."""
    tree = ast.parse((REPO / "hatch_build.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = getattr(node, "target", None) if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "_VENDOR_MAP" and node.value is not None:
            for s, d in ast.literal_eval(node.value):
                if s == src:
                    return d
    raise AssertionError(f"hatch_build.py vendors no {src}")


def _wheel_shaped(sp: Path) -> None:
    """``core/projects`` where ``packages = [.., "core", ..]`` puts it, ``corvin_core`` where ``sources`` maps it, the hook where
    ``_VENDOR_MAP`` vendors it."""
    py = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = py["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "core" in wheel["packages"]
    shutil.copytree(REPO / "core" / "projects", sp / "core" / "projects", ignore=shutil.ignore_patterns("__pycache__"))
    (sp / "core" / "__init__.py").write_text((REPO / "core" / "__init__.py").read_text(encoding="utf-8")
                                             if (REPO / "core" / "__init__.py").is_file() else "", encoding="utf-8")
    shutil.copytree(REPO / "core" / "console" / "corvin_core", sp / wheel["sources"]["core/console/corvin_core"],
                    ignore=shutil.ignore_patterns("__pycache__", "tests"))
    dest = _vendor_dest("corvin_operator/voice/hooks")
    shutil.copytree(REPO / "corvin_operator" / "voice" / "hooks", sp / dest, ignore=shutil.ignore_patterns("__pycache__", "tests"))


class WheelShapedLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sp = self.tmp / "site-packages"
        _wheel_shaped(self.sp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, code: str) -> dict:
        prog = f"import sys, json; sys.path[:0] = [{str(self.sp)!r}]\n" + code
        r = subprocess.run([sys.executable, "-I", "-c", prog], capture_output=True, text=True, timeout=120, cwd=str(self.tmp),
                           env={**os.environ, "HOME": str(self.tmp)})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_hint_test_loads_from_the_vendored_hook(self):
        out = self._run("from core.projects import hints\n"
                        "print(json.dumps({'file': hints.__file__, 'why': hints.available(), 'gate': str(hints.gate_path()),"
                        " 'forge': hints.looks_protected('/home/u/forge-lab'), 'plain': hints.looks_protected('/home/u/alpha')}))")
        self.assertTrue(out["file"].startswith(str(self.sp)), out)
        self.assertIsNone(out["why"], out)
        self.assertEqual(Path(out["gate"]), self.sp / _vendor_dest("corvin_operator/voice/hooks") / "path_gate.py")
        self.assertEqual((out["forge"], out["plain"]), (True, False))

    def test_a_missing_vendored_hook_is_named_not_guessed(self):
        shutil.rmtree(self.sp / _vendor_dest("corvin_operator/voice/hooks"))
        out = self._run("from core.projects import hints\nprint(json.dumps({'why': hints.available()}))")
        self.assertIn("could not be loaded", out["why"])


def _uv() -> str | None:
    return shutil.which("uv")


def _uv_env() -> dict[str, str]:
    """uv's package cache lives below the REAL home; the test's HOME is a sandbox, so name the cache explicitly (read for the
    offline install; nothing of Corvin is in it)."""
    env = dict(os.environ)
    if "UV_CACHE_DIR" not in env and os.name != "nt":
        import pwd  # noqa: PLC0415
        real = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "uv"
        if real.is_dir():
            env["UV_CACHE_DIR"] = str(real)
    return env


class RealWheel(unittest.TestCase):
    wheel: Path | None = None
    venv_py: Path | None = None
    work: Path | None = None

    @classmethod
    def setUpClass(cls):
        uv = _uv()
        if uv is None:
            raise unittest.SkipTest("uv is not on PATH - the real-wheel proof builds the wheel with `uv build --wheel`")
        cls.work = Path(tempfile.mkdtemp(prefix="corvin-wheel-"))
        given = os.environ.get("CORVIN_TEST_WHEEL")
        if given:
            cls.wheel = Path(given)
        else:
            if not (REPO / "core" / "console" / "corvin_console" / "web-next" / "dist" / "index.html").is_file():
                raise unittest.SkipTest("the console SPA is not built - hatch_build.py refuses a UI-less wheel")
            r = subprocess.run([uv, "build", "--wheel", "--out-dir", str(cls.work / "dist")], cwd=str(REPO), capture_output=True,
                               text=True, timeout=900, env=_uv_env())
            if r.returncode != 0:
                raise unittest.SkipTest(f"`uv build --wheel` failed (rc={r.returncode}): {r.stderr[-800:]}")
            cls.wheel = next((cls.work / "dist").glob("*.whl"))
        venv = cls.work / "venv"
        r = subprocess.run([uv, "venv", "-q", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", str(venv)],
                           capture_output=True, text=True, timeout=300, env=_uv_env())
        if r.returncode != 0:
            raise unittest.SkipTest(f"`uv venv` failed: {r.stderr[-400:]}")
        cls.venv_py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        r = subprocess.run([uv, "pip", "install", "-q", "--offline", "--python", str(cls.venv_py), str(cls.wheel)],
                           capture_output=True, text=True, timeout=900, env=_uv_env())
        if r.returncode != 0:
            raise unittest.SkipTest(f"the wheel's dependencies are not in the uv cache (offline install failed): {r.stderr[-400:]}")

    @classmethod
    def tearDownClass(cls):
        if cls.work is not None:
            shutil.rmtree(cls.work, ignore_errors=True)

    def test_a_managed_project_is_created_from_the_wheel(self):
        run = Path(tempfile.mkdtemp(prefix="corvin-wheel-run-"))
        try:
            src = small_catalogue(run / "src")
            write_template(src, "plain", ["core"])
            r = subprocess.run([str(self.venv_py), "-I", str(HERE / "_wheel_projects_driver.py"), str(run), str(src)],
                               capture_output=True, text=True, timeout=600, cwd=str(run), env={**os.environ, "HOME": str(run)})
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
            site = Path(out["hints_file"]).parents[2]
            self.assertNotIn(str(REPO), out["hints_file"], "the driver must load the INSTALLED wheel, not the checkout")
            self.assertIsNone(out["available"], out)
            self.assertEqual(Path(out["gate"]), site / "corvin_core" / "_vendor" / "corvin_operator" / "voice" / "hooks" / "path_gate.py")
            self.assertEqual(out["default_storage"], "managed")
            self.assertEqual(out["plan"][0], 200, out["plan"])
            self.assertEqual(out["create"][0], 201, out["create"])
            checks = {c["id"]: c for c in out["health"][1]["checks"]}
            self.assertTrue(checks["hint_source"]["ok"], checks["hint_source"])
        finally:
            shutil.rmtree(run, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
