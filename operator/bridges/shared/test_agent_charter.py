"""Unit tests for agent_charter.py (ADR-0131).

Run:
  python3 -m pytest operator/bridges/shared/test_agent_charter.py -v
  or:
  python3 operator/bridges/shared/test_agent_charter.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve().parent
if str(_THIS) not in sys.path:
    sys.path.insert(0, str(_THIS))

import agent_charter as _ac

_PASS = 0
_FAIL = 0


def t(name: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        print(f"  PASS  {name}")
        _PASS += 1
    else:
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        _FAIL += 1


def _sample_data(**overrides: Any) -> dict[str, Any]:
    today = date.today()
    d: dict[str, Any] = {
        "agent_id":        "forge_tool:project:lead-scoring",
        "name":            "Lead Scoring Agent",
        "kind":            "forge_tool",
        "scope":           "project",
        "problem":         "Manual process",
        "success_metric":  "≤ 7h/week",
        "baseline":        12.0,
        "target":          7.0,
        "unit":            "h/week",
        "it_owner":        "admin:alice",
        "business_owner":  "member:bob",
        "compliance_owner": "admin:carol",
        "created_at":      today.isoformat(),
        "review_date":     (today + timedelta(days=180)).isoformat(),
        "sunset_date":     (today + timedelta(days=365)).isoformat(),
        "data_class":      "INTERNAL",
        "egress_zone":     "eu_cloud",
        "engine_allowlist": ["claude_code"],
        "version":         1,
    }
    d.update(overrides)
    return d


def case_validate_ok() -> None:
    print("=== validate_charter — valid ===")
    errors = _ac.validate_charter(_sample_data())
    t("no errors on valid charter", errors == [])


def case_validate_missing_fields() -> None:
    print("=== validate_charter — missing fields ===")
    data = _sample_data()
    del data["it_owner"]
    errors = _ac.validate_charter(data)
    t("missing it_owner caught", any("it_owner" in e for e in errors))

    data = _sample_data()
    del data["review_date"]
    errors = _ac.validate_charter(data)
    t("missing review_date caught", any("review_date" in e for e in errors))


def case_validate_bad_kind() -> None:
    print("=== validate_charter — bad kind ===")
    errors = _ac.validate_charter(_sample_data(kind="bad_kind"))
    t("bad kind caught", any("kind" in e for e in errors))


def case_validate_bad_dates() -> None:
    print("=== validate_charter — date constraints ===")
    today = date.today()
    # sunset too close to review (must be > review + 14d)
    data = _sample_data(
        review_date=(today + timedelta(days=30)).isoformat(),
        sunset_date=(today + timedelta(days=35)).isoformat(),
    )
    errors = _ac.validate_charter(data)
    t("sunset < review+14d caught", any("sunset_date" in e for e in errors))


def case_validate_bad_agent_id() -> None:
    print("=== validate_charter — bad agent_id ===")
    errors = _ac.validate_charter(_sample_data(agent_id="bad id with spaces!"))
    t("bad agent_id caught", any("agent_id" in e for e in errors))

    # valid pattern
    errors = _ac.validate_charter(_sample_data(agent_id="skill:user:my-tool"))
    t("valid agent_id passes", errors == [])


def case_compute_status() -> None:
    print("=== compute_status ===")
    today = date.today()

    # Active: dates well in future
    data = _sample_data(
        review_date=(today + timedelta(days=60)).isoformat(),
        sunset_date=(today + timedelta(days=200)).isoformat(),
    )
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    t("active status", _ac.compute_status(charter, now_date=today) == _ac.STATUS_ACTIVE)

    # Review pending: within 14d window
    data = _sample_data(
        review_date=(today + timedelta(days=10)).isoformat(),
        sunset_date=(today + timedelta(days=200)).isoformat(),
    )
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    t("review_pending status (10d before review)", _ac.compute_status(charter, now_date=today) == _ac.STATUS_REVIEW_PENDING)

    # Review overdue
    data = _sample_data(
        review_date=(today - timedelta(days=5)).isoformat(),
        sunset_date=(today + timedelta(days=200)).isoformat(),
    )
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    t("review_overdue status", _ac.compute_status(charter, now_date=today) == _ac.STATUS_REVIEW_OVERDUE)

    # Pending sunset (past review + 14d)
    data = _sample_data(
        review_date=(today - timedelta(days=20)).isoformat(),
        sunset_date=(today + timedelta(days=30)).isoformat(),
    )
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    t("pending_sunset status", _ac.compute_status(charter, now_date=today) == _ac.STATUS_PENDING_SUNSET)

    # Disabled by date
    data = _sample_data(
        review_date=(today - timedelta(days=60)).isoformat(),
        sunset_date=(today - timedelta(days=1)).isoformat(),
    )
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    t("disabled status (past sunset_date)", _ac.compute_status(charter, now_date=today) == _ac.STATUS_DISABLED)

    # Disabled by flag
    data = _sample_data(
        review_date=(today + timedelta(days=60)).isoformat(),
        sunset_date=(today + timedelta(days=200)).isoformat(),
    )
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": True})
    t("disabled status (flag set)", _ac.compute_status(charter, now_date=today) == _ac.STATUS_DISABLED)


def case_sign_offs() -> None:
    print("=== sign-off logic ===")
    data = _sample_data()
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})

    # No signs yet
    t("no role signed initially", not _ac.has_role_signed(charter, "it"))
    t("current_signed_scope is None", _ac.current_signed_scope(charter) is None)

    # IT signs for project scope
    ok, reason = _ac.can_sign_for_scope(charter, "it", "project")
    t("IT can sign for project", ok, reason)

    charter.sign_offs.append(_ac.SignOff(role="it", signer="alice", signed_at="2026-06-15"))
    t("IT signed", _ac.has_role_signed(charter, "it"))
    t("current_signed_scope = project", _ac.current_signed_scope(charter) == "project")

    # Business can now sign for user scope
    ok, reason = _ac.can_sign_for_scope(charter, "business", "user")
    t("Business can sign for user after IT", ok, reason)

    # Compliance cannot sign for tenant_wide without Business
    ok, reason = _ac.can_sign_for_scope(charter, "compliance", "tenant_wide")
    t("Compliance blocked without Business", not ok)

    # Cannot sign already-signed role
    ok, reason = _ac.can_sign_for_scope(charter, "it", "project")
    t("IT cannot sign again", not ok)


def case_crud() -> None:
    print("=== load/save/list ===")
    import os
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        try:
            data = _sample_data()
            charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})

            # Save and reload
            _ac.save_charter("_default", charter)
            loaded = _ac.load_charter("_default", charter.agent_id)
            t("loaded charter is not None", loaded is not None)
            if loaded:
                t("agent_id preserved", loaded.agent_id == charter.agent_id)
                t("name preserved", loaded.name == charter.name)
                t("baseline preserved", loaded.baseline == charter.baseline)

            # List
            all_charters = _ac.list_charters("_default")
            t("list returns 1 charter", len(all_charters) == 1)

            # Load non-existent
            missing = _ac.load_charter("_default", "forge_tool:project:does-not-exist")
            t("missing charter returns None", missing is None)
        finally:
            del os.environ["CORVIN_HOME"]


def case_path_traversal() -> None:
    print("=== path-traversal protection ===")
    import os
    import re
    sanitize = re.compile(r"[^A-Za-z0-9_\-]")
    malicious_ids = [
        "../../../etc/passwd",
        "forge_tool:project:../../secrets",
        "skill:user:\x00null",
        "forge_tool:project:..\\windows\\system32",
        "skill:tenant_wide:lead scoring!",
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        try:
            for agent_id in malicious_ids:
                path = _ac._charter_path("_default", agent_id)
                # Must not escape charters directory
                charters_dir = _ac._charters_dir("_default")
                t(
                    f"path stays inside charters_dir for {agent_id!r}",
                    str(path).startswith(str(charters_dir)),
                )
                t(
                    f"path has no '..' for {agent_id!r}",
                    ".." not in path.parts,
                )
        finally:
            del os.environ["CORVIN_HOME"]


def case_compliance_pre_check() -> None:
    print("=== compliance_pre_check ===")
    data = _sample_data(data_class="SECRET", egress_zone="eu_cloud")
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    failures = _ac.compliance_pre_check(charter)
    t("SECRET + eu_cloud fails check", "data_class_egress_mismatch" in failures)

    data = _sample_data(data_class="INTERNAL", egress_zone="eu_cloud", engine_allowlist=[])
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    failures = _ac.compliance_pre_check(charter)
    t("empty engine_allowlist fails check", "engine_allowlist_empty" in failures)

    data = _sample_data(data_class="INTERNAL", egress_zone="eu_cloud")
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    failures = _ac.compliance_pre_check(charter)
    t("valid charter passes all checks", failures == [])


def case_save_exclusive() -> None:
    print("=== save_charter exclusive (TOCTOU guard) ===")
    import os
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        try:
            data = _sample_data()
            charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})

            # First exclusive save must succeed
            _ac.save_charter("_default", charter, exclusive=True)
            t("first exclusive save succeeds", _ac.load_charter("_default", charter.agent_id) is not None)

            # Second exclusive save must raise FileExistsError
            raised = False
            try:
                _ac.save_charter("_default", charter, exclusive=True)
            except FileExistsError:
                raised = True
            t("second exclusive save raises FileExistsError", raised)

            # Non-exclusive save over existing file must still work
            charter2 = _ac._charter_from_dict({**data, "version": 2, "sign_offs": [], "disabled": False})
            _ac.save_charter("_default", charter2)
            loaded = _ac.load_charter("_default", charter2.agent_id)
            t("non-exclusive overwrite succeeds", loaded is not None and loaded.version == 2)
        finally:
            del os.environ["CORVIN_HOME"]


def case_compliance_local_egress() -> None:
    print("=== compliance_pre_check — local egress (L34 alignment) ===")
    data = _sample_data(data_class="SECRET", egress_zone="local")
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    failures = _ac.compliance_pre_check(charter)
    t("SECRET + local passes egress check", "data_class_egress_mismatch" not in failures)

    data = _sample_data(data_class="CONFIDENTIAL", egress_zone="local")
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    failures = _ac.compliance_pre_check(charter)
    t("CONFIDENTIAL + local passes egress check", "confidential_requires_local_egress" not in failures)

    data = _sample_data(data_class="CONFIDENTIAL", egress_zone="eu_cloud")
    charter = _ac._charter_from_dict({**data, "sign_offs": [], "disabled": False})
    failures = _ac.compliance_pre_check(charter)
    t("CONFIDENTIAL + eu_cloud fails egress check", "confidential_requires_local_egress" in failures)


def main() -> None:
    global _PASS, _FAIL
    print("\ntest_agent_charter.py\n")
    case_validate_ok()
    case_validate_missing_fields()
    case_validate_bad_kind()
    case_validate_bad_dates()
    case_validate_bad_agent_id()
    case_compute_status()
    case_sign_offs()
    case_crud()
    case_path_traversal()
    case_compliance_pre_check()
    case_save_exclusive()
    case_compliance_local_egress()
    print(f"\n{'='*40}")
    print(f"  PASS: {_PASS}  FAIL: {_FAIL}")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
