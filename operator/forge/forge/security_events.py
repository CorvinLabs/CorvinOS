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

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any


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
    """
    if not path.exists():
        return None
    try:
        last: str | None = None
        with path.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                h = rec.get("hash")
                if isinstance(h, str) and h:
                    last = h
        return last
    except OSError:
        return None


_ANCHOR_KEY: bytes | None = None
_ANCHOR_KEY_LOADED = False


def _anchor_key() -> bytes | None:
    """ADR-0137 M2: load/create the audit-chain MAC key, stored OUTSIDE the
    audit directory (``~/.config/corvin-voice/audit_anchor.key``, mode 0600, or
    ``CORVIN_AUDIT_ANCHOR_KEY``). A writer who can edit ``audit.jsonl`` cannot
    read this key, so it cannot forge the per-record HMAC after rewriting +
    rehashing a record — ``verify_chain`` then detects the tamper. Returns None
    when the key can't be created/read (graceful: records carry no ``mac`` and
    verify falls back to hash-only, preserving the legacy contract)."""
    global _ANCHOR_KEY, _ANCHOR_KEY_LOADED
    if _ANCHOR_KEY_LOADED:
        return _ANCHOR_KEY
    key: bytes | None = None
    try:
        env = os.environ.get("CORVIN_AUDIT_ANCHOR_KEY", "").strip()
        kp = (Path(env).expanduser() if env
              else Path(os.path.expanduser("~/.config/corvin-voice/audit_anchor.key")))
        if not kp.exists():
            kp.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(kp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(os.urandom(32))
            except FileExistsError:
                pass  # created concurrently by another process
        data = kp.read_bytes()
        key = data if len(data) >= 16 else None
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


def _mac_chain_marker_path(chain_path: Path) -> Path:
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
    now carries none has its marker but zero macs → genuine strip."""
    base = _mac_sentinel_path().parent / "mac_active_chains"
    digest = hashlib.sha256(os.path.abspath(str(chain_path)).encode("utf-8")).hexdigest()[:32]
    return base / digest


def _chain_had_mac(chain_path: Path) -> bool:
    """True iff THIS chain has ever written a mac (per-chain marker present)."""
    try:
        return _mac_chain_marker_path(chain_path).exists()
    except Exception:  # noqa: BLE001
        return False


def _mark_mac_active(now: float | None = None, chain_path: Path | None = None) -> None:
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
    if chain_path is not None:
        try:
            mp = _mac_chain_marker_path(chain_path)
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
    "_dropped_fields", "_unfiltered",
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
_AUDIT_RESERVED_KEYS: frozenset[str] = frozenset({
    "tenant_id", "channel", "chat_key", "user", "persona",
})
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
}

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


def register_event_allowlist(event_type: str, fields: "set[str] | frozenset[str]") -> None:
    """Register/extend the positive allowlist for an event type (ADR-0129 M2).
    Other modules (console, compute) may fold their allowlists in here."""
    _EVENT_ALLOWLIST[event_type] = frozenset(fields) | _EVENT_ALLOWLIST.get(event_type, frozenset())


def _audit_key_forbidden(ks: str) -> bool:
    return ks in _AUDIT_FORBIDDEN_EXACT or any(tok in ks for tok in _AUDIT_FORBIDDEN_SUBSTR)


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


def _audit_scrub(value: Any, _depth: int = 0):
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
            sv, drop = _audit_scrub(v, _depth + 1)
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
            sv, drop = _audit_scrub(item, _depth + 1)
            if not drop:
                out_list.append(sv)
        return out_list, False
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
    cleaned: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in details.items():
        ks = str(k).lower()
        on_allowlist = allow is not None and ks in allow
        # M2 positive allowlist (top level): key must be allowed or reserved.
        if allow is not None and not on_allowlist and ks not in _AUDIT_RESERVED_KEYS:
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
        sv, drop = _audit_scrub(v)
        if drop:
            dropped.append(str(k))
            continue
        cleaned[k if isinstance(k, str) else str(k)] = sv
    if dropped:
        cleaned["_dropped_fields"] = sorted(set(dropped))
    return cleaned, dropped


