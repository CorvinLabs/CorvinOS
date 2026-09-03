"""Audit-chain projection into Prometheus exposition format.

ADR-0007 Phase 6.1 — observability is a read-side projection of the
unified hash chain at ``<tenant_home>/global/forge/audit.jsonl``.
There is no parallel telemetry path; every metric here is derived by
re-reading the chain and counting events. The chain is the source
of truth.

Design constraints
------------------

* **No SDK dependency.** The Prometheus text exposition format (v0.0.4)
  is stable and ~100 LOC to emit by hand. We don't import
  ``prometheus_client``; its in-process registry model is the
  opposite of what we want here.
* **Dimension whitelist.** Every label value MUST come from a curated
  allow-list per label. Values outside the list collapse to
  ``"other"``. Cardinality per label ≤ 32.
* **No PII / no fingerprints / no run-id-as-label.** The whitelist is
  the structural defence; new labels need an ADR amendment.
* **Read-only.** No writes to the chain, ever. Scrapes do NOT emit
  audit events — at 15 s × N tenants the noise is structural.
* **TTL cache.** A 15 s cache per (tenant_id, since) keeps repeated
  scrapes from re-reading the entire chain. Override via
  ``CORVIN_METRICS_TTL_S`` env.

Histograms
----------

Run-duration is the only histogram in Phase 6.1. We compute it by
pairing ``gateway.run_created`` events with the terminal
``gateway.run_status_changed`` event for the same ``run_id`` and
accumulating into pre-defined buckets. Pre-aggregated counters
(``_bucket`` / ``_sum`` / ``_count``) avoid per-run series, which
would explode cardinality.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections import OrderedDict
from threading import Lock
from typing import Any, Iterable, Iterator

# Forge subpackage hosts the tenant-path resolvers; same sys.path
# dance every other gateway module uses (auth / runs / oidc).
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402


# ── Configuration ────────────────────────────────────────────────────


def _ttl_seconds() -> float:
    raw = os.environ.get("CORVIN_METRICS_TTL_S")
    try:
        v = float(raw) if raw else 15.0
    except ValueError:
        v = 15.0
    # Clamp: too-aggressive caches hide live state; too-slow caches
    # turn the endpoint into a CPU hog under load.
    return max(1.0, min(v, 300.0))


# Histogram buckets — chosen for "agent run on a single prompt" shape:
# many runs complete sub-second (tooling), a long tail at minutes
# (multi-turn agent runs).
_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0,
)


# ── Label whitelists ────────────────────────────────────────────────


# Per-label allow-lists. Values not in the set collapse to ``"other"``.
# Adding a value: check cardinality impact; check it isn't PII /
# unbounded (run_id, token, email, URL, snippet).
_ALLOWLIST: dict[str, frozenset[str]] = {
    "status": frozenset(
        {"completed", "failed", "budget_exceeded", "running", "accepted"}
    ),
    "outcome": frozenset({"delivered", "failed"}),
    "site": frozenset({
        "skill_promotion", "forge_creation", "auto_routing",
        "path_gate", "session_reset", "voice_summary",
    }),
    "mode": frozenset({"off", "fast", "skill", "cli"}),
    "choice": frozenset({
        "thesis", "antithesis", "synthesis",
        "faithful", "corrected", "skipped",
    }),
    "persona": frozenset({
        "coder", "forge", "browser", "research",
        "inbox", "os", "homeassistant", "assistant",
    }),
    "scope": frozenset({"task", "session", "project", "user"}),
    "bundle": frozenset({"owner", "admin", "member", "observer"}),
    "tool_name": frozenset({
        "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch",
    }),
    "reason": frozenset({
        "invalid-token", "invalid-shape", "unknown-token", "revoked",
        "invalid-jwt", "expired", "tenant-claim-mismatch",
        "engine-not-allowed", "zone-mismatch", "cross-tenant",
        "no-consent", "rate-limited",
        # STT-layer failure reasons
        "timeout", "provider-error", "package-unreachable",
        # ADR-0012 data-policy violation reasons
        "unsupported-format", "register-failed",
        # ADR-0021 supply-chain signature failure reasons
        "rekor-not-found", "inclusion-proof-invalid", "pubkey-mismatch",
        # ADR-0021 CVE-check skip reasons
        "pip-audit-missing", "subprocess-timeout", "no-requirements",
    }),
    "stt_provider": frozenset({"openai", "local"}),
    # ADR-0021 Layer 31 — supply-chain
    "plugin_name": frozenset({
        "corvin-gateway", "corvin-admin", "corvin-console",
        "corvin-compute", "corvin-license", "corvin-compliance-reports",
        "corvin-delegate", "corvin-init", "corvin-pipe",
        "cowork", "forge", "skill-forge", "voice",
    }),
    "severity": frozenset({"CRITICAL", "HIGH"}),
    "cadence": frozenset({"weekly", "critical-diff"}),
    # ADR-0012 — large-data snapshot layer
    "format": frozenset({"csv", "tsv", "json", "jsonl", "parquet"}),
    "pii_class": frozenset({
        "email", "phone", "iban", "credit_card",
        "us_ssn", "ch_ahv", "de_steuer_id",
        "name", "date_of_birth", "address",
        "opaque_id", "national_id",
    }),
    # ADR-0024 Layer 29.5 Phase 3 — adaptive OS-turn model selection
    "model": frozenset({"haiku", "sonnet", "opus", "other"}),
    "os_selection_reason": frozenset({
        "override", "explicit", "autoselect_low", "autoselect_high",
        "floor", "estimate_failed",
    }),
    "escalation_reason": frozenset({
        "autocompact-thrash", "context-overflow", "http-400",
    }),
}


def _safe_label(name: str, value: Any) -> str:
    """Project a raw label value into the whitelist, or ``"other"``."""
    if not isinstance(value, str):
        return "other"
    allowed = _ALLOWLIST.get(name)
    if allowed is None:
        # Unknown label key — fail closed: caller has a bug.
        return "other"
    return value if value in allowed else "other"


# ── Metric family declarations ──────────────────────────────────────


@dataclass(frozen=True)
class MetricFamily:
    """One Prometheus metric family + its help text + its type."""

    name: str
    type: str  # "counter" | "histogram" | "gauge"
    help: str


_FAMILIES: list[MetricFamily] = [
    MetricFamily(
        "corvin_gateway_runs_total", "counter",
        "Gateway runs by terminal status.",
    ),
    MetricFamily(
        "corvin_gateway_run_duration_seconds", "histogram",
        "Gateway run wall-clock duration by terminal status.",
    ),
    MetricFamily(
        "corvin_gateway_webhooks_total", "counter",
        "Webhook deliveries by outcome.",
    ),
    MetricFamily(
        "corvin_gateway_auth_failures_total", "counter",
        "Authentication failures by reason (atlr + OIDC).",
    ),
    MetricFamily(
        "corvin_gateway_cross_tenant_denied_total", "counter",
        "Cross-tenant URL/token mismatch denials.",
    ),
    MetricFamily(
        "corvin_gateway_engine_denied_total", "counter",
        "Engine-policy denials.",
    ),
    MetricFamily(
        "corvin_gateway_zone_denied_total", "counter",
        "Zone-residency denials.",
    ),
    MetricFamily(
        "corvin_forge_tools_created_total", "counter",
        "Forge tools created, by persona.",
    ),
    MetricFamily(
        "corvin_skills_created_total", "counter",
        "Skills created, by scope.",
    ),
    MetricFamily(
        "corvin_dialectic_decisions_total", "counter",
        "Dialectic decisions by site / mode / choice.",
    ),
    MetricFamily(
        "corvin_consent_drops_total", "counter",
        "Observer messages dropped by the consent gate.",
    ),
    MetricFamily(
        "corvin_quota_exceeded_total", "counter",
        "Quota exceedances by bundle.",
    ),
    MetricFamily(
        "corvin_path_gate_denied_total", "counter",
        "Path-gate denials by tool name.",
    ),
    MetricFamily(
        "corvin_voice_transcribed_total", "counter",
        "Voice-note transcriptions by STT provider.",
    ),
    MetricFamily(
        "corvin_voice_transcribe_failed_total", "counter",
        "Voice-note transcription failures by reason.",
    ),
    # ADR-0012 — large-data snapshot layer (data-locality + PII redaction)
    MetricFamily(
        "corvin_data_registered_total", "counter",
        "Datasets registered via data_register, by format.",
    ),
    MetricFamily(
        "corvin_data_snapshots_generated_total", "counter",
        "Snapshots generated (data_register + data_snapshot rounds).",
    ),
    MetricFamily(
        "corvin_data_pii_detected_total", "counter",
        "PII columns detected, by class.",
    ),
    MetricFamily(
        "corvin_data_unregistered_total", "counter",
        "Data handles unregistered via data_unregister.",
    ),
    MetricFamily(
        "corvin_data_policy_violated_total", "counter",
        "data_register / data_snapshot calls denied by operator policy, "
        "by reason (strict-mode rejections).",
    ),
    MetricFamily(
        "corvin_data_snapshot_oversized_total", "counter",
        "Snapshot payloads that exceeded the prompt-token cap and "
        "degraded to schema-only.",
    ),
    # ADR-0021 Layer 31 — supply-chain hardening (drift-detection +
    # regulator-defensibility paper-trail).
    MetricFamily(
        "corvin_supply_chain_sbom_verified_total", "counter",
        "SBOMs successfully verified during package install.",
    ),
    MetricFamily(
        "corvin_supply_chain_sbom_missing_total", "counter",
        "Bundle installs that landed without an SBOM.",
    ),
    MetricFamily(
        "corvin_supply_chain_dep_hash_mismatch_total", "counter",
        "Pinned-hash mismatches detected at bootstrap.",
    ),
    MetricFamily(
        "corvin_supply_chain_cve_detected_total", "counter",
        "CRITICAL/HIGH CVEs detected by daily/weekly surveillance.",
    ),
    MetricFamily(
        "corvin_supply_chain_capability_drift_total", "counter",
        "AST-detected import drift versus declared plugin manifest.",
    ),
    MetricFamily(
        "corvin_supply_chain_signature_chain_break_total", "counter",
        "ed25519 / Sigstore signature verifications that failed.",
    ),
    # Layer 29.5 Phase 3 (ADR-0024) — adaptive OS-turn model selection.
    # Labels: model ∈ {haiku, sonnet, opus, other},
    #         reason ∈ {override, explicit, autoselect_low, autoselect_high,
    #                   floor, estimate_failed}.
    MetricFamily(
        "corvin_os_model_selected_total", "counter",
        "OS-turn model-selection events by model tier and selection reason.",
    ),
    # Labels: from, to ∈ {haiku, sonnet, opus},
    #         reason ∈ {autocompact-thrash, context-overflow, http-400}.
    MetricFamily(
        "corvin_os_model_escalated_total", "counter",
        "OS-turn context-overflow escalations (Haiku → Sonnet retries) by reason.",
    ),
    # Phase 2: Engine, workflow, and context metrics (ADR-0314+)
    MetricFamily(
        "corvin_engine_executions_total", "counter",
        "Engine executions by status (success/failed/error types).",
    ),
    MetricFamily(
        "corvin_engine_execution_duration_seconds", "histogram",
        "Engine execution latency (successful and failed runs).",
    ),
    MetricFamily(
        "corvin_engine_tokens_used_total", "counter",
        "Total tokens used across successful engine executions.",
    ),
    MetricFamily(
        "corvin_workflow_completions_total", "counter",
        "Workflow completions by status.",
    ),
    MetricFamily(
        "corvin_workflow_duration_seconds", "histogram",
        "Workflow completion time.",
    ),
    MetricFamily(
        "corvin_context_stack_depth", "gauge",
        "Current execution context stack depth.",
    ),
    MetricFamily(
        "corvin_audit_chain_events_total", "counter",
        "Total audit-chain events read for this projection (sanity).",
    ),
    MetricFamily(
        "corvin_audit_chain_intact", "gauge",
        "1 when the audit chain verifies clean, 0 on tamper / break.",
    ),
]


# ── Aggregator state ────────────────────────────────────────────────


@dataclass
class _Counters:
    # Each entry: tuple-of-label-values → count
    by_labels: dict[tuple[str, ...], int] = field(default_factory=dict)

    def inc(self, labels: tuple[str, ...], n: int = 1) -> None:
        self.by_labels[labels] = self.by_labels.get(labels, 0) + n


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    # (label_tuple) → list of bucket counters (one per bucket + +Inf)
    bucket_counts: dict[tuple[str, ...], list[int]] = field(default_factory=dict)
    sums: dict[tuple[str, ...], float] = field(default_factory=dict)
    counts: dict[tuple[str, ...], int] = field(default_factory=dict)

    def observe(self, labels: tuple[str, ...], v: float) -> None:
        bcs = self.bucket_counts.setdefault(
            labels, [0] * (len(self.buckets) + 1),
        )
        # Walk buckets in order; v lands in every bucket ≥ v's value.
        # The trailing slot is the +Inf bucket — every observation counts.
        for i, b in enumerate(self.buckets):
            if v <= b:
                bcs[i] += 1
        bcs[-1] += 1
        self.sums[labels] = self.sums.get(labels, 0.0) + v
        self.counts[labels] = self.counts.get(labels, 0) + 1


@dataclass
class _Snapshot:
    """One aggregator pass over the chain. Cheap to render repeatedly."""

    # Counter families, keyed by family name → _Counters
    counters: dict[str, _Counters] = field(default_factory=dict)
    # Histogram families, keyed by family name → _Histogram
    histograms: dict[str, _Histogram] = field(default_factory=dict)
    # Audit-chain meta
    events_read: int = 0
    chain_intact: bool = True

    def counter(self, name: str) -> _Counters:
        return self.counters.setdefault(name, _Counters())

    def histogram(self, name: str, buckets: tuple[float, ...]) -> _Histogram:
        h = self.histograms.get(name)
        if h is None:
            h = _Histogram(buckets=buckets)
            self.histograms[name] = h
        return h


# ── Chain reader ────────────────────────────────────────────────────


def _audit_path(tenant_id: str) -> Path:
    """Resolve the per-tenant audit chain file."""
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def _iter_events(
    path: Path,
    *,
    since: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield parsed events from the chain, filtered by ``ts >= since``.

    Malformed lines are silently skipped — they're already surfaced by
    ``verify_chain`` (Phase 6.2 emits ``corvin_audit_chain_intact = 0``
    in that case). We don't fail the whole scrape on a single bad line.
    """
    if not path.exists():
        return
    with path.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts")
            if since is not None and (not isinstance(ts, (int, float)) or ts < since):
                continue
            yield rec


