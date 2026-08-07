"""ADR-0222 Phase 2 — Real-traffic measurement sampler for the decision gate.

Collects {direct, F5-tier, TDE} trials on the same tasks and aggregates them
into BandEvidence for the gate to consume. The gate upgrades from assumption-sourced
predictions to measured verdicts once min_samples_per_band accumulates.

OPERATOR NOTES (k=5, read before enabling a measurement week)
-------------------------------------------------------------
COST: a sampled turn runs the whole task THREE times — the TDE run itself plus a
direct baseline and an F5 tier baseline — and then two judge calls. Budget ~3x the
tokens of an unmeasured turn. ``TDE_MEASUREMENT_SAMPLE_RATE`` thins this.

QUOTA: the two baseline turns are NOT charged against the shared daily
agentic-compute pool (``compute_units_per_day``, the counter TDE/ACS/compute runs
share). Only the user's own TDE turn is charged, by the normal chokepoint. This is
deliberate — charging the diagnostic arms would end a free-tier measurement week
after ~3 sampled turns and defeat its purpose — but it means the flag lets an
instance spend un-metered compute. It is therefore a MAINTAINER-ONLY switch
(default OFF, exact opt-in ``TDE_MEASUREMENT_ENABLED=1``); do not enable it on an
instance whose compute budget is supposed to be capped by the pool.

DATA: measurement.jsonl is written outside the hash-chained audit log and, by
default, WITHOUT model output text — only tokens, losses and output lengths (see
``PERSIST_OUTPUTS_ENV``). The task prompt itself is never persisted.

WHY NO SAMPLES? Every refusal path logs its reason. The most common is a run whose
steps were only partially token-instrumented: the sampler skips it rather than
book an under-counted TDE cost (see chat_runtime's coverage gate).
"""

from __future__ import annotations

import os
import json
import logging
import time
import threading
import asyncio
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional
from collections import defaultdict
from .decision_gate import BandEvidence

_log = logging.getLogger(__name__)

#: Bands the gate groups evidence by. Kept as a runtime tuple because a
#: ``Literal`` annotation is NOT enforced at runtime — validation needs a real
#: membership check (see MeasurementSample.__post_init__).
VALID_BANDS: tuple[str, ...] = ("trivial", "moderate", "complex")

#: Persist the full LLM outputs into measurement.jsonl. Default OFF: the gate
#: reads ONLY tokens and losses, so writing the raw text of a user's task and
#: three model answers to disk would be collection without analytic purpose
#: (GDPR Art. 5(1)(c) data minimisation). Opt-in strictly for locally debugging
#: the judge. When off, samples persist output LENGTHS instead of text, which is
#: all that is ever needed to sanity-check a suspicious loss score.
PERSIST_OUTPUTS_ENV = "TDE_MEASUREMENT_PERSIST_OUTPUTS"


