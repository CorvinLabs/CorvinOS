"""ADR-0232 "Phase 1B" privacy skeleton — defused (adversarial review 2026-09-27).

``core.compliance.privacy_subsystems`` held duplicates of four mandatory
mechanisms (in-memory consent, regex flow guard, threshold house rules, an
erasure orchestrator that deleted nothing and reported success). Each now
refuses to construct and names the canonical implementation — the same module
the boot tripwire asserts. These tests pin that the duplicates cannot run and
that the canonical modules they point at exist.
"""

from pathlib import Path

import pytest

from core.compliance.privacy_subsystems import (
    ConsentGate,
    ConsentRecord,
    DataClassification,
    ErasureOrchestrator,
    FlowGuard,
    HouseRules,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("cls, canonical", [
    (ConsentGate, "corvin_operator/bridges/shared/consent.py"),
    (FlowGuard, "corvin_operator/bridges/shared/data_classification.py"),
    (HouseRules, "corvin_operator/bridges/shared/house_rules.py"),
    (ErasureOrchestrator, "corvin_operator/bridges/shared/erasure_orchestrator.py"),
])
def test_duplicate_mechanism_refuses_and_names_the_canonical_one(cls, canonical):
    with pytest.raises(NotImplementedError) as exc:
        cls(object())
    assert canonical in str(exc.value)
    assert (REPO / canonical).is_file(), f"named canonical module {canonical} must exist"


def test_erasure_duplicate_cannot_report_success():
    """The old orchestrator returned True for an erasure it never performed."""
    with pytest.raises(NotImplementedError):
        ErasureOrchestrator(None).process_erasure_request("default", "user1")  # pragma: no cover


def test_value_types_stay_usable():
    assert DataClassification.PII.value == "pii"
    rec = ConsentRecord(tenant_id="default", user_id="u", purpose="analytics",
                        granted_at="2000-01-01T00:00:00+00:00", ttl_seconds=1)
    assert rec.is_valid() is False  # long expired
