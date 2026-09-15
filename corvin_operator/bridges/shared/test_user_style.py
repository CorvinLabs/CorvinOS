"""End-to-end tests for the autonomous user-style learner (Layer 26).

Drives the full pipeline against a synthetic audit chain in a tempdir
sandbox. No live LLM calls — the dialectic judge is mocked. Verifies:

  - aggregate_clusters reads outcome events correctly
  - confidence gate rejects low-N / approval-heavy clusters
  - shadow start is idempotent and writes audit
  - shadow A/B evaluation: with-bullet cohort with lower negative_ratio
    promotes; same/worse rejects
  - promote writes style.md AND audit
  - live evaluation rollback after window with high rejection rate
  - cooldown prevents immediate re-entry
  - HARD_CAP enforced on live bullets
  - daily sweep orchestrates the whole chain
  - audit hash chain stays intact end-to-end (verify_chain)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure the bridge tree is importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Forge for the audit chain helpers.
_FORGE_TOP = _HERE.parent.parent / "forge"
if str(_FORGE_TOP) not in sys.path:
    sys.path.insert(0, str(_FORGE_TOP))

import user_style as us  # noqa: E402
from forge.security_events import write_event, verify_chain  # noqa: E402


# ── Test helpers ───────────────────────────────────────────────────────────

def _emit_outcome(audit_path: Path, *, signal: str, skills: list[str],
                  prev_run_id: str, ts: float) -> None:
    """Write one synthetic ``skill.outcome_graded`` event."""
    write_event(
        audit_path, "skill.outcome_graded",
        tool="adapter",
        ts=ts,
        details={
            "signal":      signal,
            "prev_run_id": prev_run_id,
            "skills":      skills,
            "score":       0.1 if signal == "rejection"
                           else 0.9 if signal == "approval" else 0.3,
        },
    )


def _gen_run_ids_split(n: int) -> tuple[list[str], list[str]]:
    """Return (with_active_ids, without_active_ids) of N each.

    Iterates over candidate IDs and partitions by ``shadow_active_for_seed``
    so the A/B cohort assignment is deterministic.
    """
    with_active: list[str] = []
    without:     list[str] = []
    i = 0
    while len(with_active) < n or len(without) < n:
        rid = f"run_{i:08d}"
        if us.shadow_active_for_seed(rid):
            if len(with_active) < n:
                with_active.append(rid)
        else:
            if len(without) < n:
                without.append(rid)
        i += 1
        if i > 100_000:  # safety
            raise RuntimeError("could not split run-ids")
    return with_active, without


# ── Sandbox base ───────────────────────────────────────────────────────────

class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="user-style-e2e-"))
        # Both audit + store live under the sandbox so the test never
        # touches the real ~/.corvin tree.
        self.corvin = self.tmp
        self.audit = us._audit_path(corvin_home=self.corvin)
        self.audit.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── 1. Aggregator ──────────────────────────────────────────────────────────

class AggregateTests(_Base):
    def test_empty_chain_yields_no_clusters(self) -> None:
        self.assertEqual(
            us.aggregate_clusters(self.audit, corvin_home=self.corvin),
            [],
        )

    def test_groups_by_skill_and_counts_per_signal(self) -> None:
        ts = time.time()
        for i in range(3):
            _emit_outcome(self.audit, signal="rejection",
                          skills=["skill_A"], prev_run_id=f"r{i}", ts=ts - i)
        for i in range(2):
            _emit_outcome(self.audit, signal="approval",
                          skills=["skill_A"], prev_run_id=f"a{i}", ts=ts - i)
        _emit_outcome(self.audit, signal="rephrase",
                      skills=["skill_B"], prev_run_id="b0", ts=ts)

        clusters = us.aggregate_clusters(
            self.audit, corvin_home=self.corvin, now=ts + 1,
        )
        self.assertEqual(len(clusters), 2)
        by_name = {c.skill_name: c for c in clusters}
        self.assertEqual(by_name["skill_A"].counts.rejection, 3)
        self.assertEqual(by_name["skill_A"].counts.approval,  2)
        self.assertEqual(by_name["skill_A"].counts.total,     5)
        self.assertAlmostEqual(by_name["skill_A"].counts.negative_ratio, 0.6)
        self.assertEqual(by_name["skill_B"].counts.rephrase, 1)

    def test_horizon_drops_old_events(self) -> None:
        now = time.time()
        old = now - 30 * 86400
        _emit_outcome(self.audit, signal="rejection",
                      skills=["skill_X"], prev_run_id="rx", ts=old)
        _emit_outcome(self.audit, signal="rejection",
                      skills=["skill_X"], prev_run_id="ry", ts=now)

        clusters = us.aggregate_clusters(
            self.audit, corvin_home=self.corvin,
            since_days=14, now=now,
        )
        # Only the recent one survives.
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].counts.total, 1)


# ── 2. Confidence gate ─────────────────────────────────────────────────────

class ConfidenceGateTests(_Base):
    def _cluster(self, *, rej: int, reph: int, app: int) -> us.Cluster:
        c = us.SignalCounts(rejection=rej, rephrase=reph, approval=app)
        return us.Cluster(
            cluster_id="cl_test", skill_name="skill_T", counts=c,
        )

    def test_too_few_hits_blocks(self) -> None:
        self.assertFalse(us.passes_confidence_gate(
            self._cluster(rej=4, reph=0, app=0),  # below MIN_HITS
        ))

    def test_below_negative_threshold_blocks(self) -> None:
        # 5 hits but only 50% negative → below 70% threshold
        self.assertFalse(us.passes_confidence_gate(
            self._cluster(rej=2, reph=1, app=2),
        ))

    def test_passes_when_strongly_negative(self) -> None:
        self.assertTrue(us.passes_confidence_gate(
            self._cluster(rej=4, reph=2, app=0),
        ))


# ── 3. Judge integration ──────────────────────────────────────────────────

class JudgeTests(_Base):
    def test_judge_faithful_passes(self) -> None:
        cluster = us.Cluster(
            cluster_id="cl_x", skill_name="skill_X",
            counts=us.SignalCounts(rejection=5, rephrase=2, approval=0),
        )
        out = us.judge_overfit(
            cluster, "draft",
            judge_fn=lambda c, d: True,
        )
        self.assertTrue(out)

    def test_judge_overfit_blocks(self) -> None:
        cluster = us.Cluster(
            cluster_id="cl_x", skill_name="skill_X",
            counts=us.SignalCounts(rejection=5, rephrase=2, approval=0),
        )
        out = us.judge_overfit(
            cluster, "draft",
            judge_fn=lambda c, d: False,
        )
        self.assertFalse(out)

    def test_judge_exception_treated_as_overfit(self) -> None:
        def raises(c, d):
            raise RuntimeError("boom")
        cluster = us.Cluster(
            cluster_id="cl_x", skill_name="skill_X",
            counts=us.SignalCounts(rejection=5, rephrase=2, approval=0),
        )
        self.assertFalse(us.judge_overfit(cluster, "d", judge_fn=raises))


# ── 4. Shadow lifecycle ────────────────────────────────────────────────────

class ShadowLifecycleTests(_Base):
    def _make_cluster(self, name: str = "skill_S") -> us.Cluster:
        return us.Cluster(
            cluster_id=us._cluster_id(name), skill_name=name,
            counts=us.SignalCounts(rejection=4, rephrase=2, approval=0),
        )

    def test_start_shadow_writes_candidate_and_audit(self) -> None:
        c = us.start_shadow(
            self._make_cluster(), corvin_home=self.corvin,
        )
        self.assertEqual(c.state, "shadow")
        self.assertIsNotNone(c.bullet_id)

        cands = us.load_candidates(corvin_home=self.corvin)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].skill_name, "skill_S")

        # Audit event landed
        events = [e for e in _read_jsonl(self.audit)
                  if e.get("event_type") == "user_style.candidate_proposed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["skill_name"], "skill_S")

    def test_start_shadow_idempotent_on_cluster_id(self) -> None:
        cluster = self._make_cluster()
        c1 = us.start_shadow(cluster, corvin_home=self.corvin)
        c2 = us.start_shadow(cluster, corvin_home=self.corvin)
        self.assertEqual(c1.bullet_id, c2.bullet_id)
        self.assertEqual(
            len(us.load_candidates(corvin_home=self.corvin)),
            1,
        )


# ── 5. A/B shadow evaluation ──────────────────────────────────────────────

class ShadowEvaluationTests(_Base):
    def test_promote_when_with_bullet_cohort_improves(self) -> None:
        skill = "skill_AB_promote"
        # Start shadow at t0; window ends at t0 + SHADOW_DAYS.
        t0 = time.time() - (us.SHADOW_DAYS + 1) * 86400
        cluster = us.Cluster(
            cluster_id=us._cluster_id(skill), skill_name=skill,
            counts=us.SignalCounts(rejection=4, rephrase=2, approval=0),
        )
        candidate = us.start_shadow(
            cluster, corvin_home=self.corvin, now=t0,
        )

        # Build A/B cohorts: 10 events each with deterministic run-ids.
        with_ids, without_ids = _gen_run_ids_split(10)

        # WITH-bullet cohort: only 1 rejection out of 10 (10% negative)
        for i, rid in enumerate(with_ids):
            sig = "rejection" if i == 0 else "approval"
            _emit_outcome(self.audit, signal=sig, skills=[skill],
                          prev_run_id=rid, ts=t0 + i + 1)
        # WITHOUT-bullet cohort: 6 rejections out of 10 (60% negative)
        for i, rid in enumerate(without_ids):
            sig = "rejection" if i < 6 else "approval"
            _emit_outcome(self.audit, signal=sig, skills=[skill],
                          prev_run_id=rid, ts=t0 + 100 + i)

        verdict = us.evaluate_shadow(
            candidate, self.audit,
            now=time.time(), corvin_home=self.corvin,
        )
        self.assertEqual(verdict, "promote")

    def test_reject_when_with_bullet_cohort_does_not_help(self) -> None:
        skill = "skill_AB_reject"
        t0 = time.time() - (us.SHADOW_DAYS + 1) * 86400
        cluster = us.Cluster(
            cluster_id=us._cluster_id(skill), skill_name=skill,
            counts=us.SignalCounts(rejection=4, rephrase=2, approval=0),
        )
        candidate = us.start_shadow(
            cluster, corvin_home=self.corvin, now=t0,
        )
        with_ids, without_ids = _gen_run_ids_split(10)
        # Both cohorts equally bad.
        for i, rid in enumerate(with_ids):
            sig = "rejection" if i < 5 else "approval"
            _emit_outcome(self.audit, signal=sig, skills=[skill],
                          prev_run_id=rid, ts=t0 + i + 1)
        for i, rid in enumerate(without_ids):
            sig = "rejection" if i < 5 else "approval"
            _emit_outcome(self.audit, signal=sig, skills=[skill],
                          prev_run_id=rid, ts=t0 + 100 + i)
        verdict = us.evaluate_shadow(
            candidate, self.audit,
            now=time.time(), corvin_home=self.corvin,
        )
        self.assertEqual(verdict, "reject")

    def test_continue_when_window_not_elapsed(self) -> None:
        skill = "skill_AB_pending"
        cluster = us.Cluster(
            cluster_id=us._cluster_id(skill), skill_name=skill,
            counts=us.SignalCounts(rejection=4, rephrase=2, approval=0),
        )
        now = time.time()
        candidate = us.start_shadow(
            cluster, corvin_home=self.corvin, now=now,
        )
        # No time passed → continue
        verdict = us.evaluate_shadow(
            candidate, self.audit, now=now, corvin_home=self.corvin,
        )
        self.assertEqual(verdict, "continue")


# ── 6. Promote → live + style.md ──────────────────────────────────────────

class PromoteTests(_Base):
    def test_promote_moves_to_live_writes_style_md_and_audit(self) -> None:
        cluster = us.Cluster(
            cluster_id=us._cluster_id("skill_P"), skill_name="skill_P",
            counts=us.SignalCounts(rejection=5, rephrase=2, approval=0),
        )
        candidate = us.start_shadow(cluster, corvin_home=self.corvin)
        us.promote_bullet(candidate, corvin_home=self.corvin)

        live = us.load_live(corvin_home=self.corvin)
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].state, "live")
        self.assertIsNotNone(live[0].live_started_at)

        style_md = us._style_md_path(corvin_home=self.corvin).read_text()
        self.assertIn("skill_P", style_md)
        self.assertIn("Auto-learned user style", style_md)

        events = [e for e in _read_jsonl(self.audit)
                  if e.get("event_type") == "user_style.bullet_promoted"]
        self.assertEqual(len(events), 1)

    def test_hard_cap_enforced_on_live(self) -> None:
        # Manually fill live with HARD_CAP entries.
        live: list[us.Candidate] = []
        for i in range(us.HARD_CAP):
            live.append(us.Candidate(
                bullet_id=f"b_{i}", cluster_id=f"cl_{i}",
                skill_name=f"skill_{i}",
                bullet_text="...",
                state="live", live_started_at=float(i),
            ))
        us.save_live(live, corvin_home=self.corvin)

        # Promote one more — oldest gets dropped.
        cluster = us.Cluster(
            cluster_id=us._cluster_id("skill_NEW"), skill_name="skill_NEW",
            counts=us.SignalCounts(rejection=5, rephrase=2, approval=0),
        )
        candidate = us.start_shadow(cluster, corvin_home=self.corvin)
        us.promote_bullet(candidate, corvin_home=self.corvin, now=999.0)

        live2 = us.load_live(corvin_home=self.corvin)
        self.assertEqual(len(live2), us.HARD_CAP)
        names = {c.skill_name for c in live2}
        self.assertIn("skill_NEW", names)
        self.assertNotIn("skill_0", names)  # oldest dropped


# ── 7. Live rollback ──────────────────────────────────────────────────────

class RollbackTests(_Base):
    def test_rollback_when_negative_rate_exceeds_threshold(self) -> None:
        skill = "skill_RB"
        # Bullet went live LIVE_WINDOW_DAYS+1 ago.
        live_started = time.time() - (us.LIVE_WINDOW_DAYS + 1) * 86400
        bullet = us.Candidate(
            bullet_id="b_rb", cluster_id=us._cluster_id(skill),
            skill_name=skill, bullet_text="...",
            state="live", live_started_at=live_started,
        )
        us.save_live([bullet], corvin_home=self.corvin)

        # Build last-week chain: 8 rejections out of 10 = 80% negative
        now = time.time()
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"rb_r{i}", ts=now - 100 + i)
        for i in range(2):
            _emit_outcome(self.audit, signal="approval", skills=[skill],
                          prev_run_id=f"rb_a{i}", ts=now - 50 + i)

        verdict = us.evaluate_live_bullet(
            bullet, self.audit, now=now, corvin_home=self.corvin,
        )
        self.assertEqual(verdict, "rollback")

        us.rollback_bullet(bullet, corvin_home=self.corvin, now=now)
        self.assertEqual(us.load_live(corvin_home=self.corvin), [])
        self.assertTrue(us.in_cooldown(
            bullet.cluster_id, corvin_home=self.corvin, now=now,
        ))
        # Audit fired
        events = [e for e in _read_jsonl(self.audit)
                  if e.get("event_type") == "user_style.bullet_rolled_back"]
        self.assertEqual(len(events), 1)

    def test_keep_when_negative_rate_below_threshold(self) -> None:
        skill = "skill_KEEP"
        live_started = time.time() - (us.LIVE_WINDOW_DAYS + 1) * 86400
        bullet = us.Candidate(
            bullet_id="b_keep", cluster_id=us._cluster_id(skill),
            skill_name=skill, bullet_text="...",
            state="live", live_started_at=live_started,
        )
        us.save_live([bullet], corvin_home=self.corvin)

        now = time.time()
        for i in range(2):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"k_r{i}", ts=now - 100 + i)
        for i in range(8):
            _emit_outcome(self.audit, signal="approval", skills=[skill],
                          prev_run_id=f"k_a{i}", ts=now - 50 + i)

        verdict = us.evaluate_live_bullet(
            bullet, self.audit, now=now, corvin_home=self.corvin,
        )
        self.assertEqual(verdict, "keep")

    def test_keep_when_window_not_elapsed(self) -> None:
        skill = "skill_FRESH"
        bullet = us.Candidate(
            bullet_id="b_fr", cluster_id=us._cluster_id(skill),
            skill_name=skill, bullet_text="...",
            state="live", live_started_at=time.time(),
        )
        us.save_live([bullet], corvin_home=self.corvin)
        # All rejections, but bullet just went live.
        now = time.time() + 60
        for i in range(20):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"fr_{i}", ts=now)
        verdict = us.evaluate_live_bullet(
            bullet, self.audit, now=now, corvin_home=self.corvin,
        )
        self.assertEqual(verdict, "keep")


# ── 8. Cooldown ───────────────────────────────────────────────────────────

class CooldownTests(_Base):
    def test_cooldown_blocks_re_entry_in_sweep(self) -> None:
        skill = "skill_CD"
        # Pre-populate cooldown so the cluster cannot return.
        now = time.time()
        cd = {us._cluster_id(skill): now + us.COOLDOWN_DAYS * 86400}
        us.save_cooldown(cd, corvin_home=self.corvin)

        # Build a chain that would otherwise pass the gate.
        for i in range(10):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"cd_{i}", ts=now - 100 + i)

        summary = us.run_daily_sweep(
            audit_path=self.audit, corvin_home=self.corvin,
            judge_fn=lambda c, d: True, now=now,
        )
        # Cluster appears in aggregator but never enters shadow.
        self.assertGreaterEqual(summary["clusters"], 1)
        self.assertEqual(summary["candidates_proposed"], 0)
        self.assertEqual(us.load_candidates(corvin_home=self.corvin), [])


# ── 9. Daily sweep (orchestration) ────────────────────────────────────────

class DailySweepTests(_Base):
    def test_full_pipeline_passes(self) -> None:
        skill = "skill_E2E"
        now = time.time()

        # Build 8 negative + 1 approval over the last 7 days.
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"e2e_r{i}", ts=now - 86400 - i)
        _emit_outcome(self.audit, signal="approval", skills=[skill],
                      prev_run_id="e2e_a0", ts=now - 86400 - 9)

        # Sweep with a FAITHFUL judge → candidate enters shadow.
        summary = us.run_daily_sweep(
            audit_path=self.audit, corvin_home=self.corvin,
            judge_fn=lambda c, d: True, now=now,
        )
        self.assertEqual(summary["candidates_proposed"], 1)
        cands = us.load_candidates(corvin_home=self.corvin)
        self.assertEqual(len(cands), 1)

    def test_judge_overfit_drops_with_cooldown(self) -> None:
        skill = "skill_OF"
        now = time.time()
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"of_{i}", ts=now - i)

        summary = us.run_daily_sweep(
            audit_path=self.audit, corvin_home=self.corvin,
            judge_fn=lambda c, d: False,  # OVERFIT verdict
            now=now,
        )
        self.assertEqual(summary["candidates_proposed"], 0)
        self.assertTrue(us.in_cooldown(
            us._cluster_id(skill), corvin_home=self.corvin, now=now,
        ))


# ── 10. A/B selector ─────────────────────────────────────────────────────

class SelectorTests(_Base):
    def test_live_always_picked_shadow_half_the_time(self) -> None:
        live = us.Candidate(
            bullet_id="bl", cluster_id="clive", skill_name="skill_L",
            bullet_text="LIVE-bullet", state="live",
            live_started_at=time.time(),
        )
        us.save_live([live], corvin_home=self.corvin)
        shadow = us.Candidate(
            bullet_id="bs", cluster_id="cshad", skill_name="skill_S",
            bullet_text="SHADOW-bullet", state="shadow",
            shadow_started_at=time.time(),
        )
        us.save_candidates([shadow], corvin_home=self.corvin)

        with_seeds, without_seeds = _gen_run_ids_split(20)

        # Live always present
        for sid in with_seeds + without_seeds:
            live_b, _ = us.shadow_pick_for_turn(
                sid, corvin_home=self.corvin,
            )
            self.assertIn("LIVE-bullet", live_b)

        # Shadow only on parity-1 seeds
        for sid in with_seeds:
            _, sb = us.shadow_pick_for_turn(sid, corvin_home=self.corvin)
            self.assertIn("SHADOW-bullet", sb)
        for sid in without_seeds:
            _, sb = us.shadow_pick_for_turn(sid, corvin_home=self.corvin)
            self.assertEqual(sb, [])


# ── 11. Audit chain integrity end-to-end ─────────────────────────────────

class ChainIntegrityTests(_Base):
    def test_full_lifecycle_chain_remains_intact(self) -> None:
        skill = "skill_CHAIN"
        # Some outcome events
        for i in range(8):
            _emit_outcome(self.audit, signal="rejection", skills=[skill],
                          prev_run_id=f"ch_{i}", ts=time.time() - 10 + i)

        # Sweep → propose → promote → rollback (forced)
        cluster = us.Cluster(
            cluster_id=us._cluster_id(skill), skill_name=skill,
            counts=us.SignalCounts(rejection=8, rephrase=0, approval=0),
        )
        candidate = us.start_shadow(cluster, corvin_home=self.corvin)
        us.promote_bullet(candidate, corvin_home=self.corvin)
        live = us.load_live(corvin_home=self.corvin)[0]
        us.rollback_bullet(live, corvin_home=self.corvin)

        ok, problems = verify_chain(self.audit)
        self.assertTrue(ok, msg=f"chain broken: {problems}")
        self.assertEqual(problems, [])

    def test_audit_event_carries_only_metadata(self) -> None:
        """The chain must NEVER carry the raw bullet body — only metadata.

        Mirror of the L23 voice-transcribe / L24 data-snapshot privacy
        rule. Adapted here: bullet_text is operator-readable in
        style.md but does not belong in the audit chain.
        """
        cluster = us.Cluster(
            cluster_id=us._cluster_id("skill_PII"),
            skill_name="skill_PII",
            counts=us.SignalCounts(rejection=8, rephrase=0, approval=0),
        )
        candidate = us.start_shadow(cluster, corvin_home=self.corvin)
        us.promote_bullet(candidate, corvin_home=self.corvin)
        us.rollback_bullet(candidate, corvin_home=self.corvin)

        for ev in _read_jsonl(self.audit):
            if not (ev.get("event_type") or "").startswith("user_style."):
                continue
            details = ev.get("details", {}) or {}
            # bullet_text is the literal generated rule; must not appear
            for v in details.values():
                if isinstance(v, str):
                    self.assertNotIn(
                        candidate.bullet_text, v,
                        msg=f"bullet body leaked into audit: {ev}",
                    )


# ── tiny helpers ──────────────────────────────────────────────────────────

def _read_jsonl(p: Path) -> list[dict]:
    import json
    out: list[dict] = []
    if not p.exists():
        return out
    with p.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


if __name__ == "__main__":
    unittest.main(verbosity=2)
