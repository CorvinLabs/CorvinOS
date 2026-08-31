"""Regression: `corvin serve` / `corvin-serve` failed with `ModuleNotFoundError:
No module named 'ops'` on a real, fresh Windows install (2026-08-04 live
report), even though every `corvin*` console script is `ops.launcher....:main`
(pyproject.toml [project.scripts]) and `ops` has been declared a wheel package
(`packages = ["corvinOS", "core", "ops"]`) since 2026-06-29.

Root cause: `ops/` and `ops/launcher/` were PEP 420 implicit namespace
packages -- no `__init__.py` at either level (only the nested
`ops/launcher/corvin/` had one). That resolved fine under plain `pip install`
and `uv tool install` on Linux (verified directly against the live PyPI
0.10.109 wheel in this session), but implicit namespace packages depend on
`sys.path` directory-scanning behavior that is documented to differ across
platforms/importers -- explicit `__init__.py` is the standard fix to make
`ops`/`ops.launcher` regular (unambiguous) packages instead.

Fixing the missing `__init__.py` files surfaced a SECOND, structurally more
dangerous gap while verifying the fix: this repo's build hook ships only
`git ls-files`-tracked content (see test_hatch_build_vendor_ignore.py's own
history -- the untracked-audio-file incident that motivated it). A freshly
created `ops/__init__.py` was silently ABSENT from a real `uv build --wheel`
output until `git add`ed. Any future new top-level file in a packaged
directory that isn't staged before a release build vanishes from the wheel
with no error anywhere in the pipeline. This test builds a REAL wheel (not a
mock) and inspects its real contents, closing exactly the gap that let this
ship for over a month undetected.

Run: python3 -m pytest tests/test_ops_package_ships_complete.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _git_tracked(path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        cwd=_REPO, capture_output=True, text=True,
    )
    return result.returncode == 0


class TestSourceTree:
    """Cheap, always-on: catches "forgot to git add" before any build runs."""

    def test_ops_init_exists_on_disk(self) -> None:
        assert (_REPO / "ops" / "__init__.py").is_file()

    def test_ops_launcher_init_exists_on_disk(self) -> None:
        assert (_REPO / "ops" / "launcher" / "__init__.py").is_file()

    def test_ops_init_is_git_tracked(self) -> None:
        assert _git_tracked("ops/__init__.py"), (
            "ops/__init__.py exists but is not `git add`ed -- it will be "
            "silently ABSENT from the next release wheel (the build hook "
            "ships only git-tracked content)."
        )

    def test_ops_launcher_init_is_git_tracked(self) -> None:
        assert _git_tracked("ops/launcher/__init__.py"), (
            "ops/launcher/__init__.py exists but is not `git add`ed -- it "
            "will be silently ABSENT from the next release wheel."
        )


@pytest.mark.skipif(
    subprocess.run([sys.executable, "-c", "import hatchling"], capture_output=True).returncode != 0,
    reason="hatchling (build backend) not installed in this environment",
)
class TestRealWheelContent:
    """Expensive, real: actually builds the wheel and inspects it -- the
    exact step that was previously only exercised manually (see
    test_hatch_build_vendor_ignore.py's docstring) and missed this gap for
    over a month."""

    @classmethod
    def setup_class(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="corvinos-wheel-build-")
        result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", cls._tmpdir.name],
            cwd=_REPO, capture_output=True, text=True, timeout=300,
        )
        cls._build_result = result
        cls._wheel_path = None
        if result.returncode == 0:
            wheels = list(Path(cls._tmpdir.name).glob("*.whl"))
            if wheels:
                cls._wheel_path = wheels[0]

    @classmethod
    def teardown_class(cls) -> None:
        cls._tmpdir.cleanup()

    def test_build_succeeded(self) -> None:
        assert self._build_result.returncode == 0, (
            self._build_result.stdout + self._build_result.stderr
        )
        assert self._wheel_path is not None, "uv build did not produce a .whl file"

    def test_wheel_contains_ops_init(self) -> None:
        if self._wheel_path is None:
            pytest.skip("wheel build failed -- see test_build_succeeded")
        with zipfile.ZipFile(self._wheel_path) as z:
            names = set(z.namelist())
        assert "ops/__init__.py" in names
        assert "ops/launcher/__init__.py" in names
        assert "ops/launcher/corvin/__init__.py" in names

    def test_wheel_contains_every_scripted_entry_module(self) -> None:
        """Every ops.launcher.* module referenced by a [project.scripts]
        entry point in pyproject.toml must actually be in the wheel --
        the exact class of gap that broke `corvin serve` end to end."""
        if self._wheel_path is None:
            pytest.skip("wheel build failed -- see test_build_succeeded")
        pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
        import re
        entry_modules = set(
            re.findall(r'=\s*"(ops\.[\w.]+):', pyproject)
        )
        assert entry_modules, "no ops.* console-script entries found in pyproject.toml"
        with zipfile.ZipFile(self._wheel_path) as z:
            names = set(z.namelist())
        for module in entry_modules:
            expected_path = module.replace(".", "/") + ".py"
            assert expected_path in names, (
                f"{module} is a [project.scripts] entry point but "
                f"{expected_path} is missing from the wheel"
            )

    def test_fresh_venv_can_import_ops_launcher_corvin_cli(self) -> None:
        """The actual failure mode reported live: `from ops.launcher.corvin.cli
        import main` inside the installed console-script wrapper."""
        if self._wheel_path is None:
            pytest.skip("wheel build failed -- see test_build_succeeded")
        with tempfile.TemporaryDirectory(prefix="corvinos-venv-") as venv_dir:
            subprocess.run(
                [sys.executable, "-m", "venv", venv_dir],
                check=True, capture_output=True, timeout=60,
            )
            venv_python = Path(venv_dir) / "bin" / "python"
            if not venv_python.exists():
                venv_python = Path(venv_dir) / "Scripts" / "python.exe"
            install = subprocess.run(
                [str(venv_python), "-m", "pip", "install", "--no-deps", "--quiet", str(self._wheel_path)],
                capture_output=True, text=True, timeout=120,
            )
            assert install.returncode == 0, install.stdout + install.stderr
            check = subprocess.run(
                [str(venv_python), "-c", "from ops.launcher.corvin.cli import main; print('OK')"],
                capture_output=True, text=True, timeout=30,
            )
            assert check.returncode == 0, check.stdout + check.stderr
            assert "OK" in check.stdout


if __name__ == "__main__":
    import unittest
    unittest.main()
