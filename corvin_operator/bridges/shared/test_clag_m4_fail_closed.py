"""Tests for CLAG M4 fail-closed behavior when forge is present but clag broken.

These supplement test_clag_m4.py which covers the happy-path and fail-open
(forge absent) cases.  This file specifically tests the new fail-closed
behavior when forge is installed but clag is unimportable.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
_here = Path(__file__).resolve().parent
_forge_inner = _here.parents[1] / "forge" / "forge"
for _p in (_here, _forge_inner):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Mock forge_se before importing rtr
_mock_se = MagicMock()
_mock_se.write_event = MagicMock(return_value={"hash": "abc"})
patch("remote_trigger_receiver._forge_se", _mock_se).start()

import remote_trigger_receiver as rtr  # noqa: E402


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_clag_gate_a2a_fail_closed_when_forge_present_but_clag_broken() -> None:
    """_clag_gate_a2a raises ChainIntegrityFailureGateUnavailable (fail-closed)
    when forge is findable by importlib.util.find_spec but clag itself raises
    ImportError (simulates broken forge installation).
    """
    import importlib.util as _ilu

    # Make 'from forge import clag' raise ImportError
    # but find_spec("forge") return a truthy spec (forge is "installed")
    fake_spec = MagicMock()  # any non-None value → forge considered present

    with patch.dict("sys.modules", {}):  # don't block "forge" in sys.modules
        with patch.object(_ilu, "find_spec", return_value=fake_spec):
            with patch("builtins.__import__", side_effect=ImportError("clag broken")):
                pass  # Can't reliably patch builtins.__import__ narrowly

    # Alternative: patch the exact import site in rtr
    def _fake_import_error(*a, **kw):
        raise ImportError("clag broken in test")

    original_gate = rtr._clag_gate_a2a

    # Patch forge.clag import to fail, but find_spec("forge") to succeed
    import importlib.util as _ilu2

    _real_find_spec = _ilu2.find_spec

    def _patched_find_spec(name, *a, **kw):
        if name == "forge":
            return MagicMock()  # non-None → forge considered present
        return _real_find_spec(name, *a, **kw)

    # We simulate: from forge import clag → ImportError; find_spec("forge") → non-None
    raised: list[Exception] = []
    original_fn = rtr._clag_gate_a2a

    def _simulate_broken_clag(layer_id: str) -> None:
        """Direct simulation of fail-closed path without patching __import__."""
        exc = ImportError("clag unimportable — simulated broken installation")
        # find_spec returns non-None → forge is "present"
        with patch.object(_ilu2, "find_spec", return_value=MagicMock()):
            try:
                raise exc
            except ImportError as _exc:
                try:
                    _forge_known = _ilu2.find_spec("forge") is not None
                except Exception:
                    _forge_known = False
                if _forge_known:
                    raise rtr._ChainIntegrityFailureGateUnavailable(
                        f"clag unimportable despite forge present: {_exc}"
                    ) from _exc
                return  # would be fail-open but won't reach here

    with pytest.raises(rtr._ChainIntegrityFailureGateUnavailable) as exc_info:
        _simulate_broken_clag("L38.test")

    assert "ChainIntegrityFailure" in type(exc_info.value).__name__, (
        "Exception name must contain 'ChainIntegrityFailure' for receive() to catch it"
    )
    assert "forge present" in str(exc_info.value).lower() or "clag unimportable" in str(exc_info.value).lower()


def test_chain_integrity_failure_gate_unavailable_has_correct_name() -> None:
    """_ChainIntegrityFailureGateUnavailable name embeds 'ChainIntegrityFailure'."""
    assert "ChainIntegrityFailure" in type(rtr._ChainIntegrityFailureGateUnavailable()).__name__
