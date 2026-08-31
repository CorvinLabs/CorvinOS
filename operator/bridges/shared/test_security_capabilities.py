"""Per-subtask E2E — ADR-0141 Tier 3: SecurityCapabilityRegistry.

Covers the structural contract:
  * register/assert round-trip and CapabilityMissingError shape
  * each in-process security layer self-registers at import time
  * bootstrap_core_capabilities() registers the full mandatory set
    (incl. the out-of-process path_gate by file presence)
  * module_self_hash() shape + missing-file behaviour

Runnable standalone: ``python3 operator/bridges/shared/test_security_capabilities.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


import security_capabilities as sc  # noqa: E402


def test_register_assert_roundtrip() -> None:
    sc._clear_registry()
    sc.register_capability("path_gate", version="2.1", file_hash="sha256:abc")
    rec = sc.get_capability("path_gate")
    t("register stores record", rec is not None and rec.version == "2.1")
    t("is_registered true", sc.is_registered("path_gate"))
    # assert against a single-name list passes
    sc.assert_capabilities_present(["path_gate"])
    t("assert passes when present", True)


def test_assert_raises_on_missing() -> None:
    sc._clear_registry()
    sc.register_capability("audit", version="3.0")
    raised = None
    try:
        sc.assert_capabilities_present(["audit", "egress_gate", "consent_gate"])
    except sc.CapabilityMissingError as e:
        raised = e
    t("CapabilityMissingError raised", raised is not None)
    t(
        "missing list is sorted + correct",
        raised is not None and raised.missing == ["consent_gate", "egress_gate"],
        detail=str(raised.missing if raised else None),
    )


def test_module_self_hash() -> None:
    h = sc.module_self_hash(__file__)
    t("self-hash has sha256: prefix", h.startswith("sha256:") and len(h) == 7 + 64)
    t("missing file -> empty", sc.module_self_hash(REPO / "does-not-exist-xyz") == "")


def test_layer_self_registration_on_import() -> None:
    sc._clear_registry()
    # Importing a layer module must trigger its module-level register_capability.
    import data_classification  # noqa: F401
    import egress_gate  # noqa: F401
    import consent  # noqa: F401
    t("data_classification self-registered", sc.is_registered("data_classification"))
    t("egress_gate self-registered", sc.is_registered("egress_gate"))
    t("consent_gate self-registered", sc.is_registered("consent_gate"))


def test_bootstrap_full_set() -> None:
    sc._clear_registry()
    state = sc.bootstrap_core_capabilities()
    missing = [k for k, v in state.items() if not v]
    t(
        "bootstrap registers all mandatory capabilities",
        not missing,
        detail=f"missing={missing}",
    )
    # path_gate is out-of-process — must be registered by file presence.
    pg = sc.get_capability("path_gate")
    t("path_gate registered by presence", pg is not None)
    t(
        "path_gate version parsed from file",
        pg is not None and pg.version == "2.1",
        detail=str(pg.version if pg else None),
    )
    t(
        "path_gate file_hash populated",
        pg is not None and pg.file_hash.startswith("sha256:"),
    )


def main() -> int:
    test_register_assert_roundtrip()
    test_assert_raises_on_missing()
    test_module_self_hash()
    test_layer_self_registration_on_import()
    test_bootstrap_full_set()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