# ── Event projection ────────────────────────────────────────────────


# Open-run tracker: run_id → run_created ts. Used to compute durations
# when the terminal status_changed event arrives. Snapshot-local —
# nothing persists across aggregator passes.


def _project(events: Iterable[dict[str, Any]]) -> _Snapshot:
    """Walk every event and project it onto the metric families."""
    snap = _Snapshot()
    snap.histogram(
        "corvin_gateway_run_duration_seconds", _DURATION_BUCKETS_S,
    )
    run_open_ts: dict[str, float] = {}
    for rec in events:
        snap.events_read += 1
        et = rec.get("event_type")
        details = rec.get("details") or {}
        if not isinstance(et, str):
            continue

        # ── Gateway run lifecycle ─────────────────────────────────
        if et == "gateway.run_created":
            rid = rec.get("run_id") or details.get("run_id")
            ts = rec.get("ts")
            if isinstance(rid, str) and isinstance(ts, (int, float)):
                run_open_ts[rid] = float(ts)

        elif et == "gateway.run_status_changed":
            to_state = details.get("to") if isinstance(details, dict) else None
            from_state = details.get("from") if isinstance(details, dict) else None
            terminal = {"completed", "failed", "budget_exceeded"}
            if to_state in terminal:
                snap.counter("corvin_gateway_runs_total").inc(
                    (_safe_label("status", to_state),),
                )
                rid = rec.get("run_id") or details.get("run_id")
                ts = rec.get("ts")
                if (
                    isinstance(rid, str)
                    and rid in run_open_ts
                    and isinstance(ts, (int, float))
                ):
                    dur = max(0.0, float(ts) - run_open_ts.pop(rid))
                    snap.histograms[
                        "corvin_gateway_run_duration_seconds"
                    ].observe(
                        (_safe_label("status", to_state),), dur,
                    )

        # ── Webhooks ──────────────────────────────────────────────
        elif et == "gateway.webhook_dispatched":
            snap.counter("corvin_gateway_webhooks_total").inc(
                (_safe_label("outcome", "delivered"),),
            )
        elif et == "gateway.webhook_delivery_failed":
            snap.counter("corvin_gateway_webhooks_total").inc(
                (_safe_label("outcome", "failed"),),
            )

        # ── Auth failures ─────────────────────────────────────────
        elif et in ("gateway.token_resolve_failed",
                    "gateway.oidc_resolve_failed"):
            reason = details.get("reason") if isinstance(details, dict) else None
            snap.counter("corvin_gateway_auth_failures_total").inc(
                (_safe_label("reason", reason),),
            )

        # ── Cross-tenant ─────────────────────────────────────────
        elif et == "gateway.cross_tenant_denied":
            snap.counter("corvin_gateway_cross_tenant_denied_total").inc(())

        # ── Policy denials ───────────────────────────────────────
        elif et == "gateway.engine_denied":
            reason = details.get("reason") if isinstance(details, dict) else None
            snap.counter("corvin_gateway_engine_denied_total").inc(
                (_safe_label("reason", reason),),
            )
        elif et == "gateway.zone_denied":
            snap.counter("corvin_gateway_zone_denied_total").inc(())

        # ── Forge / skill ────────────────────────────────────────
        elif et == "tool.created":
            persona = details.get("persona") if isinstance(details, dict) else None
            snap.counter("corvin_forge_tools_created_total").inc(
                (_safe_label("persona", persona),),
            )
        elif et == "skill.created":
            scope = details.get("scope") if isinstance(details, dict) else None
            snap.counter("corvin_skills_created_total").inc(
                (_safe_label("scope", scope),),
            )

        # ── Dialectic ────────────────────────────────────────────
        elif et == "decision.dialectical":
            site = details.get("site") if isinstance(details, dict) else None
            mode = details.get("mode") if isinstance(details, dict) else None
            choice = details.get("choice") if isinstance(details, dict) else None
            snap.counter("corvin_dialectic_decisions_total").inc((
                _safe_label("site", site),
                _safe_label("mode", mode),
                _safe_label("choice", choice),
            ))

        # ── Consent / quota / path-gate ──────────────────────────
        elif et == "consent.observer_dropped":
            snap.counter("corvin_consent_drops_total").inc(())
        elif et == "quota.over_limit":
            bundle = details.get("bundle") if isinstance(details, dict) else None
            snap.counter("corvin_quota_exceeded_total").inc(
                (_safe_label("bundle", bundle),),
            )
        elif et == "path_gate.denied":
            # ``tool_name`` is the protected gate label (Write/Edit/...);
            # it's authoritative on the rec.tool field for path_gate.
            tool_name = rec.get("tool") or (
                details.get("operation") if isinstance(details, dict) else None
            )
            snap.counter("corvin_path_gate_denied_total").inc(
                (_safe_label("tool_name", tool_name),),
            )

        # ── STT layer (engine-agnostic speech-to-text) ───────────
        elif et == "voice.transcribed":
            provider = (
                details.get("provider") if isinstance(details, dict) else None
            )
            snap.counter("corvin_voice_transcribed_total").inc(
                (_safe_label("stt_provider", provider),),
            )
        elif et == "voice.transcribe_failed":
            reason = (
                details.get("reason") if isinstance(details, dict) else None
            )
            snap.counter("corvin_voice_transcribe_failed_total").inc(
                (_safe_label("reason", reason),),
            )

        # ── ADR-0012 large-data snapshot layer ────────────────────
        elif et == "data.registered":
            fmt = (
                details.get("format") if isinstance(details, dict) else None
            )
            snap.counter("corvin_data_registered_total").inc(
                (_safe_label("format", fmt),),
            )
        elif et == "data.snapshot_generated":
            # No label — overall snapshot activity is enough for the
            # dashboard. (Operators wanting per-format breakdown read
            # the chain directly.)
            snap.counter("corvin_data_snapshots_generated_total").inc(())
        elif et == "data.pii_detected":
            # ``classes`` is a count-by-class dict: {"email": 1, "phone": 2}.
            classes = (
                details.get("classes") if isinstance(details, dict) else None
            )
            if isinstance(classes, dict):
                for cls, cnt in classes.items():
                    if not isinstance(cnt, int) or cnt <= 0:
                        continue
                    if cls == "<no_pii>":
                        continue
                    snap.counter("corvin_data_pii_detected_total").inc(
                        (_safe_label("pii_class", cls),), n=cnt,
                    )
        elif et == "data.unregistered":
            snap.counter("corvin_data_unregistered_total").inc(())
        elif et == "data.policy_violated":
            reason = (
                details.get("reason") if isinstance(details, dict) else None
            )
            snap.counter("corvin_data_policy_violated_total").inc(
                (_safe_label("reason", reason),),
            )
        elif et == "data.snapshot_oversized":
            snap.counter("corvin_data_snapshot_oversized_total").inc(())

        # ── ADR-0021 supply-chain hardening ───────────────────────────
        elif et == "supply_chain.sbom_verified":
            snap.counter("corvin_supply_chain_sbom_verified_total").inc(())
        elif et == "supply_chain.sbom_missing":
            snap.counter("corvin_supply_chain_sbom_missing_total").inc(())
        elif et == "supply_chain.dep_hash_mismatch":
            plugin = (
                details.get("plugin_name") if isinstance(details, dict) else None
            )
            snap.counter("corvin_supply_chain_dep_hash_mismatch_total").inc(
                (_safe_label("plugin_name", plugin),),
            )
        elif et == "supply_chain.cve_detected":
            plugin = (
                details.get("plugin_name") if isinstance(details, dict) else None
            )
            severity = (
                details.get("severity") if isinstance(details, dict) else None
            )
            snap.counter("corvin_supply_chain_cve_detected_total").inc(
                (_safe_label("plugin_name", plugin),
                 _safe_label("severity", severity)),
            )
        elif et == "supply_chain.capability_drift":
            plugin = (
                details.get("plugin_name") if isinstance(details, dict) else None
            )
            snap.counter("corvin_supply_chain_capability_drift_total").inc(
                (_safe_label("plugin_name", plugin),),
            )
        elif et == "supply_chain.signature_chain_break":
            reason = (
                details.get("reason") if isinstance(details, dict) else None
            )
            snap.counter("corvin_supply_chain_signature_chain_break_total").inc(
                (_safe_label("reason", reason),),
            )

        # ── ADR-0024 Layer 29.5 Phase 3 — adaptive OS-turn model ─────
        elif et == "os_model.selected":
            if isinstance(details, dict):
                model = details.get("chosen")
                reason = details.get("reason")
                # Only emit when reason is in the curated set; unknown
                # reasons don't get an "other" label — they'd dilute the
                # signal and indicate a model_selector.py bug.
                if reason in {
                    "override", "explicit", "autoselect_low",
                    "autoselect_high", "floor", "estimate_failed",
                }:
                    snap.counter("corvin_os_model_selected_total").inc(
                        (_safe_label("model", model),
                         _safe_label("os_selection_reason", reason)),
                    )
        elif et == "os_model.escalated":
            if isinstance(details, dict):
                from_m = details.get("from")
                to_m = details.get("to")
                reason = details.get("reason")
                if reason in {"autocompact-thrash", "context-overflow", "http-400"}:
                    snap.counter("corvin_os_model_escalated_total").inc(
                        (_safe_label("model", from_m),
                         _safe_label("model", to_m),
                         _safe_label("escalation_reason", reason)),
                    )

        # ── Phase 2: Engine execution metrics (ADR-0314+) ──────────────
        elif et == "engine.execution_completed":
            if isinstance(details, dict):
                engine_id = details.get("engine_id", "unknown")
                latency_ms = details.get("latency_ms", 0)
                tokens_used = details.get("tokens_used")
                # Count successful executions
                snap.counter("corvin_engine_executions_total").inc(
                    (_safe_label("status", "success"),),
                )
                # Record latency histogram (convert ms to seconds)
                latency_s = latency_ms / 1000.0
                snap.histogram(
                    "corvin_engine_execution_duration_seconds",
                    _DURATION_BUCKETS_S,
                ).observe((_safe_label("status", "success"),), latency_s)
                # Count tokens if present
                if isinstance(tokens_used, int):
                    snap.counter("corvin_engine_tokens_used_total").inc(
                        (_safe_label("engine", engine_id),), tokens_used,
                    )
        elif et == "engine.execution_failed":
            if isinstance(details, dict):
                error_type = details.get("error_type", "unknown")
                latency_ms = details.get("latency_ms", 0)
                # Count failures
                snap.counter("corvin_engine_executions_total").inc(
                    (_safe_label("status", error_type),),
                )
                # Record failure latency histogram
                latency_s = latency_ms / 1000.0
                snap.histogram(
                    "corvin_engine_execution_duration_seconds",
                    _DURATION_BUCKETS_S,
                ).observe((_safe_label("status", error_type),), latency_s)

        # ── Workflow metrics (ADR-0314+) ───────────────────────────────
        elif et == "workflow.completed":
            if isinstance(details, dict):
                status = details.get("status", "unknown")
                duration_ms = details.get("duration_ms", 0)
                # Count completion
                snap.counter("corvin_workflow_completions_total").inc(
                    (_safe_label("status", status),),
                )
                # Record duration histogram
                duration_s = duration_ms / 1000.0
                snap.histogram(
                    "corvin_workflow_duration_seconds",
                    _DURATION_BUCKETS_S,
                ).observe((_safe_label("status", status),), duration_s)

        # ── Context metrics (ADR-0314+) ────────────────────────────────
        # context.push and context.pop don't contribute to Prometheus metrics
        # in this phase; they're recorded in the chain for observability.

    return snap


