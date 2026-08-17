"""Allow-lists for HealingTrace (ADR-0180) — single source of truth.

All allowlists are frozensets. Import from here only; never define local
copies elsewhere. A schema bump (htrace/2) adds a new HTRACE_FIELD_ALLOWLIST_V2
alongside this one; never mutate an existing constant.
"""
from __future__ import annotations

# ── Module namespace allowlist (stack_frames.ns) ─────────────────────────────
# Only frames from canonical CorvinOS namespaces appear in exported records.
# Any frame whose normalised ns is not here gets {"ns": "[external]", ...}.
NS_ALLOWLIST: frozenset[str] = frozenset({
    "chat_runtime",
    "spawn_gates",
    "audit",
    "audit_chain",
    "forge",
    "skill_forge",
    "delegation",
    "hermes_engine",
    "acs_runtime",
    "nerve",
    "nerve_builtins",
    "boot_healer",
    "anomaly_detector",
    "repair_actions",
    "repair",
    "task_manager",
    "telemetry",
    "htrace",
    "htrace_uploader",
    "htrace_consent",
    "egress_gate",
    "house_rules",
    "consent_gate",
    "user_model",
    "session_recall",
    "a2a",
    "error_signature",
    "maintenance_loop",
    "maintainer_capability",
    "maintainer_cli",
    "acl",
    "quota",
    "path_gate",
    "corvin_console",
    "engine_healer",
    "diagnosis",
    "diagnosis_synth",
    "patch_generator",
    "reproduction",
    "replay",
    "adapter",
    "_spawn_gates",
})

# ── Event sequence allowlist (event_sequence field) ───────────────────────────
# Only L16 audit event names on this list may appear. Security-sensitive event
# names are intentionally excluded. Keep in sync with L16 event catalogue.
EVENT_SEQ_ALLOWLIST: frozenset[str] = frozenset({
    "os_turn.started",
    "os_turn.completed",
    "os_turn.failed",
    "compute.quota_exceeded",
    "compute.delegated",
    "compute.started",
    "compute.completed",
    "compute.failed",
    "heal.triggered",
    "heal.action",
    "heal.success",
    "heal.failure",
    "heal.skipped",
    "spawn.gated",
    "spawn.rejected",
    "spawn.started",
    "spawn.completed",
    "engine.selected",
    "engine.fallback",
    "voice.started",
    "voice.completed",
    "voice.error",
    "session.started",
    "session.completed",
    "session.error",
    "task.started",
    "task.completed",
    "task.failed",
    "forge.tool_created",
    "forge.tool_called",
    "skill_forge.skill_created",
    "nerve.scan_started",
    "nerve.scan_completed",
    "nerve.repair_attempted",
    "nerve.repair_succeeded",
    "nerve.repair_failed",
    "htrace.record.written",
    "htrace.upload.sent",
    "htrace.upload.error",
    "htrace.upload.skipped",
})

# ── Config key allowlist (config_profile_hash) ────────────────────────────────
# Only these canonical CorvinOS config key names enter the hash. Values never do.
CONFIG_KEY_ALLOWLIST: frozenset[str] = frozenset({
    "engine",
    "model",
    "voice_enabled",
    "voice_engine",
    "discord_bridge_enabled",
    "telegram_bridge_enabled",
    "whatsapp_bridge_enabled",
    "slack_bridge_enabled",
    "acs_enabled",
    "acs_quota_enabled",
    "forge_enabled",
    "skill_forge_enabled",
    "delegation_enabled",
    "recall_enabled",
    "user_model_enabled",
    "house_rules_enabled",
    "audit_enabled",
    "telemetry_healing_traces",
    "data_residency",
    "tenant_shape",
    "allowed_engines",
    "hermes_enabled",
    "copilot_enabled",
    "a2a_enabled",
})

# ── Top-level field allowlist (the full HealingTrace schema) ──────────────────
# Records with ANY field not listed here are dropped entirely — never sent.
HTRACE_FIELD_ALLOWLIST: frozenset[str] = frozenset({
    "schema",
    "corvin_version",
    "platform",
    "python",
    "error_fingerprint",
    "error_type",
    "error_module_ns",
    "error_function",
    "error_line",
    "error_template",
    "stack_frames",
    "event_sequence",
    "heal_action",
    "heal_outcome",
    "config_profile_hash",
    "tenant_shape",
    "ts_day",
    "consent_act_id",
    "instance_token",
})

# ── Stack-frame field allowlist ───────────────────────────────────────────────
STACK_FRAME_FIELD_ALLOWLIST: frozenset[str] = frozenset({"ns", "fn", "ln"})

# ── Controlled value sets ─────────────────────────────────────────────────────
HEAL_OUTCOME_VALUES: frozenset[str] = frozenset({"success", "failure", "skipped"})
TENANT_SHAPE_VALUES: frozenset[str] = frozenset({"single", "multi"})
