"""Phase 1 Gate — E2E reachability proof for Method Discovery (ADR-0548).

Drives the real subsystem end to end: real ``MethodDiscovery``, real ADR-0314
``EventStore``, real platform hash-chained audit writer (redirected to tmp).
Nothing in the write path is mocked — the gate is specifically about whether
observations reach the chain, so a mocked chain would prove nothing.

The gate asks for four things:
  1. completed tasks emit ``method_observation`` events;
  2. 5+ patterns are discovered across 50 test tasks, each at confidence >= 0.78;
  3. the audit chain verifies (hash links intact, tampering detected);
  4. the dashboard endpoint can serve what was discovered.

Sample-size note, load-bearing for reading these tests: confidence >= 0.78
constrains how the 50 tasks may be *distributed*, not just how many there are.
Under the ADR-0548 formula a pattern needs N >= 10 observations to clear 0.78
(at N=5 the ceiling for ANY pattern is 0.95*0.90*0.80 = 0.684 — the sample-size
boost alone forbids it). So the 50 tasks are 5 task types x 10 observations,
which is the smallest distribution that satisfies the gate as stated. Spreading
the same 50 tasks over 10 patterns would yield zero discoveries.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.skills.os_skills.confidence_scorer import DISCOVERY_THRESHOLD
from core.skills.os_skills.method_discovery import MethodDiscovery
from core.skills.os_skills.observability import GENESIS_HASH

TENANT = "tenant_gate"
NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

#: Five real tasks as an operator would actually run them (ADR-0548's own
#: worked example workflow), each a feature task that succeeded.
FIVE_TASKS = [
    ("feat-auth-refresh", "feature", 4),
    ("feat-quota-panel", "feature", 3),
    ("feat-audit-export", "feature", 4),
    ("feat-consent-ttl", "feature", 5),
    ("feat-tenant-switch", "feature", 3),
]
SEQUENCE = ["/dialectical-reasoning", "/loop-driven-engineering", "/e2e-wiring-proof", "/code-review"]
LATENCIES = [1523, 342, 1200, 450]

#: The Gate's 50-task corpus: five task types, ten successful runs each, every
#: run using a distinct four-skill sequence for its type. Ten per type is the
#: minimum that clears 0.78 (see module docstring).
GATE_TASK_TYPES = ["feature", "refactor", "bugfix", "security", "performance"]
GATE_RUNS_PER_TYPE = 10


def _sequence_for(task_type: str) -> list[str]:
    """A distinct 4-skill sequence per task type.

    Each starts with an exotic skill: without one the base rate for a 4-skill
    sequence is 0.85 and N would have to reach 30 to clear 0.78, which 50 tasks
    cannot supply five times over.
    """
    lead = {
        "feature": "/dialectical-reasoning",
        "refactor": "/drift-detection",
        "bugfix": "/root-cause-by-layer",
        "security": "/security-review",
        "performance": "/reproducibility-first",
    }[task_type]
    return [lead, f"/{task_type}-plan", "/e2e-wiring-proof", "/code-review"]


async def _run_gate_corpus(md: MethodDiscovery) -> int:
    """Drive the full 50-task corpus. Returns the number of tasks observed."""
    count = 0
    for task_type in GATE_TASK_TYPES:
        sequence = _sequence_for(task_type)
        for i in range(GATE_RUNS_PER_TYPE):
            await md.observe(
                task_id=f"{task_type}-{i}",
                task_type=task_type,
                task_complexity=3,
                skill_sequence=sequence,
                skill_latencies_ms=[100] * len(sequence),
                outcome="success",
                outcome_details={"regressions": 0},
                timestamp=NOW - timedelta(days=GATE_RUNS_PER_TYPE - i),
            )
            count += 1
    return count


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
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


async def _run_five_tasks(md: MethodDiscovery) -> list:
    out = []
    for i, (task_id, task_type, complexity) in enumerate(FIVE_TASKS):
        out.append(
            await md.observe(
                task_id=task_id,
                task_type=task_type,
                task_complexity=complexity,
                skill_sequence=SEQUENCE,
                skill_latencies_ms=LATENCIES,
                outcome="success",
                outcome_details={"reason": "all tests pass", "regressions": 0},
                timestamp=NOW - timedelta(days=len(FIVE_TASKS) - i),
            )
        )
    return out


def _chain_records(tmp_path: Path) -> list[dict]:
    chain = tmp_path / "chain" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


# ── Gate criterion 1: five tasks -> five observations ───────────────────────


class TestGateObservationsEmitted:
    async def test_five_tasks_emit_five_observations(self, sandbox):
        md = MethodDiscovery(TENANT)
        observed = await _run_five_tasks(md)
        assert len(observed) == 5

        stored = await md.load_observations()
        assert len(stored) == 5
        assert [o.task_id for o in stored] == [t[0] for t in FIVE_TASKS]

    async def test_every_observation_reached_the_core_audit_chain(self, sandbox):
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        records = _chain_records(sandbox)
        method_records = [
            r for r in records
            if "method.observation" in json.dumps(r)
        ]
        assert len(method_records) == 5

    async def test_chain_carries_no_payload_content(self, sandbox):
        """GDPR Art. 5: the immutable chain copy is metadata only. The skill
        sequence and outcome details live in the disk record, not the chain."""
        md = MethodDiscovery(TENANT)
        await md.observe(
            task_id="t1", task_type="feature", task_complexity=3,
            skill_sequence=SEQUENCE, skill_latencies_ms=LATENCIES, outcome="success",
            outcome_details={"reason": "SENTINEL_MUST_NOT_REACH_CHAIN"},
        )
        assert "SENTINEL_MUST_NOT_REACH_CHAIN" not in json.dumps(_chain_records(sandbox))


# ── Gate criterion 2: a pattern is discovered ───────────────────────────────


class TestGatePatternDiscovered:
    async def test_gate_five_patterns_discovered_in_fifty_tasks(self, sandbox):
        """THE PHASE 1 GATE: 50 test tasks -> 5+ patterns, each >= 0.78."""
        md = MethodDiscovery(TENANT)
        assert await _run_gate_corpus(md) == 50

        newly = await md.discover()
        assert len(newly) >= 5, f"expected 5+ discovered patterns, got {len(newly)}"
        for pattern, breakdown in newly:
            assert pattern.confidence_score >= DISCOVERY_THRESHOLD, (
                f"{pattern.pattern_id} at {pattern.confidence_score:.4f}"
            )
            assert pattern.observation_count == GATE_RUNS_PER_TYPE
            assert pattern.success_rate == 1.0
            assert breakdown.explain()  # EU AI Act Art. 50 derivation present

        # One pattern per task type, none merged across types.
        assert {p.task_type for p, _ in newly} == set(GATE_TASK_TYPES)

    async def test_gate_discoveries_are_all_audited(self, sandbox):
        """Every discovered pattern must have produced a chain-backed
        ``method_discovered`` event carrying its derivation."""
        md = MethodDiscovery(TENANT)
        await _run_gate_corpus(md)
        newly = await md.discover()

        announced = await md.sink.read_discovered_payloads()
        assert len(announced) == len(newly) >= 5
        by_id = {a["pattern_id"]: a for a in announced}
        for pattern, _ in newly:
            record = by_id[pattern.pattern_id]
            assert record["confidence_derivation"]["confidence"] == pattern.confidence_score
            assert record["task_type"] == pattern.task_type
            assert len(record["observation_ids"]) == GATE_RUNS_PER_TYPE

    async def test_five_tasks_yield_one_stratified_pattern(self, sandbox):
        """Sub-gate: a single task type with only 5 runs is observed and
        aggregated correctly, but stays below the announcement bar."""
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        scored = await md.current_patterns()
        assert len(scored) == 1

        pattern, breakdown = scored[0]
        assert pattern.task_type == "feature"
        assert list(pattern.skill_sequence) == SEQUENCE
        assert pattern.observation_count == 5
        assert pattern.success_rate == 1.0
        assert len(pattern.observation_ids) == 5
        assert breakdown.confidence == pattern.confidence_score

    async def test_078_is_arithmetically_unreachable_at_five_observations(self, sandbox):
        """Why the gate corpus is 10-per-type and not 5: at N=5 the sample-size
        boost (0.80) alone caps any pattern at 0.684. Locked so a future change
        that silently makes N=5 'pass' shows up as a failing test rather than
        as a nicer-looking dashboard."""
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        (pattern, _), = await md.current_patterns()
        assert pattern.confidence_score < DISCOVERY_THRESHOLD
        assert pattern.confidence_score <= 0.95 * 0.90 * 0.80  # ceiling at N=5

    async def test_discovery_evidence_links_back_to_real_observations(self, sandbox):
        """'Prove this was learned, not hardcoded': every observation_id on the
        discovery event must resolve to an observation in the chain."""
        md = MethodDiscovery(TENANT)
        await _run_gate_corpus(md)
        newly = await md.discover()
        known = {o.observation_id for o in await md.load_observations()}
        for pattern, _ in newly:
            assert set(pattern.observation_ids) <= known
            assert len(pattern.observation_ids) == GATE_RUNS_PER_TYPE

    async def test_task_types_do_not_contaminate_each_other(self, sandbox):
        """Constraint 1 end to end: the same sequence on security tasks stays a
        separate pattern with its own confidence."""
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        for i in range(5):
            await md.observe(
                task_id=f"sec-{i}", task_type="security", task_complexity=5,
                skill_sequence=SEQUENCE, skill_latencies_ms=LATENCIES, outcome="failure",
                timestamp=NOW - timedelta(days=5 - i),
            )
        scored = await md.current_patterns()
        by_type = {p.task_type: p for p, _ in scored}
        assert set(by_type) == {"feature", "security"}
        assert by_type["feature"].success_rate == 1.0
        assert by_type["security"].success_rate == 0.0
        assert by_type["feature"].confidence_score > by_type["security"].confidence_score


# ── Gate criterion 3: audit chain verified ──────────────────────────────────


class TestGateAuditChainVerified:
    async def test_observation_chain_verifies_after_five_tasks(self, sandbox):
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        result = await md.sink.verify_chain()
        assert result.ok is True, result.error
        assert result.count == 5
        assert result.head == md.sink.chain_head()

    async def test_empty_chain_verifies_at_genesis(self, sandbox):
        md = MethodDiscovery(TENANT)
        result = await md.sink.verify_chain()
        assert result.ok is True
        assert result.count == 0
        assert result.head == GENESIS_HASH

    async def test_core_platform_chain_verifies(self, sandbox):
        """The platform's own hash chain (the one the boot tripwire checks)
        must still verify after Method Discovery has written to it."""
        from core.learning.event_persistence import _resolve_core_audit

        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        audit = _resolve_core_audit()
        ok, bad = audit.verify_audit(sandbox / "chain" / "audit.jsonl")
        assert ok is True, bad

    async def test_tampered_observation_is_detected(self, sandbox):
        """Edit a recorded payload on disk; verification must fail."""
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        assert (await md.sink.verify_chain()).ok is True

        partitions = sorted(md.sink._store.events_dir.glob("*.jsonl"))
        assert partitions
        target = partitions[0]
        lines = [json.loads(l) for l in target.read_text().splitlines() if l.strip()]
        lines[0]["payload"]["outcome"] = "failure"  # rewrite history
        target.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

        result = await md.sink.verify_chain()
        assert result.ok is False
        assert "tampered" in (result.error or "")

    async def test_deleted_observation_is_detected(self, sandbox):
        """Removing a link must break the chain, not silently shorten it."""
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        partitions = sorted(md.sink._store.events_dir.glob("*.jsonl"))
        lines = [l for l in partitions[0].read_text().splitlines() if l.strip()]
        assert len(lines) >= 3
        del lines[1]
        partitions[0].write_text("\n".join(lines) + "\n")

        result = await md.sink.verify_chain()
        assert result.ok is False
        assert "broken link" in (result.error or "")


# ── Gate criterion 4: the dashboard can serve it ────────────────────────────


class TestGateDashboardData:
    async def test_pattern_payload_is_json_serialisable_for_the_api(self, sandbox):
        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        scored = await md.current_patterns()
        body = json.dumps(
            [{**p.to_payload(), "confidence_derivation": b.to_payload()} for p, b in scored]
        )
        parsed = json.loads(body)
        assert parsed[0]["task_type"] == "feature"
        assert parsed[0]["observation_count"] == 5
        assert 0.0 <= parsed[0]["confidence_score"] <= 0.95

    async def test_route_handler_returns_patterns(self, sandbox):
        """Reachability: the console handler itself, not a reimplementation."""
        from core.console.corvin_console.routes.learning import (
            _method_patterns_response,
        )

        md = MethodDiscovery(TENANT)
        await _run_five_tasks(md)
        body = await _method_patterns_response(TENANT)
        assert body["tenant_id"] == TENANT
        assert body["observation_count"] == 5
        assert body["chain_verified"] is True
        assert len(body["patterns"]) == 1
        assert body["patterns"][0]["confidence_score"] > 0
        assert body["patterns"][0]["confidence_derivation"]["sequence_length"] == 4

    def test_endpoint_is_reachable_over_http(self, sandbox):
        """E2E wiring proof over the REAL transport boundary.

        Goes through the mounted ASGI app with an actual HTTP request, not a
        direct call to the handler: this is the only form that proves the route
        is registered on the app the console actually serves. (Modern FastAPI
        defers router flattening, so inspecting ``app.routes`` proves nothing.)

        Uses ``standalone.create_app()`` — the app ``corvinos-serve`` actually
        runs — so the asserted URL is the real ``/v1/console/...`` one the
        frontend calls. ``corvin_console.app.app`` mounts the same router
        WITHOUT that prefix, so testing against it would pass while the browser
        got a 404.
        """
        import asyncio

        from fastapi.testclient import TestClient

        from core.console.corvin_console.deps import require_session
        from core.console.corvin_console.standalone import create_app

        app = create_app()

        md = MethodDiscovery(TENANT)
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _run_five_tasks(md)
        )

        class _Session:
            tenant_id = TENANT
            user = "operator"

        app.dependency_overrides[require_session] = lambda: _Session()
        try:
            with TestClient(app) as client:
                response = client.get("/v1/console/learning/patterns")
        finally:
            app.dependency_overrides.pop(require_session, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tenant_id"] == TENANT
        assert body["chain_verified"] is True
        assert body["observation_count"] == 5
        assert len(body["patterns"]) == 1
        assert body["patterns"][0]["task_type"] == "feature"
        assert body["patterns"][0]["confidence_explanation"]

    def test_endpoint_requires_a_session(self, sandbox):
        """No session -> denied, never an unscoped read of somebody's patterns."""
        from fastapi.testclient import TestClient

        from core.console.corvin_console.standalone import create_app

        with TestClient(create_app()) as client:
            status = client.get("/v1/console/learning/patterns").status_code
        # 401 from require_session, or 403 if the dual-gate middleware rejects
        # the anonymous caller first. Either is a denial; 200 would not be.
        assert status in (401, 403), status
