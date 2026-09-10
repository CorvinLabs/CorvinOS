"""Structured security events with optional sha256 hash-chain.

Every event is a JSON object with these stable fields:

    ts          (float)  unix epoch seconds
    event_type  (str)    one of EVENT_SEVERITY's keys (or any string;
                         unknown types default to severity INFO)
    severity    (str)    INFO | WARNING | ERROR | CRITICAL
    run_id      (str)    optional — empty when not tied to a specific run
    tool        (str)    optional — tool name when applicable
    details     (object) free-form, event-specific

When ``hash_chain=True`` (the default), each record additionally carries:

    prev_hash   (str)    the ``hash`` of the previous record (or "" for the
                         first chain entry)
    hash        (str)    sha256(prev_hash || canonical_record_json)[:16]

Tampering with any field of a record, or removing/inserting a record,
breaks the chain at that point and ``verify_chain`` reports the offset —
**but only for append-time / partial tampering.** The hash is keyless
``sha256``, so a writer-capable attacker who edits a record AND recomputes
every subsequent hash produces a chain that ``verify_chain`` still accepts.
Full-rewrite resistance requires the ADR-0137 external anchor (keyed MAC /
NBAC genesis / TSA), which is verified separately. Do not rely on this
self-verification alone as tamper-evidence against an attacker with write
access to the chain file.
"""
from __future__ import annotations

import collections
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# 2026-08-03, reported live: a real Windows install's audit.jsonl accumulated
# 1092 scattered hash-chain breaks over its history and eventually hit a
# hard, non-recoverable boot failure (ADR-0232/0233 tripwire, no override —
# core/compliance/corvin_compliance_reports/tripwire.py). Root cause: this
# module's cross-process write lock is `fcntl.flock()`, and forge's own
# Windows compat shim (forge/_wincompat.py, installed before any forge
# submodule's `import fcntl` runs) intentionally degrades flock/lockf to a
# NO-OP on Windows — correct for forge's REGISTRY files (single-host,
# already atomic via temp-file+rename per that shim's own docstring), but
# that assumption does not hold here: the lock below exists BECAUSE
# voice-adapter and forge-MCP-server are different PROCESSES writing the
# same chain — exactly the "cross-process advisory locks" case the shim's
# docstring waves off as "a Linux multi-process-deployment concern". That
# dismissal is wrong for CorvinOS specifically: adapter + console + N
# bridge daemons are separate processes on every platform, Windows
# included, so multi-process is the NORMAL case here, not an edge one.
# With no real lock, two racing processes can both read the same
# `prev_hash` and both append a record claiming to follow it — a
# hash-chain fork that manifests as exactly the observed scattered,
# intermittent corruption, permanent because the chain is append-only.
#
# Fixed with real Windows locking via msvcrt.locking() for this one
# security-critical section, instead of relying on the (correctly) inert
# fcntl shim. msvcrt has no whole-file or shared/exclusive distinction like
# flock — the portable fix locks a single, well-known byte as a pure mutex
# indicator: every writer/reader locks that SAME byte before touching the
# file, regardless of where the real read/append happens, which correctly
# serializes access. Operates on the raw fd via os.lseek (not the buffered
# TextIOWrapper's own seek) so it is unaffected by the file being opened in
# append mode. Locking a byte beyond the current EOF is well-defined Windows
# behaviour and the standard basis for this exact cross-platform idiom.
#
# 2026-08-06, reported live (Discord, CLAG L22.engine_spawn): that sentinel
# byte must NOT be offset 0. Unlike POSIX flock, Windows LockFile ranges are
# MANDATORY and enforced per-HANDLE, not per-process — a locked byte is
# unreadable even by the process that locked it, through any other handle.
# write_event() takes this lock on its append handle and then calls
# _last_hash(path), which opens the chain a SECOND time and scans it in
# _TAIL_BLOCK (8 KiB) blocks from the end. On a chain smaller than one block
# that scan starts at offset 0 — i.e. straight into our own lock —
# and fails with ERROR_LOCK_VIOLATION → PermissionError [Errno 13].
# Symptom: after the very first record (size==0 short-circuits the scan) NO
# hash-chained write ever succeeds again; only hash_chain=False events land,
# and CLAG fail-closes every engine spawn with `audit_write_failed`. The same
# collision hits audit_health_check(), whose verify_chain() re-read under the
# read lock surfaced as `verify_errored / PermissionError`. Note this is NOT
# the read-only-attribute failure fixed in 0.10.112 (that one is a genuine
# os.open EACCES); the retry added there cannot help, because this
# PermissionError is raised by the read inside _last_hash, not by os.open.
# So: lock a byte far past any plausible EOF, where it can never overlap a
# real record. Every participant derives it from the same constant, so the
# cross-process mutex the lock exists for is fully preserved.
#
# Branches on msvcrt's IMPORTABILITY, not sys.platform, and checks it at
# call time rather than gating the def at module-import time — the module
# only exists on a real Windows Python build, so this is equivalent in
# production, but it also means a test can monkeypatch the module-level
# `msvcrt` name (e.g. a fake module) to exercise the Windows branch on any
# platform, instead of needing an import-time reload.
try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]


# Offset of the Windows mutex sentinel byte: 1 TiB, i.e. past the EOF of any
# audit chain that could exist (the rotation threshold is orders of magnitude
# below this). Locking beyond EOF is legal and does not extend the file.
_LOCK_SENTINEL_OFFSET = 1 << 40


def _lock_chain(fh, shared: bool = False) -> None:  # noqa: ARG001 - no distinct shared mode on Windows
    if msvcrt is not None:
        fd = fh.fileno()
        saved = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _LOCK_SENTINEL_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        finally:
            os.lseek(fd, saved, os.SEEK_SET)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)


def _unlock_chain(fh) -> None:
    if msvcrt is not None:
        fd = fh.fileno()
        saved = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _LOCK_SENTINEL_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.lseek(fd, saved, os.SEEK_SET)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