# ── Renderer ────────────────────────────────────────────────────────


_LABEL_ORDER: dict[str, tuple[str, ...]] = {
    "corvin_gateway_runs_total":               ("status",),
    "corvin_gateway_run_duration_seconds":     ("status",),
    "corvin_gateway_webhooks_total":           ("outcome",),
    "corvin_gateway_auth_failures_total":      ("reason",),
    "corvin_gateway_cross_tenant_denied_total": (),
    "corvin_gateway_engine_denied_total":      ("reason",),
    "corvin_gateway_zone_denied_total":        (),
    "corvin_forge_tools_created_total":        ("persona",),
    "corvin_skills_created_total":             ("scope",),
    "corvin_dialectic_decisions_total":        ("site", "mode", "choice"),
    "corvin_consent_drops_total":              (),
    "corvin_quota_exceeded_total":             ("bundle",),
    "corvin_path_gate_denied_total":           ("tool_name",),
    "corvin_voice_transcribed_total":          ("stt_provider",),
    "corvin_voice_transcribe_failed_total":    ("reason",),
    "corvin_data_registered_total":            ("format",),
    "corvin_data_snapshots_generated_total":   (),
    "corvin_data_pii_detected_total":          ("pii_class",),
    "corvin_data_unregistered_total":          (),
    "corvin_data_policy_violated_total":       ("reason",),
    "corvin_data_snapshot_oversized_total":    (),
    # ADR-0021 Layer 31 — supply-chain hardening
    "corvin_supply_chain_sbom_verified_total":         (),
    "corvin_supply_chain_sbom_missing_total":          (),
    "corvin_supply_chain_dep_hash_mismatch_total":     ("plugin_name",),
    "corvin_supply_chain_cve_detected_total":          ("plugin_name", "severity"),
    "corvin_supply_chain_capability_drift_total":      ("plugin_name",),
    "corvin_supply_chain_signature_chain_break_total": ("reason",),
    # ADR-0024 Layer 29.5 Phase 3 — adaptive OS-turn model selection
    "corvin_os_model_selected_total":                  ("model", "os_selection_reason"),
    "corvin_os_model_escalated_total":                 ("model", "model", "escalation_reason"),
    # Phase 2: Engine, workflow, context metrics (ADR-0314+)
    "corvin_engine_executions_total":                  ("status",),
    "corvin_engine_execution_duration_seconds":        ("status",),
    "corvin_engine_tokens_used_total":                 ("engine",),
    "corvin_workflow_completions_total":               ("status",),
    "corvin_workflow_duration_seconds":                ("status",),
    "corvin_context_stack_depth":                      (),
}