@dataclass
class MeasurementSample:
    """One sampled trial of {direct, tier, TDE} on a task."""

    task_id: str                    # run_id or unique task identifier
    task_band: Literal["trivial", "moderate", "complex"]  # task complexity band
    timestamp: float                # unix time

    # Direct turn (user's model, single-call baseline)
    direct_tokens: int
    direct_output: str

    # F5 whole-task-tier baseline
    tier_tokens: int
    tier_output: str
    tier_loss: float                # vs direct (0.0 = identical, 1.0 = unrelated)

    # TDE multi-step decomposition
    tde_tokens: int
    tde_output: str
    tde_loss: float                 # vs direct (0.0 = identical, 1.0 = unrelated)

    # Metadata
    quality_judge_model: str = "haiku"  # the model that scored losses
    data_source: str = "measured"       # always "measured" for real samples

    def __post_init__(self) -> None:
        """Validate sample data integrity — FAIL-CLOSED (ADR-0222 honesty invariant).

        A sample that reaches the gate is evidence a routing default gets flipped
        on. Every field that could carry a FABRICATED number is rejected here
        rather than defaulted, because the failure mode is silent and one-directional:
        a missing token count read as 0 becomes "(direct - 0) / direct = 100% savings",
        i.e. the strongest possible pro-TDE evidence produced by an absent measurement.
        Refusing the sample loses one data point; accepting it corrupts the verdict.
        """
        # Loss values must be in [0.0, 1.0] range (semantic similarity)
        if not (0.0 <= self.tier_loss <= 1.0):
            raise ValueError(f"tier_loss must be in [0.0, 1.0], got {self.tier_loss}")
        if not (0.0 <= self.tde_loss <= 1.0):
            raise ValueError(f"tde_loss must be in [0.0, 1.0], got {self.tde_loss}")

        # Token counts must be STRICTLY POSITIVE, not merely non-negative. A zero
        # is never a real measurement of an LLM turn that produced output — it is
        # an unwired usage field (exactly the k=4 defect: result["usage"] did not
        # exist, so every sample would have carried tde_tokens=0). direct_tokens=0
        # additionally divides by zero in the gate's savings formula.
        for _name in ("direct_tokens", "tier_tokens", "tde_tokens"):
            _val = getattr(self, _name)
            if _val <= 0:
                raise ValueError(
                    f"{_name} must be > 0 (got {_val}) — a zero token count is an "
                    "unmeasured turn, and would read as fabricated savings"
                )

        # task_band must be a band the gate actually groups by. The Literal
        # annotation does NOT enforce this at runtime, so an unknown band would
        # silently create a phantom evidence group that never reaches
        # min_samples_per_band and quietly starves the verdict.
        if self.task_band not in VALID_BANDS:
            raise ValueError(
                f"task_band must be one of {VALID_BANDS}, got {self.task_band!r}"
            )

    def to_persistable_dict(self) -> dict[str, Any]:
        """Serialise for measurement.jsonl, redacting raw model output by default.

        The gate needs tokens + losses only. Full outputs stay in memory for the
        judge and are written to disk ONLY under PERSIST_OUTPUTS_ENV (see module
        docstring). Lengths are always kept — enough to spot a truncated or empty
        answer behind a surprising loss score, without storing the text itself.
        """
        data = asdict(self)
        if os.getenv(PERSIST_OUTPUTS_ENV) == "1":
            return data
        for _field in ("direct_output", "tier_output", "tde_output"):
            data[f"{_field}_chars"] = len(data.get(_field) or "")
            data[_field] = "<redacted>"
        return data


@dataclass
class AggregatedBandEvidence:
    """Rolled-up stats for a band (avg tokens, losses, sample count)."""

    band: str
    samples: list[MeasurementSample] = field(default_factory=list)

    @property
    def direct_tokens(self) -> float:
        """Average tokens for direct turn on this band."""
        if not self.samples:
            return 0.0
        return sum(s.direct_tokens for s in self.samples) / len(self.samples)

    @property
    def tier_tokens(self) -> float:
        """Average tokens for F5 tier baseline on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tier_tokens for s in self.samples) / len(self.samples)

    @property
    def tier_loss(self) -> float:
        """Average quality loss for F5 tier baseline on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tier_loss for s in self.samples) / len(self.samples)

    @property
    def tde_tokens(self) -> float:
        """Average tokens for TDE decomposition on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tde_tokens for s in self.samples) / len(self.samples)

    @property
    def tde_loss(self) -> float:
        """Average quality loss for TDE decomposition on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tde_loss for s in self.samples) / len(self.samples)

    @property
    def n_measured(self) -> int:
        """Number of measured samples backing this aggregation."""
        return len(self.samples)


def aggregate_measured_evidence(
    samples: list[MeasurementSample],
) -> list[BandEvidence]:
    """Rolls MeasurementSamples into the BandEvidence format decision_gate expects.

    Groups samples by task_band and aggregates {tokens, losses} within each band.
    Every output has data_source="measured" so the gate knows this is real data,
    not assumptions.

    Args:
        samples: List of real-traffic MeasurementSample trials.

    Returns:
        List of BandEvidence ready for decision_gate.evaluate_tde_verdict().
    """
    by_band: dict[str, list[MeasurementSample]] = defaultdict(list)
    for sample in samples:
        by_band[sample.task_band].append(sample)

    evidence_list: list[BandEvidence] = []
    for band, band_samples in by_band.items():
        agg = AggregatedBandEvidence(band=band, samples=band_samples)
        evidence = BandEvidence(
            band=band,
            direct_tokens=agg.direct_tokens,
            tde_tokens=agg.tde_tokens,
            tde_loss=agg.tde_loss,
            tier_tokens=agg.tier_tokens,
            tier_loss=agg.tier_loss,
            n_measured=agg.n_measured,
            data_source="measured",  # <- THE KEY: upgrades from assumptions
        )
        evidence_list.append(evidence)

    return evidence_list


