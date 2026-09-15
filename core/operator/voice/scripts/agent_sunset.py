"""Agent Sunset Daemon — ADR-0131 M3.

Daily timer: walks all tenant agent charters, evaluates lifecycle state,
emits audit events for state transitions, hard-disables expired agents.

Usage:
  python3 -m corvin.agent_sunset
  (also invoked via corvin-agent-sunset.service / .timer at 04:00)

MUST NOT import anthropic.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import agent_charter as _ac  # noqa: E402
from paths import corvin_home as _corvin_home  # noqa: E402


def _iter_tenant_ids() -> list[str]:
    tenants_dir = _corvin_home() / "tenants"
    if not tenants_dir.exists():
        return ["_default"]
    return [d.name for d in tenants_dir.iterdir() if d.is_dir()]


def run_once(now_date: date | None = None) -> dict[str, int]:
    """Walk all tenants and process charter expirations. Returns counts."""
    if now_date is None:
        now_date = date.today()

    counts: dict[str, int] = {
        "checked": 0,
        "review_pending": 0,
        "review_overdue": 0,
        "pending_sunset": 0,
        "disabled": 0,
        "already_disabled": 0,
    }

    for tenant_id in _iter_tenant_ids():
        try:
            charters = _ac.list_charters(tenant_id)
        except Exception as exc:
            print(f"[agent_sunset] error listing charters for {tenant_id}: {exc}", file=sys.stderr)
            continue

        for charter in charters:
            counts["checked"] += 1
            if charter.disabled:
                counts["already_disabled"] += 1
                continue

            status = _ac.compute_status(charter, now_date=now_date)

            if status == _ac.STATUS_REVIEW_PENDING:
                counts["review_pending"] += 1
                try:
                    days = _ac.days_until(charter.review_date, now_date=now_date)
                    _ac.emit_review_pending(tenant_id, charter.agent_id, days)
                except Exception:
                    pass

            elif status == _ac.STATUS_REVIEW_OVERDUE:
                counts["review_overdue"] += 1
                try:
                    days = (now_date - date.fromisoformat(charter.review_date)).days
                    _ac.emit_review_overdue(tenant_id, charter.agent_id, days)
                except Exception:
                    pass

            elif status == _ac.STATUS_PENDING_SUNSET:
                counts["pending_sunset"] += 1
                try:
                    days = _ac.days_until(charter.sunset_date, now_date=now_date)
                    _ac.emit_pending_sunset(tenant_id, charter.agent_id, days)
                except Exception:
                    pass

            elif status == _ac.STATUS_DISABLED:
                counts["disabled"] += 1
                # Hard-disable: audit-first, then write flag (L16 invariant)
                try:
                    prior_scope = _ac.current_signed_scope(charter) or "none"
                    _ac.emit_sunset(tenant_id, charter.agent_id, charter.kind, prior_scope)
                    charter.disabled = True
                    _ac.save_charter(tenant_id, charter)
                    print(f"[agent_sunset] DISABLED {charter.agent_id} "
                          f"(tenant={tenant_id}, scope={prior_scope})")
                except Exception as exc:
                    print(f"[agent_sunset] error disabling {charter.agent_id}: {exc}",
                          file=sys.stderr)

    return counts


def main() -> None:
    print("[agent_sunset] starting daily sweep")
    counts = run_once()
    print(f"[agent_sunset] done: {counts}")


if __name__ == "__main__":
    main()
