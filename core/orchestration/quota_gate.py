"""Shared access to the operator-side license quota counter.

Why this module exists
----------------------
Three orchestration call sites enforce a per-day license quota before doing
paid work (brain tasks, skill_forge, tool_forge). All three reached the
counter through an import path that can never resolve:

    from operator.license.quota_counter import increment_and_check   # x2
    from core.operator.license.quota_counter import increment_and_check

``operator`` is a Python *stdlib* module, so the first form resolves to the
stdlib and raises ``ModuleNotFoundError: 'operator' is not a package``;
``core.operator`` simply does not exist. The consequences were not "quota is
not enforced" but worse:

* ``brain.py`` re-raised the failure, so EVERY brain task aborted before
  starting.
* ``skill_forge_subsystem`` / ``tool_forge_subsystem`` caught it in a broad
  ``except Exception``, so every ``skill_create`` / ``tool_forge`` call
  returned ``{"success": False}`` with a ModuleNotFoundError as its message.

The working convention in this repo (used by ``corvin_console`` and by
``corvin_orchestration/mcp_server.py``) is to put ``operator/`` itself on
``sys.path`` and then import the subtree *bare* -- ``from license.quota_counter
import ...``. Centralising that here keeps the sys.path handling in one place
instead of three, so the next call site cannot reinvent a broken path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional


def _ensure_operator_on_path() -> None:
    """Make ``operator/`` importable as bare top-level packages. Idempotent."""
    # Wheel install: the operator subtrees are vendored under
    # corvin_console/_vendor/operator/ and this bootstrap mirrors them onto
    # sys.path. No-op in a source checkout (the _vendor dir does not exist).
    try:
        from corvin_core._operator_bootstrap import ensure_operator_on_path

        ensure_operator_on_path()
    except ImportError:
        pass

    # Source checkout: operator/ is a real sibling of core/.
    # core/orchestration/quota_gate.py -> parents[2] == repo root
    operator_root = Path(__file__).resolve().parents[2] / "operator"
    if operator_root.is_dir() and str(operator_root) not in sys.path:
        sys.path.insert(0, str(operator_root))


def corvin_home() -> Path:
    """Resolve the CorvinOS data root.

    Honours ``CORVIN_HOME`` and falls back to ``~/.corvin``. The three quota
    call sites hard-coded ``Path.home() / ".corvin"``, which is wrong twice
    over: an operator running against a different root (this repo ships a
    repo-local ``.corvin`` that is the live one) had their quota counted in a
    directory the rest of the install never reads, and a test run incremented
    the REAL operator's daily counters -- a `pytest tests/` run consumed the
    free tier's tool_forge budget for the day.
    """
    return Path(os.environ.get("CORVIN_HOME") or (Path.home() / ".corvin"))


def increment_and_check(
    corvin_home_path: Optional[Path],
    feature: str,
    tenant_id: str,
) -> int:
    """Atomically increment today's quota counter for ``feature``.

    Raises:
        LicenseLimitError: when today's quota for this feature/tenant is
            exhausted. Deliberately propagated -- the quota gate is
            fail-closed, and a caller that swallows this turns a licensing
            boundary into a suggestion.
    """
    _ensure_operator_on_path()
    from license.quota_counter import increment_and_check as _impl

    return _impl(corvin_home_path or corvin_home(), feature, tenant_id)


def get_today_count(
    corvin_home_path: Optional[Path],
    feature: str,
    tenant_id: str,
) -> Any:
    """Read today's counter for ``feature`` without incrementing it."""
    _ensure_operator_on_path()
    from license.quota_counter import get_today_count as _impl

    return _impl(corvin_home_path or corvin_home(), feature, tenant_id)
