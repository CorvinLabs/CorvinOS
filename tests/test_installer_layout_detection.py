"""F2 (2026-09-03 installation review): the installer's wheel-vs-checkout
detection must be driven by the REAL layout, not by a single marker file.

`corvinOS/installer/core.py` used to say "source checkout iff pyproject.toml
exists next to corvinOS/". The wheel's old `sources` mapping lifted
core/gateway/pyproject.toml to the site-packages root, so EVERY pip install
was taken for a checkout: step 13 ran npm builds against site-packages and
`corvin-uninstall` suggested `rm -rf <site-packages>`. The existing installer
tests never caught it because they patch `_IS_WHEEL_INSTALL` instead of
exercising the detection.

These tests call the real `_is_source_checkout()` against on-disk layouts
built in tmp_path (no constant patching), plus the live checkout itself.
"""
from __future__ import annotations

from pathlib import Path

from corvinOS.installer import core as installer_core

_REPO = Path(__file__).resolve().parents[1]

_CORVINOS_PYPROJECT = '[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "corvinos"\nversion = "9.9.9"\n'
_GATEWAY_PYPROJECT = '[project]\nname = "corvin-gateway"\nversion = "0.1.0"\n'


def _checkout(root: Path, *, pyproject: str | None = _CORVINOS_PYPROJECT, core: bool = True, operator: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if core:
        (root / "core").mkdir()
    if operator:
        (root / "operator").mkdir()
    (root / "corvinOS" / "installer").mkdir(parents=True)
    return root


def test_real_checkout_is_detected_as_source() -> None:
    assert installer_core._is_source_checkout(_REPO) is True
    assert installer_core._pyproject_project_name(_REPO / "pyproject.toml") == "corvinos"


def test_full_layout_is_source(tmp_path: Path) -> None:
    assert installer_core._is_source_checkout(_checkout(tmp_path / "src")) is True


def test_stray_pyproject_alone_is_wheel(tmp_path: Path) -> None:
    """The exact F2 shape: a foreign pyproject.toml sitting at the site-packages
    root, no core/ or operator/ dirs → wheel."""
    site = _checkout(tmp_path / "site-packages", pyproject=_GATEWAY_PYPROJECT, core=False, operator=False)
    assert installer_core._is_source_checkout(site) is False


def test_stray_pyproject_with_core_but_no_operator_is_wheel(tmp_path: Path) -> None:
    """A wheel install DOES have core/ in site-packages (it is a packaged
    root); it never has operator/. The name check must hold on its own too."""
    site = _checkout(tmp_path / "site-packages", pyproject=_GATEWAY_PYPROJECT, core=True, operator=False)
    assert installer_core._is_source_checkout(site) is False
    site2 = _checkout(tmp_path / "site-packages2", pyproject=_CORVINOS_PYPROJECT, core=True, operator=False)
    assert installer_core._is_source_checkout(site2) is False


def test_no_pyproject_is_wheel(tmp_path: Path) -> None:
    site = _checkout(tmp_path / "site-packages", pyproject=None)
    assert installer_core._is_source_checkout(site) is False


def test_wrong_project_name_is_wheel(tmp_path: Path) -> None:
    site = _checkout(tmp_path / "site-packages", pyproject=_GATEWAY_PYPROJECT)
    assert installer_core._is_source_checkout(site) is False


def test_malformed_pyproject_is_wheel(tmp_path: Path) -> None:
    site = _checkout(tmp_path / "site-packages", pyproject="[project\nname = corvinos\n")
    assert installer_core._is_source_checkout(site) is False


def test_module_constant_reflects_live_layout() -> None:
    """The module-level flag must be derived from the same function — in the
    repo checkout (where this suite runs) that means "not a wheel"."""
    assert installer_core._IS_WHEEL_INSTALL is (not installer_core._is_source_checkout(installer_core._REPO_ROOT))
    assert installer_core._IS_WHEEL_INSTALL is False
