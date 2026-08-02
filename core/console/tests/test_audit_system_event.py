"""Regression test for a real bug: routes/models.py's periodic model-
catalog refresh called console_audit.system_event(...), which did not
exist on the audit module — every call raised AttributeError, silently
swallowed by _refresh_once_impl's broad except, so the audit trail for
model-catalog refresh events was permanently empty and the failure
recurred on every scheduled refresh (reported from a live Windows
instance: "AttributeError: module 'corvin_console.audit' has no
attribute 'system_event' — repeated every 5 minutes").

Run: pytest core/console/tests/test_audit_system_event.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge",
           _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_console import audit as console_audit


def test_system_event_exists_and_is_callable():
    assert hasattr(console_audit, "system_event")
    assert callable(console_audit.system_event)


def test_system_event_matches_real_caller_shape(tmp_path, monkeypatch):
    """Real call shape from routes/models.py's _refresh_once_impl —
    must not raise AttributeError (the reported bug) or anything else."""
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
    console_audit.system_event(
        tenant_id="_test",
        event="model_catalog_refreshed",
        details={
            "provider": "anthropic",
            "model_count": 3,
            "reachable": True,
        },
    )
    console_audit.system_event(
        tenant_id="_test",
        event="model_catalog_refresh_failed",
        details={
            "provider": "anthropic",
            "error": "timeout",
            "reachable": False,
        },
    )


def test_system_event_still_rejects_forbidden_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
    try:
        console_audit.system_event(
            tenant_id="_test",
            event="some_event",
            details={"password": "shouldnotbeloggable"},
        )
        assert False, "expected AuditFieldNotAllowed"
    except console_audit.AuditFieldNotAllowed:
        pass


def test_routes_models_call_sites_use_real_signature():
    """AST check: routes/models.py's two system_event() call sites must
    only pass keyword arguments system_event() actually accepts — catches
    a signature drift silently before it ever runs in production again."""
    import ast
    import inspect

    src = (_REPO / "core" / "console" / "corvin_console" / "routes" / "models.py").read_text()
    tree = ast.parse(src)
    sig_params = set(inspect.signature(console_audit.system_event).parameters)

    call_sites = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "system_event"
        ):
            call_sites += 1
            for kw in node.keywords:
                assert kw.arg in sig_params, (
                    f"routes/models.py passes unknown kwarg {kw.arg!r} to "
                    f"system_event(); real params are {sorted(sig_params)}"
                )
    assert call_sites == 2, f"expected 2 system_event call sites, found {call_sites}"