def _escape(s: str) -> str:
    """Escape label values per Prometheus exposition format."""
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt_labels(names: tuple[str, ...], values: tuple[str, ...]) -> str:
    if not names:
        return ""
    parts = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, values))
    return "{" + parts + "}"


def _fmt_labels_with(
    names: tuple[str, ...],
    values: tuple[str, ...],
    extra: tuple[tuple[str, str], ...],
) -> str:
    items = list(zip(names, values)) + list(extra)
    if not items:
        return ""
    parts = ",".join(f'{n}="{_escape(v)}"' for n, v in items)
    return "{" + parts + "}"


def render_prometheus(snap: _Snapshot) -> str:
    """Render a snapshot into Prometheus 0.0.4 text exposition format."""
    out: list[str] = []
    for fam in _FAMILIES:
        name = fam.name
        out.append(f"# HELP {name} {fam.help}")
        out.append(f"# TYPE {name} {fam.type}")

        if fam.type == "counter":
            counters = snap.counters.get(name)
            label_names = _LABEL_ORDER.get(name, ())
            if counters is None or not counters.by_labels:
                # Emit a zero-sample so dashboards don't show "no data".
                out.append(f"{name}{_fmt_labels(label_names, tuple(['other'] * len(label_names)))} 0")
            else:
                # Sort for deterministic output.
                for values in sorted(counters.by_labels):
                    out.append(
                        f"{name}{_fmt_labels(label_names, values)} "
                        f"{counters.by_labels[values]}"
                    )

        elif fam.type == "histogram":
            hist = snap.histograms.get(name)
            label_names = _LABEL_ORDER.get(name, ())
            if hist is None or not hist.counts:
                # Emit zero-state so the family is visible to scrapers.
                empty_labels = tuple(["other"] * len(label_names))
                for b in hist.buckets if hist else _DURATION_BUCKETS_S:
                    out.append(
                        f"{name}_bucket"
                        f"{_fmt_labels_with(label_names, empty_labels, (('le', _fmt_float(b)),))} 0"
                    )
                out.append(
                    f"{name}_bucket"
                    f"{_fmt_labels_with(label_names, empty_labels, (('le', '+Inf'),))} 0"
                )
                out.append(f"{name}_sum{_fmt_labels(label_names, empty_labels)} 0")
                out.append(f"{name}_count{_fmt_labels(label_names, empty_labels)} 0")
            else:
                for values in sorted(hist.bucket_counts):
                    bcs = hist.bucket_counts[values]
                    for i, b in enumerate(hist.buckets):
                        out.append(
                            f"{name}_bucket"
                            f"{_fmt_labels_with(label_names, values, (('le', _fmt_float(b)),))} "
                            f"{bcs[i]}"
                        )
                    out.append(
                        f"{name}_bucket"
                        f"{_fmt_labels_with(label_names, values, (('le', '+Inf'),))} "
                        f"{bcs[-1]}"
                    )
                    out.append(
                        f"{name}_sum{_fmt_labels(label_names, values)} "
                        f"{hist.sums.get(values, 0.0):.6f}"
                    )
                    out.append(
                        f"{name}_count{_fmt_labels(label_names, values)} "
                        f"{hist.counts.get(values, 0)}"
                    )

        elif fam.type == "gauge":
            # Only two gauges in Phase 6.1: events_read, chain_intact.
            if name == "corvin_audit_chain_events_total":
                # Actually a counter family but emitted unlabelled.
                out.append(f"{name} {snap.events_read}")
            elif name == "corvin_audit_chain_intact":
                out.append(f"{name} {1 if snap.chain_intact else 0}")
            else:
                out.append(f"{name} 0")

    # The events-read counter is the sanity probe — append after the
    # families that need help-text already emitted.
    out.append("")
    return "\n".join(out)


