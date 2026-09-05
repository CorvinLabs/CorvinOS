"""MethodObservation / PatternRecognition unit tests (ADR-0548 Phase 1, tasks 1.1 + 1.4).

The audit chain is REAL throughout (``core.learning.event_persistence`` writing
to the platform's hash-chained writer), redirected to tmp by the sandbox
fixture. Nothing here mocks the audit trail: the fail-closed behaviour is the
feature under test, and a mock would assert only that the mock was called.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.skills.os_skills.confidence_scorer import ConfidenceScorer
from core.skills.os_skills.method_discovery import (
    MethodDiscovery,
    MethodObservation,
    PatternRecognition,
    WorkstylePattern,
)
from core.skills.os_skills.observability import GENESIS_HASH, MethodAuditSink

TENANT = "tenant_md"
OTHER_TENANT = "tenant_other"
NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    """Temp learning dir + temp audit chain (same shape as the ADR-0314 suite)."""
    import core.paths as _paths

    monkeypatch.setattr(
        _paths, "tenant_learning_dir", lambda t: tmp_path / "tenants" / t / "learning"
    )
    monkeypatch.setattr(
        _paths, "tenant_audit_file", lambda t: tmp_path / "tenants" / t / "audit.jsonl"
    )
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "chain" / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    return tmp_path


def make_observation(**overrides) -> MethodObservation:
    kwargs = dict(
        tenant_id=TENANT,
        task_id="task-1",
        task_type="feature",
        task_complexity=3,
        skill_sequence=["/dialectical", "/loop", "/e2e", "/code-review"],
        skill_latencies_ms=[1523, 342, 1200, 450],
        outcome="success",
        outcome_details={"reason": "all tests pass", "regressions": 0},
        prev_hash=GENESIS_HASH,
        timestamp=NOW,
    )
    kwargs.update(overrides)
    return MethodObservation.create(**kwargs)


# ── MethodObservation: immutability + hashability (constraint 1) ────────────


class TestObservationImmutability:
    def test_is_frozen(self):
        obs = make_observation()
        with pytest.raises(Exception):
            obs.outcome = "failure"  # type: ignore[misc]

    def test_is_hashable(self):
        """Audit-safe means usable as a dict key / set member."""
        obs = make_observation()
        assert hash(obs) is not None
        assert len({obs, make_observation()}) == 1

    def test_lists_are_coerced_to_tuples(self):
        """A caller passing a list must still get a hashable object, not a
        TypeError at hash() time."""
        obs = make_observation(skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2])
        assert isinstance(obs.skill_sequence, tuple)
        assert isinstance(obs.skill_latencies_ms, tuple)
        assert hash(obs)

    def test_outcome_details_roundtrips_through_canonical_json(self):
        obs = make_observation(outcome_details={"b": 2, "a": 1})
        assert obs.outcome_details == {"a": 1, "b": 2}
        assert hash(obs)  # dict payload did not break hashability


# ── MethodObservation: validation (fail-closed) ─────────────────────────────


class TestObservationValidation:
    def test_rejects_unknown_task_type(self):
        with pytest.raises(ValueError, match="task_type"):
            make_observation(task_type="brainstorming")

    def test_rejects_empty_skill_sequence(self):
        with pytest.raises(ValueError, match="skill_sequence"):
            make_observation(skill_sequence=[], skill_latencies_ms=[])

    def test_rejects_latency_length_mismatch(self):
        with pytest.raises(ValueError, match="skill_latencies_ms"):
            make_observation(skill_sequence=["/a", "/b"], skill_latencies_ms=[1])

    @pytest.mark.parametrize("complexity", [0, 6, -1, 2.5])
    def test_rejects_out_of_range_complexity(self, complexity):
        with pytest.raises(ValueError, match="task_complexity"):
            make_observation(task_complexity=complexity)

    def test_rejects_unknown_outcome(self):
        with pytest.raises(ValueError, match="outcome"):
            make_observation(outcome="mostly-ok")

    @pytest.mark.parametrize("bad", ["", "abc", "Z" * 64, "0" * 63])
    def test_rejects_malformed_prev_hash(self, bad):
        with pytest.raises(ValueError, match="prev_hash"):
            make_observation(prev_hash=bad)

    def test_rejects_null_tenant(self):
        """GDPR Art. 5/32: a null tenant must never silently become _default."""
        with pytest.raises(Exception):
            make_observation(tenant_id="")

    def test_rejects_out_of_range_feedback_score(self):
        with pytest.raises(ValueError, match="user_feedback_score"):
            make_observation(user_feedback_score=1.5)

    def test_partial_outcome_does_not_count_as_success(self):
        assert make_observation(outcome="partial").is_success is False
        assert make_observation(outcome="success").is_success is True


# ── MethodObservation: hashing + chain linkage (constraint 3) ───────────────


class TestObservationHashing:
    def test_hash_is_sha256_hex(self):
        obs = make_observation()
        assert len(obs.hash) == 64
        assert obs.hash == obs.compute_hash()

    def test_hash_changes_when_any_field_changes(self):
        base = make_observation()
        for field, value in [
            ("task_type", "bugfix"),
            ("outcome", "failure"),
            ("task_complexity", 5),
            ("skill_sequence", ["/x", "/y", "/z", "/w"]),
        ]:
            assert make_observation(**{field: value}).hash != base.hash

    def test_hash_is_reproducible_across_instances(self):
        assert make_observation().hash == make_observation().hash

    def test_observation_id_is_the_hash(self):
        obs = make_observation()
        assert obs.observation_id == obs.hash

    def test_payload_roundtrip_preserves_hash(self):
        obs = make_observation()
        restored = MethodObservation.from_payload(obs.to_payload())
        assert restored == obs
        assert restored.hash == restored.compute_hash()

    def test_tampered_payload_fails_rehash(self):
        """The tamper-evidence property the whole chain rests on."""
        payload = make_observation(outcome="failure").to_payload()
        payload["outcome"] = "success"
        restored = MethodObservation.from_payload(payload)
        assert restored.hash != restored.compute_hash()


# ── PatternRecognition: stratification (constraint 4) ───────────────────────


class TestPatternRecognition:
    @pytest.fixture
    def recognizer(self):
        return PatternRecognition(scorer=ConfidenceScorer(now=NOW))

    def _series(self, n, *, task_type="feature", outcome="success", seq=None):
        seq = seq or ["/a", "/b", "/c", "/d"]
        return [
            make_observation(
                task_id=f"{task_type}-{i}",
                task_type=task_type,
                outcome=outcome,
                skill_sequence=seq,
                skill_latencies_ms=[10] * len(seq),
                timestamp=NOW - timedelta(days=n - i),
            )
            for i in range(n)
        ]

    def test_same_sequence_different_task_types_never_merge(self, recognizer):
        """Constraint 4 / Attack 3: a feature workflow must not become a
        security recommendation just because the skills matched."""
        obs = self._series(5, task_type="feature") + self._series(5, task_type="security")
        patterns = recognizer.recognize(obs)
        assert len(patterns) == 2
        assert {p.task_type for p, _ in patterns} == {"feature", "security"}
        assert len({p.pattern_id for p, _ in patterns}) == 2

    def test_different_sequences_same_task_type_are_separate_patterns(self, recognizer):
        obs = self._series(3, seq=["/a", "/b", "/c"]) + self._series(3, seq=["/x", "/y", "/z"])
        assert len(recognizer.recognize(obs)) == 2

    def test_success_rate_counts_only_success(self, recognizer):
        obs = self._series(3, outcome="success") + self._series(1, outcome="partial")
        # Same key, so one bucket of 4 with 3 successes.
        (pattern, _), = recognizer.recognize(obs)
        assert pattern.observation_count == 4
        assert pattern.success_rate == pytest.approx(0.75)

    def test_pattern_id_is_deterministic_and_tenant_scoped(self):
        a = WorkstylePattern.make_id(TENANT, "feature", ["/a", "/b"])
        b = WorkstylePattern.make_id(TENANT, "feature", ["/a", "/b"])
        c = WorkstylePattern.make_id(OTHER_TENANT, "feature", ["/a", "/b"])
        assert a == b
        assert a != c
        assert a.startswith("feature-")

    def test_sequence_order_matters(self):
        assert WorkstylePattern.make_id(TENANT, "feature", ["/a", "/b"]) != WorkstylePattern.make_id(
            TENANT, "feature", ["/b", "/a"]
        )

    def test_first_and_last_observed_span_the_series(self, recognizer):
        (pattern, _), = recognizer.recognize(self._series(5))
        assert pattern.first_observed < pattern.last_observed
        assert pattern.observation_count == 5
        assert len(pattern.observation_ids) == 5

    def test_results_sorted_by_confidence_descending(self, recognizer):
        obs = self._series(30, task_type="feature") + self._series(2, task_type="bugfix")
        scored = recognizer.recognize(obs)
        confidences = [p.confidence_score for p, _ in scored]
        assert confidences == sorted(confidences, reverse=True)

    def test_min_observations_filters_weak_buckets(self, recognizer):
        obs = self._series(5, task_type="feature") + self._series(1, task_type="bugfix")
        assert len(recognizer.recognize(obs, min_observations=3)) == 1

    def test_mixed_tenants_raise(self, recognizer):
        obs = self._series(2) + [make_observation(tenant_id=OTHER_TENANT)]
        with pytest.raises(ValueError, match="multiple tenants"):
            recognizer.recognize(obs)

    def test_pattern_is_frozen_and_hashable(self, recognizer):
        (pattern, _), = recognizer.recognize(self._series(3))
        assert hash(pattern) is not None
        with pytest.raises(Exception):
            pattern.confidence_score = 0.99  # type: ignore[misc]

    def test_empty_input_yields_no_patterns(self, recognizer):
        assert recognizer.recognize([]) == []


# ── MethodDiscovery: event handling against the real audit chain ────────────


class TestMethodDiscoveryEventHandling:
    async def test_observe_writes_audit_and_advances_chain(self, sandbox):
        md = MethodDiscovery(TENANT)
        assert md.sink.chain_head() == GENESIS_HASH

        first = await md.observe(
            task_id="t1", task_type="feature", task_complexity=3,
            skill_sequence=["/a", "/b", "/c", "/d"], skill_latencies_ms=[1, 2, 3, 4],
            outcome="success",
        )
        assert md.sink.chain_head() == first.hash

        second = await md.observe(
            task_id="t2", task_type="feature", task_complexity=3,
            skill_sequence=["/a", "/b", "/c", "/d"], skill_latencies_ms=[1, 2, 3, 4],
            outcome="success",
        )
        assert second.prev_hash == first.hash
        assert md.sink.chain_head() == second.hash

    async def test_observation_reaches_the_core_hash_chain(self, sandbox):
        chain = sandbox / "chain" / "audit.jsonl"
        md = MethodDiscovery(TENANT)
        await md.observe(
            task_id="t1", task_type="feature", task_complexity=3,
            skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2], outcome="success",
        )
        records = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        assert any(r.get("event") == "learning.method.observation" or
                   r.get("event_type") == "learning.method.observation" for r in records)

    async def test_forked_chain_is_rejected(self, sandbox):
        """An observation that does not link to the head must not be recorded."""
        md = MethodDiscovery(TENANT)
        await md.observe(
            task_id="t1", task_type="feature", task_complexity=3,
            skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2], outcome="success",
        )
        stale = make_observation(prev_hash=GENESIS_HASH, task_id="fork")
        with pytest.raises(ValueError, match="chain fork"):
            await md.sink.record_observation(stale)

    async def test_cross_tenant_observation_rejected(self, sandbox):
        md = MethodDiscovery(TENANT)
        foreign = MethodObservation.create(
            tenant_id=OTHER_TENANT, task_id="x", task_type="feature", task_complexity=1,
            skill_sequence=["/a"], skill_latencies_ms=[1], outcome="success",
            prev_hash=md.sink.chain_head(),
        )
        with pytest.raises(ValueError, match="tenant"):
            await md.sink.record_observation(foreign)

    async def test_null_tenant_rejected_before_any_io(self, sandbox):
        with pytest.raises(Exception):
            MethodDiscovery("")

    async def test_discover_announces_only_above_threshold(self, sandbox):
        md = MethodDiscovery(TENANT)
        for i in range(3):
            await md.observe(
                task_id=f"t{i}", task_type="feature", task_complexity=3,
                skill_sequence=["/a", "/b", "/c", "/d"], skill_latencies_ms=[1, 2, 3, 4],
                outcome="success",
            )
        assert await md.discover() == []  # N=3 -> sample boost 0.60, far too low

        for i in range(3, 30):
            await md.observe(
                task_id=f"t{i}", task_type="feature", task_complexity=3,
                skill_sequence=["/a", "/b", "/c", "/d"], skill_latencies_ms=[1, 2, 3, 4],
                outcome="success",
            )
        newly = await md.discover()
        assert len(newly) == 1
        assert newly[0][0].confidence_score >= 0.78

    async def test_discovery_is_announced_at_most_once(self, sandbox):
        md = MethodDiscovery(TENANT)
        for i in range(30):
            await md.observe(
                task_id=f"t{i}", task_type="feature", task_complexity=3,
                skill_sequence=["/a", "/b", "/c", "/d"], skill_latencies_ms=[1, 2, 3, 4],
                outcome="success",
            )
        assert len(await md.discover()) == 1
        assert await md.discover() == []

    async def test_user_confirmation_surfaces_a_sub_threshold_pattern(self, sandbox):
        """Constraint 4: an explicit human 'yes' outranks the statistics."""
        md = MethodDiscovery(TENANT)
        for i in range(3):
            await md.observe(
                task_id=f"t{i}", task_type="feature", task_complexity=2,
                skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2], outcome="success",
            )
        assert await md.discover() == []

        (pattern, _), = await md.current_patterns()
        md.confirm_pattern(pattern.pattern_id)
        newly = await md.discover()
        assert len(newly) == 1
        assert newly[0][0].user_confirmed is True
        assert newly[0][0].confidence_score < 0.78

    async def test_patterns_snapshot_is_written_and_derived(self, sandbox):
        md = MethodDiscovery(TENANT)
        await md.observe(
            task_id="t1", task_type="feature", task_complexity=3,
            skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2], outcome="success",
        )
        await md.discover()
        snapshot = json.loads(md._patterns_file.read_text())
        assert snapshot["tenant_id"] == TENANT
        assert len(snapshot["patterns"]) == 1
        assert "confidence_derivation" in snapshot["patterns"][0]

        md._patterns_file.unlink()  # derived cache: losing it loses nothing
        assert len(await md.current_patterns()) == 1

    async def test_audit_backend_failure_records_nothing(self, sandbox, monkeypatch):
        """Fail-closed: no chain write -> no observation, no head advance."""
        from core.learning import event_persistence

        md = MethodDiscovery(TENANT)
        monkeypatch.setattr(
            event_persistence,
            "_resolve_core_audit",
            lambda: (_ for _ in ()).throw(RuntimeError("core audit writer unavailable")),
        )
        with pytest.raises(RuntimeError):
            await md.observe(
                task_id="t1", task_type="feature", task_complexity=3,
                skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2], outcome="success",
            )
        assert md.sink.chain_head() == GENESIS_HASH

    async def test_tenants_are_isolated(self, sandbox):
        a = MethodDiscovery(TENANT)
        b = MethodDiscovery(OTHER_TENANT, sink=MethodAuditSink(OTHER_TENANT))
        await a.observe(
            task_id="t1", task_type="feature", task_complexity=3,
            skill_sequence=["/a", "/b"], skill_latencies_ms=[1, 2], outcome="success",
        )
        assert len(await a.load_observations()) == 1
        assert await b.load_observations() == []
        assert b.sink.chain_head() == GENESIS_HASH