class MeasurementRecorder:
    """Singleton that records {direct, tier, TDE} samples during measurement week.

    Persists samples to measurement.jsonl (separate from audit chain) and
    provides aggregated BandEvidence to the decision gate. Thread-safe and
    async-safe for concurrent turns during measurement week.

    NOTE on session isolation (k=4 enhancement): This singleton mixes samples
    from all concurrent sessions. For multi-tenant isolation, implement per-session
    recorders keyed by (tenant_id, session_id). Current design fine for k=3 where
    measurement week is opt-in feature; k=4 should add session-scoped storage.
    """

    _instance: MeasurementRecorder | None = None
    _instance_lock = threading.Lock()

    def __init__(self, measurement_log_path: str | None = None):
        """Initialize the recorder.

        Args:
            measurement_log_path: Path to write JSONL samples. If None, uses default
                ~/.corvin/measurement-week/measurement.jsonl.
        """
        self.enabled = os.getenv("TDE_MEASUREMENT_ENABLED") == "1"

        if measurement_log_path is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            measurement_log_path = os.path.join(
                corvin_home, "measurement-week", "measurement.jsonl"
            )

        self.log_path = measurement_log_path
        self.samples: list[MeasurementSample] = []
        self._write_lock = threading.Lock()

        # Ensure directory exists
        if self.enabled:
            log_dir = os.path.dirname(self.log_path)
            if log_dir:
                Path(log_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(
        cls, measurement_log_path: str | None = None
    ) -> "MeasurementRecorder":
        """Get or create the singleton instance (thread-safe TOCTOU fix)."""
        if cls._instance is None:
            with cls._instance_lock:
                # Double-check inside lock to avoid TOCTOU
                if cls._instance is None:
                    cls._instance = cls(measurement_log_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._instance_lock:
            cls._instance = None

    async def record_sample(self, sample: MeasurementSample) -> None:
        """Append a sample to the in-memory buffer and persist to log (async-safe).

        Uses asyncio.to_thread() to avoid blocking the event loop on file I/O.

        Args:
            sample: The measurement sample to record.
        """
        if not self.enabled:
            return

        self.samples.append(sample)

        # Persist to measurement.jsonl asynchronously (no event loop blocking)
        try:
            await asyncio.to_thread(self._write_sample_sync, sample)
        except (IOError, OSError) as e:
            # Log but don't crash; measurement failure shouldn't block chat
            _log.warning("Failed to write measurement sample to %s: %s",
                         self.log_path, e)

    def _write_sample_sync(self, sample: MeasurementSample) -> None:
        """Synchronous file write with lock to prevent JSONL corruption.

        Executed in thread pool via asyncio.to_thread() to avoid blocking event loop.

        Args:
            sample: The measurement sample to write.
        """
        with self._write_lock:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(sample.to_persistable_dict(), default=str) + "\n")

    def get_aggregated_evidence(self) -> list[BandEvidence]:
        """Return current measured evidence aggregated by band.

        Returns:
            List of BandEvidence ready for decision_gate.evaluate_tde_verdict().
        """
        return aggregate_measured_evidence(self.samples)

    def load_from_log(self) -> None:
        """Replace the in-memory buffer with what measurement.jsonl holds.

        Useful for resuming a measurement week or analysis after restart.

        REPLACES rather than appends: this used to extend ``self.samples``, so a
        second call — or a call on a recorder that had already recorded this
        session — double-counted every sample. ``n_measured`` is the gate's
        sample-size guard, so inflating it is exactly how thin evidence sneaks
        past ``min_samples_per_band``. The log on disk is the single truth.
        """
        if not os.path.exists(self.log_path):
            return

        with self._write_lock:
            loaded: list[MeasurementSample] = []
            try:
                with open(self.log_path, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            # Drop the redaction bookkeeping fields written by
                            # to_persistable_dict() — they are not constructor
                            # args, and passing them through would raise
                            # TypeError on every redacted line (i.e. on the
                            # DEFAULT log format), silently losing the whole log.
                            data = {k: v for k, v in data.items()
                                    if not k.endswith("_chars")}
                            loaded.append(MeasurementSample(**data))
                        except (json.JSONDecodeError, TypeError, ValueError) as e:
                            _log.warning("Failed to parse measurement line: %s", e)
            except (IOError, OSError) as e:
                # Partial read: keep the buffer untouched rather than swapping in
                # a truncated set that would under-report n_measured.
                _log.warning("Failed to load measurements from %s: %s",
                             self.log_path, e)
                return
            self.samples = loaded

    def clear_samples(self) -> None:
        """Clear in-memory samples (for testing)."""
        self.samples.clear()


# ============================================================================
# k=5 Real Orchestrator — the measurement the gate actually consumes
# ============================================================================

#: InitialAnalysis complexity value -> decision_gate band name.
#:
#: The two vocabularies genuinely differ and must be translated, not assumed
#: equal: the classifier emits ``simple | moderate | complex``
#: (loss_profile_tracker "Task complexity bucket", and send_integration's
#: fast-path test compares against ``"simple"``), while the gate's bands are
#: ``trivial | moderate | complex`` (decision_gate.synthetic_evidence_from_assumptions).
#: Passing "simple" through unmapped silently routed every simple task into the
#: moderate band — leaving the trivial band permanently at n_measured=0 (so it
#: could only ever read INSUFFICIENT_DATA) while diluting the moderate band with
#: tasks that belong to a cheaper one. "trivial" is accepted as well so an
#: aligned classifier keeps working.
_COMPLEXITY_TO_BAND: dict[str, str] = {
    "simple": "trivial",
    "trivial": "trivial",
    "moderate": "moderate",
    "medium": "moderate",
    "complex": "complex",
    "hard": "complex",
}


def classify_band(task_complexity: str | None) -> Literal["trivial", "moderate", "complex"]:
    """Map an InitialAnalysis complexity string onto a decision_gate band.

    Unrecognised or absent values fall to "moderate" — the middle band — so a
    classifier label nobody anticipated can neither inflate the trivial band
    (where TDE looks best) nor the complex band (where it looks worst).
    """
    key = (task_complexity or "").strip().lower()
    band = _COMPLEXITY_TO_BAND.get(key)
    if band is None:
        # Never silent: an unmapped label routes a whole class of tasks into the
        # middle band, which looks like normal data. If the classifier's
        # vocabulary drifts, this line is the only way to notice before the
        # measurement week's evidence is already skewed.
        if key:
            _log.warning(
                "ADR-0222: unmapped complexity label %r — filing under "
                "'moderate'. Add it to _COMPLEXITY_TO_BAND if the classifier "
                "vocabulary changed.", task_complexity)
        return "moderate"
    return band  # type: ignore[return-value]


class RealTdeOrchestrator:
    """ADR-0222 k=5 — runs the REAL {direct, tier} baselines and judges them.

    Given a task that TDE has ALREADY completed (its tokens + output are passed
    in), this runs the two comparison arms and scores quality:

      1. ``whole_task_direct_baseline`` — whole task, one turn, USER's model.
         This is the reference: its loss is 0 by definition, its token count is
         the denominator of every savings figure.
      2. ``whole_task_tier_baseline`` (F5) — whole task, one turn, tier-resolved
         model. The simplest alternative per-step TDE has to beat.
      3. ``judge_loss_sync`` twice — tier-vs-direct and TDE-vs-direct — using the
         F1-upgraded judge (``CORVIN_TDE_JUDGE_MODEL``).

    SEQUENTIAL, not parallel, deliberately: the arms are each a `claude -p`
    one-shot, and running them concurrently would have them contend for the same
    CLI/rate-limit budget, putting contention noise into the very latency and
    token numbers being measured. The handoff's recommendation ("start sequential
    for a clean baseline") is kept.

    FAIL-CLOSED throughout: if an arm raises, a token count is missing, or the
    judge cannot produce a verdict, this returns ``None`` and NO sample is
    recorded. A dropped data point costs one turn of evidence; a fabricated one
    corrupts a verdict that flips a routing default.
    """

    #: Ceiling for one full measurement (both baselines + both judge calls).
    #: Beyond this the sample is abandoned — a measurement that outlives its
    #: usefulness must not keep burning quota in the background.
    TOTAL_TIMEOUT_S = 900

    @staticmethod
    def _tokens_of(local_result: Any) -> Optional[int]:
        """Extract total_tokens from a LocalResult, or None when unmeasured.

        Returns None (never 0) for a missing usage block, so the caller drops the
        sample instead of booking a zero that reads as 100% savings.
        """
        usage = getattr(local_result, "usage", None)
        if not isinstance(usage, dict):
            return None
        raw = usage.get("total_tokens")
        try:
            tokens = int(raw)
        except (TypeError, ValueError):
            return None
        return tokens if tokens > 0 else None

    @staticmethod
    def _output_of(local_result: Any) -> str:
        """Stringify a LocalResult's output for judging."""
        out = getattr(local_result, "output", None)
        if isinstance(out, str):
            return out
        return json.dumps(out, default=str) if out is not None else ""

    @classmethod
    async def orchestrate(
        cls,
        *,
        task_id: str,
        task_text: str,
        tde_tokens: int,
        tde_output: str,
        task_complexity: str | None = None,
        user_model: str | None = None,
        engine_id: str = "claude_code",
        workload_type: str | None = None,
        confidence: float | None = None,
        proc_holder: Any = None,
    ) -> MeasurementSample | None:
        """Run both baselines, judge both arms, and build one MeasurementSample.

        ``tde_tokens`` / ``tde_output`` describe the TDE run that already
        happened. ``tde_output`` MUST be the bare model answer — strip any UI
        badge before passing it, or the judge scores the badge as content and
        reports a quality loss TDE did not incur.

        Returns None when the sample cannot be honestly completed.
        """
        # Guard the TDE arm's own numbers first — cheapest possible rejection,
        # before spending two LLM turns on a sample that can never be valid.
        if not isinstance(tde_tokens, int) or tde_tokens <= 0:
            _log.warning(
                "measurement %s dropped: TDE token count is %r — the TDE arm was "
                "not usage-instrumented, so no honest comparison is possible",
                task_id, tde_tokens)
            return None
        if not (tde_output or "").strip():
            _log.warning("measurement %s dropped: empty TDE output", task_id)
            return None

        band = classify_band(task_complexity)
        statement = {"statement": task_text}

        try:
            async with _timeout_after(cls.TOTAL_TIMEOUT_S):
                from .tde_engine import (  # noqa: PLC0415 — avoid import cycle
                    whole_task_direct_baseline,
                    whole_task_tier_baseline,
                )

                direct = await whole_task_direct_baseline(
                    statement, user_model=user_model, proc_holder=proc_holder)
                tier = await whole_task_tier_baseline(
                    statement, engine_id=engine_id, user_model=user_model,
                    workload_type=workload_type, confidence=confidence,
                    proc_holder=proc_holder)

                direct_tokens = cls._tokens_of(direct)
                tier_tokens = cls._tokens_of(tier)
                if direct_tokens is None or tier_tokens is None:
                    _log.warning(
                        "measurement %s dropped: baseline usage missing "
                        "(direct=%r, tier=%r)", task_id, direct_tokens, tier_tokens)
                    return None

                direct_output = cls._output_of(direct)
                tier_output = cls._output_of(tier)
                if not direct_output.strip():
                    _log.warning(
                        "measurement %s dropped: direct baseline returned no "
                        "output, so there is no reference to judge against",
                        task_id)
                    return None

                from .loss_judge import judge_loss_sync  # noqa: PLC0415

                desc = f"whole task: {task_text[:200]}"
                tier_loss_pct = await asyncio.to_thread(
                    judge_loss_sync, desc, direct_output, tier_output)
                tde_loss_pct = await asyncio.to_thread(
                    judge_loss_sync, desc, direct_output, tde_output)
        except asyncio.TimeoutError:
            _log.warning("measurement %s dropped: exceeded %ss budget",
                         task_id, cls.TOTAL_TIMEOUT_S)
            return None
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            # Any arm failing (CLI missing, non-zero exit, unparseable envelope)
            # means this task has no honest sample. Logged with traceback, never
            # swallowed silently — the k=4 hook's bare `except: pass` is exactly
            # why two hard defects in it went unnoticed until they were read.
            _log.warning("measurement %s dropped: baseline arm failed",
                         task_id, exc_info=True)
            return None

        # judge_loss_sync returns None when the judge stack is unavailable or its
        # verdict is unparseable. Substituting a number here (0.0, or the lexical
        # fallback) would book a fabricated quality score — the precise defect
        # ADR-0222 F1 was written to close. No verdict, no sample.
        if tier_loss_pct is None or tde_loss_pct is None:
            _log.warning(
                "measurement %s dropped: judge returned no verdict "
                "(tier=%r, tde=%r)", task_id, tier_loss_pct, tde_loss_pct)
            return None

        judge_model = os.getenv("CORVIN_TDE_JUDGE_MODEL", "").strip() or "haiku"

        try:
            return MeasurementSample(
                task_id=task_id,
                task_band=band,
                timestamp=time.time(),
                direct_tokens=direct_tokens,
                direct_output=direct_output,
                tier_tokens=tier_tokens,
                tier_output=tier_output,
                tier_loss=tier_loss_pct / 100.0,
                tde_tokens=tde_tokens,
                tde_output=tde_output,
                tde_loss=tde_loss_pct / 100.0,
                quality_judge_model=judge_model,
            )
        except ValueError:
            # __post_init__ rejected the sample (out-of-range loss, non-positive
            # tokens). It is the last fail-closed backstop; honour it.
            _log.warning("measurement %s dropped: failed validation",
                         task_id, exc_info=True)
            return None

    @staticmethod
    async def _run_tde_arm(
        task_text: str, *, run_id: str, session_key: str, tenant_id: str,
    ) -> "tuple[int, str, str | None, str | None] | None":
        """Run TDE ITSELF for a whole task; return
        (tde_tokens, tde_output, complexity, workload_type) or None if unusable.

        The existing ``orchestrate`` takes the TDE arm as INPUT because TDE ran
        as the main turn. In SHADOW mode TDE never ran, so we run it HERE — its
        output is for the judge only and is NEVER shown to a user. Replicates the
        TDE mechanic from chat_runtime._stream_tde_turn (analysis → SendIntegration
        → select_engine_and_execute) plus the same full-instrumentation gate.

        Charging: this is a REAL TDE fan-out and books the shared compute pool at
        the execute chokepoint (_enforce_tde_compute_quota) — SELF-LIMITING: an
        exhausted pool returns reason=quota_exhausted → no summary → None, so a
        shadow run only ever books when there is headroom.
        """
        from .analysis_runner import run_initial_analysis_sync  # noqa: PLC0415
        from .engine_registry import EngineRegistry  # noqa: PLC0415
        from .send_integration import SendIntegration  # noqa: PLC0415

        context = {"statement": {"task": task_text}, "task_text": task_text}
        analysis = await asyncio.to_thread(
            run_initial_analysis_sync, task_text, context)
        integration = SendIntegration(
            registry=EngineRegistry(real_ipc=True),
            session_key=session_key, tenant_id=tenant_id,
        )
        _engine, result = await integration.select_engine_and_execute(
            "/use-engine tiered_delegation\n" + task_text, context, analysis,
            run_id=run_id,
        )
        result = result or {}
        if result.get("reason") == "quota_exhausted":
            _log.info("shadow %s: TDE arm skipped — shared pool exhausted", run_id)
            return None
        summary = result.get("summary") or {}
        # Full-instrumentation gate (mirror chat_runtime.py's _fully_instrumented):
        # partial instrumentation UNDER-counts TDE tokens = biased in TDE's favour.
        steps_total = summary.get("step_count") or 0
        steps_instr = summary.get("instrumented_step_count") or 0
        if steps_total <= 0 or steps_instr != steps_total:
            _log.info("shadow %s: TDE arm not fully instrumented (%s/%s) — drop",
                      run_id, steps_instr, steps_total)
            return None
        tde_tokens = summary.get("total_tokens")
        if not isinstance(tde_tokens, int) or tde_tokens <= 0:
            return None
        parts: list[str] = []
        for r in result.get("results", []) or []:
            out = getattr(r, "output", None)
            if getattr(r, "success", False) and out:
                parts.append(str(out))
        tde_output = ("\n\n".join(parts)).strip()
        if not tde_output:
            return None
        return (tde_tokens, tde_output,
                summary.get("complexity"), summary.get("task_type"))

    @classmethod
    async def orchestrate_shadow(
        cls, *, task_id: str, task_text: str, session_key: str, tenant_id: str,
        user_model: str | None = None, engine_id: str = "claude_code",
        proc_holder: Any = None,
    ) -> "MeasurementSample | None":
        """Shadow measurement — TDE never ran as the main turn (the user got the
        NATIVE answer). Run TDE HERE (output discarded), then feed the existing
        ``orchestrate`` with the TDE arm's real numbers.

        The native answer is ONLY the trigger, NOT a measured arm: it runs WITH
        tools/repo context, while ``whole_task_direct_baseline`` is deliberately
        tool-less. Using the native answer as ``direct`` would inflate the
        savings denominator and fabricate a TDE advantage — the exact defect
        ADR-0222 exists to prevent. So ``orchestrate`` still runs its own clean
        tool-less direct + tier baselines; the shadow only supplies the tde arm.
        """
        tde_arm = await cls._run_tde_arm(
            task_text, run_id=task_id, session_key=session_key,
            tenant_id=tenant_id)
        if tde_arm is None:
            return None
        tde_tokens, tde_output, complexity, workload_type = tde_arm
        return await cls.orchestrate(
            task_id=task_id, task_text=task_text,
            tde_tokens=tde_tokens, tde_output=tde_output,
            task_complexity=complexity, user_model=user_model,
            engine_id=engine_id, workload_type=workload_type,
            proc_holder=proc_holder,
        )


def _timeout_after(seconds: float) -> Any:
    """asyncio.timeout on 3.11+, falling back to a no-op on older runtimes.

    The repo targets 3.11 (asyncio.timeout landed there), but the fallback keeps
    the module importable rather than failing at definition time on 3.10.
    """
    _timeout = getattr(asyncio, "timeout", None)
    if _timeout is not None:
        return _timeout(seconds)

    class _NullCtx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    return _NullCtx()


# ============================================================================
# Test double — NOT used by any production path
# ============================================================================

class MockTdeOrchestrator:
    """Fixed-number stand-in for RealTdeOrchestrator, for tests only.

    Kept so the recorder/aggregation/gate pipeline can be exercised without
    spending LLM calls. The chat_runtime hook calls RealTdeOrchestrator; wiring
    this one into a production path would feed the decision gate invented
    numbers, which the honesty invariant forbids.
    """

    @staticmethod
    def classify_band(task_complexity: str | None) -> Literal["trivial", "moderate", "complex"]:
        """Delegate to the module-level classifier — one banding rule, not two."""
        return classify_band(task_complexity)

    @staticmethod
    def mock_direct_execution(prompt: str) -> dict[str, Any]:
        """Stub: direct turn (user model, single-call baseline)."""
        return {
            "tokens": 4500,
            "output": f"direct_answer_to_{prompt[:20]}",
            "loss": 0.0,  # Direct is reference
        }

    @staticmethod
    def mock_tier_execution(prompt: str) -> dict[str, Any]:
        """Stub: F5 whole-task-tier baseline."""
        return {
            "tokens": 4200,
            "output": f"tier_answer_to_{prompt[:20]}",
            "loss": 0.02,  # ~2% loss vs direct
        }

    @staticmethod
    async def orchestrate_measurement(
        prompt: str,
        tde_tokens: int,
        tde_output: str,
        task_complexity: str | None = None,
    ) -> MeasurementSample | None:
        """Orchestrate {direct, tier, TDE} and return sample for recording.

        k=4 Phase 1: Mock execution.
        k=5: Real parallel execution of direct + tier variants.
        """
        band = MockTdeOrchestrator.classify_band(task_complexity)
        direct = MockTdeOrchestrator.mock_direct_execution(prompt)
        tier = MockTdeOrchestrator.mock_tier_execution(prompt)

        # Synthetic TDE loss (worse than tier for demo)
        tde_loss = 0.05

        return MeasurementSample(
            task_id=f"tde-sample-{int(time.time())}",
            task_band=band,
            timestamp=time.time(),
            direct_tokens=direct["tokens"],
            direct_output=direct["output"],
            tier_tokens=tier["tokens"],
            tier_output=tier["output"],
            tier_loss=tier["loss"],
            tde_tokens=tde_tokens,
            tde_output=tde_output,
            tde_loss=tde_loss,
            quality_judge_model="haiku",
        )