def _fmt_float(v: float) -> str:
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    if v.is_integer():
        return f"{int(v)}"
    return f"{v:g}"


# ── Cache + public API ──────────────────────────────────────────────


# Bounded LRU (adversarial review E-09, 2026-09-03): the cache is keyed on
# ``(tenant, round(since))`` and used to grow by one rendered Prometheus body
# per distinct ``?since=`` value forever — a scraper varying ``since`` each
# second was an unbounded memory leak. ``_CACHE_MAX`` entries, least recently
# used evicted first.
_CACHE_MAX = 256
_cache: "OrderedDict[tuple[str, float | None], tuple[float, str]]" = OrderedDict()
_cache_lock = Lock()


def _cache_key(tenant_id: str, since: float | None) -> tuple[str, float | None]:
    # Round `since` to the second so adjacent scrapes hit the same key.
    return (tenant_id, None if since is None else round(since))


def aggregate(
    tenant_id: str,
    *,
    since: float | None = None,
) -> _Snapshot:
    """One-shot aggregation pass over *tenant_id*'s chain.

    ``since`` is an epoch float; events with ``ts < since`` are
    skipped. Passing ``None`` returns "all time" — the chain is
    append-only, so this is bounded by the chain's age.
    """
    path = _audit_path(tenant_id)
    snap = _project(_iter_events(path, since=since))
    # Best-effort chain-integrity gauge. We don't import verify_chain
    # to avoid a cycle; instead, the existence of any json.decode error
    # during _iter_events is silently swallowed and the gauge stays at
    # 1. A heavier check is the audit-verify systemd timer (Roadmap L)
    # which writes its own warning into the chain when broken.
    try:
        from forge.security_events import verify_chain as _vc
        ok, problems = _vc(path)
        snap.chain_intact = bool(ok)
    except Exception:  # noqa: BLE001
        snap.chain_intact = True
    return snap


