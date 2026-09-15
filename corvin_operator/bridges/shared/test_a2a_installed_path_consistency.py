"""Regression: the A2A origin/endpoint default-directory resolvers used to be
FOUR independently-computed implementations (remote_trigger_receiver.py,
remote_trigger_sender.py, a2a_http_server.py, a2a_google_sender.py) plus a
FIFTH, structurally different one in the Console
(core/console/corvin_console/routes/a2a_pair.py, a fixed
``Path(__file__).resolve().parents[3]``).

In a repo checkout they all happen to land on the same directory. In an
INSTALLED (uv-tool / pip / wheel) deployment they do not: the four
bridges/shared modules walk up from their OWN ``__file__`` looking for a
``.corvin_repo``/``plugins`` marker, which does not exist inside a vendored
``corvin_console/_vendor/operator/bridges/shared/`` tree, so they silently
fell back to "a directory next to this file" — a bogus location distinct
from the Console's own ``parents[3]`` answer.

Found live 2026-08-04 debugging a real Windows install: the Console-side
friendship-ack handler wrote a valid origin file to ITS location; the
receiver's OWN OriginRegistry, constructed with no directory of its own,
resolved to the DIFFERENT bogus location and never saw it — every request
from a freshly paired peer failed closed with ``unknown_origin``, forever,
on every installed deployment (not just this one).

Fix: all four bridges/shared resolvers now anchor off the INSTALLED
``corvin_console`` package's own location first (fixed nesting depth
relative to site-packages/venv-root, matching a2a_pair.py's own formula
exactly), falling back to the marker-walk only when ``corvin_console``
itself is not importable at all (the original 2026-08-01/02 "minimal
standalone deployment" scenario these functions were written for).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import remote_trigger_receiver as _rtr  # noqa: E402
import remote_trigger_sender as _rts  # noqa: E402
import a2a_http_server as _a2a_http  # noqa: E402
import a2a_google_sender as _a2a_google  # noqa: E402


def _install_fake_corvin_console(monkeypatch, venv_root: Path) -> None:
    """Simulate `corvin_console` importable from
    ``<venv_root>/Lib/site-packages/corvin_console/__init__.py`` — the exact
    nesting depth a real uv-tool/pip install has, without needing a real
    package on disk."""
    fake_init = venv_root / "Lib" / "site-packages" / "corvin_console" / "__init__.py"
    fake_init.parent.mkdir(parents=True, exist_ok=True)
    fake_init.write_text("", encoding="utf-8")
    fake_module = types.ModuleType("corvin_console")
    fake_module.__file__ = str(fake_init)
    monkeypatch.setitem(sys.modules, "corvin_console", fake_module)


class TestInstalledLayoutConsistency:
    """All four resolvers must agree with a2a_pair.py's own formula
    (Path(__file__ of corvin_console/routes/a2a_pair.py).resolve().parents[3])
    given the SAME installed corvin_console location."""

    def test_all_four_resolvers_agree_under_simulated_install(self, tmp_path, monkeypatch):
        venv_root = tmp_path / "corvinos-venv"
        _install_fake_corvin_console(monkeypatch, venv_root)

        expected_operator_root = venv_root / "operator"

        receiver_origins = _rtr._default_repo_relative("cowork", "remote_origins")
        sender_endpoints = _rts._default_endpoints_dir()
        http_cowork = _a2a_http._default_cowork_dir()
        google_endpoints = _a2a_google._default_google_sender_endpoints_dir()

        assert receiver_origins == expected_operator_root / "cowork" / "remote_origins"
        assert sender_endpoints == expected_operator_root / "cowork" / "remote_endpoints"
        assert http_cowork == expected_operator_root / "cowork"
        assert google_endpoints == expected_operator_root / "cowork" / "remote_endpoints"

        # The load-bearing assertion: sender and receiver must land in the
        # SAME cowork directory, or a friendship-ack write (via the
        # receiver's origins_dir) and a subsequent send (via the sender's
        # endpoints_dir) split across two locations again.
        assert receiver_origins.parent == sender_endpoints.parent
        assert receiver_origins.parent == http_cowork

    def test_falls_back_to_marker_walk_when_corvin_console_unimportable(self, monkeypatch):
        # Simulate corvin_console genuinely absent (the original
        # "minimal standalone deployment" scenario these functions were
        # first written for) — must not raise, must still resolve via the
        # repo-marker walk from this real checkout.
        monkeypatch.setitem(sys.modules, "corvin_console", None)
        result = _rtr._default_repo_relative("cowork", "remote_origins")
        assert result.name == "remote_origins"
        repo_root = result.parents[2]  # remote_origins -> cowork -> operator -> repo_root
        assert (repo_root / ".corvin_repo").exists() or (repo_root / "plugins").is_dir()

    def test_repo_checkout_still_resolves_to_real_repo_root(self):
        # Sanity check against the ACTUAL dev checkout (no mocking) — the
        # fix must not regress the common case.
        result = _rts._default_endpoints_dir()
        assert result.parts[-3:] == ("operator", "cowork", "remote_endpoints")
        repo_root = result.parents[2]
        assert (repo_root / ".corvin_repo").exists() or (repo_root / "plugins").is_dir()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
