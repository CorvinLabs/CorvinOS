"""Regression tests for the CLAG fail-closed hardening (consent + disclosure).

Before this change, ``_clag_gate`` swallowed *any* ``ImportError`` and returned
silently — meaning the ADR-0133 chain-integrity pre-check could disable itself
without a trace if the ``clag`` module ever became unimportable (the exact
self-modification risk CLAG exists to prevent). These tests pin the new
behaviour:

  * forge package present but ``clag`` unimportable -> raise (fail-CLOSED)
  * the raised type name contains ``ChainIntegrityFailure`` so substring-based
    call sites treat it as an integrity failure
  * ``is_granted`` fail-closes (returns ``(False, "chain-integrity-failed")``)
    when the gate raises
"""
import builtins
import importlib

import consent  # noqa: E402
import disclosure  # noqa: E402


def _block_clag_import(monkeypatch):
    """Force ``from clag import ...`` to raise ImportError, leave others intact."""
    _real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "clag":
            raise ImportError("simulated: clag unimportable")
        return _real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)


def test_consent_clag_gate_fails_closed_when_clag_unimportable(monkeypatch):
    """forge dir exists (repo checkout) + clag import fails -> raise, never silent."""
    _block_clag_import(monkeypatch)
    raised = None
    try:
        consent._clag_gate("L16.consent_gate")
    except Exception as exc:  # noqa: BLE001
        raised = exc
    assert raised is not None, "gate must NOT fail-open silently when forge is present"
    assert "ChainIntegrityFailure" in type(raised).__name__, (
        "exception name must embed ChainIntegrityFailure for substring detection"
    )


def test_disclosure_clag_gate_fails_closed_when_clag_unimportable(monkeypatch):
    _block_clag_import(monkeypatch)
    raised = None
    try:
        disclosure._clag_gate("L19.disclosure_gate")
    except Exception as exc:  # noqa: BLE001
        raised = exc
    assert raised is not None
    assert "ChainIntegrityFailure" in type(raised).__name__


def test_is_granted_fails_closed_when_gate_raises(monkeypatch):
    """End-to-end: a raising gate must deny consent, not allow it."""
    def _boom(_layer_id):
        raise consent.ChainIntegrityFailureGateUnavailable("simulated")

    monkeypatch.setattr(consent, "_clag_gate", _boom)
    granted, reason = consent.is_granted("discord", "chatX", "uid-123")
    assert granted is False
    assert reason == "chain-integrity-failed"


if __name__ == "__main__":
    import sys
    sys.exit(importlib.import_module("pytest").main([__file__, "-q"]))