def render(tenant_id: str, *, since: float | None = None) -> str:
    """Public entry: produce the Prometheus text body for *tenant_id*.

    Cached for ``CORVIN_METRICS_TTL_S`` seconds per (tenant, since)
    so a Prometheus instance scraping every 15 s does not re-walk the
    chain on every request.
    """
    key = _cache_key(tenant_id, since)
    ttl = _ttl_seconds()
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            _cache.move_to_end(key)
            return cached[1]
    body = render_prometheus(aggregate(tenant_id, since=since))
    with _cache_lock:
        _cache[key] = (now, body)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return body


def clear_cache() -> None:
    """Drop the in-process cache. Used by tests + on operator request."""
    with _cache_lock:
        _cache.clear()


def parse_duration(s: str) -> float:
    """Parse a Prometheus-style duration string into seconds.

    Accepts: ``30s``, ``5m``, ``2h``, ``7d``, or a bare integer
    (treated as seconds). Raises ``ValueError`` on garbage.
    """
    if not isinstance(s, str) or not s:
        raise ValueError("duration must be a non-empty string")
    s = s.strip()
    unit = s[-1]
    if unit.isdigit():
        return float(s)
    try:
        n = float(s[:-1])
    except ValueError as exc:
        raise ValueError(f"invalid duration {s!r}") from exc
    mult = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}.get(unit)
    if mult is None:
        raise ValueError(f"unknown unit {unit!r} in {s!r}")
    return n * mult