EVENT_SEVERITY: dict[str, str] = {
    # registry lifecycle (past-tense canonical names, emitted by registry.py)
    "tool.created":              "INFO",
    "tool.deleted":              "INFO",
    "tool.promoted":             "INFO",
    # security: secrets guard (runner.py — tool with meta.secrets + use_sandbox=False)
    "forge.secrets_no_sandbox":  "WARNING",
    "tool.tamper_detected":      "WARNING",
    # policy enforcement
    "policy.import_denied":      "WARNING",
    "policy.namespace_denied":   "WARNING",
    "policy.budget_clamped":     "INFO",
    "acl.persona_denied":        "WARNING",
    # capability/persona gate (layer 9 — cross-persona forge access)
    "tool.namespace_denied":     "WARNING",
    "skill.namespace_denied":    "WARNING",
    "policy.reloaded":           "INFO",
    "policy.reload_failed":      "ERROR",
    # runtime guardrails
    "rate_limit.exceeded":       "WARNING",
    "circuit_breaker.rejected":  "WARNING",
    "circuit_breaker.opened":    "WARNING",
    "circuit_breaker.half_open": "INFO",
    "circuit_breaker.closed":    "INFO",
    # other
    "permission.denied":         "WARNING",
    "secret.redacted":           "INFO",
    "budget.exceeded":           "WARNING",
    # layer 16 v3 — secret vault (capability-style injection)
    "tool.secrets_injected":     "INFO",
    "acl.persona_secret_denied": "WARNING",
    "secret.vault_missing":      "WARNING",
    "secret.vault_malformed":    "ERROR",
    # integrity
    "audit.integrity_violation": "CRITICAL",
    # R4 — chain-convergence seam. Emitted ONCE into the canonical chain when a
    # writer that used to resolve a different file for the same tenant scope
    # starts writing here; names the superseded chain's path key, its final tail
    # hash and its record count so an auditor following the canonical chain can
    # reach the historical file and verify it. Never a merge, never a rewrite —
    # the chains are append-only.
    "audit.chain_supersedes":    "WARNING",
    # Layer 34 — Data classification + flow guard (ADR-0042)
    "data_flow.approved":        "INFO",
    "data_flow.blocked":         "CRITICAL",
    # Layer 35 — Network egress lockdown (ADR-0043)
    # ADR-0167 M1 — Entangled License Ratchet integration
    "egress.approved":           "INFO",
    "egress.blocked":            "CRITICAL",
    "egress.policy_disabled":    "WARNING",
    "egress.preset_loaded":      "INFO",
    "egress.ratchet_decision":   "INFO",     # M1: ratchet-derived policy decision
    "egress.ratchet_committed":  "INFO",     # M1: commitment hash written to chain
    # Layer 36 — GDPR Art. 17 erasure orchestrator (ADR-0045)
    "erasure.requested":         "WARNING",
    "erasure.applied":           "INFO",
    "erasure.skipped":           "INFO",
    "erasure.failed":            "CRITICAL",
    "erasure.completed":         "WARNING",
    # Layer 37 — Audit-at-rest encryption + retention (ADR-0044)
    "audit.rotation_link":          "INFO",
    "audit.rotation_started":       "INFO",
    "audit.rotation_failed":        "CRITICAL",
    "audit.segment_sealed":         "INFO",
    "audit.segment_retired":        "INFO",
    "audit.unseal_requested":       "WARNING",
    "audit.segment_timestamped":    "INFO",     # RFC 3161 TSA timestamp applied — non-fatal
    "audit.tsa_request_failed":     "WARNING",  # TSA unavailable — non-fatal by design (CLAUDE.md)
    # session lifecycle (layer 8 — /new /clear /reset and the daily timeout sweep)
    "session.reset":             "INFO",
    "session.timeout":           "INFO",
    # path_gate hook (layer 10 — direct-FS-write protection on forge/skill-forge workspaces)
    "path_gate.denied":          "WARNING",
    # ADR-0109 M6 — engine-trace: tool call annotated with ACS worker context.
    # Allowed fields: tool_name, worker_id, run_id, decision, tenant_id.
    # Forbidden: tool input params, output content, file paths.
    "forge.tool_executed":       "INFO",
    # path_gate AST gate — LLM-generated Python code execution (layer 10 extension)
    # Metadata only: language, outcome, blocked_reason. Never code content.
    "code.exec_attempt":         "INFO",
    "code.exec_blocked":         "WARNING",
    # layer-16 v2 — network-sharing audit (visibility for browser/research personas)
    "tool.network_share":        "INFO",
    # layer-16 v2 — PIN-elevation lifecycle (E)
    "auth.elevation_grant":      "INFO",
    "auth.elevation_revoke":     "INFO",
    "auth.elevation_required":   "WARNING",
    # STT layer (engine-agnostic speech-to-text — voice notes from bridges)
    "voice.transcribed":         "INFO",
    "voice.transcribe_failed":   "WARNING",
    # ADR-0008 — bridges runtime state migration (in-repo → ~/.corvin/bridges/)
    "bridges.path_migrated":     "INFO",
    # ADR-0012 — large-data snapshot layer (data-locality + PII redaction)
    "data.registered":           "INFO",
    "data.snapshot_generated":   "INFO",
    "data.pii_detected":         "INFO",
    "data.unregistered":         "INFO",
    "data.policy_violated":      "WARNING",
    "data.snapshot_oversized":   "WARNING",
    # ADR-0023 Layer 32 — strict-anonymisation snapshot mode.
    # Metadata only — no field names, no values, no regex hits in
    # clear. Per-event allow-list in corvin_data/mcp_handlers.py.
    "data.strict_anonymisation_applied":     "INFO",
    "data.anonymisation_rejected_pii_leak":  "WARNING",
    # ADR-0013 — compute-worker plugin (out-of-LLM-loop iteration driver)
    "compute.run_started":         "INFO",
    "compute.iteration_completed": "INFO",
    "compute.run_terminal":        "INFO",
    "compute.run_failed":          "WARNING",
    "compute.worker_unreachable":  "WARNING",
    "compute.run_recovering":      "INFO",
    # ADR-0026 — Compute Fabric (fabric backends, Oracle, parallel, datasource adapters)
    # Metadata only — parameter values, weights, training data, Oracle output NEVER in chain.
    # steering_keys carries key NAMES only, never direction/magnitude.
    "compute.backend_session_started":  "INFO",
    "compute.epoch_completed":          "INFO",
    "compute.oracle_steer_applied":     "INFO",
    "compute.oracle_subprocess_failed": "WARNING",
    "compute.shard_completed":          "INFO",
    "compute.aggregation_completed":    "INFO",
    "compute.backend_plugin_enabled":   "INFO",
    "compute.backend_plugin_disabled":  "INFO",
    "compute.checkpoint_written":       "INFO",
    "compute.artifact_registered":      "INFO",
    "compute.resource_slot_denied":     "WARNING",
    # ADR-0099 — Anthropic Batch API compute backend.
    # Metadata only — no prompt text, no inference output, no batch content.
    "compute.batch_submitted":     "INFO",
    "compute.batch_completed":     "INFO",
    "compute.batch_partial":       "WARNING",
    "compute.batch_cancelled":     "INFO",
    "compute.batch_gate_blocked":  "WARNING",
    "compute.batch_api_error":     "ERROR",
    "compute.batch_fallback":      "INFO",
    # ADR-0026 Section D — DataSourceAdapter system.
    # Metadata only — credentials, raw data, raw watermark values NEVER in chain.
    # watermark fields are sha256[:8] hashes, never raw values.
    "datasource.registered":            "INFO",
    "datasource.schema_refreshed":      "INFO",
    "datasource.connection_tested":     "INFO",
    "datasource.connection_failed":     "WARNING",
    "datasource.watermark_advanced":    "INFO",
    "datasource.residency_violation":   "WARNING",
    "datasource.pii_detected":          "INFO",
    "datasource.adapter_enabled":       "INFO",
    "datasource.adapter_disabled":      "INFO",
    "datasource.preview_generated":     "INFO",
    "datasource.unregistered":          "INFO",
    # ADR-0014 — admin-UI plugin (operator-facing web console)
    "admin.session_started":       "INFO",
    "admin.session_ended":         "INFO",
    "admin.session_denied":        "WARNING",
    "admin.action_performed":      "INFO",
    "admin.action_failed":         "WARNING",
    "admin.export_generated":      "INFO",
    # ADR-0015 — corvin-console plugin (owner-self-service web UI)
    "console.session_started":          "INFO",
    "console.session_ended":            "INFO",
    "console.session_denied":           "WARNING",
    "console.action_performed":         "INFO",
    "console.action_failed":            "WARNING",
    "console.engine_setting_updated":   "INFO",  # ADR-0067 M2.4
    # Layer 26 — autonomous user-style learner (closed-loop bullet pipeline)
    "user_style.candidate_proposed":  "INFO",
    "user_style.candidate_rejected":  "WARNING",
    "user_style.bullet_promoted":     "INFO",
    "user_style.bullet_rolled_back":  "WARNING",
    # Layer 27 — personal tools (user's permanent forge library, me.* namespace)
    "tool.user_saved":     "INFO",
    "tool.user_removed":   "INFO",
    # Layer 28 — conversation recall + user modeling (ADR-0016)
    # Metadata only — text content never lands in the chain.
    "memory.turn_indexed":          "INFO",
    "memory.recall_query":          "INFO",
    "memory.indexing_failed":       "WARNING",
    "memory.user_model_distilled":  "INFO",
    "memory.user_model_distill_failed": "WARNING",
    "memory.user_model_forgotten":  "INFO",
    # Layer 28 — GDPR Art. 17 recall purge (L36 handler emits per-layer confirmation).
    # Metadata only: layer_id, count. subject_id is NEVER logged (pseudonymity).
    "memory.recall_purged":         "WARNING",
    # Layer 28.1 — GDPR Art. 17 turn deletion (conversation_recall.forget()).
    # Metadata only: channel, chat_key, before_ts, rows_deleted. Never text content.
    # Detail allow-list lives in conversation_recall.py::_AUDIT_ALLOWED_FIELDS.
    "memory.turns_forgotten":       "WARNING",
    # Social federation (Layer 39 CorvinFed) — deletion tracking.
    # Metadata only: post_id_prefix (≤8 hex chars), actor_id_prefix (≤16 chars).
    # Audit-first invariant: emitted BEFORE the deletion executes.
    "social.post_deleted":          "INFO",
    "social.actor_deleted":         "INFO",
    # Layer 22 — WorkerEngine session lifecycle (ADR-0049)
    "worker_session.deleted":       "INFO",
    # Layer 18 — Bridge access control: observer/whitelist management
    "bridge.observer_removed":      "INFO",
    # ADR-0166 — Session Participation Gate (SPG)
    "spg.mode_changed":             "INFO",
    "spg.guest_invited":            "INFO",
    "spg.guest_removed":            "INFO",
    "spg.message_dropped":          "INFO",
    # ADR-0017 Phase III — license-gate plugin (corvin-license).
    # Metadata only — JWT body / customer_id / signing key NEVER in chain.
    "license.activated":            "INFO",
    "license.expired":              "WARNING",
    "license.grace_started":        "WARNING",
    "license.violated":             "WARNING",
    "license.revoked":              "WARNING",
    # ADR-0019 — customer-self-service portal (GET /v1/license/me).
    # Metadata only — JWT bytes returned to the client never appear
    # in the chain (only the customer + bearer fingerprints).
    "license.portal_served":        "INFO",
    "license.portal_denied":        "WARNING",
    # ADR-0093 M1.4 — sync-disable anomaly signal (WARNING, not ERROR:
    # air-gapped deployments legitimately set this; the signal is for
    # operators who see it unexpectedly in their aggregated audit logs).
    "license.sync_disabled":        "WARNING",
    # ADR-0094 — resource-quota enforcement events.
    # Metadata only: feature name, tier, requested/limit values, channel, chat_key.
    # Never log task text, instruction content, or file paths.
    "license.limit_exceeded":        "WARNING",
    "license.instance_id_mismatch":  "WARNING",   # Personal tier bound to wrong installation
    "license.gate_bypassed":         "CRITICAL",  # CORVIN_AGENTS_SKIP_LIVE without CORVIN_INTEGRATION_TEST
    "license.module_unavailable":    "CRITICAL",  # license import failed → gates are fail-open
    "license.gate_error":            "WARNING",   # unexpected exception in a license gate (ADR-0138 M2)
    "license.instance_id_mode_error": "WARNING",  # instance_id.json is world/group-readable (ADR-0138 M3)
    "license.token_source":          "INFO",      # token discovery source at boot (ADR-0138 M5 A3)
    "audit.instance_seed_rotated":   "CRITICAL",  # new instance_seed.key on existing chain (ADR-0138 M5 G1)
    "compute.quota_exceeded":        "WARNING",
    "engine.blocked_by_license":     "WARNING",
    "bridge.blocked_by_license":     "WARNING",
    "tenant.blocked_by_license":     "WARNING",
    # ADR-0032 — AWPKG package lifecycle
    "package.installed":            "INFO",
    "package.removed":              "INFO",
    "package.install_denied":       "WARNING",
    "package.inspect":              "INFO",
    # ADR-0017 Phase II — compliance-reports plugin (corvin-compliance-reports).
    # Metadata only — report content never lands in the chain (output_path
    # + metadata stats are the only fields surfaced).
    "compliance.report_generated":  "INFO",
    "compliance.report_failed":     "WARNING",
    # Layer 29 — corvin-delegate plugin (Claude Code as OS, other engines
    # as swappable workers). Metadata only — prompt/output text NEVER in
    # the chain. Per-event allow-list in corvin_delegate/audit.py.
    "delegate.invoked":             "INFO",
    "delegate.completed":           "INFO",
    "delegate.failed":              "WARNING",
    # Layer 29.3a — faithfulness judge on worker output.
    "delegate.output_judged":       "INFO",
    # Layer 29.4a — tenant policy + engine-zone gate (datenresidenz).
    "delegate.engine_policy_denied": "WARNING",
    "delegate.zone_policy_denied":   "WARNING",
    # Layer 29.5 — bwrap sandbox lifecycle.
    "delegate.sandboxed":           "INFO",
    "delegate.sandbox_unavailable": "WARNING",
    # Layer 29.6 — pre-flight prompt-safety classifier metadata.
    "delegate.prompt_classified":   "INFO",
    # Layer 30 (ADR-0022) — engine-agnostic Forge + SkillForge via delegation.
    # Metadata only — skill bodies + MCP-config contents NEVER in the chain.
    # Per-event allow-list in corvin_delegate/audit.py.
    "delegate.skill_injected":      "INFO",
    "delegate.mcp_wired":           "INFO",
    # Layer 29.5 Phase 3 (ADR-0024) — adaptive OS-turn model selection.
    # Metadata only — prompt/system-prompt text NEVER in the chain.
    # Per-event allow-list + forbidden-fields in model_selector.py.
    "os_model.selected":            "INFO",
    "os_model.escalated":           "WARNING",
    # ADR-0017 Phase V — corvin-enterprise plugin mount lifecycle.
    # Emitted from the proprietary overlay; registered here so the
    # severity contract is the open-core's source of truth.
    "enterprise.mounted":           "INFO",
    "enterprise.feature_denied":    "WARNING",
    # ADR-0017 Phase V — corvin-enterprise scheduled-reports feature
    # (the first commercial premium feature). Metadata only — report
    # bodies live on disk, never in the chain. Per-event allow-list
    # in corvin_enterprise/audit.py.
    "scheduled_report.created":     "INFO",
    "scheduled_report.deleted":     "INFO",
    "scheduled_report.fired":       "INFO",
    "scheduled_report.skipped":     "WARNING",
    "scheduled_report.failed":      "WARNING",
    # ADR-0019 — license-signing & distribution pipeline.
    # Metadata only — JWT bytes / signing key NEVER in chain.
    # cloud.license_requested emitted by the Stripe-webhook receiver;
    # cloud.license_signed by the air-gapped signer's outbound writer;
    # cloud.license_sign_rejected by the signer when a request is
    # malformed / off-tier / HMAC-mismatched (distinct from
    # license.violated which fires at install time);
    # cloud.license_delivered / cloud.license_delivery_failed by the
    # delivery service (Mailgun + /v1/licenses/me portal endpoint).
    "cloud.license_requested":      "INFO",
    "cloud.license_signed":         "INFO",
    "cloud.license_sign_rejected":  "WARNING",
    "cloud.license_delivered":      "INFO",
    "cloud.license_delivery_failed": "WARNING",
    # ADR-0020 Layer 30 Phase 30.1 — Engine-Trust-Härtung.
    # Per-engine manifest tier-gate + binary-pin events. Phases 30.2
    # (canary-drift) and 30.3 (output-sentinel) register their own
    # event-types when they land. Metadata only — manifest body /
    # binary bytes / output text NEVER in chain. Per-event allow-list
    # in operator/bridges/shared/engine_trust.py.
    "engine.trust_tier_violated":    "WARNING",
    "engine.trust_manifest_expired": "WARNING",
    "engine.binary_hash_mismatch":   "WARNING",
    "engine.trust_manifest_missing": "WARNING",
    # ADR-0020 Layer 30 Phase 30.2 — Refusal-Canary-Loop.
    # Daily-probed engine refusal scores + drift detection. Metadata
    # only — probe text / LLM output / verdict text NEVER in chain.
    # Per-event allow-list in operator/voice/scripts/engine_canary.py.
    "engine.refusal_probe_completed": "INFO",
    "engine.refusal_probe_failed":    "WARNING",
    "engine.canary_probes_updated":   "INFO",
    "engine.canary_drift_detected":   "WARNING",
    # ADR-0020 Layer 30 Phase 30.3 — Output-Sentinel.
    # Per-spawn second-sight LLM judge against assistant output.
    # Metadata only — judge verdict text + LLM output NEVER in chain.
    # Per-event allow-list in operator/bridges/shared/output_sentinel.py.
    "engine.sentinel_blocked":      "WARNING",
    "engine.sentinel_passed":       "INFO",
    "engine.sentinel_unparseable":  "WARNING",
    # ADR-0021 Layer 31 — Supply-Chain-Härtung.
    # Drift-detection + regulator-defensibility paper-trail. Metadata
    # only — dependency lists, CVE bodies, exploit text, signature
    # bytes, private keys NEVER in chain. Per-event allow-list in
    # core/gateway/corvin_gateway/sbom.py and
    # operator/voice/scripts/supply_chain_verify.py.
    "supply_chain.sbom_verified":           "INFO",
    "supply_chain.sbom_missing":            "WARNING",
    "supply_chain.dep_hashes_updated":      "INFO",
    "supply_chain.dep_hash_mismatch":       "WARNING",
    "supply_chain.cve_detected":            "WARNING",
    "supply_chain.capability_drift":        "WARNING",
    "supply_chain.signature_rekor_verified": "INFO",
    "supply_chain.signature_chain_break":    "WARNING",
    # Phase 31.1.2 extras for operational visibility
    "supply_chain.frozen_baseline_breach_attempted": "WARNING",
    "supply_chain.cve_check_skipped":       "WARNING",
    # ADR-0067 M2.2 — HermesEngine OS-turn lifecycle events.
    # Metadata only — engine_id, persona, error_class. NEVER prompt/output/URL.
    "hermes.turn_start":        "INFO",
    "hermes.turn_end":          "INFO",
    "hermes.turn_error":        "WARNING",
    "hermes.stream_timeout":    "WARNING",
    "hermes.ollama_unavailable": "WARNING",
    # ADR-0067 M2.2 — OpenCodeEngine OS-turn lifecycle (parity fix).
    "opencode.turn_start":      "INFO",
    "opencode.turn_end":        "INFO",
    "opencode.turn_error":      "WARNING",
    "opencode.stream_timeout":  "WARNING",
    # Layer-29 companion — per-chat worker-engine preference switch.
    # Metadata only — engine_id + model alias land in the chain; no
    # prompt / output / user-free-text. Per-event allow-list in
    # operator/bridges/shared/engine_switch.py::_AUDIT_ALLOWED.
    "engine.pref_switched":                 "INFO",
    # ADR-0052 F1 — Compliance Assertion Layer (CAL)
    # Emitted when a CAL predicate denies an action. CRITICAL severity ensures
    # voice-audit verify surfaces these immediately. Metadata only:
    # action_type, reason, predicate_count — no user content, no prompt.
    "compliance_assertion.violated":        "CRITICAL",
    # ADR-0052 F3 — audit disk-headroom monitoring
    "audit.disk_headroom_low":              "WARNING",
    "audit.disk_full_blocked":              "CRITICAL",
    # ADR-0052 F4 — consent TOCTOU drop
    "consent.toctou_drop":                  "WARNING",
    # ADR-0052 F5 — worker memory path escape
    "worker_memory.path_escape":            "CRITICAL",
    # ADR-0052 F8 — forge sandbox bwrap failures
    "forge.bwrap_unavailable":              "CRITICAL",
    "forge.vault_injection_failed":         "CRITICAL",
    # ADR-0052 F9 — skill content drift / injection suspended
    "skill_forge.content_drift":            "WARNING",
    "skill_forge.content_rehash":           "INFO",
    "skill_forge.injection_suspended":      "CRITICAL",
    # ADR-0052 F10 — instance identity rotation
    "instance_identity.rotated":            "WARNING",
    "instance_identity.missing":            "CRITICAL",
    # Instance Binding Certificate (IBC) lifecycle — instance identity + key management.
    # Metadata only — cert body, key material, hardware identifiers NEVER in chain.
    "instance.ibc_issued":                  "INFO",
    "instance.ibc_verified":                "INFO",
    "instance.ibc_expired":                 "WARNING",
    "instance.ibc_revoked":                 "CRITICAL",
    "instance.key_rotated":                 "WARNING",
    "instance.ibc_sig_failed":              "CRITICAL",
    "instance.ibc_hardware_mismatch":       "WARNING",
    # ADR-0145 M3 — hardware tethering
    "instance.hardware_bound":              "INFO",
    # ADR-0153 M3 — per-event instance_id / Ed25519 audit-signature attestation.
    # Emitted best-effort; never blocks a chain write.
    "instance.audit_sig_failed":            "WARNING",   # signing failed at write time
    "instance.audit_sig_verified":          "INFO",      # verify path: signature OK
    "instance.audit_sig_invalid":           "WARNING",   # verify path: bad signature
    # ADR-0153 M4 — CorvinID cert lifecycle (erasure + deanonymisation).
    # Strictly metadata — no email, no full UUID, no cert content in chain.
    "identity.certificate_revoked":         "WARNING",   # audit-first: before cert deletion
    "identity.resolution_requested":        "CRITICAL",  # deanonymisation — always CRITICAL
    # ADR-0052 F6 — disclosure uid coverage
    "disclosure.uid_family_remap":          "WARNING",
    # ADR-0052 F7 — quota lock timeout
    "quota.lock_timeout":                   "WARNING",
    # Layer 38 — A2A core receiver/sender lifecycle (ADR-0048, eight canonical events).
    # Metadata only — instruction text, worker output, attachment content NEVER in chain.
    # Allow-list: task_id, origin_id, endpoint_id, persona, channel, chat_key, reason,
    #   nonce_prefix, status, filter_pass_count, filter_reject_count, engine_id,
    #   ttl_s, duration_ms, sender_instance_id, instance_id_match, http_status.
    "A2A.envelope_received":    "INFO",     # audit-first — written before any spawn or response
    "A2A.envelope_sent":        "INFO",
    "A2A.engine_spawned":       "INFO",
    "A2A.result_filtered":      "INFO",
    "A2A.response_signed":      "INFO",
    "A2A.response_received":    "INFO",
    "A2A.request_rejected":     "WARNING",  # security-relevant: failed validation/HMAC/nonce
    "A2A.response_rejected":    "WARNING",
    "A2A.nonce_store_fallback": "WARNING",  # in-memory nonce store active (no persistent store)
    # Layer 38 M4 — A2A Invite-Token Protocol (ADR-0063)
    # Metadata only — hk/rk/url/iid/full-token NEVER in chain.
    # Allow-list: ikey (16-hex prefix), oid, lbl, exp, su, pa, bidirectional.
    "A2A.invite_created":   "INFO",
    "A2A.invite_accepted":  "INFO",
    "A2A.invite_revoked":   "WARNING",
    # ADR-0096 — MCP Plugin Manager
    # Allow-list: tool_id, source, scope, tenant_id, reason, sha256_prefix (16 hex).
    # NEVER: secret values, full URLs with credentials, tool output, runtime command.
    "mcp_plugin.installed":    "INFO",
    "mcp_plugin.activated":    "INFO",
    "mcp_plugin.deactivated":  "INFO",
    "mcp_plugin.removed":      "WARNING",
    "mcp_plugin.spawn_blocked": "CRITICAL",
    # ADR-0101 — Task Worker Pool: WorkerEngine integration + gate compliance.
    # Metadata only — instruction text / prompt / output NEVER in chain.
    # Per-event allow-list enforced in task_worker_pool.py::_task_audit_emit().
    # chat_key_prefix: first 8 chars only (never full session ID).
    "task.spawn_started":   "INFO",
    "task.spawn_terminal":  "INFO",
    "task.spawn_denied":    "WARNING",
    # ADR-0103 — A2A Network Membership Attestation.
    # Metadata only — SesT bytes, instruction, pairing cert body NEVER in chain.
    # Allow-list: instance_id, sest_fp_prefix (16 hex chars), pairing_id,
    # origin_id, endpoint_id, reason, grace_days_remaining, manifest_age_days.
    "a2a.pairing_authorized":   "INFO",
    "a2a.pairing_denied":       "WARNING",
    "a2a.manifest_fetched":     "INFO",
    "a2a.manifest_stale":       "WARNING",
    "a2a.attestation_failed":   "WARNING",
    # ADR-0141 — Layer Integrity Protocol (LIP). Metadata only — NEVER file
    # paths, file content, or modification timestamps. Allow-list:
    # reason, missing, layer_count, mismatch_count, host, instance_id_match,
    # protocol_version, persona, channel, chat_key.
    "security.capability_missing":       "CRITICAL",  # Tier 3 — mandatory layer absent
    "layer_integrity.verified":          "INFO",      # Tier 1 — manifest + layer hashes ok
    "layer_integrity.manifest_invalid":  "CRITICAL",  # Tier 1 — manifest present but unverifiable
    "layer_integrity.manifest_absent":   "WARNING",   # Tier 1 — pre-rollout state (no manifest yet)
    "layer_integrity.mismatch":          "CRITICAL",  # Tier 1 — a layer file hash differs from manifest
    "a2a.layer_integrity_mismatch":      "WARNING",   # Tier 2 — peer envelope hash rejected
    "a2a.peer_audit_anomaly":            "WARNING",   # Tier 4 — peer chain not advancing (advisory)
    # NOTE: ADR-0142 ext.* events are registered further below (co-located with
    # their positive allow-list); do not duplicate them here.
    # ADR-0104 — Autonomous Compute Shell (ACS, Layer 25b).
    # Second compute engine alongside L25 Compute Worker; handles agentic
    # decision loops (DELEGATE/COMPLETE/FAIL manager protocol).
    # Metadata only — manager JSON, worker output, task instructions,
    # workflow goals, artifact content NEVER in chain.
    # Allow-list: run_id, workflow_id, tenant_id, engine_id, iteration,
    # worker_id, decision, status, gate_id, reason, tokens_used, depth.
    "acs.run_start":            "INFO",
    "acs.run_error":            "ERROR",
    "acs.workflow_complete":    "INFO",
    "acs.workflow_failed":      "WARNING",
    "acs.budget_exhausted":     "WARNING",
    "acs.manager_call":         "INFO",
    "acs.manager_error":        "WARNING",
    "acs.manager_parse_error":  "WARNING",
    "acs.delegation":           "INFO",
    "acs.worker_spawned":        "INFO",
    "acs.worker_traced":         "INFO",
    "acs.manager_decided":       "INFO",
    "acs.worker_error":         "WARNING",
    "acs.worker_l34_blocked":     "WARNING",
    "acs.worker_l35_blocked":     "WARNING",
    "acs.worker_l35_unavailable": "WARNING",  # L35 gate failed to load (YAML error, import error)
    "acs.l34_unavailable":       "WARNING",  # L34 gate module unavailable (matches l35_unavailable)
    # L44 acceptable-use (ADR-0143) gate at the ACSRuntime.run chokepoint.
    # check_l44 itself emits the canonical house_rules.{allowed,denied,escalated}
    # event; these two are run-scoped markers (metadata only — never goal text).
    "acs.run_blocked_house_rules":     "WARNING",  # L44 deny/escalate — run refused, no spawn
    "acs.house_rules_gate_unavailable": "CRITICAL",  # spawn_gates unimportable — fail-closed DENY
    "acs.datasource_snapshot":   "INFO",     # ADR-0127 datasource binding snapshot taken
    "acs.gate_chain_evaluated": "INFO",
    "acs.gate_abort":           "WARNING",
    "acs.max_rejections_reached": "WARNING",
    # ACS manager-level gate events — parallel to worker-level l34/l35 entries above.
    "acs.manager_l34_blocked":       "WARNING",
    "acs.manager_l35_blocked":       "WARNING",
    "acs.manager_gates_unavailable": "WARNING",
    "acs.worker_gates_unavailable":  "WARNING",
    # ACS adaptive + convergence diagnostics (ADR-0105 M4).
    "acs.m4_adaptive_workers": "INFO",
    "acs.loss_plateau":        "WARNING",
    "acs.loss_regression":     "WARNING",
    # ACS-X (ADR-0155) — Autonomous Command Selector Extended. Metadata only:
    # primitive class, confidence, path (heuristic/llm), channel/chat_key.
    # NEVER: task text, user message, directive content, LLM prompt/response.
    "acs_x.classified":          "INFO",     # primitive selected for incoming task
    "acs_x.directive_injected":  "INFO",     # <acs_directive> block added to system prompt
    "acs_x.fallback_llm":        "INFO",     # Haiku-4.5 used (heuristic confidence < 0.7)
    "acs_x.classify_failed":     "WARNING",  # exception during classification — fail-open
    "acs_x.persona_suppressed":  "INFO",     # directive suppressed: worker persona can't execute primitive (ADR-0160 M4a)
    # ATO — Autonomous Task Orchestration (ADR-0164 M3/M4)
    "task_orchestrator.plan_generated":    "INFO",     # M3 Forge tool: structured plan emitted
    "task_orchestrator.convergence_low":   "WARNING",  # M4: conv_rate < 0.60 over >=5 samples
    "task_orchestrator.goal_template_weak":"WARNING",  # M4: goal_revision_rate > 0.30 over >=5 samples
    "task_orchestrator.strategy_drift":    "WARNING",  # M4: strategy_correction_rate > 0.20 over >=5 samples
    # ATO — Dispatch integration (ADR-0165 M5/M6/M7).
    # *_hint = advisory (Phase 1): plan computed, no actual routing.
    # *_routed = actual (Phase 2): real engine dispatch or L25 bypass occurred.
    "task_orchestrator.delegation_hint":   "INFO",    # M5 advisory: delegation target computed
    "task_orchestrator.delegation_routed": "INFO",    # M5 actual: engine dispatched per plan
    "task_orchestrator.model_selected":    "INFO",    # M6: advisory model hint computed
    "task_orchestrator.compute_hint":      "INFO",    # M7 advisory: compute strategy computed
    "task_orchestrator.compute_routed":    "INFO",    # M7 actual: compute bypass activated
    # Chat Command Center (ADR-0168) — CCC entity extraction and routing.
    # Details: entity_type, confidence, forced (bool), action_id, tenant_id.
    # NEVER: prompt text, slot values that may contain PII (names, UIDs).
    "ccc.entity_extracted":  "INFO",    # M1: entity plan computed from chat prompt
    "ccc.action_dispatched": "INFO",    # M2: command router dispatched to OS subsystem
    "ccc.action_error":      "WARNING", # M2: dispatch failed
    # Engine-level lifecycle events — allow correlation of WorkerEngine
    # execution with the ACS worker that spawned it.
    # Allow-list: run_id, worker_id, engine_id, model_id, locality,
    # tenant_id, duration_ms, tokens_used, exit_code.
    # NEVER: prompt text, output text, tool input/output.
    "acs.engine_started":       "INFO",
    "acs.engine_completed":     "INFO",
    "acs.engine_error":         "WARNING",
    # EU AI Act Art. 14 — Human oversight audit.
    # Emitted by the dialectic gate when an operator explicitly disables AI
    # deliberation for a security-relevant site (skill_promotion, forge_creation,
    # path_gate, session_reset, auto_routing) whose bundle default is NOT "off".
    # Metadata only: site, override_source (profile/config), persona, channel_id.
    # NEVER: decision content, thesis/antithesis text.
    "human_oversight.override":  "WARNING",
    # OS-turn audit (EU AI Act Art. 12/13: traceability for every user interaction).
    # Metadata only — no prompt text, no output, no tool inputs/outputs (GDPR Art. 5).
    # Allowed fields: turn_id, chat_key, persona, tool_name, duration_ms,
    #   tools_called, exit_code, timed_out, model (model id only).
    "os_turn.started":          "INFO",
    "os_turn.tool_called":      "INFO",
    "os_turn.completed":        "INFO",
    "os_turn.error":            "WARNING",
    # ADR-0116 M1 — Delegation Context.
    # delegation_id: UUID4 generated per delegate_* tool call; threads through
    # all child events so the parent chain can reconstruct the full delegation tree.
    # Allow-list: delegation_id, turn_id, engine_id, persona, channel, chat_key,
    #   target_engine, duration_ms, relay_count, status.
    # NEVER: prompt text, tool inputs, tool outputs (GDPR Art. 5).
    "delegation.started":       "INFO",
    "delegation.ended":         "INFO",
    "delegation.error":         "WARNING",
    "worker.relay_block_start": "INFO",
    "worker.event_relayed":     "INFO",
    "worker.relay_block_end":   "INFO",
    # ADR-0116 M2 — Worker Audit Gateway.
    # Workers call audit.write_event MCP tool; server validates event_type
    # against this allowlist and strips unknown/forbidden keys from details.
    "audit.worker_event_written":  "INFO",
    "audit.worker_event_rejected": "WARNING",
    # ADR-0116 M4 — A2A Chain Anchoring.
    # chain tail hashes in HMAC-covered wire protocol payload.
    # Allow-list: task_id, peer_instance_id, our_chain_tail (16-hex prefix),
    #   peer_chain_tail (16-hex prefix), nonce_prefix, match.
    # NEVER: full chain hashes (only 16-hex prefix), chain content.
    "A2A.chain_anchor_sent":      "INFO",
    "A2A.chain_anchor_received":  "INFO",
    "A2A.chain_anchor_verified":  "INFO",
    "A2A.chain_tail_unavailable": "WARNING",
    # ADR-0117 M1 — Genesis Block.
    # Allow-list: instance_id, network_id, software_commit, network_pubkey_fp,
    #   issued_at, genesis_hash_prefix (16-hex only).
    # NEVER: genesis_sig, full hash.
    "chain.genesis":             "INFO",
    "chain.genesis_invalid":     "CRITICAL",
    "chain.genesis_missing":     "WARNING",
    # ADR-0117 M2 — Epoch Certificates.
    # Allow-list: instance_id, network_id, epoch_number, genesis_hash_prefix,
    #   chain_tail_prefix, expires_at.
    # NEVER: cert_sig, full hashes.
    "chain.epoch_issued":        "INFO",
    "chain.epoch_stale":         "WARNING",
    "chain.epoch_offline":       "WARNING",
    "chain.epoch_hard_deadline": "CRITICAL",
    # ADR-0117 M4 — Per-Envelope Chain DNA.
    # Allow-list: task_id, origin_id, our_genesis_hash_prefix,
    #   peer_genesis_hash_prefix, network_id, match.
    # NEVER: full hashes, genesis_sig.
    "A2A.chain_dna_verified":       "INFO",
    "A2A.chain_dna_mismatch":       "WARNING",
    "A2A.chain_dna_genesis_absent": "WARNING",
    # ADR-0121 — CorvinFlow multi-node orchestration (EU AI Act Art. 12/13/14).
    # Metadata only — step task templates, step outputs, flow inputs NEVER in chain.
    # Allow-list: run_id, flow_id, flow_version, step_id, target_node, node_type,
    #   step_count, status, reason, tokens_used, steps_done, wall_time_elapsed_s.
    # NEVER: task text, output text, budget snapshots with financial data.
    "mesh_flow.run_started":      "INFO",
    "mesh_flow.run_completed":    "INFO",
    "mesh_flow.run_paused":       "INFO",
    "mesh_flow.step_dispatched":  "INFO",
    "mesh_flow.budget_exceeded":  "WARNING",
    "mesh_flow.checkpoint_paused": "WARNING",
    "mesh_flow.audit_bypassed":   "CRITICAL",
    # ADR-0132 — License-Seeded Audit DNA (LSAD).
    # chain_dna field is injected into every hash-chained event's details by
    # write_event(); these two events mark DNA seed transitions.
    "license.chain_dna_seeded":   "INFO",    # adapter sets paid/free DNA seed at boot
    "license.chain_dna_mismatch": "CRITICAL", # verify detects tampering/tier-switch
    # ADR-0133 — Chain-Locked Adaptive Gating (CLAG).
    # Allow-list: layer_id, epoch, tail_hash_prefix (16 hex), dna_prefix (16 hex),
    #   cit_fp (16 hex), ttl, prev_epoch_tail_prefix, reason_code.
    # NEVER: full HMAC, full tail hash, instruction/output text, user identifiers.
    "audit.cit_issued":       "INFO",     # gate() issued a Chain Integrity Token
    "audit.epoch_anchor":     "INFO",     # epoch boundary checkpoint
    "chain.integrity_failed": "CRITICAL", # chain broken — operation blocked
    # ADR-0135 — Chain Continuity Anchor.
    # Allow-list: tail_hash_prefix (16 hex), event_count.
    # NEVER: full tail hash, anchor HMAC key, HMAC value.
    "audit.chain_anchor_written":   "INFO",
    "audit.chain_anchor_verified":  "INFO",
    "audit.chain_anchor_absent":    "WARNING",
    "audit.chain_continuity_break": "CRITICAL",
    # ADR-0136 — Free-tier LSAD DNA authenticity (per-instance seed).
    # Allow-list: chain_dna_tier ("free" or "paid"), instance_seed_fp (first 8 hex).
    # NEVER: instance_seed_hex, full fingerprint, any key material.
    "audit.chain_metadata":         "INFO",
    # Bridge events missing from earlier revisions — must mirror audit.py
    "bridge.reset_prewarned":         "WARNING",
    "bridge.budget_rejected":         "WARNING",
    "bridge.engine_policy_denied":    "WARNING",
    # ADR-0127 datasource binding — explicit registrations (already in allowlist above)
    "tool.datasource_env_injected":   "INFO",
    # ADR-0142 — Layer Extension API (ext.* lifecycle + runtime).
    # Metadata only — allow-list: name, version, scope, event_type, hook, reason
    # (plus reserved tenant_id/channel/chat_key/user/persona). NEVER hook
    # input/output content. Positive allow-list registered below.
    "ext.installed":                  "INFO",
    "ext.removed":                    "INFO",
    "ext.enabled":                    "INFO",
    "ext.disabled":                   "INFO",
    "ext.hook_denied":                "WARNING",
    "ext.load_failed":                "WARNING",
    "ext.core_namespace_rejected":    "CRITICAL",
    # ADR-0156 M7 — Custom Layer Registry lifecycle.
    # Metadata only — allow-list: layer_name, tier, tenant_id, channel, reason.
    # NEVER manifest contents, tool code, secret values, or file paths.
    "custom_layer.installed":         "INFO",
    "custom_layer.enabled":           "INFO",
    "custom_layer.disabled":          "INFO",
    "custom_layer.removed":           "INFO",
    "custom_layer.boot_limit_exceeded":             "WARNING",
    "custom_layer.boot_limit_enforcement_failed":   "CRITICAL",
    # ADR-0157 — L44 Resilient Classifier (house_rules.*).
    # Emitted by the provider-chain wrapper; details are metadata-only.
    "house_rules.provider_fallback":    "INFO",    # M3: Hermes failed, cloud Haiku used
    "house_rules.classifier_degraded":  "WARNING", # M4: N errors in sliding window
}


_write_lock = threading.Lock()

#: The ONE event type that may legitimately enter the chain without a ``hash``:
#: the CRITICAL gap marker a health check writes when the chain it reports on is
#: already broken (so the marker cannot depend on a sound predecessor). Even
#: this record is bound to the chain: the writer stamps ``prev_hash`` = the
#: current tail and (when a key exists) a keyed ``mac``, and ``verify_chain``
#: refuses it anywhere else (2026-09-07 adversarial finding F-A1: a forged
#: hash-less record was previously accepted at ANY position).
CHAIN_GAP_EVENT = "audit.chain_gap_detected"


class AuditTenantMismatch(ValueError):
    """``write_event(details={"tenant_id": X})`` was called while the process
    tenant is Y — the record is refused and a type/count-only
    ``audit.tenant_mismatch`` record lands in the CONTEXT tenant's chain.

    A ``ValueError`` rather than a ``PermissionError``: the latter is an
    ``OSError`` and would be swallowed by the I/O-resilience handlers of every
    best-effort caller. Enforced at the chain chokepoint (F-A6) so the 30+
    direct ``write_event`` callers cannot bypass the wrapper's check.
    """


def _current_tenant_id() -> str:
    """Process tenant (``CORVIN_TENANT_ID`` → ``_default``). Fail-CLOSED: an
    invalid env value or an unimportable resolver refuses the write."""
    try:
        current_tenant = None
        try:
            from .tenants import current_tenant  # type: ignore[import]
        except ImportError:
            try:
                from forge.tenants import current_tenant  # type: ignore[import]
            except ImportError:
                # Loaded as a top-level module (adapter runtime): the sibling
                # tenants.py — but NOT the 3-line compat stub at
                # operator/forge/tenants.py, which has no resolver.
                import tenants as _t  # type: ignore[import]
                current_tenant = getattr(_t, "current_tenant", None)
        if current_tenant is not None:
            return current_tenant()
        # Last resort: the same contract inline (env → _default, validated).
        env = os.environ.get("CORVIN_TENANT_ID", "")
        if not env:
            return "_default"
        if env.startswith("__") or not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,62}", env):
            raise ValueError(f"invalid CORVIN_TENANT_ID {env!r}")
        return env
    except Exception as exc:  # noqa: BLE001
        raise AuditTenantMismatch(
            f"process tenant unresolvable ({type(exc).__name__}) — tenant-tagged "
            "audit record refused"
        ) from exc

# ADR-0153 M3 — when True, verify_chain() also verifies instance_sig on
# records that carry it. Set by voice-audit via set_verify_sigs(True).
_VERIFY_SIGS: bool = False


def set_verify_sigs(enabled: bool) -> None:
    """Enable/disable instance_sig verification in verify_chain (ADR-0153 M3)."""
    global _VERIFY_SIGS  # noqa: PLW0603
    _VERIFY_SIGS = bool(enabled)


# ── LSAD: active DNA seed (module-level, per-process) ─────────────────────────
# Default: free-tier seed.  Adapter calls set_chain_dna_seed() after loading
# a valid license to switch to the paid-tier seed for this process.
_active_dna_seed: str | None = None   # None = lazy-init to free-tier on first write
_pending_seed_reset: str | None = None  # set by set_chain_dna_seed(); consumed on next write

# ADR-0136: per-instance free-tier seed (not the public constant).
# Set at boot by set_instance_dna_seed(); used as the lazy-init value when
# no paid-tier seed is active.  None = fall back to public constant (legacy chains).
_instance_dna_seed: str | None = None


def set_chain_dna_seed(seed: str) -> None:
    """Set the active DNA seed for this process (called by adapter at license load).

    The NEXT write_event() call will use this seed as the chain_dna directly
    (not evolved from the previous event), creating a visible "seam" in the chain
    that marks the exact point where the paid license became active. All subsequent
    events evolve from this seam value.
    """
    global _active_dna_seed, _pending_seed_reset
    with _write_lock:
        _active_dna_seed = seed
        _pending_seed_reset = seed  # force-set the DNA value on the very next write


def set_instance_dna_seed(seed: str) -> None:
    """Set the per-instance DNA seed for free-tier chains (ADR-0136).

    Unlike set_chain_dna_seed() (paid-tier upgrade), this does NOT create a
    visible seam — the instance seed is the baseline for free-tier chains,
    not a tier switch.  Only applied when no paid-tier seed is already active.
    """
    global _instance_dna_seed  # noqa: PLW0603
    with _write_lock:
        _instance_dna_seed = seed


def get_active_seed() -> str | None:
    """Return the currently active LSAD DNA seed for this process.

    ``None`` means the adapter has not loaded a license yet; the next
    ``write_event()`` call will lazy-initialise to the instance-seeded
    free-tier seed (ADR-0136) or the legacy public constant as a fallback.

    Used by ``clag.gate()`` to auto-populate ``dna_seed`` for tier-coupled
    CIT derivation without requiring every call site to import and pass the
    seed explicitly.
    """
    return _active_dna_seed


def get_audit_chain_tail(path: Path) -> str | None:
    """Return the ``hash`` field of the last chain record, or None.

    ADR-0116 M4: used to compute sender_chain_tail / receiver_chain_tail for
    the A2A wire protocol.  Best-effort — returns None on I/O error or empty
    chain.  Only a 16-hex prefix of the result should appear in audit details
    (never the full 64-hex hash — see ADR-0116 allow-list).

    Delegates to :func:`_last_hash`, which has the SAME semantics (last record
    carrying a non-empty ``hash``; blank, unparseable and pre-chain records
    skipped) and reads BACKWARDS from the end of the file. This function was the
    forward-walking twin that ``_last_hash`` was written to replace in 2026-07-27
    and it was never redirected: ``clag.gate()`` calls it on every consent check,
    so the boot tripwire ``consent_gate_denies_by_default`` json.loads()-ed all
    588 964 records of the maintainer's chain — 2.8 s of the 13 s boot, more than
    the chain verification itself (ADR-0640 R4).
    """
    return _last_hash(path) or None