def _last_hash(path: Path) -> str:
    """Walk the file and return the ``hash`` of the last chain entry, or "" """
    if not path.exists():
        return ""
    last = ""
    with path.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = rec.get("hash")
            if isinstance(h, str) and h:
                last = h
    return last


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
    _filtered, _ = filter_audit_details(details or {}, event_type=event_type,
                                        unfiltered=unfiltered)
    # ADR-0129 M3 — make a floor bypass visible/auditable inline (a separate
    # event would re-enter write_event under the chain lock → deadlock).
    if unfiltered and isinstance(_filtered, dict):
        _filtered = {**_filtered, "_unfiltered": True}
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
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with open(fd, "a", closefd=True) as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                if hash_chain:
                    # Re-read prev hash *after* taking the lock — another
                    # process may have written between our last read and now.
                    prev = _last_hash(path)
                    rec["prev_hash"] = prev

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
                        _mark_mac_active(chain_path=path)

                    # ADR-0153 M3 — additive instance attestation.
                    # Both fields are added AFTER hash/mac so they are NOT part of
                    # the chain integrity computation (they are out-of-band attestation,
                    # not chain state). Best-effort: any failure skips silently.
                    try:
                        try:
                            from operator.bridges.shared import instance_identity as _iid  # type: ignore[import]
                        except ImportError:
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
                    fh.write(json.dumps(rec, allow_nan=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
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
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
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
                hash_chain=False,  # chain write about to fail — don't corrupt it
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


def verify_chain(path: Path, *, initial_prev: str = "") -> tuple[bool, list[dict]]:
    """Walk the audit file and verify hash-chain integrity.

    Returns ``(ok, problems)`` where ``problems`` is a list of dicts each
    naming the violation:

        {"line": 42, "issue": "tampered",
         "expected_hash": "...", "actual_hash": "..."}
        {"line": 43, "issue": "broken_chain",
         "expected_prev": "...", "actual_prev": "..."}
        {"line": 44, "issue": "invalid_json"}

    Lines without a ``hash`` field are treated as pre-chain entries and
    are skipped (legitimate if hash_chain was disabled when written).

    ``initial_prev`` (default ``""``) is the expected ``prev_hash`` of
    the first chain entry. Pass the previous segment's tail hash when
    verifying a rotated audit segment (ADR-0044 / Layer 37 cross-segment
    verification) so the first entry's ``prev_hash`` (typically that of
    an ``audit.rotation_link`` event) is checked against the cross-
    segment boundary rather than the legacy empty-string default.
    """
    if not path.exists():
        return True, []
    problems: list[dict] = []
    prev = initial_prev
    chain_started = False
    # R2-FND-04/06: MAC-epoch + full-strip detection state.
    mac_required = False       # set once a mac'd record is seen under an available key
    mac_seen_count = 0         # total records carrying a mac field
    _no_key_ok = os.environ.get("CORVIN_AUDIT_VERIFY_NO_KEY_OK", "").strip() in ("1", "true", "yes")
    line_no = 0
    with path.open("r") as fh:
        for line in fh:
            line_no += 1
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                problems.append({"line": line_no, "issue": "invalid_json"})
                continue

            if "hash" not in rec:
                # Pre-chain or hash_chain-disabled entry; not part of integrity
                continue

            chain_started = True
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
                    try:
                        from operator.bridges.shared import instance_identity as _iid  # type: ignore[import]
                    except ImportError:
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
    _is_live_chain = path.name == "audit.jsonl"
    if (_is_live_chain and chain_started and mac_seen_count == 0
            and _anchor_key() is not None and _chain_had_mac(path)):
        problems.append({"issue": "mac_stripped_chain",
                         "detail": "MAC active on this chain but it now carries no mac"})

    # Records written with hash_chain=False are intentionally unchained and
    # are not an integrity error — verify_chain reports True when all chained
    # records verify correctly (including the case where none are chained).
    return len(problems) == 0, problems