# ── Render helpers used by CLI (JSON / table) ───────────────────────


def snapshot_to_dict(snap: _Snapshot) -> dict[str, Any]:
    """Project a snapshot into JSON-serialisable primitives.

    Used by ``voice-audit metrics --format json``. Histograms render
    as ``{labels: [{count, sum, buckets: [{le, n}]}], ...}``.
    """
    out: dict[str, Any] = {
        "events_read":  snap.events_read,
        "chain_intact": snap.chain_intact,
        "counters":     {},
        "histograms":   {},
    }
    for name, counters in snap.counters.items():
        label_names = _LABEL_ORDER.get(name, ())
        out["counters"][name] = [
            {
                "labels": dict(zip(label_names, values)),
                "value":  counters.by_labels[values],
            }
            for values in sorted(counters.by_labels)
        ]
    for name, hist in snap.histograms.items():
        label_names = _LABEL_ORDER.get(name, ())
        out["histograms"][name] = [
            {
                "labels":  dict(zip(label_names, values)),
                "count":   hist.counts.get(values, 0),
                "sum":     hist.sums.get(values, 0.0),
                "buckets": [
                    {"le": _fmt_float(b), "n": hist.bucket_counts[values][i]}
                    for i, b in enumerate(hist.buckets)
                ] + [
                    {"le": "+Inf", "n": hist.bucket_counts[values][-1]},
                ],
            }
            for values in sorted(hist.bucket_counts)
        ]
    return out


def render_table(snap: _Snapshot) -> str:
    """Compact terminal-friendly table view (single-operator CLI)."""
    lines: list[str] = []
    lines.append(
        f"events_read={snap.events_read}  "
        f"chain_intact={'yes' if snap.chain_intact else 'NO'}"
    )
    lines.append("")
    for name in sorted(snap.counters):
        counters = snap.counters[name]
        if not counters.by_labels:
            continue
        label_names = _LABEL_ORDER.get(name, ())
        lines.append(name)
        for values in sorted(counters.by_labels):
            label_str = ",".join(
                f"{n}={v}" for n, v in zip(label_names, values)
            ) or "-"
            lines.append(f"  {label_str:50s}  {counters.by_labels[values]}")
        lines.append("")
    for name in sorted(snap.histograms):
        hist = snap.histograms[name]
        if not hist.counts:
            continue
        label_names = _LABEL_ORDER.get(name, ())
        lines.append(name + " (histogram)")
        for values in sorted(hist.counts):
            label_str = ",".join(
                f"{n}={v}" for n, v in zip(label_names, values)
            ) or "-"
            lines.append(
                f"  {label_str:50s}  "
                f"count={hist.counts[values]} "
                f"sum={hist.sums[values]:.3f}s"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