_ANCHOR_KEY: bytes | None = None
_ANCHOR_KEY_LOADED = False
#: Reason a PRESENT key file was refused (e.g. "insecure_mode:0o644"), else None.
_ANCHOR_KEY_REFUSED: str | None = None


def _anchor_key_path() -> Path:
    env = os.environ.get("CORVIN_AUDIT_ANCHOR_KEY", "").strip()
    return (Path(env).expanduser() if env
            else Path(os.path.expanduser("~/.config/corvin-voice/audit_anchor.key")))


def _anchor_key() -> bytes | None:
    """ADR-0137 M2: load/create the audit-chain MAC key, stored OUTSIDE the
    audit directory (``~/.config/corvin-voice/audit_anchor.key``, mode 0600, or
    ``CORVIN_AUDIT_ANCHOR_KEY``). A writer who can edit ``audit.jsonl`` cannot
    read this key, so it cannot forge the per-record HMAC after rewriting +
    rehashing a record — ``verify_chain`` then detects the tamper. Returns None
    when the key can't be created/read (graceful: records carry no ``mac`` and
    verify falls back to hash-only, preserving the legacy contract)."""
    global _ANCHOR_KEY, _ANCHOR_KEY_LOADED, _ANCHOR_KEY_REFUSED
    if _ANCHOR_KEY_LOADED:
        return _ANCHOR_KEY
    key: bytes | None = None
    try:
        kp = _anchor_key_path()
        if not kp.exists():
            kp.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(kp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(os.urandom(32))
            except FileExistsError:
                pass  # created concurrently by another process
        # F-A14: a MAC key readable by group/other is no anchor at all — an
        # attacker who can read it forges every mac. Refuse it (records carry
        # no mac; verify_chain reports ``anchor_key_insecure_mode`` so the
        # tripwire fails the boot) instead of silently trusting it. POSIX
        # mode bits are meaningless on Windows, where the check is skipped.
        if os.name != "nt":
            mode = kp.stat().st_mode & 0o777
            if mode & 0o077:
                reason = f"insecure_mode:{oct(mode)}"
                if _ANCHOR_KEY_REFUSED != reason:
                    try:
                        import logging as _lg
                        _lg.getLogger("corvin.audit").critical(
                            "audit anchor key refused: mode %s allows group/other "
                            "access — run: chmod 600 %s", oct(mode), kp,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                _ANCHOR_KEY_REFUSED = reason
                _ANCHOR_KEY = None
                return None
        data = kp.read_bytes()
        key = data if len(data) >= 16 else None
        _ANCHOR_KEY_REFUSED = None
    except Exception:  # noqa: BLE001 — key unavailable → graceful hash-only
        key = None
    # Cache ONLY a successful load. Caching None permanently means a process
    # that called this before the key file existed (e.g. a long-running adapter
    # that started ahead of the first key-creating writer) would emit EVERY
    # record without a mac for its whole lifetime — producing the mac-missing
    # gaps that break verify_chain's MAC-epoch enforcement on a multi-process
    # chain. Leaving _ANCHOR_KEY_LOADED False on miss lets the next write retry
    # and pick the key up once it exists. (Incident 2026-06-17.)
    _ANCHOR_KEY = key
    if key is not None:
        _ANCHOR_KEY_LOADED = True
    return _ANCHOR_KEY


def _mac_sentinel_path() -> Path:
    """R2-FND-04: out-of-tree marker recording that this host has enabled the
    per-record MAC. Lives NEXT TO the anchor key (same dir, same protection),
    NOT in the audit dir — so a filesystem attacker who can rewrite
    ``audit.jsonl`` cannot also delete the proof that MACs were expected.

    The `hash` is computed over the record WITHOUT the `mac` field, so simply
    deleting `mac` from every record leaves both the per-record hash and the
    chain tail (and thus the chain_anchor) intact — a silent downgrade to
    hash-only. The sentinel lets verify_chain detect a fully-stripped chain
    (sentinel present + key available + chained records + zero macs)."""
    env = os.environ.get("CORVIN_AUDIT_ANCHOR_KEY", "").strip()
    base = (Path(env).expanduser().parent if env
            else Path(os.path.expanduser("~/.config/corvin-voice")))
    return base / "audit_mac_active"


def _is_tmp_path(p: Path) -> bool:
    """True when *p* lives under a temp directory (tests, throwaway chains)."""
    try:
        s = os.path.abspath(str(p))
    except Exception:  # noqa: BLE001
        return False
    roots = {tempfile.gettempdir(), "/tmp", "/var/tmp"}
    if any(s == r or s.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
        return True
    return "pytest-of-" in s


def _skip_out_of_tree_markers(chain_path: Path) -> bool:
    """F-A14: a throwaway (tmp) chain must never stamp markers beside the
    operator's REAL anchor key — that is how ``mac_active_chains/`` grew to
    267k files / 1.1 GB. Markers are still written when the anchor key itself
    is in a tmp dir (a test that redirected CORVIN_AUDIT_ANCHOR_KEY), because
    then nothing real is littered and the strip detector stays testable."""
    return _is_tmp_path(chain_path) and not _is_tmp_path(_anchor_key_path().parent)


#: (abspath, inode) → (genesis, legacy_prefix, genesis_prev, genesis_has_mac,
#: byte offset of the genesis line, the genesis line's bytes). The last two
#: VALIDATE the cache: an in-place rewrite of the file keeps the inode, so an
#: inode-keyed cache alone kept answering the OLD genesis for a file whose
#: first chained record had been replaced (R2-A1: the reviewer's whole-file
#: rewrite passed verify in the same process that had cached the genesis).
_GENESIS_CACHE: dict[tuple[str, int], tuple[str, int, str, bool, int, bytes]] = {}
_GENESIS_SCAN_LIMIT = 20000


def _chain_identity_ex(chain_path: Path) -> tuple[str | None, int, str, bool]:
    """``(genesis, legacy_prefix, genesis_prev, genesis_has_mac)`` of a chain.

    ``genesis`` is the ``hash`` of the first hash-bearing record (``None`` for
    an absent/empty/hash-less file); ``legacy_prefix`` counts the hash-less
    records BEFORE it (the pre-chain prefix the verifier tolerates, R2-A2);
    ``genesis_prev`` is that record's ``prev_hash`` (non-empty after a Layer 37
    rotation); ``genesis_has_mac`` says whether the chain started under the
    MAC epoch — a chain that did can never legitimately grow a hash-less
    prefix.

    Keyed by content rather than path (F-A14) so a chain keeps its
    genesis-keyed markers when the install moves. The (path, inode) cache is
    validated by re-reading the genesis line at its recorded offset, so an
    in-place rewrite invalidates it instead of being masked by it."""
    try:
        st = chain_path.stat()
    except OSError:
        return None, 0, "", False
    key = (os.path.abspath(str(chain_path)), st.st_ino)
    cached = _GENESIS_CACHE.get(key)
    if cached:
        g, prefix, gprev, gmac, off, line = cached
        try:
            with chain_path.open("rb") as fh:
                fh.seek(off)
                if fh.readline() == line:
                    return g, prefix, gprev, gmac
        except OSError:
            pass
        _GENESIS_CACHE.pop(key, None)
    prefix = 0
    try:
        with chain_path.open("rb") as fh:
            off = 0
            for i, raw in enumerate(fh):
                line_off, off = off, off + len(raw)
                if i > _GENESIS_SCAN_LIMIT:
                    break
                s = raw.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                h = rec.get("hash") if isinstance(rec, dict) else None
                if isinstance(h, str) and h:
                    gprev = rec.get("prev_hash")
                    gprev = gprev if isinstance(gprev, str) else ""
                    gmac = "mac" in rec
                    _GENESIS_CACHE[key] = (h, prefix, gprev, gmac, line_off, raw)
                    return h, prefix, gprev, gmac
                prefix += 1
    except OSError:
        return None, 0, "", False
    return None, prefix, "", False


def _chain_identity(chain_path: Path) -> str | None:
    """Stable identity of a chain: the ``hash`` of its first hash-bearing record."""
    return _chain_identity_ex(chain_path)[0]


def _marker_name(genesis: str) -> str:
    return "g-" + hashlib.sha256(genesis.encode("utf-8")).hexdigest()[:32]


def _legacy_mac_chain_marker_path(chain_path: Path) -> Path:
    """Pre-2026-09-07 marker location (keyed by absolute PATH). Read-only
    fallback so chains stamped under the old scheme stay strip-protected."""
    base = _mac_sentinel_path().parent / "mac_active_chains"
    digest = hashlib.sha256(os.path.abspath(str(chain_path)).encode("utf-8")).hexdigest()[:32]
    return base / digest


def _chain_tail_anchor_path(chain_path: Path, *, genesis: str | None = None) -> Path | None:
    """F-A12: out-of-tree record of the chain's CURRENT tail hash, beside the
    anchor key. Deleting the last N records leaves a chain that self-verifies;
    only an external copy of the tail can expose the truncation."""
    g = genesis or _chain_identity(chain_path)
    if not g:
        return None
    return _mac_sentinel_path().parent / "chain_tails" / _marker_name(g)


def _record_chain_tail(chain_path: Path, tail_hash: str, *, genesis: str | None = None) -> None:
    """Persist the tail hash atomically (tmp + os.replace). Best-effort: a
    failure must never break an audit write. Called under the chain lock."""
    if not tail_hash or _skip_out_of_tree_markers(chain_path):
        return
    try:
        tp = _chain_tail_anchor_path(chain_path, genesis=genesis)
        if tp is None:
            return
        tp.parent.mkdir(parents=True, exist_ok=True)
        tmp = tp.with_name(tp.name + f".{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"tail": tail_hash, "ts": time.time()}))
        os.replace(tmp, tp)
    except Exception:  # noqa: BLE001
        pass


def _hash_in_recent_tail(chain_path: Path, h: str, *, window_bytes: int = 512 * 1024) -> bool:
    """True when the 16-hex ``h`` appears as a record hash in the file's last bytes."""
    try:
        size = chain_path.stat().st_size
        with chain_path.open("rb") as fh:
            fh.seek(max(0, size - window_bytes))
            return (b'"hash": "%s"' % h.encode("ascii")) in fh.read()
    except Exception:  # noqa: BLE001
        return False


def _read_chain_tail(chain_path: Path) -> str | None:
    try:
        tp = _chain_tail_anchor_path(chain_path)
        if tp is None or not tp.exists():
            return None
        t = json.loads(tp.read_text()).get("tail")
        return t if isinstance(t, str) and t else None
    except Exception:  # noqa: BLE001
        return None


# ── R2-A1/R2-A2: PATH-keyed chain identity record ───────────────────────────
#
# The genesis-keyed markers above (tail anchor, per-chain mac marker) answer
# "what do we know about the chain whose first record hashes to G?". They are
# silent about a file whose first record was REPLACED: a whole-file rewrite
# with a fresh genesis (recomputed hashes, every mac stripped) simply has no
# markers, verifies hash-only, and the tripwire boots on it. The path-keyed
# record below answers the other question — "which chain is supposed to live
# at this path?" — and is written beside the anchor key, where an in-tree
# attacker cannot reach it:
#
#     <key dir>/chain_ids/<sha256(abspath)[:32]> =
#         {"genesis": G, "tail": T, "legacy_prefix": N, "mac": bool,
#          "ts": ..., "rotated_from": [...]}
#
# * ``genesis`` is set the first time this process chains a record onto the
#   file and NEVER changed by the writer. A file at that path whose genesis
#   differs is ``chain_replaced`` (verify fails, the boot tripwire refuses;
#   the writer also stamps ``_chain_replaced_from`` into its next record so
#   the fact survives in-tree). The one legitimate genesis change — a Layer 37
#   rotation — updates the record through :func:`note_chain_rotation`, called
#   by the sealer under the rotation lock right after it wrote the link.
# * ``tail`` is the path-keyed tail anchor (same tolerance window as the
#   genesis-keyed one).
# * ``legacy_prefix`` is the number of hash-less records before the genesis
#   when the chain was first anchored; a prefix that has GROWN was prepended.
# * ``mac`` records that this chain has carried a mac — the path-keyed
#   strip marker, independent of the genesis.
#
# Like every other marker, never written for a throwaway (tmp) chain unless
# the anchor key itself is in tmp (``_skip_out_of_tree_markers``).


def _chain_path_record_path(chain_path: Path) -> Path:
    """R3-A3: keyed on the RESOLVED path, matching ``tripwire._current_chain_key``.

    ``os.path.abspath`` does not follow symlinks, so ONE physical chain reached
    through the documented compat symlink (``<corvin_home>/global`` →
    ``tenants/_default/global``) hashed to a DIFFERENT key than the same chain
    reached through the real path — and the aliasing reader silently saw no
    identity record at all: no ``chain_replaced``, no ``records_prepended``, no
    path-keyed tail. Resolving makes the two readers agree.

    Records written before this change still live at the abspath location;
    :func:`_read_chain_path_record` falls back to it READ-ONLY (never written to
    again, so the two locations cannot diverge)."""
    try:
        real = str(Path(chain_path).resolve())
    except OSError:  # pragma: no cover - resolve() is non-strict on 3.6+
        real = os.path.abspath(str(chain_path))
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:32]
    return _mac_sentinel_path().parent / "chain_ids" / digest


def _legacy_chain_path_record_path(chain_path: Path) -> Path:
    """Pre-R3-A3 (abspath-keyed) location — read-only compatibility."""
    digest = hashlib.sha256(os.path.abspath(str(chain_path)).encode("utf-8")).hexdigest()[:32]
    return _mac_sentinel_path().parent / "chain_ids" / digest


def _read_chain_path_record(chain_path: Path) -> dict | None:
    try:
        candidates = [_chain_path_record_path(chain_path)]
        legacy = _legacy_chain_path_record_path(chain_path)
        if legacy != candidates[0]:
            candidates.append(legacy)
        for rp in candidates:
            if not rp.exists():
                continue
            d = json.loads(rp.read_text())
            if not isinstance(d, dict) or not isinstance(d.get("genesis"), str) or not d["genesis"]:
                continue
            return d
        return None
    except Exception:  # noqa: BLE001
        return None


def _write_chain_path_record(chain_path: Path, rec: dict) -> None:
    """Atomic (tmp + os.replace), 0600. Best-effort: never breaks an audit write."""
    try:
        rp = _chain_path_record_path(chain_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        tmp = rp.with_name(rp.name + f".{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(rec))
        os.replace(tmp, rp)
    except Exception:  # noqa: BLE001
        pass


def _record_chain_path_state(chain_path: Path, *, genesis: str, tail: str,
                             legacy_prefix: int, mac: bool) -> None:
    """Writer side, under the chain lock, after every chained write.

    Creates the record on first sight; afterwards only ``tail`` and ``mac``
    move. A genesis mismatch is NOT reconciled here — the record keeps naming
    the chain that was anchored, so every later verify reports
    ``chain_replaced`` until the operator restores the chain (or, for a
    legitimate rotation, the sealer called :func:`note_chain_rotation`)."""
    if not genesis or not tail or _skip_out_of_tree_markers(chain_path):
        return
    existing = _read_chain_path_record(chain_path)
    if existing is None:
        _write_chain_path_record(chain_path, {
            "genesis": genesis, "tail": tail, "legacy_prefix": int(legacy_prefix),
            "mac": bool(mac), "ts": time.time(),
        })
        return
    if existing.get("genesis") != genesis:
        return  # chain_replaced — leave the anchored identity untouched
    existing["tail"] = tail
    existing["mac"] = bool(existing.get("mac")) or bool(mac)
    existing["ts"] = time.time()
    _write_chain_path_record(chain_path, existing)


# ── R4 — chain-convergence seams: make a SPLIT reachable, never merged ───────
#
# Six chain files held one tenant's GDPR Art. 30 trail on the maintainer install
# (see ``forge/paths.py::tenant_audit_chain`` for the measured breakdown). The
# forward fix is that every writer now resolves ONE path. That leaves the
# historical files: append-only and hash-chained, so merging, reordering or
# deleting them would destroy the exact integrity property the trail exists for.
#
# What is admissible is a POINTER. When a writer converges onto the canonical
# chain and a sibling chain for the same scope exists, we
#   (a) append ONE chained ``audit.chain_supersedes`` record to the CANONICAL
#       chain naming the sibling's path key, genesis, final tail hash, byte size
#       and record count — so an auditor reading the canonical chain learns the
#       sibling exists and can verify it end-to-end against the recorded tail;
#   (b) write the reverse pointer (``superseded_by``) into the SIBLING's
#       out-of-tree chain-identity record — so an auditor who starts from the
#       sibling is led forward. The reverse pointer is deliberately NOT appended
#       to the sibling file: the seam must not itself extend a chain that is
#       being frozen, and the identity record already lives outside the audit
#       tree next to the anchor key, where a filesystem attacker who can rewrite
#       audit.jsonl cannot reach it.
#
# Both halves are idempotent — a seam is recorded once per (canonical, sibling)
# pair, keyed on the sibling's tail at the moment of convergence.

CHAIN_SEAM_EVENT = "audit.chain_supersedes"


def chain_path_key(chain_path: Path) -> str:
    """Stable, host-independent handle for a chain FILE: sha256(realpath)[:32].

    The same digest ``_chain_path_record_path`` indexes identity records by, so a
    seam record's ``superseded_key`` names a record an auditor can actually open.
    """
    try:
        real = str(Path(chain_path).resolve())
    except OSError:
        real = os.path.abspath(str(chain_path))
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:32]


def _chain_stats(chain_path: Path) -> tuple[int, int]:
    """``(size_bytes, record_count)``; ``(0, 0)`` when the file is absent."""
    try:
        size = chain_path.stat().st_size
    except OSError:
        return 0, 0
    n = 0
    try:
        with chain_path.open("rb") as fh:
            for _ in fh:
                n += 1
    except OSError:
        return size, 0
    return size, n


def record_chain_supersession(
    canonical: Path, superseded: Path, *, reason: str = "chain_convergence",
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Link a live canonical chain to a sibling chain it supersedes.

    Returns the written seam record, or ``None`` when there is nothing to link
    (the sibling is absent/empty, is the same file, or the seam already exists).
    Never merges, rewrites, reorders or truncates either chain.
    """
    try:
        canonical = Path(canonical)
        superseded = Path(superseded)
        if not superseded.is_file():
            return None
        try:
            if canonical.resolve() == superseded.resolve():
                return None  # one file, two names (the ADR-0007 compat symlink)
        except OSError:
            pass
        try:
            if superseded.stat().st_size == 0:
                return None
        except OSError:
            return None
        tail = _read_chain_tail(superseded) or ""
        sup_key = chain_path_key(superseded)
        can_key = chain_path_key(canonical)

        # Idempotence FIRST, and deliberately before any O(n) work. The sibling's
        # identity record remembers the seam we wrote for it; re-seam only when
        # its tail has MOVED since (a writer we have not converged yet is still
        # appending there — worth recording again).
        #
        # ``_chain_stats`` counts records by reading the whole file. On the
        # maintainer install the largest sibling is 315 MB, and this runs from
        # the BOOT tripwire — computing it before the idempotence check would
        # add a 315 MB read to every single boot, forever, to re-derive a number
        # that has not changed. That is exactly the unbounded O(n) boot cost the
        # ADR-0640 R4 prefix witness exists to remove; do not reintroduce it.
        existing = _read_chain_path_record(superseded) or {}
        prior = existing.get("superseded_by")
        if isinstance(prior, dict) and prior.get("canonical_key") == can_key \
                and prior.get("tail") == tail:
            return None

        size, records = _chain_stats(superseded)
        if size == 0 or records == 0:
            return None
        genesis = _chain_identity(superseded) or ""

        rec = write_event(
            canonical, CHAIN_SEAM_EVENT, severity="WARNING",
            details={
                "seam": "chain_convergence",
                "seam_reason": str(reason)[:64],
                "superseded_key": sup_key,
                "superseded_genesis": genesis[:32],
                "superseded_tail": tail[:32],
                "superseded_size_bytes": size,
                "superseded_records": records,
                "canonical_key": can_key,
            },
        )
        # (b) reverse pointer, out-of-tree, next to the anchor key.
        if not _skip_out_of_tree_markers(superseded):
            merged = dict(existing)
            if not merged.get("genesis"):
                merged["genesis"] = genesis
            merged["tail"] = tail or merged.get("tail", "")
            merged["superseded_by"] = {
                "canonical_key": can_key,
                "canonical_path": _resolved_str(canonical),
                "tail": tail,
                "seam_hash": str(rec.get("hash", "")),
                "ts": time.time(),
            }
            _write_chain_path_record(superseded, merged)
        return rec
    except Exception:  # noqa: BLE001 — a seam must never break an audit write
        return None


def chain_seam_links(chain_path: Path) -> list[dict[str, Any]]:
    """Every sibling chain reachable from *chain_path*, in either direction.

    Traversal for an auditor (and for ``tests/security/test_audit_chain_seam.py``):

    * FORWARD — reading the canonical chain, every ``audit.chain_supersedes``
      record yields ``{"direction": "supersedes", "key", "genesis", "tail",
      "records", "size_bytes"}``: enough to locate the sibling's identity record
      and verify the sibling against the tail hash recorded here.
    * BACKWARD — reading a superseded chain, its out-of-tree identity record
      yields ``{"direction": "superseded_by", "key", "path", "tail", "seam_hash"}``,
      pointing at the chain that took over.

    Returns ``[]`` when the chain stands alone.
    """
    out: list[dict[str, Any]] = []
    try:
        with Path(chain_path).open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if CHAIN_SEAM_EVENT not in line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("event_type") != CHAIN_SEAM_EVENT:
                    continue
                d = r.get("details") or {}
                out.append({
                    "direction":  "supersedes",
                    "key":        d.get("superseded_key", ""),
                    "genesis":    d.get("superseded_genesis", ""),
                    "tail":       d.get("superseded_tail", ""),
                    "records":    d.get("superseded_records", 0),
                    "size_bytes": d.get("superseded_size_bytes", 0),
                    "seam_hash":  r.get("hash", ""),
                })
    except OSError:
        pass
    rec = _read_chain_path_record(chain_path) or {}
    back = rec.get("superseded_by")
    if isinstance(back, dict):
        out.append({
            "direction": "superseded_by",
            "key":       back.get("canonical_key", ""),
            "path":      back.get("canonical_path", ""),
            "tail":      back.get("tail", ""),
            "seam_hash": back.get("seam_hash", ""),
        })
    return out


def note_chain_rotation(chain_path: Path, *, link_hash: str) -> None:
    """Layer 37 rotation hook: the sealer just replaced ``chain_path`` with a
    fresh live file whose only record is the ``audit.rotation_link`` hashing
    to ``link_hash``. Re-anchor the path-keyed identity to that genesis and
    remember where it came from. Called under the rotation lock; best-effort."""
    if not link_hash or _skip_out_of_tree_markers(chain_path):
        return
    existing = _read_chain_path_record(chain_path) or {}
    history = list(existing.get("rotated_from") or [])
    if existing.get("genesis"):
        history.append(str(existing["genesis"])[:16])
    _write_chain_path_record(chain_path, {
        "genesis": link_hash, "tail": link_hash, "legacy_prefix": 0,
        "mac": bool(existing.get("mac")), "ts": time.time(),
        "rotated_from": history[-32:],
        # R3-A1: the OUT-OF-TREE fact that this genesis is a rotation link the
        # sealer minted. Nothing inside audit.jsonl can produce it, so it — not
        # the shape of a record in the file — is what later verifies consult.
        "rotation_genesis": str(link_hash),
    })


# ── ADR-0640 R4: durable PREFIX WITNESS (boot-path memoisation) ─────────────
#
# ``audit_chain_history_clean`` walks the WHOLE chain on every boot. On the
# maintainer install that is 315 MB / 588 827 records / ~5.7 s, and the cost is
# O(n) in a file that only ever grows — so boot time (and every test that boots
# the console app) rises without bound. Skipping history verification is not an
# option: a break anywhere in an append-only file is permanent and must stay
# visible.
#
# The chain is append-only, so a prefix that verified once and is PROVABLY
# unchanged need not be re-walked. "Provably unchanged" is a SHA-256 over the
# prefix bytes — not the record count, not the tail hash, not an mtime. That
# distinction is the whole security argument:
#
#   * a tail-hash-at-offset witness would be forgeable — edit a record in the
#     middle of the prefix and rehash forward is caught, but edit it WITHOUT
#     rehashing (which is exactly the "tampered"/"mac_tampered" class this
#     verifier exists to find) leaves the hash at the offset untouched;
#   * a byte digest is invalidated by ANY change to ANY byte of the prefix —
#     an in-place edit, a truncation, a whole-file replacement, a prepend, a
#     re-encoded line — and any mismatch falls back to a FULL walk.
#
# What is memoised is therefore only the LINE-NUMBERED problems and the
# loop-carried state (chain position, MAC epoch, tail window) of a byte range
# that has been re-proven identical this very boot. Detecting an arbitrary
# silent byte change in an n-byte file cannot cost less than reading n bytes;
# what the witness removes is the per-record JSON parse + canonicalisation +
# two SHA-256s + HMAC, which is ~30x the cost of the raw read.
#
# The witness lives beside the anchor key (``chain_witness/<sha256(realpath)>``,
# 0600), keyed by the resolved path exactly like the chain-identity record, and
# is itself MAC'd under the anchor key. Missing, unreadable, version-mismatched,
# MAC-invalid, path-mismatched, key-rotated, shorter-than-claimed or
# digest-mismatched → FULL walk. There is no path from a bad witness to trust.

_CHAIN_WITNESS_VERSION = 1
#: A witness stores the prefix's line-numbered problems verbatim. A chain with
#: more than this many is pathological; refuse to memoise it rather than write
#: a multi-megabyte witness (correctness is unaffected — it just walks fully).
_CHAIN_WITNESS_MAX_PROBLEMS = 20000


def _verify_no_key_ok() -> bool:
    return os.environ.get("CORVIN_AUDIT_VERIFY_NO_KEY_OK", "").strip() in ("1", "true", "yes")


def _resolved_str(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:  # pragma: no cover - resolve() is non-strict
        return os.path.abspath(str(path))


def _chain_witness_path(chain_path: Path) -> Path:
    """Beside the anchor key, keyed on the RESOLVED path (R3-A3 rationale)."""
    digest = hashlib.sha256(_resolved_str(chain_path).encode("utf-8")).hexdigest()[:32]
    return _mac_sentinel_path().parent / "chain_witness" / digest


def _witness_anchor_fp() -> str:
    """Identity of the key the memoised MAC verdicts were produced under.

    A rotated, removed or newly-refused anchor key changes every ``mac_*``
    verdict in the prefix, so the witness must not survive it."""
    ak = _anchor_key()
    if ak is None:
        return "none:" + (_ANCHOR_KEY_REFUSED or "absent")
    return "key:" + hashlib.sha256(b"corvin.chain.witness\n" + ak).hexdigest()[:32]


def _witness_mac(payload: str) -> str | None:
    ak = _anchor_key()
    if ak is None:
        return None
    return hmac.new(ak, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _prefix_hasher(path: Path, nbytes: int):
    """SHA-256 over the first *nbytes* bytes, or ``None`` if the file is shorter.

    Returns the live hasher so the caller can keep feeding it the suffix and get
    the NEXT witness's digest without a second pass over the prefix."""
    h = hashlib.sha256()
    remaining = int(nbytes)
    try:
        with Path(path).open("rb") as fh:
            while remaining > 0:
                chunk = fh.read(min(1 << 20, remaining))
                if not chunk:
                    return None
                remaining -= len(chunk)
                h.update(chunk)
    except OSError:
        return None
    return h


def _read_chain_witness(chain_path: Path, *, initial_prev: str):
    """``(state, hasher)`` when a witness is valid for *chain_path* right now.

    Fail-closed in every branch: any doubt returns ``(None, None)`` and the
    caller does a full walk."""
    if _skip_out_of_tree_markers(chain_path) or _VERIFY_SIGS:
        return None, None
    try:
        wp = _chain_witness_path(chain_path)
        if not wp.exists():
            return None, None
        doc = json.loads(wp.read_text())
        body = doc.get("body") if isinstance(doc, dict) else None
        if not isinstance(body, dict) or int(body.get("v", 0)) != _CHAIN_WITNESS_VERSION:
            return None, None
        expected_mac = _witness_mac(_canonical(body))
        got_mac = doc.get("mac")
        if expected_mac is None:
            # No anchor key: nothing can authenticate the witness, so only a
            # witness that was itself written key-less is admissible — and the
            # anchor fingerprint below pins that it was the same key-less state.
            if got_mac is not None:
                return None, None
        elif not isinstance(got_mac, str) or not hmac.compare_digest(got_mac, expected_mac):
            return None, None
        if body.get("path") != _resolved_str(chain_path):
            return None, None
        if body.get("anchor") != _witness_anchor_fp():
            return None, None
        if body.get("initial_prev") != initial_prev:
            return None, None
        if bool(body.get("no_key_ok")) != _verify_no_key_ok():
            return None, None
        nbytes = int(body.get("bytes", 0))
        digest = body.get("digest")
        if nbytes <= 0 or not isinstance(digest, str) or not digest:
            return None, None
        if not isinstance(body.get("problems"), list) or not isinstance(
                body.get("recent_hashes"), list):
            return None, None
        st = Path(chain_path).stat()
        if st.st_size < nbytes:
            # Truncated / replaced by something shorter — the full walk (and its
            # tail-anchor + chain-identity checks) is what must report that.
            return None, None
        hasher = _prefix_hasher(Path(chain_path), nbytes)
        if hasher is None or not hmac.compare_digest(hasher.hexdigest(), digest):
            return None, None
        return body, hasher
    except Exception:  # noqa: BLE001 - a bad witness is never a reason to trust
        return None, None


def _write_chain_witness(chain_path: Path, state: dict, *, initial_prev: str,
                         hasher=None) -> None:
    """Persist the walk's end state. Best-effort: never breaks a verify."""
    if _skip_out_of_tree_markers(chain_path) or _VERIFY_SIGS:
        return
    if not state or not state.get("complete"):
        return  # the file ends mid-line: the byte range is not a record boundary
    nbytes = int(state.get("bytes", 0))
    if nbytes <= 0:
        return
    problems = list(state.get("problems") or [])
    if len(problems) > _CHAIN_WITNESS_MAX_PROBLEMS:
        return
    try:
        if hasher is None:
            hasher = _prefix_hasher(Path(chain_path), nbytes)
            if hasher is None:
                return
        body = {
            "v": _CHAIN_WITNESS_VERSION,
            "path": _resolved_str(chain_path),
            "initial_prev": initial_prev,
            "anchor": _witness_anchor_fp(),
            "no_key_ok": _verify_no_key_ok(),
            "bytes": nbytes,
            "lines": int(state.get("lines", 0)),
            "digest": hasher.hexdigest(),
            "prev": str(state.get("prev", "")),
            "chain_started": bool(state.get("chain_started")),
            "mac_required": bool(state.get("mac_required")),
            "mac_seen_count": int(state.get("mac_seen_count", 0)),
            "nonlink_chained": int(state.get("nonlink_chained", 0)),
            "recent_hashes": list(state.get("recent_hashes") or []),
            "problems": problems,
            "ts": time.time(),
        }
        doc = {"body": body, "mac": _witness_mac(_canonical(body))}
        wp = _chain_witness_path(chain_path)
        wp.parent.mkdir(parents=True, exist_ok=True)
        tmp = wp.with_name(wp.name + f".{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(doc))
        os.replace(tmp, wp)
    except Exception:  # noqa: BLE001
        pass


def _first_chained_record(chain_path: Path) -> dict | None:
    """The first hash-bearing record of a chain file, or ``None``."""
    try:
        with Path(chain_path).open("rb") as fh:
            for raw in fh:
                st = raw.strip()
                if not st:
                    continue
                try:
                    rec = json.loads(st)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(rec, dict) and rec.get("hash"):
                    return rec
    except OSError:
        return None
    return None


def _rotation_link_authenticated(chain_path: Path, pathrec: dict | None,
                                 genesis: str | None) -> bool:
    """True only when an OUT-OF-TREE fact says this genesis is a Layer 37 link.

    R3-A1 (2026-09-07). Round 2 recognised a rotation by the SHAPE of the first
    record in ``audit.jsonl``: event_type ``audit.rotation_link`` whose
    ``prev_hash`` equalled the tail in the path record. The in-code argument
    ("a rewriter cannot read the path record") was false — ``_record_chain_path_state``
    sets that tail to the hash of the last chained record on every write, so the
    recorded tail is byte-identical to the last ``hash`` IN THE FILE the attacker
    is rewriting. Forging a rotation_link genesis that borrows the file's own tail
    therefore suppressed ``chain_replaced`` + ``tail_truncated`` + ``mac_stripped_chain``
    in one move, and the only residue was a LINE-NUMBERED ``broken_chain`` at line 1
    which ``audit_chain_intact`` treats as historical once the file exceeds
    ``TAIL_RECORDS`` — a length the attacker chooses.

    Rotation is now recognised by facts an in-tree rewriter cannot mint:

    * ``pathrec["rotation_genesis"]`` — written by :func:`note_chain_rotation`,
      which ``audit_sealer.rotate_and_seal()`` calls under the rotation lock right
      after it wrote the link. It lives beside the anchor key.
    * the link's own ``mac``, an HMAC under the anchor key. The sealer has the key;
      an attacker who can only edit ``audit.jsonl`` does not.

    Neither present → not a rotation. Fail-closed: no key and no record means the
    answer is "no", never "probably"."""
    if not genesis:
        return False
    if isinstance(pathrec, dict):
        recorded = pathrec.get("rotation_genesis")
        if isinstance(recorded, str) and recorded and hmac.compare_digest(recorded, genesis):
            return True
    ak = _anchor_key()
    if ak is None:
        return False
    rec = _first_chained_record(chain_path)
    if not isinstance(rec, dict):
        return False
    if str(rec.get("event_type", "")) != "audit.rotation_link":
        return False
    mac = rec.get("mac")
    if not isinstance(mac, str) or not mac:
        return False
    canon = _canonical({k: v for k, v in rec.items()
                        if k not in CHAIN_HASH_EXCLUDED_FIELDS}).encode("utf-8")
    expected = hmac.new(ak, str(rec.get("prev_hash", "")).encode("utf-8") + b"\n" + canon,
                        hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(mac, expected)


def _is_legitimate_rotation(chain_path: Path, pathrec: dict,
                            genesis: str | None, genesis_prev: str) -> bool:
    """True when a changed genesis is a Layer 37 rotation, not a replacement.

    Three conditions, ALL required: the link binds to the tail this path record
    remembers, the genesis really is that link, and the rotation is
    AUTHENTICATED out of tree (:func:`_rotation_link_authenticated`). The last
    one is the load-bearing half — the first two are shape, and shape is exactly
    what an attacker rewriting the file controls (R3-A1)."""
    if not genesis or not genesis_prev:
        return False
    recorded_tail = pathrec.get("tail")
    if not isinstance(recorded_tail, str) or not recorded_tail:
        return False
    if genesis_prev != recorded_tail:
        return False
    return _rotation_link_authenticated(chain_path, pathrec, genesis)


def _is_primary_live_layout(chain_path: Path) -> bool:
    """True for ``<root>/global/forge/audit.jsonl`` — the chain every
    ``write_event`` default resolver and the boot tripwire point at (repo
    ``.corvin``, ``~/.corvin`` and every ``tenants/<id>/global/forge``). The
    host-wide ``audit_mac_active`` sentinel is consulted ONLY for this layout:
    per-session / per-tenant-root chains that legitimately never carried a mac
    (incident 2026-06-17) are not under ``global/forge``."""
    try:
        p = Path(chain_path)
        return (p.name == "audit.jsonl" and p.parent.name == "forge"
                and p.parent.parent.name == "global")
    except Exception:  # noqa: BLE001
        return False


def _mac_chain_marker_path(chain_path: Path, *, genesis: str | None = None) -> Path:
    """Out-of-tree per-CHAIN marker recording that THIS chain has carried a mac.

    The host-global sentinel (``audit_mac_active``) cannot distinguish a chain
    that was fully mac-stripped from one that legitimately never carried a mac
    (a session that ran no mac-writing tool): once ANY chain on the host stamps
    the host sentinel, the full-strip detector would false-positive on every
    other zero-mac chain (incident 2026-06-17: 20+ legacy/session chains broke
    ``voice-audit verify --all``). A per-chain marker, keyed by the chain's
    absolute path and kept beside the anchor key (so a filesystem attacker who
    strips macs cannot also delete the proof), makes the detector sound: a chain
    that never had a mac has no marker and is exempt; a chain that had a mac and
    now carries none has its marker but zero macs → genuine strip.

    Keyed by the chain's GENESIS hash (F-A14) — content identity, not path —
    with the legacy path-keyed location as read-only fallback."""
    g = genesis or _chain_identity(chain_path)
    if g:
        return _mac_sentinel_path().parent / "mac_active_chains" / _marker_name(g)
    return _legacy_mac_chain_marker_path(chain_path)


def _chain_had_mac(chain_path: Path) -> bool:
    """True iff THIS chain has ever written a mac (per-chain marker present,
    under the genesis-keyed or the legacy path-keyed scheme)."""
    try:
        return (_mac_chain_marker_path(chain_path).exists()
                or _legacy_mac_chain_marker_path(chain_path).exists())
    except Exception:  # noqa: BLE001
        return False


def _mark_mac_active(now: float | None = None, chain_path: Path | None = None,
                     *, genesis: str | None = None) -> None:
    """Idempotently record that MAC writing is active — both host-wide and (when
    ``chain_path`` is given) per chain. Best-effort: a failure here must never
    break an audit write."""
    stamp = json.dumps({"since": now if now is not None else time.time()})
    try:
        sp = _mac_sentinel_path()
        if not sp.exists():
            sp.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(sp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(stamp)
            except FileExistsError:
                pass
    except Exception:  # noqa: BLE001
        pass
    if chain_path is not None and not _skip_out_of_tree_markers(chain_path):
        try:
            mp = _mac_chain_marker_path(chain_path, genesis=genesis)
            if not mp.exists():
                mp.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(str(mp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as fh:
                        fh.write(stamp)
                except FileExistsError:
                    pass
        except Exception:  # noqa: BLE001
            pass


def _mac_active_since() -> float | None:
    """Return the timestamp MAC was first enabled on this host, or None when the
    sentinel is absent/unreadable. A legacy sentinel without a timestamp returns
    0.0 (treat all chains as post-MAC — conservative)."""
    try:
        sp = _mac_sentinel_path()
        if not sp.exists():
            return None
        raw = sp.read_text().strip()
        if not raw:
            return 0.0
        try:
            return float(json.loads(raw).get("since", 0.0))
        except (ValueError, AttributeError, TypeError):
            return 0.0
    except Exception:  # noqa: BLE001
        return None


def _canonical(rec: dict[str, Any]) -> str:
    """Stable JSON serialization for hashing — sorted keys, no whitespace.

    allow_nan=False so a non-finite float can never produce a non-RFC-8259
    chain line (the floor already drops them; this is defence-in-depth)."""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ── Chain-integrity hash exclusion set (single source of truth) ─────────────
#
# Fields that ``write_event`` appends to a record AFTER computing ``hash`` and
# are therefore NOT part of the chain-integrity hash. Any verifier that
# recomputes the hash-link MUST exclude exactly this set — including CLAG's
# fast-path ``_verify_hash_link``. Drift between this set and a verifier's
# exclusion list manifests as a fail-closed false positive on every record
# that carries the missing field:
#   · ``mac``          — ADR-0137 M2 keyed MAC (added when an anchor key exists).
#   · ``hash``         — the field being recomputed itself.
#   · ``instance_id`` / ``instance_sig`` — ADR-0153 M3 additive per-event
#     attestation (out-of-band, NOT chain state). Adding these without updating
#     CLAG's verifier tripped the L22 engine-spawn gate on every signed
#     ``session.reset``.
CHAIN_HASH_EXCLUDED_FIELDS: tuple[str, ...] = (
    "hash", "mac", "instance_id", "instance_sig",
)


# ── ADR-0129 M1 — structural audit-detail allowlist (the floor) ─────────────
#
# The audit chain is permanent and tamper-evident; anything written here can
# never be redacted. This floor — enforced at the single chain-writer
# chokepoint — guarantees no emitter (guarded or not) can leak content / PII /
# secrets into a chain event's `details`, regardless of which layer wrote it.
#
# Design notes (false-positive avoidance is load-bearing):
#  * Content-ish names use EXACT key match so legit metadata survives:
#    "text" is forbidden but "text_len" is kept; "output"→drop but
#    "output_hash"→keep; "instruction"→drop but "instruction_hash"→keep;
#    "rows"→drop but "rows_sampled"→keep; "token"→drop but "tokens_used"/
#    "input_tokens"→keep.
#  * Only UNAMBIGUOUS secret tokens use substring match (db_password,
#    client_secret, …). "token" is deliberately NOT a substring (token counts
#    are legit metadata).
#  * Oversize string/blob values are dropped (content/transcripts are long;
#    metadata is short). Generous cap to avoid dropping legit reason strings.
#
# Never RAISES (audit is best-effort) and never logs the dropped VALUE — only
# the key name, inline under `_dropped_fields`.

_AUDIT_FORBIDDEN_EXACT: frozenset[str] = frozenset({
    "prompt", "output", "text", "transcript", "message", "content", "body",
    "instruction", "payload", "sample", "rows", "raw", "stdout", "stderr",
    "query", "token", "api_key", "apikey", "access_token", "refresh_token",
    "email", "password", "secret",
    # Writer-only markers — deny on INPUT so a caller can't forge them; the
    # writer re-injects the genuine ones after filtering (review HIGH #2/#3).
    "_dropped_fields", "_unfiltered", "_tail_truncated_since",
    "_chain_replaced_from", "_pii_fingerprinted",
})
_AUDIT_FORBIDDEN_SUBSTR: tuple[str, ...] = (
    "password", "passphrase", "secret", "credential", "private_key",
    "authorization", "cookie", "csrf", "api_key",
)
_AUDIT_MAX_DETAIL_VALUE_LEN = 2048

# High-precision SECRET shapes scanned in string VALUES (not just keys) —
# review MEDIUM #4. Deliberately ONLY unambiguous credential tokens: the
# surroundings review (2026-06) showed a broad email pattern here is a
# false-positive magnet — it matched WhatsApp JIDs (`…@s.whatsapp.net`),
# ActivityPub actor ids, email-channel chat_keys, and URL userinfo
# (`https://user@host/…`), silently dropping the payload of security events
# (path_gate.denied target/command), sender attribution, and error reasons.
# Those `@`-bearing values are pseudonymous IDs / URLs, NOT free-text PII.
# Email-as-PII is still caught by the `email` KEY denylist; a raw email under
# a benign key is a low-frequency residual risk we accept to keep the floor
# from corrupting the forensic trail. Credential tokens stay — they are
# unambiguous and high-value.
_AUDIT_SECRET_VALUE_RE = re.compile(
    r"sk-[A-Za-z0-9]{20,}"                              # OpenAI-style key
    r"|AKIA[0-9A-Z]{16}"                                # AWS access key id
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"             # PEM private key
    r"|gh[pousr]_[A-Za-z0-9]{20,}"                      # GitHub token
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"                    # Slack token
)

# ADR-0129 M2 — optional per-event POSITIVE allowlist (tightening on top of
# the M1 denylist floor). For a registered event type, ONLY these keys (plus
# the structural ``_dropped_fields`` / ``tenant_id`` reserved keys) survive;
# unknown keys are dropped + flagged. Unregistered events fall back to the
# denylist-only floor. Registered conservatively: only event families whose
# exact field set is known + tested (start with the audit events this floor's
# authors own — the previously-UNGUARDED sensitive ones). Other layers keep
# their own pre-write allowlists (console/compute) as defence-in-depth.
# Structural forensic spine — these survive a positive allowlist regardless of
# which event registers (review MEDIUM #5: prevents silently dropping the
# cross-event context when a bridge event that goes through audit_event —
# which injects channel/chat_key/user/persona — is later allowlisted).
#: Structural spine. These keys survive both the M2 positive allowlist and the
#: F-A4 vocabulary floor for EVERY event type, because they are what makes a
#: record attributable (GDPR Art. 30). The exemption is from the KEY filters
#: only — since 2026-09-07 their VALUES are PII-scanned like any other, and a
#: value with an email/phone shape is replaced by its sha256[:8] fingerprint
#: rather than dropped (see :func:`filter_audit_details`). Adding a key here
#: means "this names WHO/WHERE, never WHAT" — never a message, reason or title.
_AUDIT_RESERVED_KEYS: frozenset[str] = frozenset({
    "tenant_id", "channel", "chat_key", "user", "persona",
})
# F-A4 (2026-09-07) — the floor is DEFAULT-DENY for keys. An event type with a
# registered positive allowlist admits only its own keys; every OTHER event
# type admits only keys from this universal metadata vocabulary. Anything else
# is dropped and named in ``_dropped_fields`` — never widened back to
# allow-all. The vocabulary was built from a static scan of every emitter in
# the repo (details={...} literals and audit_event(**kw) forwards) minus the
# content/PII-shaped names ("snippet", "line_excerpt", "userName", "request",
# …). Adding a key here is a maintainer decision: it must be metadata — an id,
# a count, a code, a hash prefix — never free text, never a raw identifier of
# a natural person. The values under these keys are additionally scanned for
# email/phone shapes (``_audit_value_pii``) on non-allowlisted event types.
#
# HOW A WRITER GETS ITS KEYS THROUGH (read this before adding to the set):
#   1. Preferred — register a per-event positive allowlist once at import time:
#          from forge.security_events import register_event_allowlist
#          register_event_allowlist("my.event", {"run_id", "outcome", "count"})
#      Only those keys (plus the reserved structural spine channel/chat_key/
#      user/persona/tenant_id) survive for that event type; the universal
#      vocabulary below is then NOT consulted for it.
#   2. Otherwise the top-level keys of ``details`` must be in
#      ``_AUDIT_KNOWN_KEYS``. Unknown keys are dropped — never written — and
#      their NAMES (never values) are listed under ``details._dropped_fields``,
#      so a writer whose keys vanish can see exactly which ones to register.
#   3. Nested dict keys are not vocabulary-checked; they pass the denylist
#      floor (``_AUDIT_FORBIDDEN_*``), the oversize cap and the PII value scan.
# A record's chain reference (``audit_ref``), identifiers (``*_id``), counts,
# codes, hashes and outcomes are the shape this vocabulary admits.
_AUDIT_KNOWN_KEYS: frozenset[str] = frozenset({
    "acs_id", "action", "action_id", "action_type", "activated", "actor_id", "actor_id_prefix",
    "adapter", "adaptive_n", "age_s", "agent_prefix", "aggregate_score", "allowed",
    "allowed_engines", "approver_count", "artifact_count", "asst_chars", "attachment_count",
    "attempt", "attempts", "attention_budget", "audio_s", "audit_ref", "audit_retained",
    "average_latency_ms", "base_hash", "base_n", "before_ts", "blocked", "boot_layer",
    "break_lines", "breaker_state", "broken_count", "broken_records", "budget_s",
    "budget_status", "buffer_lines", "bullet_id", "cache_hit", "bundle", "by_tool", "caller_persona",
    "can_delegate", "cap_tokens", "capability", "capability_count", "capacity", "category",
    "cause", "chain_dna", "changed_by_hash", "changes", "channel", "channel_id", "chars",
    "chat_id", "chat_key", "chat_key_hash", "choice", "chosen", "cit_fp", "claimed_plugin_id",
    "classes", "classification", "cleared_by", "cluster_id", "columns", "confidence",
    "configured", "consecutive_failures", "consent_granted_by", "consent_reason",
    "conservative_mode", "content_hash", "context_id", "conv_rate", "corvin_home", "count",
    "daily_tokens_limit", "daily_tokens_used", "data_classification", "data_handle",
    "datasource", "decision", "decision_hash", "decision_type", "decisions_count",
    "declared_boot_layer", "dedup_key_hash", "delegation_id", "delegation_target", "deleted",
    "deleted_bases", "deleted_layers", "delivered", "dependents", "depth", "detail", "details",
    "dna_prefix", "domain", "dropped", "dropped_count", "dropped_event_type", "dropped_oldest",
    "dropped_severity", "duration_hours", "duration_ms", "effective", "endorsement_id",
    "endpoint_id", "engine", "engine_attestation", "engine_id", "engine_zone", "entity_id",
    "entity_type", "entries", "env_keys", "epoch", "error", "error_class", "error_code",
    "error_count", "error_message", "error_strategy", "error_type", "estimated_tokens",
    "event", "event_count", "event_id", "event_type", "execution_strategy", "existing_uid",
    "exit_code", "expected_path", "expected_type", "expires_at", "extra", "fail_count",
    "failed", "failed_branches", "failed_count", "failed_runs", "failure_count", "fallback",
    "fallback_engine", "fallback_to", "feature", "field", "file", "file_size_bytes",
    "files_deleted", "fingerprint", "first_break_line", "first_failures", "first_problems",
    "first_ts", "fixed", "forbid_engines", "forced", "format", "found", "free_bytes", "from",
    "from_line", "from_role", "from_uid", "from_uids", "gate_count", "goal_revision_rate",
    "grant_id", "granted_by_hash", "granted_via", "grantee_prefix", "grantee_type", "grantor",
    "grantor_role", "has_audio", "has_signature", "hash", "healing_action", "hook",
    "hook_registered", "host", "ibc_jti", "id", "incident_id", "input_keys", "input_tokens",
    "installed_by", "instance_id_match", "instruction_hash", "instruction_len", "interval_s",
    "issue", "issuer", "iteration", "iterations", "job_id", "converge", "jti", "k_max",
    "keypair_path_prefix", "keys_after",
    "keys_before", "killed", "kind", "lang", "last_break_line", "last_error", "last_status",
    "last_ts", "latency_ms", "layer", "layer_count", "layer_id", "layer_name", "len", "level",
    "levels", "limit", "limit_msgs", "limit_tokens", "limit_value", "lint_errors",
    "llm_confidence", "locality", "lockout_s", "lom", "lom_audit_write", "lom_bound",
    "lom_hash", "loop_id",
    "loss_delta", "loss_gap", "loss_total", "manifest", "manifest_age_days",
    "matched_pattern_count", "matched_rule", "matcher", "max_attempts", "max_bytes",
    "max_depth", "max_loops", "member_prefix", "messages_total", "method", "metric", "mime",
    "min_seal_version", "mismatch_count", "missing", "mode", "model", "model_id", "msg_id",
    "n_subtasks", "name", "negative_ratio", "network_egress", "new_id_prefix", "new_status",
    "node_id", "node_type", "nodes", "nonce_epoch", "ok", "old_id_prefix", "old_status",
    "one_shot_share", "operations", "operator_initiated", "org_handle", "origin", "origin_id",
    "outcome", "output_hash", "output_tokens", "overwrite", "owner_prefix", "owner_text_len",
    "p50_ms", "p99_ms", "package_id", "parent_span_id", "parent_worker_id",
    "participant_count", "passed", "path", "payload_sha256", "payload_size", "persona",
    "phase", "pii_risk", "pipeline_id", "plugin_id", "plugin_type", "point", "policy",
    "policy_path", "post_id", "post_id_prefix", "post_type", "prev_epoch_tail_prefix",
    "prev_hash", "prev_run_id", "primitive", "prior", "prior_action", "prior_bundle",
    "priority", "probe_set_sha256", "problem_count", "problem_lines", "prompt_length",
    "protocol_version", "provider", "public_key_hex_prefix", "publisher", "purged_envelopes",
    "query_chars", "query_count", "query_latency_ms", "queue_depth", "queued", "quorum_size",
    "quota", "reason", "reason_code", "recipient_count", "recommended_model",
    "records_deleted", "redacted_class_count", "redacted_classes", "removed_by", "request_id",
    "request_id_prefix", "requested_tenant_id", "requested_value", "requires_consent",
    "reset_by", "reset_mode", "resolved_model", "result", "result_count", "retry_after_s",
    "returned_type", "revoked_tenant_id", "revoker", "revoker_role", "risk", "role",
    "rowcount", "rows_deleted", "rows_sampled", "run_id", "runs_per_minute", "sandbox", "samples",
    "saved_from_scope", "scope", "scope_label", "scope_root", "score", "seam", "seam_reason",
    "secret_ref", "secrets_used", "sender_hash", "sender_instance_id", "seq", "session_id",
    "set_by", "settings_path", "severity", "sha", "sha256", "sha256_prefix", "sid_fingerprint",
    "signal", "since", "site", "size", "size_b", "size_bytes", "skill_id", "skill_name",
    "skill_version", "skills", "skipped", "snapshot_bytes", "snapshot_id", "snapshot_taken",
    "solicited", "source", "span_id", "spawn_nonce", "spawned", "stack_size", "stage",
    "started_at", "state", "state_purged", "status", "step", "strategy",
    "strategy_correction_rate", "subdir_count", "subdirs", "subject", "subtask_count",
    "succeeded", "success_rate", "success_rate_percent", "successful_runs",
    "superseded_key", "superseded_genesis", "superseded_tail", "superseded_size_bytes",
    "superseded_records", "canonical_key", "tag_count",
    "tail_hash_prefix", "target", "target_engine", "target_instance_id_prefix", "target_role",
    "task_id", "task_type", "tenant_check", "tenant_count", "tenant_id", "tenant_zone",
    "text_len", "threshold_bytes", "throttled_count", "tier", "timed_out", "timeout_s", "to",
    "to_line", "toctou_max_s", "token_type", "tokens", "tokens_available", "tokens_remaining",
    "tokens_requested", "tokens_total", "tokens_used", "tool", "tool_call_count", "tool_id",
    "tool_name", "tool_names", "tools_called", "total", "total_layers", "total_probes",
    "total_records", "total_runs", "total_tokens_used", "trace_available",
    "trigger_chain_hash", "trigger_event", "triggered_by", "tripwire", "truncated",
    "truncated_at_line", "trust_level", "ttl", "ttl_s", "turn_id", "uid", "uid_hash", "ulo_id",
    "unparseable", "unpinned_count", "until", "update_count", "used", "user", "user_chars",
    "user_id", "validation_type", "verdict", "verification_complete", "version", "via",
    "voice", "wait_time_ms", "wall_clock_s", "wall_ms", "want_voice", "why", "window_end",
    "window_s", "window_start", "withheld_sensitive", "worker_id", "workers_spawned",
    "workflow", "workflow_id", "zone",
})

# High-precision PII VALUE shapes for non-allowlisted event types. Deliberately
# excludes the pseudonymous "@"-bearing ids the 2026-06 surroundings review
# found (WhatsApp JIDs ``…@s.whatsapp.net`` / ``…@g.us`` / ``…@lid``, URL
# userinfo ``https://user@host/``) — those are channel identities, not PII.
_AUDIT_EMAIL_VALUE_RE = re.compile(
    r"(?<![\w/:@.+-])[A-Za-z0-9._%+-]+@"
    r"(?!s\.whatsapp\.net\b|g\.us\b|lid\b|broadcast\b)"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])"
)
# International (+CC …) or trunk-prefixed (0…) phone numbers with 8–15 digits.
# Requires a leading "+" or a separator so hashes, snowflake ids, timestamps
# and version strings (no "+", no separator, or too few/many digits) never match.
_AUDIT_PHONE_VALUE_RE = re.compile(
    r"(?<![\w.:/-])(?:\+\d{1,3}|0\d{1,4})(?:[\s./()-]{0,3}\d){6,13}(?![\w-])"
)


def _audit_value_pii(v: str) -> bool:
    """True when a string VALUE carries an email or phone shape (F-A4)."""
    if _AUDIT_EMAIL_VALUE_RE.search(v):
        return True
    for m in _AUDIT_PHONE_VALUE_RE.finditer(v):
        tok = m.group(0)
        digits = sum(c.isdigit() for c in tok)
        if 8 <= digits <= 15 and (tok.startswith("+") or any(c in " ./()-" for c in tok)):
            return True
    return False


_EVENT_ALLOWLIST: dict[str, frozenset[str]] = {
    # ADR-0171 M1 — universal engine-span audit (engine-agnostic, every path).
    # Canonical here so the allowlist is load-bearing regardless of import order;
    # engine_span._register_allowlists() unions the same set (idempotent).
    # NEVER: prompt/output/transcript text, raw uid/email (GDPR Art. 5).
    "engine.span.start": frozenset({
        "span_id", "parent_span_id", "role", "engine_id", "model_id",
        "run_id", "turn_id", "started_at",
    }),
    "engine.span.end": frozenset({
        "span_id", "parent_span_id", "role", "engine_id", "model_id",
        "run_id", "turn_id", "status", "duration_ms", "tokens_used", "tool_call_count",
        "trace_available",  # ADR-0172 M1: signals a worker-trace.jsonl exists
    }),
    # ADR-0104 ACS core events — explicit allowlists for every emitted event.
    # Metadata only; comment at EVENT_SEVERITY block lists the intent.
    # NEVER: prompt/output, manager JSON, worker result, goal/task text (GDPR Art. 5).
    "acs.run_start": frozenset({"run_id", "workflow_id", "max_loops", "max_depth"}),
    "acs.run_error": frozenset({"run_id"}),
    "acs.workflow_complete": frozenset({
        "run_id", "iteration", "workers_spawned", "artifact_count",
    }),
    "acs.workflow_failed": frozenset({"run_id", "iteration", "reason"}),
    "acs.budget_exhausted": frozenset({"run_id", "reason", "iteration"}),
    "acs.manager_call": frozenset({"run_id", "iteration"}),
    "acs.manager_error": frozenset({"run_id", "reason"}),
    "acs.manager_parse_error": frozenset({"run_id", "iteration"}),
    "acs.manager_decided": frozenset({
        "run_id", "iteration", "decision_type", "decision_hash",
        "n_subtasks", "model_id", "spawn_nonce",
    }),
    "acs.delegation": frozenset({
        "run_id", "depth", "parent_worker_id", "subtask_count",
    }),
    "acs.worker_spawned": frozenset({
        "run_id", "worker_id", "iteration", "depth", "engine_id", "model_id",
        "instruction_hash", "spawn_nonce", "parent_worker_id", "can_delegate",
    }),
    # engine_attestation is a nested dict {engine_id, model_id, locality} —
    # top-level key must be allowlisted so it passes the M2 gate; _audit_scrub
    # recursively cleans its contents via the denylist floor.
    "acs.worker_traced": frozenset({
        "run_id", "worker_id", "status", "confidence", "output_hash",
        "duration_ms", "tokens_used", "spawn_nonce", "engine_attestation",
    }),
    "acs.worker_error": frozenset({"run_id", "worker_id", "reason"}),
    "acs.engine_started": frozenset({
        "run_id", "worker_id", "engine_id", "model_id", "locality",
    }),
    "acs.engine_completed": frozenset({
        "run_id", "worker_id", "engine_id", "model_id", "locality",
        "duration_ms", "tokens_used", "exit_code",
    }),
    "acs.engine_error": frozenset({
        "run_id", "worker_id", "engine_id", "model_id", "duration_ms",
    }),
    "acs.worker_l34_blocked": frozenset({"run_id", "worker_id", "engine"}),
    "acs.gate_chain_evaluated": frozenset({
        "run_id", "iteration", "passed", "aggregate_score",
        "gate_count", "loss_total", "loss_delta",
    }),
    "acs.gate_abort": frozenset({"run_id", "reason"}),
    "acs.max_rejections_reached": frozenset({"run_id", "count"}),
    # Manager-level gate blocks (parallel to worker_l34_blocked/worker_l35_blocked).
    "acs.manager_l34_blocked": frozenset({"run_id", "engine"}),
    "acs.manager_l35_blocked": frozenset({"run_id", "engine"}),
    "acs.manager_gates_unavailable": frozenset({"run_id", "engine", "reason"}),
    "acs.worker_gates_unavailable": frozenset({"run_id", "worker_id", "engine", "reason"}),
    # ADR-0105 M4 adaptive + convergence diagnostics.
    "acs.m4_adaptive_workers": frozenset({
        "run_id", "iteration", "adaptive_n", "base_n", "loss_gap",
    }),
    "acs.loss_plateau": frozenset({"run_id", "iteration", "loss_total"}),
    "acs.loss_regression": frozenset({"run_id", "iteration", "loss_delta"}),
    # ADR-0127 datasource binding (were audit-silent before; now positively
    # constrained — never a connection string, secret value, or DB row).
    "tool.datasource_env_injected": frozenset({"persona", "env_keys"}),
    "acs.datasource_snapshot": frozenset({
        "run_id", "datasource", "adapter", "classification",
        "snapshot_taken", "snapshot_bytes", "withheld_sensitive",
    }),
    "acs.l34_unavailable": frozenset({"engine_id"}),
    "acs.worker_l35_blocked": frozenset({"worker_id", "engine"}),
    # L44 acceptable-use markers — run_id + workflow_id only, NEVER goal text.
    "acs.run_blocked_house_rules":      frozenset({"run_id", "workflow_id"}),
    "acs.house_rules_gate_unavailable": frozenset({"run_id", "workflow_id", "reason"}),
    # ACS-X (ADR-0155)
    "acs_x.classified":          frozenset({"primitive", "confidence", "path",
                                             "channel", "chat_key"}),
    "acs_x.directive_injected":  frozenset({"primitive", "channel", "chat_key"}),
    "acs_x.fallback_llm":        frozenset({"primitive", "llm_confidence", "model"}),
    "acs_x.classify_failed":     frozenset({"error_class", "channel", "chat_key"}),
    "acs_x.persona_suppressed":  frozenset({"primitive", "persona", "channel", "chat_key"}),
    # ATO — Autonomous Task Orchestration (ADR-0164 M3/M4)
    "task_orchestrator.plan_generated":     frozenset({"task_type", "execution_strategy",
                                                        "k_max", "channel", "chat_key", "tenant_id"}),
    "task_orchestrator.convergence_low":    frozenset({"task_type", "conv_rate",
                                                        "samples", "tenant_id"}),
    "task_orchestrator.goal_template_weak": frozenset({"task_type", "goal_revision_rate",
                                                        "samples", "tenant_id"}),
    "task_orchestrator.strategy_drift":     frozenset({"task_type", "strategy_correction_rate",
                                                        "samples", "tenant_id"}),
    # ATO — Dispatch integration (ADR-0165 M5/M6/M7) — engine_id in all entries
    # *_hint: advisory (no actual routing); *_routed: actual dispatch occurred.
    "task_orchestrator.delegation_hint":    frozenset({"task_type", "delegation_target",
                                                        "engine_id", "channel", "chat_key", "tenant_id"}),
    "task_orchestrator.delegation_routed":  frozenset({"task_type", "delegation_target",
                                                        "data_classification", "confidence",
                                                        "engine_id", "channel", "chat_key", "tenant_id"}),
    "task_orchestrator.model_selected":     frozenset({"task_type", "recommended_model",
                                                        "resolved_model", "engine_id",
                                                        "channel", "tenant_id"}),
    "task_orchestrator.compute_hint":       frozenset({"task_type", "strategy",
                                                        "engine_id",
                                                        "channel", "chat_key", "tenant_id"}),
    "task_orchestrator.compute_routed":     frozenset({"task_type", "strategy",
                                                        "confidence", "engine_id",
                                                        "channel", "chat_key", "tenant_id"}),
    # CCC — Chat Command Center (ADR-0168).
    # PII exclusion: no prompt text, no slot values, no entity names in details.
    "ccc.entity_extracted":  frozenset({"entity_type", "confidence", "forced",
                                         "action_id", "channel", "chat_key", "tenant_id"}),
    "ccc.action_dispatched": frozenset({"entity_type", "action_id", "entity_id",
                                         "status", "channel", "chat_key", "tenant_id"}),
    "ccc.action_error":      frozenset({"entity_type", "action_id",
                                         "channel", "chat_key", "tenant_id"}),
    # Vault access mirror — KEY NAMES only, never the secret value.
    "vault.get": frozenset({"name", "source", "ok"}),
    "vault.set": frozenset({"name", "source", "ok"}),
    "vault.unlock": frozenset({"name", "source", "ok"}),
    "vault.forget": frozenset({"name", "source", "ok"}),
    # Secret injection into bwrap env — secret NAMES only (values never in chain).
    # "secrets_used" would be dropped by the _AUDIT_FORBIDDEN_SUBSTR "secret"
    # match; the positive allowlist here overrides the denylist floor for this
    # event so the forensic record of which keys were injected is preserved.
    "tool.secrets_injected": frozenset({"persona", "secrets_used"}),
    # ADR-0142 — Layer Extension API. Strictly metadata: extension identity +
    # which hook + a reason code. NEVER hook input/output content. The
    # extension_api._filter_ext_details gate enforces the same set client-side;
    # this is the writer-side defence-in-depth.
    "ext.installed": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    "ext.removed": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    "ext.enabled": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    "ext.disabled": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    "ext.hook_denied": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    "ext.load_failed": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    "ext.core_namespace_rejected": frozenset({"name", "version", "scope", "event_type", "hook", "reason"}),
    # ADR-0156 M7 — Custom Layer Registry (custom_layer.*).
    # Strictly metadata: identity + tier + a reason code.
    # NEVER manifest contents, tool code, secret values, or file paths.
    "custom_layer.installed":           frozenset({"layer_name", "tier", "reason"}),
    "custom_layer.enabled":             frozenset({"layer_name", "tier"}),
    "custom_layer.disabled":            frozenset({"layer_name", "tier"}),
    "custom_layer.removed":             frozenset({"layer_name", "tier", "reason"}),
    "custom_layer.boot_limit_exceeded":           frozenset({"layer_name", "tier", "reason"}),
    "custom_layer.boot_limit_enforcement_failed": frozenset({"reason"}),
    # ADR-0157 — L44 Resilient Classifier (house_rules.*).
    # Strictly metadata: provider identity, counts, window size. NEVER task text.
    "house_rules.provider_fallback":    frozenset({"provider", "cause", "fallback_to"}),
    "house_rules.classifier_degraded":  frozenset({"error_count", "window_s"}),
    # ADR-0141 — Layer Integrity Protocol. Strictly metadata: a reason code,
    # which layers / counts, and network-attestation match flags. NEVER file
    # paths, file content, or modification timestamps.
    "security.capability_missing": frozenset({"reason", "missing"}),
    "layer_integrity.verified": frozenset({"reason", "layer_count"}),
    "layer_integrity.manifest_invalid": frozenset({"reason"}),
    "layer_integrity.manifest_absent": frozenset({"reason"}),
    "layer_integrity.mismatch": frozenset({"reason", "mismatch_count"}),
    "a2a.layer_integrity_mismatch": frozenset(
        {"reason", "origin_id", "endpoint_id", "protocol_version", "channel", "chat_key"}
    ),
    "a2a.peer_audit_anomaly": frozenset(
        {"reason", "endpoint_id", "instance_id_match", "channel", "chat_key"}
    ),
    # ADR-0145 — IBC lifecycle. Strictly metadata: JTI prefix (16 hex chars,
    # non-reversible), reason codes, origin_id. NEVER email, license_id, pubkey
    # material, or full JWT content.
    "instance.ibc_issued":            frozenset({"ibc_jti"}),
    "instance.ibc_verified":          frozenset({"origin_id", "ibc_jti", "sender_instance_id"}),
    "instance.ibc_expired":           frozenset(set()),
    "instance.ibc_revoked":           frozenset({"origin_id", "reason", "ibc_jti"}),
    "instance.key_rotated":           frozenset(set()),
    "instance.ibc_sig_failed":        frozenset({"origin_id", "reason", "ibc_jti"}),
    "instance.ibc_hardware_mismatch": frozenset({"reason"}),
    "instance.hardware_bound":        frozenset({"ibc_jti"}),
    "instance.attestation_verified":  frozenset({"origin_id", "trust_level", "ibc_jti"}),
    "instance.attestation_failed":    frozenset({"origin_id", "trust_level", "ibc_jti", "reason"}),
    # ADR-0153 M4 — CorvinID cert lifecycle. Metadata-only: 8-char instance_id
    # prefix, request_id. NEVER email, full UUID, subject_id, or cert content.
    "identity.certificate_revoked":  frozenset({"request_id"}),
    "identity.resolution_requested": frozenset({"target_instance_id_prefix"}),
    # ADR-0152 — Layer 24 PII-detection metric/ROPA forensic spine. ``classes``
    # is a count-map of PII CLASS LABELS → integer counts (counts only, never
    # values, never column names). The label "email" collides with the M1 key
    # denylist; the positive allowlist + count-map preservation (below) keep the
    # label→count map intact so corvin_data_pii_detected_total and the GDPR ROPA
    # report do not silently under-report email-class detections.
    "data.pii_detected": frozenset({"data_handle", "classes"}),
    # Gateway webhook secret-resolution failure. ``secret_ref`` is the vault key
    # NAME (never the secret value); it collides with the "secret" substring
    # denylist, so the positive allowlist overrides the floor for this event —
    # same pattern as tool.secrets_injected / vault.* (names only).
    "gateway.webhook_secret_missing": frozenset({"run_id", "secret_ref", "host"}),
    # ADR-0166 — Session Participation Gate (SPG).
    # Strictly metadata — uid_hash/changed_by_hash/granted_by_hash/sender_hash
    # are sha256 prefixes (never raw UIDs). msg_id is an opaque message handle.
    # NEVER raw UID, username, message content, or file paths.
    "spg.mode_changed":    frozenset({"mode", "changed_by_hash", "channel", "chat_key", "tenant_id"}),
    "spg.guest_invited":   frozenset({"uid_hash", "ttl_s", "granted_by_hash", "channel", "chat_key", "tenant_id"}),
    "spg.guest_removed":   frozenset({"uid_hash", "channel", "chat_key", "tenant_id"}),
    "spg.message_dropped": frozenset({"msg_id", "sender_hash", "mode", "reason", "channel", "chat_key", "tenant_id"}),
    # ADR-0163 — ULO store corruption event; chat_key_hash is sha256 prefix (never raw key).
    "ulo.store_corrupted":    frozenset({"channel", "chat_key_hash"}),
    # ADR-0163 — ULO CRUD events. ulo_id is an opaque UUID; action is a controlled vocab string.
    # NEVER: raw objective text, user input, or raw chat_key.
    "ulo.objective_created":  frozenset({"ulo_id", "priority", "scope", "channel"}),
    "ulo.objective_updated":  frozenset({"ulo_id", "action", "channel"}),
    "ulo.objective_deleted":  frozenset({"ulo_id", "channel"}),
    # ADR-0030/0243 — plugin lifecycle (structural identity only, never manifest
    # content, settings values or error text).
    "plugin.loaded": frozenset({
        "plugin_id", "plugin_type", "boot_layer", "version", "tenant_id", "origin", "source",
    }),
    "plugin.unloaded": frozenset({
        "plugin_id", "plugin_type", "boot_layer", "version", "tenant_id", "origin", "source",
        "reason", "operator_initiated",
    }),
    "plugin.load_failed": frozenset({
        "plugin_id", "plugin_type", "boot_layer", "version", "tenant_id", "origin", "source",
        "reason", "error_class", "error_type",
    }),
    # ADR-0017 Phase II — compliance reports: report identity + counters + the
    # chain anchor hash; report CONTENT never enters the chain.
    "compliance.report_generated": frozenset({
        "report_type", "tenant_id", "period_start_ts", "period_end_ts", "total_events",
        "chain_intact", "anchor_hash", "page_count_estimate", "output_path", "duration_ms",
    }),
    "compliance.report_failed": frozenset({
        "report_type", "tenant_id", "reason", "error_class", "error_type",
    }),
    "plugin.disabled": frozenset({
        "plugin_id", "plugin_type", "boot_layer", "version", "tenant_id", "origin", "source", "reason",
    }),
    "plugin.boot_layer_rejected": frozenset({"plugin_id", "tenant_id", "declared_boot_layer", "reason"}),
    # Layer 18 — read-only member drop: counts/prefixes only, never the text.
    "bridge.read_only_drop": frozenset({
        "first_drop", "text_len", "tenant_id", "channel", "chat_id_prefix", "chat_key", "user", "persona",
    }),
    # ADR-0015 — console actions: target identity + outcome code, never the request body.
    "console.action_performed": frozenset({
        "action", "target_id", "target_type", "tenant_id", "sid_fingerprint", "ok", "reason_code", "reason",
    }),
    "console.action_failed": frozenset({
        "action", "target_id", "target_type", "tenant_id", "sid_fingerprint", "ok", "reason_code", "reason",
    }),
    "console.action_denied": frozenset({
        "action", "target_id", "target_type", "tenant_id", "sid_fingerprint", "ok", "reason_code", "reason",
    }),
    # ── R4-A: GDPR Art. 17 erasure (L36, ADR-0045) ──────────────────────────
    # The orchestrator (operator/bridges/shared/erasure_orchestrator.py) built a
    # careful metadata-only vocabulary — a closed ``ReasonCode`` enum, a
    # fail-closed ``_assert_safe_audit_value`` that refuses any value carrying a
    # path separator or exception shape — and then had NO allowlist here, so the
    # ADR-0640 default-deny floor threw away exactly the fields that design
    # exists to carry. Measured 2026-09-07: ``erasure.requested`` reached the
    # chain as ``{"request_id": ..., "scope": ..., "_dropped_fields":
    # ["requester", "subject_id"]}`` and ``erasure.completed`` lost
    # ``subject_id``, ``overall_status`` AND ``applied_count`` while
    # ``failed_count`` survived — an asymmetry nobody could see by eye. The
    # immutable Art. 30 trail recorded that AN erasure happened and not who was
    # erased, who asked, or whether it worked.
    #
    # ``subject_id`` / ``requester`` are IDENTIFIERS, and on a bridge install
    # they can be an e-mail or a phone number. Writing one verbatim into an
    # append-only never-redactable chain — as the record of that subject's
    # ERASURE — would be its own Art. 17 defect, so both are listed in
    # ``_AUDIT_PSEUDONYM_KEYS`` below: a PII-shaped value is fingerprinted to
    # the same sha256[:8] pseudonym the reserved spine uses, never dropped.
    "erasure.requested": frozenset({
        "request_id", "subject_id", "requester", "scope", "tenant_id",
    }),
    "erasure.applied": frozenset({
        "request_id", "subject_id", "layer_id", "status", "count", "code",
        "duration_ms", "tenant_id",
    }),
    "erasure.skipped": frozenset({
        "request_id", "subject_id", "layer_id", "status", "count", "code",
        "duration_ms", "tenant_id",
    }),
    "erasure.failed": frozenset({
        "request_id", "subject_id", "layer_id", "status", "count", "code",
        "duration_ms", "tenant_id",
    }),
    "erasure.completed": frozenset({
        "request_id", "subject_id", "overall_status", "applied_count",
        "failed_count", "tenant_id",
    }),
    "erasure.trail_failed": frozenset({
        "request_id", "subject_id", "error_type", "tenant_id",
    }),
    # ── R4-B: L37 / ADR-0044 audit-at-rest sealing ──────────────────────────
    # Same root cause. Reproduced through the real ``rotate_and_seal``:
    # ``audit.segment_sealed`` landed as ``{"_dropped_fields": [...],
    # "chain_dna": ...}`` — the record carried nothing but the list of what was
    # thrown away. ``audit.unseal_requested`` lost BOTH ``requester`` and
    # ``sealed_segment``, so a decryption of an encrypted segment was
    # unattributable. All values are file NAMES (never paths — the emitters pass
    # ``.name``), an operator-config sealer kind, and counters.
    "audit.segment_sealed": frozenset({
        "sealed_segment", "sealer_cmd", "rotated_size_bytes",
    }),
    "audit.segment_timestamped": frozenset({
        "sealed_segment", "timestamp_token_name", "tsa_success",
    }),
    "audit.segment_retired": frozenset({"sealed_segment", "age_days"}),
    "audit.unseal_requested": frozenset({
        "sealed_segment", "requester", "sealer_cmd",
    }),
    # ── R4-B: supply-chain verifier (operator/voice/scripts/supply_chain_verify.py)
    # It carries its own ``_ALLOWED_FIELDS`` map and never registered it here, so
    # ``supply_chain.cve_detected`` reached the chain without ``cve_id`` or
    # ``package_name`` and ``capability_drift`` landed empty. Mirrored verbatim.
    "supply_chain.cve_detected": frozenset({
        "plugin_name", "package_name", "package_version", "cve_id", "severity",
        "fix_available", "cadence",
    }),
    "supply_chain.cve_check_skipped": frozenset({"plugin_name", "reason"}),
    "supply_chain.capability_drift": frozenset({
        "plugin_name", "undeclared_imports", "unused_declared",
    }),
    # ── R4-B: HAC coordinator (L25 compute), console setup, A2A manifest ─────
    # Numeric/identifier telemetry that landed with an empty body.
    "compute.hac_started": frozenset({
        "hac_id", "manager_count", "budget", "backprop_gate",
    }),
    "compute.hac_round_started": frozenset({"hac_id", "round", "managers_to_run"}),
    "compute.hac_root_loss_computed": frozenset({
        "hac_id", "round", "root_loss", "sub_losses",
    }),
    "compute.backprop_gate_opened": frozenset({
        "hac_id", "round", "attributions", "budget_remaining",
    }),
    "compute.hac_budget_reallocated": frozenset({
        "hac_id", "from_manager", "to_manager", "fraction", "trigger",
    }),
    "setup.onboarding_complete": frozenset({"default_engine", "engine_count"}),
    "setup.engine_probe_run": frozenset({"engine_ids", "found_count"}),
    "a2a.manifest_fetched": frozenset({"age_days", "revoked_count", "sig_verified"}),
    "a2a.manifest_stale": frozenset({"age_days", "sig_verified"}),
    "a2a.manifest_cache_sig_invalid": frozenset({"age_days"}),
    # ── R4-B: L25 compute (ADR-0013) — mirror of
    # ``core/compute/corvin_compute/audit.py::_ALLOWED_FIELDS``. That dict was
    # enforced on the way IN and unknown to this floor on the way OUT.
    # Mirrored here (not registered at import time from there) so the floor is
    # import-order independent; guarded against drift by
    # tests/security/test_audit_detail_floor_coverage.py.
    "compute.batch_api_error": frozenset({
        "batch_id_prefix", "error_class", "run_id", "tenant_id"
    }),
    "compute.batch_cancelled": frozenset({
        "batch_id_prefix", "reason", "run_id", "tenant_id"
    }),
    "compute.batch_completed": frozenset({
        "batch_id_prefix", "candidate_count", "duration_ms", "run_id", "tenant_id"
    }),
    "compute.batch_fallback": frozenset({
        "candidate_count", "reason", "run_id", "tenant_id"
    }),
    "compute.batch_gate_blocked": frozenset({
        "reason", "run_id", "tenant_id"
    }),
    "compute.batch_partial": frozenset({
        "batch_id_prefix", "candidate_count", "failed_candidate_count", "run_id",
        "tenant_id"
    }),
    "compute.batch_submitted": frozenset({
        "batch_id_prefix", "candidate_count", "run_id", "tenant_id"
    }),
    "compute.iteration_completed": frozenset({
        "cache_hit", "iter", "loss", "param_fingerprint", "run_id", "strategy",
        "tenant_id", "wall_ms"
    }),
    "compute.run_aborted": frozenset({
        "engine_id", "iterations_done", "run_id", "tenant_id"
    }),
    "compute.run_failed": frozenset({
        "error_class", "error_message", "iter", "run_id", "tenant_id"
    }),
    "compute.run_recovering": frozenset({
        "history_size", "resume_from_iter", "run_id", "tenant_id"
    }),
    "compute.run_started": frozenset({
        "budget", "run_id", "strategy", "tenant_id", "tool_name"
    }),
    "compute.run_terminal": frozenset({
        "best_loss", "convergence_reason", "run_id", "state", "tenant_id",
        "total_iterations", "total_wall_s"
    }),
    "compute.worker_unreachable": frozenset({
        "attempted_socket", "tenant_id"
    }),
    # ── R4-B: ADR-0026 compute fabric — mirror of
    # ``core/compute/corvin_compute/fabric/audit_events.py::FABRIC_AUDIT_EVENTS``
    # (hashes and counts only: ``checkpoint_path_hash`` never a path,
    # ``steering_keys`` never a magnitude, never a model weight or a param value).
    "compute.aggregation_completed": frozenset({
        "final_metric", "n_shards", "run_id", "strategy", "tenant_id"
    }),
    "compute.artifact_registered": frozenset({
        "artifact_path_hash", "artifact_size_b", "backend", "run_id", "tenant_id"
    }),
    "compute.backend_plugin_disabled": frozenset({
        "plugin_name", "tenant_id"
    }),
    "compute.backend_plugin_enabled": frozenset({
        "plugin_name", "plugin_version", "tenant_id"
    }),
    "compute.backend_session_started": frozenset({
        "backend", "backend_version", "run_id", "shard_index", "tenant_id"
    }),
    "compute.checkpoint_written": frozenset({
        "checkpoint_path_hash", "epoch", "run_id", "tenant_id",
        # A SECOND emitter, operator/bridges/shared/compute_awp_importer.py:732
        # (awpkg watermark restore), carries the checkpoint's file NAME and a
        # controlled provenance token instead of a run/epoch. The floor is the
        # UNION of what every legitimate emitter needs — an allowlist narrower
        # than its emitters is how a record lands with an empty body.
        "checkpoint_name", "source"
    }),
    "compute.epoch_completed": frozenset({
        "epoch", "metric_value", "primary_metric", "run_id", "shard_index",
        "tenant_id", "wall_ms"
    }),
    "compute.oracle_steer_applied": frozenset({
        "divergence_detected", "epoch", "run_id", "steering_keys", "tenant_id"
    }),
    "compute.oracle_subprocess_failed": frozenset({
        "epoch", "failure_reason", "run_id", "tenant_id"
    }),
    "compute.resource_slot_denied": frozenset({
        "available_slots", "backend", "requested_slots", "run_id", "tenant_id"
    }),
    "compute.shard_completed": frozenset({
        "final_metric", "run_id", "shard_index", "tenant_id", "total_shards"
    }),
    # ── R4-B: ADR-0026 DataSourceAdapter — mirror of
    # ``fabric/datasources/audit_events.py::DATASOURCE_AUDIT_EVENTS``
    # (secret key NAMES only, watermark HASHES only, PII class COUNTS only).
    "datasource.adapter_disabled": frozenset({
        "adapter_name", "tenant_id"
    }),
    "datasource.adapter_enabled": frozenset({
        "adapter_name", "adapter_version", "tenant_id"
    }),
    "datasource.connection_failed": frozenset({
        "adapter", "error_class", "name"
    }),
    "datasource.connection_tested": frozenset({
        "adapter", "latency_ms", "name", "ok"
    }),
    "datasource.pii_detected": frozenset({
        "name", "pii_class_counts"
    }),
    "datasource.preview_generated": frozenset({
        "n_rows_requested", "n_rows_returned", "name", "pii_columns_redacted"
    }),
    "datasource.registered": frozenset({
        "adapter", "auth_secret_key_names", "estimated_rows", "name",
        "pii_columns_detected", "region"
    }),
    "datasource.residency_violation": frozenset({
        "datasource_name", "declared_region", "tenant_zone"
    }),
    "datasource.schema_refreshed": frozenset({
        "adapter", "columns", "name", "pii_tagged_columns"
    }),
    "datasource.unregistered": frozenset({
        "adapter", "had_checkpoint", "name"
    }),
    "datasource.watermark_advanced": frozenset({
        "name", "new_watermark_hash", "previous_watermark_hash", "rows_read"
    }),
    # R4 — chain-convergence seam (see EVENT_SEVERITY above). Content-free by
    # construction: a label from a closed set, two sha256 path keys, the
    # superseded chain's genesis + final tail hash, its size and record count.
    # The absolute PATH is deliberately NOT here — it is host-specific and lives
    # in the out-of-tree chain-identity record, which the path key indexes.
    "audit.chain_supersedes": frozenset({
        "seam", "seam_reason", "superseded_key", "superseded_genesis",
        "superseded_tail", "superseded_size_bytes", "superseded_records",
        "canonical_key", "tenant_id",
    }),
}

#: R4-A: keys whose value is an IDENTIFIER that may legitimately carry a PII
#: SHAPE (an e-mail, a phone) on a bridge install. They are neither dropped
#: (a record of an erasure with no subject is useless) nor written verbatim
#: (an append-only chain can never be redacted): a PII-shaped value is replaced
#: by ``sha256(value)[:8]`` — the SAME transform ``adapter._pii_fp`` and the
#: reserved spine apply, so all three collide into one pseudonym namespace and
#: an operator can still correlate. Non-PII-shaped values (a uuid request id, an
#: opaque uid) pass through untouched.
_AUDIT_PSEUDONYM_KEYS: frozenset[str] = frozenset({
    "subject_id", "requester",
})

# ADR-0152 — count-map fields: a registered (event_type, field) whose value is a
# {label: count} dict where KEYS are a controlled vocabulary of category labels
# (NOT PII values). _audit_scrub would otherwise drop labels that collide with
# the PII key denylist (e.g. "email"). Such a field is preserved VERBATIM only
# when it is registered here AND strictly shaped (see _is_safe_count_map):
# anything else falls through to the normal recursive scrub, so no PII value,
# oversize blob, or free-text key can ever ride along.
_EVENT_COUNTMAP_FIELDS: dict[str, frozenset[str]] = {
    "data.pii_detected": frozenset({"classes"}),
}
# Safe count-map LABEL shape: short, lowercase identifier, optionally wrapped in
# angle brackets for sentinel labels like "<no_pii>". Deliberately excludes "@",
# ".", "+", spaces and digits-leading — so an actual email/phone/free-text value
# can never masquerade as a label key.
_COUNTMAP_LABEL_RE = re.compile(r"^<?[a-z][a-z0-9_]*>?$")
_COUNTMAP_MAX_KEYS = 64


def _is_safe_count_map(v: Any) -> bool:
    """True iff *v* is a non-empty dict of safe-label → non-negative-int counts."""
    if not isinstance(v, dict) or not v or len(v) > _COUNTMAP_MAX_KEYS:
        return False
    for k, n in v.items():
        if not isinstance(k, str) or len(k) > 32 or not _COUNTMAP_LABEL_RE.match(k):
            return False
        # bool is an int subclass — reject it explicitly; counts are real ints.
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            return False
    return True


#: R2-A6: the two field names on a shipped allowlist that ARE denylisted and are
#: nevertheless permitted — each a maintainer decision recorded here, not a
#: property any caller can claim. Both carry NAMES only, never a value:
#: ``secrets_used`` is a list of vault KEY names, ``secret_ref`` is a config
#: pointer. Anything else that trips the denylist is refused, so a future
#: allowlist cannot quietly re-open the floor by naming a content field.
_VETTED_FORBIDDEN_ALLOWLIST_FIELDS: frozenset[tuple[str, str]] = frozenset({
    ("tool.secrets_injected", "secrets_used"),
    ("gateway.webhook_secret_missing", "secret_ref"),
    # R4-B (2026-09-07), same class as the two above: the ADR-0026 DataSource
    # manifest's ``auth.secret_keys`` is a list of vault key NAMES and its owning
    # module says so at the field ("list of key NAMES, never values",
    # fabric/datasources/audit_events.py). It trips the "secret" substring
    # denylist. Vetted, not claimable by a caller.
    ("datasource.registered", "auth_secret_key_names"),
})


class AuditAllowlistRefused(ValueError):
    """An event allowlist named a field the M1 denylist forbids (R2-A6)."""


def register_event_allowlist(event_type: str, fields: "set[str] | frozenset[str]") -> None:
    """Register/extend the positive allowlist for an event type (ADR-0129 M2).
    Other modules (console, compute) may fold their allowlists in here.

    R2-A6: a registered allowlist EXEMPTS its keys from the M1 denylist floor,
    so registering ``prompt`` / ``text`` / ``email`` would turn the positive
    allowlist — the tightening mechanism — into the widest hole in the floor,
    from any importable module, with no maintainer in the loop. A denylisted
    name is therefore refused at registration (:class:`AuditAllowlistRefused`)
    rather than honoured; the caller renames the field to something
    content-free. The two shipped exceptions are enumerated above.
    """
    et = str(event_type)
    forbidden = sorted(
        f for f in fields
        if _audit_key_forbidden(str(f).lower())
        and (et, str(f)) not in _VETTED_FORBIDDEN_ALLOWLIST_FIELDS
    )
    if forbidden:
        raise AuditAllowlistRefused(
            f"event allowlist for {et!r} names denylisted field(s) {forbidden} — "
            "an allowlist may not re-admit content/PII/secret key names; rename "
            "the field to a content-free one (an id, a count, a code, a hash prefix)"
        )
    _EVENT_ALLOWLIST[et] = frozenset(fields) | _EVENT_ALLOWLIST.get(et, frozenset())


def _assert_shipped_allowlists_clean() -> None:
    """The literal ``_EVENT_ALLOWLIST`` above obeys the same rule as a runtime
    registration (R2-A6) — enforced at import so the module cannot ship a
    denylisted field name that ``register_event_allowlist`` would refuse."""
    bad = sorted(
        f"{et}.{f}"
        for et, fields in _EVENT_ALLOWLIST.items()
        for f in fields
        if _audit_key_forbidden(str(f).lower())
        and (et, str(f)) not in _VETTED_FORBIDDEN_ALLOWLIST_FIELDS
    )
    if bad:
        raise AuditAllowlistRefused(
            f"shipped event allowlists name denylisted field(s) {bad} — add a "
            "maintainer entry to _VETTED_FORBIDDEN_ALLOWLIST_FIELDS or rename them"
        )


#: R2-A6: keys whose VALUE is free text by nature. Their values are scanned for
#: e-mail / phone shapes on EVERY event type — including one with a registered
#: allowlist, which otherwise skipped the value scan entirely and wrote a
#: ``reason`` carrying an address or a number verbatim into the chain. The
#: vocabulary floor decides whether a key may be written at all; this decides
#: what may ride inside the ones that are, by design, prose.
_AUDIT_FREETEXT_KEYS: frozenset[str] = frozenset({
    "reason", "detail", "details", "summary", "description",
    "note", "notes", "message", "error_message", "error", "msg",
})


def _audit_key_forbidden(ks: str) -> bool:
    return ks in _AUDIT_FORBIDDEN_EXACT or any(tok in ks for tok in _AUDIT_FORBIDDEN_SUBSTR)


# Runs here, not at the definition above: the check needs _audit_key_forbidden,
# which this module defines after the allowlist literal.
_assert_shipped_allowlists_clean()


def _audit_value_leaks(v: Any) -> bool:
    """True if a scalar value should be dropped (oversize or matches a
    high-precision secret/PII shape). Fail-CLOSED on serialization error."""
    # Non-finite floats (NaN / Infinity) serialise to the bare tokens
    # NaN/Infinity, which are NOT valid RFC-8259 JSON. They poison the chain
    # line for every standards-compliant / cross-language verifier (jq, Go,
    # the out-of-band auditor) even though Python's json.loads tolerates them.
    # Drop them at the floor so they never reach _canonical / the on-disk line.
    if isinstance(v, float) and not math.isfinite(v):
        return True
    if isinstance(v, str):
        if len(v) > _AUDIT_MAX_DETAIL_VALUE_LEN:
            return True
        return bool(_AUDIT_SECRET_VALUE_RE.search(v))
    try:
        # Match the WRITER's serializer EXACTLY (no default= coercion). The
        # chain writer (_canonical / the on-disk json.dumps) has no default=
        # handler, so a value it cannot serialize (set, bytes, custom object)
        # must be dropped HERE — otherwise default=str would make it look
        # "short and fine", it passes the filter, and then either crashes
        # write_event (never-raise violation) or, for a set/bytes wrapping a
        # token, smuggles a secret the str-only regex never scanned.
        return len(json.dumps(v)) > _AUDIT_MAX_DETAIL_VALUE_LEN
    except Exception:  # noqa: BLE001 — unserialisable → drop (fail-closed, review #6)
        return True


_AUDIT_MAX_SCRUB_DEPTH = 6


def _audit_scrub(value: Any, _depth: int = 0, *, pii_scan: bool = False):
    """Recursively scrub a value (review CRITICAL #1 — nested bypass).

    Returns ``(scrubbed, drop)``. ``drop=True`` tells the caller to drop the
    KEY holding this value (scalar leak). Dict/list containers are cleaned
    in place (forbidden nested keys/values removed) so legit sibling metadata
    survives, and the container itself is kept (drop=False). A depth bound
    prevents unbounded recursion (and a circular ref): past the limit the
    value is treated as a scalar, whose serialization check fail-closes on
    a circular/oversize structure."""
    if _depth >= _AUDIT_MAX_SCRUB_DEPTH:
        # Fail CLOSED at the depth backstop: drop the over-deep subtree
        # wholesale. Legit audit metadata is never nested this deep; a
        # forbidden key with a short value must NOT survive just because it
        # sits past the recursion bound (review: depth fail-open).
        return (None, True)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        dropped: list[str] = []
        for k, v in value.items():
            ks = str(k).lower()
            if _audit_key_forbidden(ks):
                dropped.append(str(k))
                continue
            sv, drop = _audit_scrub(v, _depth + 1, pii_scan=pii_scan)
            if drop:
                dropped.append(str(k))
                continue
            # Coerce non-str keys: JSON object keys are strings anyway, but a
            # mixed-type or exotic key (int+str, tuple, bytes) makes the
            # writer's sort_keys json.dumps raise — fail-closed to a str key.
            out[k if isinstance(k, str) else str(k)] = sv
        if dropped:
            out["_dropped_fields"] = sorted(set(dropped))
        return out, False
    if isinstance(value, (list, tuple)):
        out_list = []
        for item in value:
            sv, drop = _audit_scrub(item, _depth + 1, pii_scan=pii_scan)
            if not drop:
                out_list.append(sv)
        return out_list, False
    if pii_scan and isinstance(value, str) and _audit_value_pii(value):
        return (None, True)
    return (None, True) if _audit_value_leaks(value) else (value, False)


def filter_audit_details(details: dict | None, *, event_type: str = "",
                         unfiltered: bool = False):
    """Return (filtered_details, dropped_keys). ADR-0129 floor + M2 allowlist.

    RECURSIVELY drops any field whose key names content/PII/secret (at any
    depth), whose value matches a secret/PII shape, or whose value is
    oversize. For an event type with a registered POSITIVE allowlist, also
    drops any top-level key not on that list. Dropped key names are recorded
    inline under ``_dropped_fields`` (never the values). ``unfiltered=True``
    bypasses everything (rare legit long allowlisted field).
    """
    if unfiltered or not isinstance(details, dict) or not details:
        return (details if isinstance(details, dict) else {}), []
    allow = _EVENT_ALLOWLIST.get(event_type)
    default_deny = allow is None  # F-A4: no per-event allowlist → vocabulary floor
    cleaned: dict[str, Any] = {}
    dropped: list[str] = []
    fingerprinted: list[str] = []
    for k, v in details.items():
        ks = str(k).lower()
        on_allowlist = allow is not None and ks in allow
        reserved = ks in _AUDIT_RESERVED_KEYS
        # M2 positive allowlist (top level): key must be allowed or reserved.
        if allow is not None and not on_allowlist and not reserved:
            dropped.append(str(k))
            continue
        # F-A4 universal vocabulary (top level) for every other event type.
        if default_deny and not reserved and ks not in _AUDIT_KNOWN_KEYS:
            dropped.append(str(k))
            continue
        # M1 denylist floor — skipped for keys explicitly on the M2 positive
        # allowlist (maintainer-vetted override, e.g. secrets_used carries
        # names only). Reserved keys are structural and never denylist-forbidden.
        if not on_allowlist and _audit_key_forbidden(ks):
            dropped.append(str(k))
            continue
        # ADR-0152 — preserve a registered count-map field verbatim. Gated on the
        # positive allowlist (on_allowlist) AND the strict shape check, so this
        # can never preserve an unregistered field or a dict carrying PII values.
        cmf = _EVENT_COUNTMAP_FIELDS.get(event_type)
        if on_allowlist and cmf and ks in cmf and _is_safe_count_map(v):
            cleaned[k if isinstance(k, str) else str(k)] = v
            continue
        # R2-A6: the PII value scan is not a property of the EVENT (registered
        # or not) — it is a property of the KEY. A free-text key is scanned
        # always; every other key keeps the vocabulary-floor behaviour.
        #
        # R3 follow-up (2026-09-07): the reserved structural spine
        # (``user`` / ``chat_key`` / ``channel`` / ``persona`` / ``tenant_id``)
        # used to skip the value scan entirely, on the reasoning that these keys
        # carry IDENTIFIERS, not free text. Measured against the live chains that
        # is false: 596 039 records held 2 962 ``user`` and 2 580 ``chat_key``
        # values with an email or phone shape — ``notifications@github.com``,
        # ``alice@company.com``, ``+491234567890``. The email/messenger bridges
        # pass the sender straight through, and ``adapter._pii_fp`` only covers
        # the adapter's OWN emitter, not ``core/learning/event_persistence.py``
        # or any other direct ``audit_event`` caller. Raw personal identifiers
        # were landing in a hash-chained, append-only, never-redactable file.
        #
        # They are now scanned like every other key — and FINGERPRINTED rather
        # than dropped. Dropping is the floor's usual answer, and it is the wrong
        # one here: ``user`` is what makes a record attributable (GDPR Art. 30)
        # and what an operator correlates on; a chain of records with the actor
        # removed is not a safer audit trail, it is a useless one. The
        # replacement is the SAME transform ``adapter._pii_fp`` applies
        # (sha256[:8] of the utf-8 bytes), so a value redacted here and one the
        # adapter already redacted collide into one pseudonym namespace. The
        # affected key names — never the values — are listed under
        # ``_pii_fingerprinted``.
        sv, drop = _audit_scrub(
            v, pii_scan=(default_deny and not reserved) or ks in _AUDIT_FREETEXT_KEYS
        )
        if drop:
            dropped.append(str(k))
            continue
        if ((reserved or ks in _AUDIT_PSEUDONYM_KEYS)
                and isinstance(sv, str) and sv and _audit_value_pii(sv)):
            sv = hashlib.sha256(sv.encode("utf-8", "surrogatepass")).hexdigest()[:8]
            fingerprinted.append(str(k))
        cleaned[k if isinstance(k, str) else str(k)] = sv
    if dropped:
        cleaned["_dropped_fields"] = sorted(set(dropped))
    if fingerprinted:
        cleaned["_pii_fingerprinted"] = sorted(set(fingerprinted))
    return cleaned, dropped


# ── ADR-0537 — LoM → source binding, applied at the writer (F-A17) ───────────

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


#: Path segments a LoM source may never live under — vendored interpreters,
#: runtime-writable tenant state, VCS internals. Mirrors
#: ``core/skills/skill_registry_phase1.py::_LOM_EXCLUDED_PARTS``.
_LOM_EXCLUDED_PARTS: frozenset[str] = frozenset({
    ".corvin", ".venv", "venv", "site-packages", "dist-packages",
    "node_modules", ".git",
    # ``.claude/worktrees/`` holds seventeen .py-bearing copies of the repo
    # INSIDE the repo root, rewritten by running agents. A LoM must never bind
    # to one: the "source" it names would change under the record.
    ".claude",
})
# NOTE — this is the SECOND copy of this list; the first is
# ``core/skills/skill_registry_phase1.py::_LOM_EXCLUDED_PARTS``. Two copies is
# exactly how the ``.claude`` fix failed to take effect everywhere the first
# time (it landed in the registry only, while THIS module is what stamps
# ``lom_hash`` for every non-skill emitter). They cannot be collapsed today —
# ``forge`` is importable from a bridge daemon that has no ``core/`` on
# sys.path, and ``core.skills`` imports nothing from ``forge.paths`` — so
# ``tests/security/test_lom_binding_contract.py::test_the_two_exclusion_lists_agree``
# fails the moment they drift again. See ADR-0654.
#: A 2 MB .py is not a LoM target — refuse rather than read+parse it.
_LOM_MAX_SOURCE_BYTES = 2 * 1024 * 1024

#: Memoised on (lom, resolved path, st_mtime_ns, st_size) exactly like the
#: registry's cache: an edit to the named file invalidates the entry, so the
#: hash always binds to the source as it is on disk NOW.
_LOM_HASH_CACHE: dict[tuple, str | None] = {}
_LOM_HASH_CACHE_LOCK = threading.Lock()
_LOM_HASH_CACHE_MAX = 1024


def _lom_source_path(file_part: str) -> Path | None:
    """The admissible source file a LoM names, or None (fail-closed).

    Byte-for-byte the registry's ``_resolve_lom_source`` rule: inside the repo
    root, a ``.py`` file that exists, not under a vendored/runtime-writable
    segment. Everything else is refused BEFORE the file is read, so this is
    never a hash oracle over arbitrary readable content (``/etc/passwd:root``).
    """
    if not file_part:
        return None
    src = Path(file_part)
    root = _repo_root()
    if not src.is_absolute():
        src = root / src
    try:
        src = src.resolve()
    except OSError:
        return None
    try:
        if not src.is_relative_to(root):
            return None
    except AttributeError:  # pragma: no cover - Python < 3.9
        if root not in src.parents:
            return None
    if src.suffix != ".py" or not src.is_file():
        return None
    if any(part in _LOM_EXCLUDED_PARTS for part in src.relative_to(root).parts):
        return None
    return src


def _lom_find_functions(tree: "Any", func_name: str) -> list:
    """Every ``def``/``async def`` a LoM's function part can name.

    ``Class.method`` binds to that method inside that class; a bare name binds
    to any def with that name. Mirrors the registry's ``_find_lom_function``.
    """
    import ast as _ast  # noqa: PLC0415
    cls_name, _, meth_name = func_name.rpartition(".")
    matches: list = []
    if cls_name:
        for cls in _ast.walk(tree):
            if isinstance(cls, _ast.ClassDef) and cls.name == cls_name:
                for node in cls.body:
                    if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == meth_name:
                        matches.append(node)
        return matches
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == func_name:
            matches.append(node)
    return matches


def lom_hash_for(lom: str) -> str | None:
    """SHA256 binding a Line-of-Moral-Responsibility label to the source it names.

    ``file:function`` hashes the named function's source segment (resolved with
    ``ast``, so it survives line drift above it); ``file:function:L<line>`` /
    ``file:function:<line>`` hashes that one source line, which **must fall
    inside the named function** (decorators included).

    Returns ``None`` when the LoM does not bind. That is the whole point, and it
    is what changed on 2026-09-07 (R4-C): this function used to fall back to
    ``sha256(lom)`` for an unresolvable label, so a fabricated LoM
    (``totally/made/up.py:fabricated``, ``/etc/passwd:root``,
    ``core/skills/boot.py:boot_skills:L1``) produced a hash indistinguishable
    from a real source binding — in a hash-chained, append-only file that
    CLAUDE.md cites as the anti-spoofing binding. Rounds 2 and 3 removed exactly
    that fallback from ``core/skills/skill_registry_phase1.py::_compute_lom_hash``
    and the fix never reached the writer, which is what stamps ``lom_hash`` for
    every NON-skill emitter.

    The caller (:func:`write_event`) still writes the record when this returns
    ``None`` — dropping a compliance record because its attribution label is
    wrong would be the worse failure — but it stamps ``lom_bound: false`` and no
    ``lom_hash``, so the two cases are distinguishable in the chain and
    :func:`verify_lom_binding` can re-derive the verdict later.

    Same contract as ``core/skills/skill_registry_phase1.py::_compute_lom_hash``;
    ``tests/security/test_lom_binding_contract.py`` pins the two together.
    """
    if not lom or not isinstance(lom, str):
        return None
    parts = lom.split(":")
    if len(parts) < 2:
        return None
    src_path = _lom_source_path(parts[0])
    if src_path is None:
        return None
    try:
        st = src_path.stat()
    except OSError:
        return None
    if st.st_size > _LOM_MAX_SOURCE_BYTES:
        return None
    key = (lom, str(src_path), st.st_mtime_ns, st.st_size)
    with _LOM_HASH_CACHE_LOCK:
        if key in _LOM_HASH_CACHE:
            return _LOM_HASH_CACHE[key]
    value = _lom_hash_uncached(lom, parts, src_path)
    with _LOM_HASH_CACHE_LOCK:
        if len(_LOM_HASH_CACHE) >= _LOM_HASH_CACHE_MAX:
            _LOM_HASH_CACHE.clear()
        _LOM_HASH_CACHE[key] = value
    return value


def _lom_hash_uncached(lom: str, parts: list[str], src_path: Path) -> str | None:
    """The read + ast-parse half of :func:`lom_hash_for` (memoised there)."""
    try:
        import ast as _ast  # noqa: PLC0415
        func_name = parts[1].strip()
        if not func_name:
            return None
        text = src_path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = _ast.parse(text)
        except SyntaxError:
            return None
        matches = _lom_find_functions(tree, func_name)
        if not matches:
            return None
        if len(parts) >= 3:
            line_str = parts[2].strip()
            if line_str[:1] in ("L", "l"):
                line_str = line_str[1:]
            try:
                line_num = int(line_str)
            except ValueError:
                return None
            lines = text.split("\n")
            if line_num < 1 or line_num > len(lines):
                return None
            # The line must lie INSIDE the named function (decorators included)
            # — otherwise the function part is decorative and any fabricated
            # name binds (R3-B1, never applied to this writer copy until R4).
            inside = any(
                min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
                <= line_num <= (n.end_lineno or n.lineno)
                for n in matches
            )
            if not inside:
                return None
            line = lines[line_num - 1]
            if not line.strip():
                # sha256("") is the same constant for every blank line in every
                # file — a hash that binds to nothing.
                return None
            return hashlib.sha256(line.encode("utf-8")).hexdigest()
        for node in matches:
            segment = _ast.get_source_segment(text, node)
            if segment:
                return hashlib.sha256(segment.encode("utf-8")).hexdigest()
        return None
    except Exception:  # noqa: BLE001 — never block a write on a binding lookup
        return None


def verify_lom_binding(lom: str, lom_hash: str | None) -> bool:
    """Re-derive a record's LoM binding and compare (ADR-0537's ``verify_lom_binding``).

    ADR-0537 specified this verifier and nothing in the tree implemented it, so
    ``lom_hash`` was computed on write and never checked on read — an auditor had
    no way to tell a source-bound hash from a label hash. Returns True iff *lom*
    still resolves to source and hashes to *lom_hash*.

    A False verdict is NOT proof of tampering on its own: the named source may
    have been legitimately edited since the record was written. It is proof that
    the record's attribution cannot be confirmed against the tree as it is now,
    which is exactly what an auditor needs to see.
    """
    if not lom_hash or not isinstance(lom_hash, str):
        return False
    expected = lom_hash_for(lom)
    return expected is not None and hmac.compare_digest(expected, lom_hash)


#: How much of the tail to read per step when looking for the last chain entry.
#: One record is a few hundred bytes, so the first block virtually always contains
#: it; the loop exists for correctness (a long run of unhashed pre-chain records),
#: not for the common case.
_TAIL_BLOCK = 8192

#: How many most-recent hashes verify_chain keeps to tell a lagging tail anchor
#: (benign write race) from a truncated tail (a hash that no longer exists).
_TAIL_ANCHOR_WINDOW = 2000


def _last_hash(path: Path) -> str:
    """Return the ``hash`` of the last chain entry, or "".

    Reads BACKWARDS from the end of the file. It used to walk the whole file
    forwards, json.loads()-ing every line, and it is called on every hash-chained
    write while holding the exclusive flock — so the cost of appending one audit
    event was O(chain length), serialised across every writer, and grew forever.

    Measured on the live chain (117 000 records) 2026-07-27: an authenticated console
    request took ~0.8 s, and eight concurrent ones took 1.3/2.1/3.1/4.1/5.2/6.0/7.1/
    7.9 s — perfectly linear, i.e. fully serialised behind this scan. /healthz, which
    writes no audit event, answered in 3.5 ms. It also made Playwright specs time out
    at 30 s under four workers, which read as "the UI is broken".

    Semantics are unchanged and that matters, because this feeds the GDPR Art. 30/32
    chain: return the hash of the LAST record that carries a non-empty ``hash``,
    skipping blank lines, unparseable lines, and legitimate pre-chain records that
    have no ``hash`` field at all. The only difference is the direction of travel.
    """
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size == 0:
        return ""

    with path.open("rb") as fh:
        pos = size
        tail = b""
        while pos > 0:
            step = min(_TAIL_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            tail = fh.read(step) + tail
            # Every complete line in `tail` except possibly the first (which may be
            # a fragment when pos > 0). Walk them newest-first.
            lines = tail.split(b"\n")
            candidates = lines if pos == 0 else lines[1:]
            for raw in reversed(candidates):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                h = rec.get("hash") if isinstance(rec, dict) else None
                if isinstance(h, str) and h:
                    return h
            # Nothing hashed in this block — keep the (possibly partial) first line
            # so the next, earlier block can complete it.
            tail = lines[0] if pos > 0 else b""
    return ""


def write_event(
    path: Path,
    event_type: str,
    *,
    severity: str | None = None,
    tool: str = "",
    run_id: str = "",
    details: dict | None = None,
    hash_chain: bool = True,
    ts: float | None = None,
    unfiltered: bool = False,
) -> dict[str, Any]:
    """Append a security event. Returns the written record (incl. hash).

    ADR-0129: ``details`` passes the structural metadata-only floor before
    it is written — forbidden (content/PII/secret) or oversize fields are
    dropped and recorded under ``_dropped_fields``. ``unfiltered=True`` skips
    the floor for the rare legitimately-long allowlisted field.
    """
    # F-A1: a hash-less record is admissible for ONE event type only — the gap
    # marker — and even that one is bound to the tail below. Everything else
    # must chain; an unchained write is refused, not silently written into a
    # file whose verifier would then reject it.
    if not hash_chain and event_type != CHAIN_GAP_EVENT:
        raise ValueError(
            f"hash_chain=False is only permitted for {CHAIN_GAP_EVENT!r} "
            f"(got {event_type!r}) — every audit record must chain"
        )
    _filtered, _ = filter_audit_details(details or {}, event_type=event_type,
                                        unfiltered=unfiltered)
    # ADR-0129 M3 — make a floor bypass visible/auditable inline (a separate
    # event would re-enter write_event under the chain lock → deadlock).
    if unfiltered and isinstance(_filtered, dict):
        _filtered = {**_filtered, "_unfiltered": True}
    # ADR-0537 / F-A17: every record that carries a LoM carries its binding.
    if isinstance(_filtered, dict):
        _lom = _filtered.get("lom")
        if isinstance(_lom, str) and _lom and not _filtered.get("lom_hash"):
            _lh = lom_hash_for(_lom)
            # R4-C: an unresolvable LoM no longer gets a sha256(label) that reads
            # exactly like a source binding. The record is still written (an audit
            # record must never be lost over a bad attribution label) but it says
            # so: ``lom_bound: false`` and NO ``lom_hash``.
            _filtered = ({**_filtered, "lom_hash": _lh, "lom_bound": True} if _lh
                         else {**_filtered, "lom_bound": False})
    # F-A6 / ADR-0007: tenant isolation at the chokepoint. A record tagged with
    # another tenant's id is refused; the refusal itself is recorded (type +
    # count only, never the foreign details) under the CONTEXT tenant.
    _tid = _filtered.get("tenant_id") if isinstance(_filtered, dict) else None
    if _tid not in (None, "") and event_type != "audit.tenant_mismatch":
        _ctx = _current_tenant_id()
        if str(_tid) != _ctx:
            _sev = str(severity)[:64] if severity else EVENT_SEVERITY.get(event_type, "INFO")
            try:
                write_event(
                    path, "audit.tenant_mismatch", severity="ERROR",
                    details={
                        "dropped_event_type": event_type, "dropped_severity": _sev,
                        "dropped_count": 1, "reason": "AuditTenantMismatch",
                        "channel": "", "chat_key": "", "user": "", "persona": "",
                        "tenant_id": _ctx,
                    },
                )
            except OSError:
                pass
            raise AuditTenantMismatch(
                f"write_event({event_type}) refused: tenant_id does not match "
                f"the process tenant — event dropped"
            )
    # Symmetry with the details floor (review #5): clamp the top-level
    # rec fields too — they are structurally meant for ids/tool names, never
    # user content, but a buggy caller must not write an unbounded blob there.
    # R2-FND-07: a non-finite top-level `ts` (NaN/Inf) makes `_canonical`
    # (allow_nan=False) raise ValueError at hash time — a code path that
    # bypasses the FND-03b failed-write CRITICAL log and loses the event with
    # no chain marker. The details floor already drops non-finite floats; mirror
    # that for the top-level fields so a buggy/hostile caller can never produce
    # a non-serialisable record. ts → wall clock if not a finite number.
    _ts = ts if (isinstance(ts, (int, float)) and not isinstance(ts, bool)
                 and math.isfinite(ts)) else time.time()
    rec: dict[str, Any] = {
        "ts":         _ts,
        "event_type": str(event_type)[:128],
        "severity":   str(severity)[:64] if severity else EVENT_SEVERITY.get(event_type, "INFO"),
        "run_id":     str(run_id)[:128],
        "tool":       str(tool)[:128],
        "details":    _filtered,
    }
    # Two-layer lock: in-process threading lock (cheap, fast),
    # plus filesystem flock so cross-process writers don't interleave
    # — voice-adapter and forge-MCP-server are different processes
    # but write to the same chain.
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use os.open with O_CREAT so the file is always created 0600,
        # independent of umask. GDPR Art. 32 requires restricted permissions.
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        except PermissionError:
            # 2026-08-04, live report: LSAD (ADR-0132) and CLAG (ADR-0133) —
            # both callers of THIS function — logged "Permission denied" on
            # this exact audit.jsonl on a real Windows install that had
            # already survived several crash/reinstall incidents this
            # session. Windows' os.chmod cannot set POSIX mode bits (see
            # _validate_mode_strict's docstring above); a file left behind
            # with its READ-ONLY attribute set — by an interrupted prior
            # write, a different security context, or antivirus quarantine
            # touching it — makes every subsequent os.open(..., O_WRONLY)
            # fail with PermissionError forever, with no self-heal, even
            # though the file is otherwise perfectly fine to append to.
            # One targeted retry: clear the read-only attribute (Windows-
            # only; os.chmod is a no-op for this purpose elsewhere) and try
            # again exactly once. Never widens who can READ the file — only
            # clears a write BLOCK that should not have been there. If the
            # file doesn't exist yet, or the second attempt still fails
            # (a genuine permissions/ACL issue, not a stale attribute),
            # the original exception propagates unchanged to the caller,
            # which already treats this as non-fatal/best-effort.
            if msvcrt is not None and Path(path).exists():
                try:
                    os.chmod(str(path), 0o600)
                except OSError:
                    pass
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            else:
                raise
        with open(fd, "a", closefd=True) as fh:
            _lock_chain(fh)
            try:
                # Identity of the chain BEFORE this write (None on a fresh file:
                # then this record's own hash becomes the genesis).
                _genesis, _legacy_prefix, _gen_prev, _gen_mac = _chain_identity_ex(path)
                if not hash_chain:
                    # F-A1: the gap marker binds to the tail it was written
                    # after — prev_hash + keyed mac, no hash — so verify_chain
                    # can tell a genuine health-check marker from a forged
                    # hash-less insertion anywhere else in the file.
                    prev = _last_hash(path)
                    rec["prev_hash"] = prev
                    _canon = _canonical(rec).encode("utf-8")
                    _ak = _anchor_key()
                    if _ak is not None:
                        rec["mac"] = hmac.new(
                            _ak, prev.encode("utf-8") + b"\n" + _canon, hashlib.sha256,
                        ).hexdigest()[:16]
                if hash_chain:
                    # Re-read prev hash *after* taking the lock — another
                    # process may have written between our last read and now.
                    prev = _last_hash(path)
                    rec["prev_hash"] = prev
                    # F-A12 (writer side): the out-of-tree tail anchor is updated
                    # under this same lock after every write, so at rest it MUST
                    # equal the file's tail. A recorded tail that is neither the
                    # tail nor anywhere in the recent file means records were
                    # deleted since the last write. Make that PERMANENT in the
                    # chain: this record carries a writer-only marker naming the
                    # vanished tail, which verify_chain reports as
                    # ``tail_truncated`` at this line forever (a later legitimate
                    # write would otherwise re-anchor over the deletion).
                    if _genesis and not _skip_out_of_tree_markers(path):
                        _recorded = _read_chain_tail(path)
                        if (_recorded and _recorded != prev
                                and not _hash_in_recent_tail(path, _recorded)):
                            rec["details"] = {**rec["details"], "_tail_truncated_since": _recorded}
                            try:
                                import logging as _lg
                                _lg.getLogger("corvin.audit").critical(
                                    "audit chain tail TRUNCATED since last write (recorded tail "
                                    "%s not found) — marker written into %s", _recorded, event_type,
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        # R2-A1 (writer side): the PATH-keyed identity record
                        # says which chain belongs at this path. A file whose
                        # genesis differs from the anchored one was REPLACED —
                        # the whole-file-rewrite attack, which leaves no
                        # genesis-keyed marker behind to contradict it. Stamp
                        # the fact into the chain so it survives in-tree too,
                        # exactly like the truncation marker above; the
                        # verifier reports it from BOTH sides.
                        _pathrec = _read_chain_path_record(path)
                        if _pathrec is not None and _pathrec.get("genesis") != _genesis:
                            if _is_legitimate_rotation(path, _pathrec, _genesis, _gen_prev):
                                # Layer 37 rotation: the fresh live file starts
                                # with the rotation_link that binds to the tail
                                # we recorded out-of-tree. Re-anchor, don't accuse.
                                note_chain_rotation(path, link_hash=_genesis)
                            else:
                                rec["details"] = {
                                    **rec["details"],
                                    "_chain_replaced_from": str(_pathrec.get("genesis"))[:16],
                                }
                                try:
                                    import logging as _lg
                                    _lg.getLogger("corvin.audit").critical(
                                        "audit chain at this path was REPLACED (anchored genesis "
                                        "%s, file now starts at %s) — marker written into %s",
                                        str(_pathrec.get("genesis"))[:16], str(_genesis)[:16],
                                        event_type,
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                    # ADR-0232/0233: Store the initial tail hash to detect if the
                    # file was modified by another writer while we held the lock.
                    # This catches broken cross-process locks on Windows that allow
                    # concurrent writes, preventing hash-chain forks.
                    _prev_hash_at_lock_time = prev

                    # ADR-0132 LSAD: inject chain_dna BEFORE computing hash so
                    # the DNA is part of the hash-chain integrity guarantee.
                    try:
                        global _pending_seed_reset  # noqa: PLW0603
                        # Dual-context import: this module loads as the package
                        # `forge.forge.security_events` (tests) AND as a top-level
                        # `security_events` (adapter runtime — forge/forge on
                        # sys.path). A bare relative import raised ImportError in
                        # the top-level case, silently dropping DNA on a SEEDED
                        # chain → DNA-free records (FND-25 gap). Mirror the
                        # established try-relative-then-absolute pattern below.
                        try:
                            from .chain_dna import (  # type: ignore[import]
                                DNA_PREFIX_LEN,
                                derive_seed_free,
                                evolve as _dna_evolve,
                                last_dna_in_chain,
                            )
                        except ImportError:
                            from chain_dna import (  # type: ignore[import]
                                DNA_PREFIX_LEN,
                                derive_seed_free,
                                evolve as _dna_evolve,
                                last_dna_in_chain,
                            )
                        reset = _pending_seed_reset
                        _pending_seed_reset = None  # consume reset before any await
                        if reset is not None:
                            # License just loaded — create a visible seam by using
                            # the paid seed directly (not evolved from prior DNA).
                            new_dna = reset[:DNA_PREFIX_LEN]
                        else:
                            last_dna, _ = last_dna_in_chain(path)
                            if last_dna:
                                new_dna = _dna_evolve(last_dna, prev)[:DNA_PREFIX_LEN]
                            else:
                                # First LSAD event in an empty/legacy chain.
                                # Prefer: paid-tier > instance-seeded free-tier > public constant.
                                seed = _active_dna_seed or _instance_dna_seed or derive_seed_free()
                                new_dna = _dna_evolve(seed, prev)[:DNA_PREFIX_LEN] if prev else seed[:DNA_PREFIX_LEN]
                        rec["details"] = {**rec["details"], "chain_dna": new_dna}
                    except Exception as _dna_exc:  # noqa: BLE001
                        # DNA is best-effort and must NEVER block an audit write.
                        # BUT silently dropping it on a SEEDED (DNA-bearing) chain
                        # opens a DNA-free insertion lane (FND-25): an attacker who
                        # makes chain_dna unimportable on a fork could write
                        # DNA-less records that verify_chain_dna skips. So when DNA
                        # was expected, make the failure OBSERVABLE (the event is
                        # still written — availability over silence).
                        if _active_dna_seed or _instance_dna_seed:
                            try:
                                import logging as _lg
                                _lg.getLogger("corvin.audit").warning(
                                    "chain_dna injection failed on a seeded chain "
                                    "(%s) — record written WITHOUT DNA; a verify "
                                    "may report a DNA gap here", type(_dna_exc).__name__,
                                )
                            except Exception:  # noqa: BLE001
                                pass

                    _canon = _canonical(rec).encode("utf-8")  # rec has no hash/mac yet
                    h = hashlib.sha256()
                    h.update(prev.encode("utf-8"))
                    h.update(b"\n")
                    h.update(_canon)
                    rec["hash"] = h.hexdigest()[:16]
                    # ADR-0137 M2: keyed MAC over the SAME canonical, under a key
                    # stored outside the audit dir. A writer who edits a record
                    # and recomputes `hash` cannot forge `mac` (no key) → the
                    # rehash is detected by verify_chain. Absent key → no mac
                    # (legacy hash-only behaviour preserved).
                    _ak = _anchor_key()
                    if _ak is not None:
                        rec["mac"] = hmac.new(
                            _ak, prev.encode("utf-8") + b"\n" + _canon,
                            hashlib.sha256,
                        ).hexdigest()[:16]
                        # R2-FND-04: record (out-of-tree) that MAC is active —
                        # host-wide AND per-chain — so a later full-strip of every
                        # `mac` field on THIS chain is detectable without
                        # false-positiving on chains that never carried a mac.
                        _mark_mac_active(chain_path=path, genesis=_genesis or rec["hash"])

                    # ADR-0153 M3 — additive instance attestation.
                    # Both fields are added AFTER hash/mac so they are NOT part of
                    # the chain integrity computation (they are out-of-band attestation,
                    # not chain state). Best-effort: any failure skips silently.
                    try:
                        # ADR-0215 F5: the dotted `from operator.bridges.shared
                        # import ...` primary attempt below can NEVER
                        # resolve (stdlib `operator` always shadows the
                        # repo's operator/ directory) — this whole block is
                        # best-effort already, so a self-contained
                        # sys.path insert (rather than relying on some
                        # other module having already done it) makes the
                        # bare import actually reliable instead of luck.
                        import sys as _sys
                        _shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
                        if _shared.is_dir() and str(_shared) not in _sys.path:
                            _sys.path.insert(0, str(_shared))
                        import instance_identity as _iid  # type: ignore[import]
                        _iid_str = _iid.get_instance_id()
                        _sig_payload = hashlib.sha256(
                            (
                                rec["event_type"]
                                + ":"
                                + str(int(rec["ts"]))
                                + ":"
                                + rec["hash"]
                            ).encode("utf-8")
                        ).digest()
                        _sig_b64 = _iid.sign_payload(_sig_payload)
                        rec["instance_id"] = _iid_str
                        rec["instance_sig"] = _sig_b64
                    except Exception:  # noqa: BLE001
                        # Signing is best-effort — never block the audit write.
                        try:
                            import logging as _lg
                            _lg.getLogger("corvin.audit").warning(
                                "instance_sig not added to %s — key unavailable or "
                                "instance_identity not importable", event_type,
                            )
                        except Exception:  # noqa: BLE001
                            pass

                try:
                    # ADR-0232/0233: Atomic fork-detection checkpoint — if the chain
                    # tail changed since we read it, the cross-process lock is broken
                    # and another writer appended while we held the lock. Re-read the
                    # actual latest hash and use it to avoid writing a fork. This is
                    # best-effort: a truly broken lock means both processes will fail,
                    # but at least one will use the correct prev_hash and restore chain
                    # continuity for subsequent writers.
                    if hash_chain:
                        current_tail = _last_hash(path)
                        if current_tail != _prev_hash_at_lock_time:
                            # File was modified while we held the lock — another
                            # writer broke the lock or the lock is non-functional.
                            # Update rec with the actual latest hash and continue
                            # (do not retry externally, keep this write atomic).
                            rec["prev_hash"] = current_tail
                            _canon = _canonical(rec).encode("utf-8")
                            h = hashlib.sha256()
                            h.update(current_tail.encode("utf-8"))
                            h.update(b"\n")
                            h.update(_canon)
                            rec["hash"] = h.hexdigest()[:16]
                            # Re-compute MAC if active (uses current_tail as seed)
                            _ak = _anchor_key()
                            if _ak is not None:
                                rec["mac"] = hmac.new(
                                    _ak, current_tail.encode("utf-8") + b"\n" + _canon,
                                    hashlib.sha256,
                                ).hexdigest()[:16]
                    fh.write(json.dumps(rec, allow_nan=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                    if hash_chain:
                        # F-A12: the tail now lives out-of-tree too.
                        _record_chain_tail(path, rec["hash"], genesis=_genesis or rec["hash"])
                        # R2-A1/R2-A2: and so does the PATH-keyed identity of the
                        # chain that belongs here (genesis + tail + the length of
                        # the tolerated hash-less legacy prefix + whether this
                        # chain has ever carried a mac). Resolvable without the
                        # genesis, so a replaced/prepended file cannot hide by
                        # having no genesis-keyed marker.
                        _record_chain_path_state(
                            path,
                            genesis=_genesis or rec["hash"],
                            tail=rec["hash"],
                            legacy_prefix=_legacy_prefix,
                            mac="mac" in rec,
                        )
                except OSError as _werr:
                    # FND-03b: a failed audit write (full / read-only fs) was
                    # silently swallowed by best-effort callers, making lost
                    # evidence invisible — an attacker who induces a write
                    # failure could suppress a deny/block event undetected. Log
                    # it CRITICAL (logging only — NO recursion into write_event)
                    # then re-raise (contract preserved): the gated action /
                    # deny still happens, but the evidence-loss is now visible.
                    try:
                        import logging as _lg
                        _lg.getLogger("corvin.audit").critical(
                            "audit write FAILED for %s (%s) — event LOST, chain "
                            "evidence at risk", event_type, type(_werr).__name__,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    raise
            finally:
                _unlock_chain(fh)
    return rec


# ── ADR-0052 F3: audit_write_or_die + disk-headroom guard ────────────────

# Minimum free bytes required on the audit partition before a write is
# attempted. 50 MB is generous for daily log rotation but tight enough to
# catch a runaway flood before the FS fills completely.
AUDIT_HEADROOM_BYTES: int = 50 * 1024 * 1024   # 50 MB
AUDIT_HEADROOM_CRITICAL_BYTES: int = 25 * 1024 * 1024  # 25 MB — CRITICAL


class AuditChainFull(OSError):
    """Raised by ``audit_write_or_die`` when the audit partition is full.

    Callers MUST propagate this — they must NOT swallow it. CI static
    analysis enforces this: any ``except AuditChainFull`` that does not
    re-raise is a lint violation.
    """


def _check_disk_headroom(path: Path) -> None:
    """Raise ``AuditChainFull`` if the partition is below the headroom threshold.

    Also emits low-headroom WARNING / CRITICAL audit events (best-effort)
    so operators see the signal in ``voice-audit verify`` before the FS fills.
    """
    import shutil as _shutil

    try:
        free = _shutil.disk_usage(path.parent).free
    except OSError:
        return  # cannot stat the partition — do not block the write

    if free < AUDIT_HEADROOM_CRITICAL_BYTES:
        # Attempt a best-effort emit — if this fails too, we're truly full.
        try:
            write_event(
                path,
                "audit.disk_full_blocked",
                severity="CRITICAL",
                details={"free_bytes": free, "threshold_bytes": AUDIT_HEADROOM_CRITICAL_BYTES},
            )
        except OSError:
            pass
        raise AuditChainFull(
            f"audit partition critically low: {free} bytes free "
            f"(threshold {AUDIT_HEADROOM_CRITICAL_BYTES})"
        )

    if free < AUDIT_HEADROOM_BYTES:
        try:
            write_event(
                path,
                "audit.disk_headroom_low",
                severity="WARNING",
                details={"free_bytes": free, "threshold_bytes": AUDIT_HEADROOM_BYTES},
            )
        except OSError:
            pass


def audit_write_or_die(
    path: Path,
    event_type: str,
    *,
    severity: str | None = None,
    tool: str = "",
    run_id: str = "",
    details: dict | None = None,
    hash_chain: bool = True,
    ts: float | None = None,
) -> dict[str, Any]:
    """Write an audit event, raising ``AuditChainFull`` on disk failure.

    Drop-in replacement for ``write_event`` at callsites where the
    'audit-before-action' invariant must be enforced structurally.

    Contract: if this function returns without raising, the event is on
    disk and fsync'd. If it raises, the caller MUST abort the action.

    Callers MUST NOT catch ``AuditChainFull`` silently.
    """
    _check_disk_headroom(path)
    try:
        return write_event(
            path, event_type,
            severity=severity, tool=tool, run_id=run_id,
            details=details, hash_chain=hash_chain, ts=ts,
        )
    except OSError as exc:
        raise AuditChainFull(
            f"audit write failed for {event_type!r}: {exc}"
        ) from exc


def verify_chain(path: Path, *, initial_prev: str = "",
                 _resume: dict | None = None,
                 _state_out: dict | None = None) -> tuple[bool, list[dict]]:
    """Walk the audit file and verify hash-chain integrity.

    Returns ``(ok, problems)`` where ``problems`` is a list of dicts each
    naming the violation:

        {"line": 42, "issue": "tampered",
         "expected_hash": "...", "actual_hash": "..."}
        {"line": 43, "issue": "broken_chain",
         "expected_prev": "...", "actual_prev": "..."}
        {"line": 44, "issue": "invalid_json"}

    A record without a ``hash`` is admissible ONLY (F-A1) as a pre-chain legacy
    entry (before the first hash-bearing record) or as a
    ``audit.chain_gap_detected`` marker whose ``prev_hash`` equals the tail it
    was written after (and whose ``mac`` verifies when present / is required
    once the MAC epoch started). Any other hash-less record is reported as
    ``unchained_record``.

    Out-of-tree checks (beside the anchor key): ``tail_truncated`` when the
    recorded tail hash (F-A12) is neither the current tail nor a recent
    predecessor, and ``anchor_key_insecure_mode`` when a key file exists but
    was refused for group/other permission bits (F-A14).

    ``initial_prev`` (default ``""``) is the expected ``prev_hash`` of
    the first chain entry. Pass the previous segment's tail hash when
    verifying a rotated audit segment (ADR-0044 / Layer 37 cross-segment
    verification) so the first entry's ``prev_hash`` (typically that of
    an ``audit.rotation_link`` event) is checked against the cross-
    segment boundary rather than the legacy empty-string default.

    ``_resume`` / ``_state_out`` are the ADR-0640-R4 witness plumbing and are
    PRIVATE — every public caller (``voice-audit verify``, the daily
    ``verify --all`` unit, every test) gets an unconditional full walk. See
    :func:`verify_chain_incremental` for what a resume means and why it cannot
    hide a break. ``_state_out``, when given, is filled with the walk's end
    state (chain position, MAC-epoch flags, tail window, the LINE-NUMBERED
    problems found so far) — never with the current-state problems computed
    after the walk, which are recomputed on every verify.
    """
    if not path.exists():
        return True, []
    _no_key_ok = _verify_no_key_ok()
    if _resume is None:
        problems: list[dict] = []
        prev = initial_prev
        chain_started = False
        # R2-FND-04/06: MAC-epoch + full-strip detection state.
        mac_required = False   # set once a mac'd record is seen under an available key
        mac_seen_count = 0     # total records carrying a mac field
        _nonlink_chained = 0   # chained records that are not a rotation link
        recent_hashes: collections.deque = collections.deque(maxlen=_TAIL_ANCHOR_WINDOW)
        line_no = 0
        start_off = 0
    else:
        # Resuming a walk whose PREFIX BYTES were just proven identical to the
        # ones that produced this state (``_read_chain_witness`` re-hashes them
        # before handing the state over). The restored values are exactly the
        # loop-carried state the prefix would have produced, so the suffix walk
        # below is bit-for-bit the continuation of a full walk.
        problems = [dict(pr) for pr in _resume["problems"]]
        prev = str(_resume["prev"])
        chain_started = bool(_resume["chain_started"])
        mac_required = bool(_resume["mac_required"])
        mac_seen_count = int(_resume["mac_seen_count"])
        _nonlink_chained = int(_resume["nonlink_chained"])
        recent_hashes = collections.deque(_resume["recent_hashes"],
                                          maxlen=_TAIL_ANCHOR_WINDOW)
        line_no = int(_resume["lines"])
        start_off = int(_resume["bytes"])
    consumed = start_off
    tail_complete = True   # False when the file ends mid-line (interrupted write)
    # Binary, not text: the resume seeks to a BYTE offset, which a TextIOWrapper
    # cookie cannot express. ``json.loads`` accepts bytes, and a line that is not
    # valid UTF-8 now reports ``invalid_json`` instead of raising out of verify.
    with path.open("rb") as fh:
        if start_off:
            fh.seek(start_off)
        for raw in fh:
            consumed += len(raw)
            tail_complete = raw.endswith(b"\n")
            line_no += 1
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                problems.append({"line": line_no, "issue": "invalid_json"})
                continue

            if not isinstance(rec, dict):
                # Syntactically valid JSON (json.loads succeeded) but not an
                # object — a bare number, null, bool, string, or list. Route
                # through the same "malformed line" reporting path as a
                # JSON decode error instead of falling through to `"hash"
                # not in rec`, which raises an uncaught TypeError for
                # int/None/bool (and silently mis-skips str/list, since
                # membership-testing a string or list never raises).
                problems.append({"line": line_no, "issue": "invalid_json"})
                continue

            if "hash" not in rec:
                if not chain_started and initial_prev == "":
                    # Legacy pre-chain prefix (written before hash chaining
                    # existed) — nothing to bind it to yet.
                    continue
                et = str(rec.get("event_type", ""))[:64]
                if et != CHAIN_GAP_EVENT:
                    problems.append({"line": line_no, "issue": "unchained_record",
                                     "event_type": et})
                    continue
                gap_prev = rec.get("prev_hash")
                if not isinstance(gap_prev, str) or gap_prev != prev:
                    problems.append({"line": line_no, "issue": "unchained_record",
                                     "event_type": et, "expected_prev": prev,
                                     "actual_prev": gap_prev if isinstance(gap_prev, str) else None})
                    continue
                _gap_canon = _canonical({k: v for k, v in rec.items()
                                         if k not in CHAIN_HASH_EXCLUDED_FIELDS}).encode("utf-8")
                _gak = _anchor_key()
                if "mac" in rec:
                    mac_seen_count += 1
                    if _gak is not None:
                        mac_required = True
                        _exp = hmac.new(_gak, prev.encode("utf-8") + b"\n" + _gap_canon,
                                        hashlib.sha256).hexdigest()[:16]
                        if not hmac.compare_digest(str(rec["mac"]), _exp):
                            problems.append({"line": line_no, "issue": "mac_tampered",
                                             "expected_mac": _exp, "actual_mac": rec["mac"]})
                    elif not _no_key_ok:
                        problems.append({"line": line_no, "issue": "mac_unverifiable_key_absent"})
                elif mac_required and _gak is not None:
                    problems.append({"line": line_no, "issue": "mac_missing"})
                continue  # a gap marker is not a link: prev stays

            chain_started = True
            recent_hashes.append(rec["hash"])
            if str(rec.get("event_type", "")) != "audit.rotation_link":
                _nonlink_chained += 1
            _d = rec.get("details")
            if isinstance(_d, dict) and _d.get("_tail_truncated_since"):
                # Writer-side truncation marker (F-A12): permanent, line-bound.
                problems.append({"line": line_no, "issue": "tail_truncated",
                                 "recorded_tail": str(_d["_tail_truncated_since"])[:16],
                                 "detail": "records deleted before this write"})
            if isinstance(_d, dict) and _d.get("_chain_replaced_from"):
                # Writer-side replacement marker (R2-A1): permanent, line-bound,
                # so the fact survives even if the out-of-tree record is later
                # re-anchored by a legitimate rotation.
                problems.append({"line": line_no, "issue": "chain_replaced",
                                 "anchored_genesis": str(_d["_chain_replaced_from"])[:16],
                                 "detail": "a chain replacement was recorded at this write"})
            actual_prev = rec.get("prev_hash", "")
            if actual_prev != prev:
                problems.append({
                    "line": line_no, "issue": "broken_chain",
                    "expected_prev": prev, "actual_prev": actual_prev,
                })

            # Recompute hash *with* the actual_prev so we localize tamper
            # to either the chain pointer or the record body. Exclusion set is
            # the single source of truth shared with CLAG (ADR-0137 M2 mac +
            # ADR-0153 M3 additive attestation, NOT part of chain integrity).
            check_rec = {k: v for k, v in rec.items()
                         if k not in CHAIN_HASH_EXCLUDED_FIELDS}
            _canon = _canonical(check_rec).encode("utf-8")
            h = hashlib.sha256()
            h.update(actual_prev.encode("utf-8"))
            h.update(b"\n")
            h.update(_canon)
            expected_hash = h.hexdigest()[:16]
            if rec["hash"] != expected_hash:
                problems.append({
                    "line": line_no, "issue": "tampered",
                    "expected_hash": expected_hash,
                    "actual_hash": rec["hash"],
                })
            # ADR-0137 M2 + R2-FND-04/06: external anchor with MANDATORY MAC
            # enforcement. `hash` is computed over the record WITHOUT `mac`, so
            # stripping `mac` leaves both the hash and the chain tail intact — a
            # silent downgrade to hash-only that defeats the anchor. Defences:
            #  * MAC-epoch: the first mac'd record (with key available) starts an
            #    epoch; EVERY later record must carry a mac (a stripped one →
            #    "mac_missing"). The legacy prefix before the first mac is exempt.
            #  * key-absent: a record that carries a mac but cannot be verified
            #    because the key is gone is a problem ("mac_unverifiable_key_absent")
            #    UNLESS CORVIN_AUDIT_VERIFY_NO_KEY_OK=1 (legitimate cross-host verify).
            _ak = _anchor_key()
            if "mac" in rec:
                mac_seen_count += 1
                if _ak is not None:
                    mac_required = True  # epoch starts here
                    expected_mac = hmac.new(
                        _ak, actual_prev.encode("utf-8") + b"\n" + _canon,
                        hashlib.sha256,
                    ).hexdigest()[:16]
                    if not hmac.compare_digest(str(rec["mac"]), expected_mac):
                        problems.append({
                            "line": line_no, "issue": "mac_tampered",
                            "expected_mac": expected_mac,
                            "actual_mac": rec["mac"],
                        })
                elif not _no_key_ok:
                    problems.append({
                        "line": line_no, "issue": "mac_unverifiable_key_absent",
                    })
            else:
                # No mac on this record. If the epoch has started (a prior record
                # was mac'd under an available key), a missing mac here is a strip.
                if mac_required and _ak is not None:
                    problems.append({
                        "line": line_no, "issue": "mac_missing",
                    })
            # ADR-0153 M3 — optional Ed25519 instance_sig verification.
            # Only runs when _VERIFY_SIGS is True (set by voice-audit --verify-sigs).
            # Best-effort: import failures are surfaced as a problem entry so the
            # caller can report them without crashing the verification loop.
            if _VERIFY_SIGS and "instance_sig" in rec and "instance_id" in rec:
                try:
                    # ADR-0215 F5: same fix as the sibling block above — the
                    # dotted primary attempt can never resolve; use a
                    # self-contained sys.path insert instead of relying on
                    # another module to have already done it.
                    import sys as _sys
                    _shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
                    if _shared.is_dir() and str(_shared) not in _sys.path:
                        _sys.path.insert(0, str(_shared))
                    import instance_identity as _iid  # type: ignore[import]
                    _sig_payload = hashlib.sha256(
                        (
                            str(rec.get("event_type", ""))
                            + ":"
                            + str(int(rec.get("ts", 0)))
                            + ":"
                            + str(rec["hash"])
                        ).encode("utf-8")
                    ).digest()
                    # Retrieve public key for the recorded instance_id. For the local
                    # instance we can load from instance_pubkey.pem; for foreign
                    # instance_ids (cross-host verify) we can't verify without their
                    # pubkey, so we skip with a note rather than failing closed.
                    local_iid = _iid.get_instance_id()
                    if rec["instance_id"] == local_iid:
                        _pubkey_b64 = _iid.get_instance_pubkey_b64()
                        _sig_ok = _iid.verify_instance_sig(
                            rec["instance_sig"], _sig_payload, _pubkey_b64
                        )
                        if not _sig_ok:
                            problems.append({
                                "line": line_no,
                                "issue": "instance_sig_invalid",
                                "instance_id_prefix": str(rec["instance_id"])[:8],
                            })
                    # else: foreign instance_id — pubkey not available locally, skip
                except Exception:  # noqa: BLE001
                    # Import or key-read failure — note it but don't break the loop
                    problems.append({
                        "line": line_no,
                        "issue": "instance_sig_verify_error",
                    })
            prev = rec["hash"]

    if _state_out is not None:
        # Snapshot BEFORE the current-state block below. Everything appended
        # from here on describes the chain as it is RIGHT NOW (an out-of-tree
        # anchor disagreeing with the file) and is recomputed on every verify;
        # baking it into a witness would let a stale fact outlive its cause.
        _state_out.update({
            "prev": prev,
            "chain_started": chain_started,
            "mac_required": mac_required,
            "mac_seen_count": mac_seen_count,
            "nonlink_chained": _nonlink_chained,
            "recent_hashes": list(recent_hashes),
            "lines": line_no,
            "bytes": consumed,
            "complete": tail_complete,
            "problems": [dict(pr) for pr in problems],
        })

    # R2-FND-04 + R3-02/R3-04 full-strip detection for the LIVE chain. The
    # earlier ts-gate (max_ts >= sentinel) was bypassable: the rehash-capable
    # attacker the MAC defends against can forge every record's `ts` to predate
    # the sentinel (ts is not MAC-covered), evading the detector. It is replaced
    # by a structural argument that needs no in-record signal:
    #   * The detector applies ONLY to the live chain (``audit.jsonl``). Sealed/
    #     rotated segments (``audit.<stamp>.jsonl``) are covered by the signed
    #     segment manifest's own full-strip detector (voice_audit, R3-03).
    #   * The gate is the PER-CHAIN marker, not the host-global sentinel. The
    #     host sentinel exists once ANY chain on the host wrote a mac, so gating
    #     on it false-positived on every other zero-mac chain — legacy chains AND
    #     fresh session chains that simply ran no mac-writing tool (incident
    #     2026-06-17: 20+ chains broke `verify --all`). `_chain_had_mac(path)` is
    #     true IFF THIS chain ever wrote a mac (out-of-tree marker beside the key,
    #     undeletable by an in-tree attacker), so a chain that never carried a mac
    #     is exempt while a chain that had a mac and now carries none is a genuine
    #     strip. No reliance on attacker-controlled in-record ts.
    # _no_key_ok is intentionally NOT consulted here (R3-04): it must suppress
    # ONLY the key-ABSENT diagnostic above, never the strip detector, which
    # already requires the key to be present.
    # R2-A1: the per-chain marker is keyed by the GENESIS, so a rewrite that
    # mints a NEW genesis has no marker and the detector above stayed silent —
    # which is precisely the whole-file-rewrite attack. Two genesis-independent
    # sources are consulted as well:
    #   * the PATH-keyed chain record (``chain_ids/<sha256(path)>``), which
    #     remembers that the chain living at this path carried a mac;
    #   * for the primary live layout (``<root>/global/forge/audit.jsonl``) the
    #     host sentinel ``audit_mac_active``. Restricted to that layout on
    #     purpose: gating every chain on the host-wide sentinel is what broke
    #     20+ legacy/session chains in incident 2026-06-17, and those chains are
    #     never at this path.
    _pathrec = None if _skip_out_of_tree_markers(path) else _read_chain_path_record(path)
    _genesis, _legacy_prefix, _gen_prev, _gen_mac = _chain_identity_ex(path)
    _rotated = bool(
        _pathrec is not None
        and _genesis
        and str(_pathrec.get("genesis") or "") != _genesis
        and _is_legitimate_rotation(path, _pathrec, _genesis, _gen_prev)
    )
    _is_live_chain = path.name == "audit.jsonl"
    _mac_expected = (
        _chain_had_mac(path)
        or bool(_pathrec and _pathrec.get("mac"))
        or (_is_primary_live_layout(path) and _mac_active_since() is not None)
    )
    # A chain that holds nothing but the ``audit.rotation_link`` a Layer 37
    # rotation just wrote is not a stripped chain: the sealer writes that record
    # by hand, without a mac, and the next real write_event re-establishes the
    # epoch. Requiring one non-link chained record (and skipping a recognised
    # rotation outright) keeps the widened detector off that legitimate window.
    if (_is_live_chain and chain_started and mac_seen_count == 0
            and _nonlink_chained > 0 and not _rotated
            and _anchor_key() is not None and _mac_expected):
        problems.append({"issue": "mac_stripped_chain",
                         "detail": "MAC active on this chain but it now carries no mac"})

    # R2-A1: a chain whose genesis is not the one anchored for this PATH was
    # replaced wholesale — recomputed hashes and stripped macs make such a file
    # self-consistent, so nothing INSIDE it can expose the swap. Only the
    # out-of-tree record can. A Layer 37 rotation is the one legitimate genesis
    # change and is recognised by its rotation_link binding to the recorded tail.
    if _pathrec is not None and chain_started:
        _anchored = str(_pathrec.get("genesis") or "")
        if _anchored and _genesis and _anchored != _genesis and not _rotated:
            problems.append({
                "issue": "chain_replaced", "anchored_genesis": _anchored[:16],
                "actual_genesis": str(_genesis)[:16],
                "detail": "the chain anchored at this path was replaced by a different one",
            })
        # R2-A2: records PREPENDED before the genesis. The pre-chain legacy
        # prefix is tolerated (chains predating hash chaining really have one),
        # but its length is a fact recorded when the chain was anchored — a
        # prefix that GREW was written by someone, and a hash-less record binds
        # to nothing, so it cannot be caught by the walk above.
        try:
            _anchored_prefix = int(_pathrec.get("legacy_prefix", 0))
        except (TypeError, ValueError):
            _anchored_prefix = 0
        if _legacy_prefix > _anchored_prefix:
            problems.append({
                "issue": "records_prepended", "expected_prefix": _anchored_prefix,
                "actual_prefix": _legacy_prefix,
                "detail": "hash-less records were inserted before the chain genesis",
            })

    # R3-A1: an UNANCHORED genesis is a LINE-LESS (current-state) problem.
    #
    # A chain's first chained record binds to ``initial_prev`` — "" for a live
    # chain, the previous segment's tail when a rotated segment is verified
    # cross-segment (ADR-0044). A genesis whose ``prev_hash`` is anything else
    # claims to continue a chain nobody vouched for. The ONE legitimate shape is
    # a Layer 37 rotation link, and that is now recognised by an out-of-tree fact
    # (``rotation_genesis`` written by note_chain_rotation under the rotation
    # lock, or the link's own anchor-key MAC), never by the record's shape.
    #
    # Reported WITHOUT a ``line`` on purpose. As a line-1 ``broken_chain`` this
    # was classified "historical" by ``tripwire.audit_chain_intact`` as soon as
    # the file grew past TAIL_RECORDS — a length the attacker chooses by padding
    # the forged file. Line-less problems describe the chain's CURRENT state and
    # block the boot regardless of file length.
    if (chain_started and _genesis and _gen_prev != initial_prev
            and not _skip_out_of_tree_markers(path)
            and not _rotation_link_authenticated(path, _pathrec, _genesis)):
        problems.append({
            "issue": "unanchored_genesis",
            "genesis": str(_genesis)[:16],
            "genesis_prev": str(_gen_prev)[:16],
            "detail": "the chain's first record continues a tail nothing vouches "
                      "for (no authenticated Layer 37 rotation link)",
        })
    # NOT a rule here: "the genesis carries a mac, therefore the chain can have
    # no legacy prefix". It reads well and is false — a legacy install really
    # does carry a hash-less prefix, and its FIRST chained record is written
    # today, hence mac'd (operator/forge/tests/test_tenant_migration_roundtrip.py
    # R5 is exactly that shape). The mac on the genesis dates the genesis, not
    # the prefix. The recorded prefix LENGTH above is the discriminator: it is
    # frozen when the chain is first anchored, and it lives beside the anchor
    # key where the in-tree attacker this defends against cannot reach it.

    # F-A12: compare the on-file tail against the out-of-tree tail anchor.
    # The anchor may lag the file by a few writes (a verify racing a writer)
    # or lead it (a write landed after our walk) — both benign. A recorded
    # tail that is neither the current tail, nor among the last
    # _TAIL_ANCHOR_WINDOW hashes, nor the file's tail right now, names a
    # record that is gone: the tail was truncated.
    if chain_started and not _skip_out_of_tree_markers(path):
        # Two recorded tails, same tolerance: the GENESIS-keyed anchor (F-A12)
        # and the PATH-keyed one (R2-A1). The second is what still answers when
        # the genesis itself was swapped, so truncation cannot be laundered by
        # rewriting the first record. Skipped right after a legitimate rotation,
        # where the recorded tail names the segment that was rotated away.
        _recorded_tails = {_read_chain_tail(path)}
        if _pathrec is not None and not _rotated:
            _recorded_tails.add(_pathrec.get("tail"))
        _file_tail = None
        for recorded in sorted(t for t in _recorded_tails if isinstance(t, str) and t):
            if recorded == prev or recorded in recent_hashes:
                continue
            if _file_tail is None:
                _file_tail = _last_hash(path)
            if recorded != _file_tail:
                problems.append({"issue": "tail_truncated",
                                 "recorded_tail": recorded, "actual_tail": prev})

    # F-A14: a present-but-refused anchor key is a broken anchor, not an
    # absent one — surface it so the boot tripwire fails closed.
    if _ANCHOR_KEY_REFUSED and _anchor_key_path().exists():
        problems.append({"issue": "anchor_key_insecure_mode",
                         "detail": f"anchor key refused ({_ANCHOR_KEY_REFUSED}); "
                                   f"chmod 600 {_anchor_key_path()}"})

    return len(problems) == 0, problems


def verify_chain_incremental(path: Path, *,
                             initial_prev: str = "") -> tuple[bool, list[dict], int]:
    """``(ok, problems, total_lines)`` — the SAME verdict as :func:`verify_chain`,
    with the already-proven prefix of an append-only chain not re-walked.

    ADR-0640 R4. This is the BOOT path only (``tripwire._verify_chain``).
    ``voice-audit verify`` / the daily ``verify --all`` unit keep calling
    :func:`verify_chain`, which is an unconditional full walk with no reliance on
    any witness — so a full, independent verification still happens on the
    operator's schedule regardless of what is on disk beside the anchor key.

    Why this cannot hide a break:

    * The witness is admitted only after the prefix bytes are re-hashed THIS
      CALL and match the recorded SHA-256. Any edit inside the prefix — with or
      without a rehash — changes the digest and forces a full walk.
    * A shorter file, a replaced file, a prepended file and a re-encoded file
      all fail the size or digest check the same way.
    * A missing, unreadable, malformed, version-mismatched or MAC-invalid
      witness, a rotated/absent/refused anchor key, a different ``initial_prev``
      or a different ``CORVIN_AUDIT_VERIFY_NO_KEY_OK`` all force a full walk.
    * The memoised problems are the prefix's LINE-NUMBERED ones. Current-state
      problems (``tail_truncated``, ``chain_replaced``, ``records_prepended``,
      ``unanchored_genesis``, ``mac_stripped_chain``, ``anchor_key_insecure_mode``)
      are never memoised: :func:`verify_chain` recomputes them after every walk,
      resumed or not, from the out-of-tree anchors.
    """
    p = Path(path)
    if not p.exists():
        return True, [], 0
    witness, hasher = _read_chain_witness(p, initial_prev=initial_prev)
    state: dict = {}
    ok, problems = verify_chain(p, initial_prev=initial_prev,
                                _resume=witness, _state_out=state)
    if witness is not None and hasher is not None:
        # Extend the already-computed prefix digest with the suffix we just
        # walked, so the next witness costs no extra pass over the prefix.
        start = int(witness.get("bytes", 0))
        end = int(state.get("bytes", start))
        if end > start:
            try:
                with p.open("rb") as fh:
                    fh.seek(start)
                    remaining = end - start
                    while remaining > 0:
                        chunk = fh.read(min(1 << 20, remaining))
                        if not chunk:
                            hasher = None
                            break
                        remaining -= len(chunk)
                        hasher.update(chunk)
            except OSError:
                hasher = None
    else:
        hasher = None
    _write_chain_witness(p, state, initial_prev=initial_prev, hasher=hasher)
    return ok, problems, int(state.get("lines", 0))
