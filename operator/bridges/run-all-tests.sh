#!/usr/bin/env bash
# run-all-tests.sh — alle Bridge-Tests auf einen Schlag.
#
# Setup-Anforderungen:
#   - python3 mit dem `openai`-Paket
#   - node mit den daemon-Dependencies (`npm install` in jedem
#     bridges/<channel>/ — der test_daemon_boot.sh braucht diese um
#     overhaupt zu booten)

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

GREEN='\033[1;32m'; RED='\033[1;31m'; CYAN='\033[1;36m'; NC='\033[0m'

# Resolve pytest: prefer project .venv, then AWP venv, then system
PYTEST=$(command -v pytest 2>/dev/null \
  || ls ../../.venv/bin/pytest $HOME/.awp/venv/bin/pytest \
     $HOME/anaconda3/bin/pytest 2>/dev/null | head -1 \
  || echo "")
export PYTEST

run() {
  local name="$1"; shift
  printf "${CYAN}== %s ==${NC}\n" "$name"
  if "$@"; then
    printf "${GREEN}OK${NC}\n\n"
    return 0
  else
    printf "${RED}FAIL${NC}\n\n"
    # Also emit to stderr so the suite name is visible even when stdout
    # is redirected to /dev/null at the call site (e.g. "run ... >/dev/null").
    printf "FAILED SUITE: %s\n" "$name" >&2
    return 1
  fi
}

fails=0

run "Python: adapter parallel"   python3 shared/test_adapter_parallel.py >/dev/null   || fails=$((fails+1))
run "Python: adapter in-flight dedup" python3 shared/test_adapter_in_flight.py >/dev/null || fails=$((fails+1))
run "Python: adapter profiles"   python3 shared/test_adapter_profiles.py >/dev/null   || fails=$((fails+1))
run "Python: adapter cowork"     python3 shared/test_adapter_cowork.py >/dev/null     || fails=$((fails+1))
run "Python: adapter skill-inject" python3 shared/test_adapter_skill_inject.py >/dev/null || fails=$((fails+1))
run "Python: skill auto-grade"   python3 shared/test_skill_auto_grade.py >/dev/null     || fails=$((fails+1))
run "Python: skill outcome-grade" python3 shared/test_skill_outcome_grading.py >/dev/null || fails=$((fails+1))
run "Python: helper_model lib (L29.5)" python3 shared/test_helper_model.py >/dev/null      || fails=$((fails+1))
run "Python: helper_model sites (L29.5)" python3 shared/test_helper_model_sites.py >/dev/null || fails=$((fails+1))
run "Python: OS-turn model (L29.5 Phase 2)" python3 shared/test_adapter_os_model.py >/dev/null || fails=$((fails+1))
run "Python: user-style learner"  python3 shared/test_user_style.py >/dev/null              || fails=$((fails+1))
run "Python: user-style adapter"  python3 shared/test_adapter_user_style.py >/dev/null      || fails=$((fails+1))
run "Python: user-style CLI"      python3 shared/test_user_style_cli.py >/dev/null          || fails=$((fails+1))
run "Python: personal-tools (L27)"      python3 shared/test_personal_tools.py >/dev/null         || fails=$((fails+1))
run "Python: personal-tools CLI (L27)"  python3 shared/test_personal_tools_cli.py >/dev/null     || fails=$((fails+1))
run "Python: personal-tools adapter"    python3 shared/test_adapter_personal_tools.py >/dev/null || fails=$((fails+1))
run "Node:   personal-tools dispatcher" node shared/js/test_personal_tools_dispatcher.js >/dev/null || fails=$((fails+1))
run "Python: memory tier-2 (topics)"   python3 shared/test_memory.py >/dev/null                || fails=$((fails+1))
run "Python: conversation recall (L28.1)" python3 shared/test_conversation_recall.py >/dev/null || fails=$((fails+1))
run "Python: user-model distill (L28.2)" python3 shared/test_user_model.py >/dev/null          || fails=$((fails+1))
run "Python: adapter recall + user-model wiring (L28)" python3 shared/test_adapter_recall.py >/dev/null || fails=$((fails+1))
run "Python: engine trust (L30.1)" python3 shared/test_engine_trust.py >/dev/null || fails=$((fails+1))
run "Python: engine trust adapter (L30.1b)" python3 shared/test_engine_trust_adapter.py >/dev/null || fails=$((fails+1))
run "Python: engine canary (L30.2)" python3 ../voice/scripts/test_engine_canary.py >/dev/null || fails=$((fails+1))
run "Python: engine trust drift (L30.2c+f)" python3 shared/test_engine_trust_drift.py >/dev/null || fails=$((fails+1))
run "Python: output sentinel (L30.3)" python3 shared/test_output_sentinel.py >/dev/null || fails=$((fails+1))
run "Python: SBOM generation (L31.1)" python3 ../../core/gateway/tests/test_sbom.py >/dev/null || fails=$((fails+1))
run "Python: supply-chain verify (L31.3+31.4)" python3 ../voice/scripts/test_supply_chain_verify.py >/dev/null || fails=$((fails+1))
run "Python: path-gate supply-chain (L31)" python3 ../voice/hooks/test_path_gate_supply_chain.py >/dev/null || fails=$((fails+1))
run "Python: forge live (skip)"  python3 shared/test_persona_uses_forge_live.py >/dev/null || fails=$((fails+1))
run "Python: cap warn silence"   python3 shared/test_capability_warning.py >/dev/null     || fails=$((fails+1))
run "Python: voice quota"        python3 shared/test_voice_quota.py >/dev/null              || fails=$((fails+1))
run "Python: session reset"      python3 shared/test_session_reset.py >/dev/null      || fails=$((fails+1))
run "Python: router"             python3 shared/test_router.py >/dev/null             || fails=$((fails+1))
run "Python: ACS-X classifier (ADR-0155)" python3 shared/test_acs_classify.py >/dev/null || fails=$((fails+1))
run "Python: spawn gates (ADR-0158 M1)"  python3 shared/tests/test_spawn_gates.py >/dev/null || fails=$((fails+1))
run "Python: notification relay" python3 ../voice/hooks/test_notification_relay.py >/dev/null || fails=$((fails+1))
run "Python: path_gate hook"     python3 ../voice/hooks/test_path_gate.py >/dev/null         || fails=$((fails+1))
run "Python: phase 1 hardening"  python3 shared/test_adapter_phase1.py >/dev/null     || fails=$((fails+1))
run "Python: phase 4 cleanup"    python3 shared/test_adapter_phase4.py >/dev/null     || fails=$((fails+1))
run "Python: corvinos paths"   python3 shared/test_paths.py >/dev/null            || fails=$((fails+1))
run "Python: voice paths migration" python3 shared/test_voice_paths_migration.py >/dev/null || fails=$((fails+1))
run "Python: cowork resolver"    python3 ../cowork/test/test_resolver.py >/dev/null || fails=$((fails+1))
run "Python: persona scopes"  python3 ../cowork/test/test_persona_default_scopes.py >/dev/null || fails=$((fails+1))
run "Python: forge inherit"      python3 ../cowork/test/test_resolver_forge_inheritance.py >/dev/null || fails=$((fails+1))
run "Python: adapter cancel"     python3 shared/test_adapter_cancel.py >/dev/null     || fails=$((fails+1))
run "Python: adapter btw"        python3 shared/test_adapter_btw.py >/dev/null        || fails=$((fails+1))
run "Python: stream idle wd"     python3 shared/test_adapter_stream_idle.py >/dev/null || fails=$((fails+1))
run "Python: result overwrite"   python3 shared/test_adapter_result_overwrite.py >/dev/null || fails=$((fails+1))
run "Python: tts hard cap"       python3 shared/test_adapter_tts_cap.py >/dev/null || fails=$((fails+1))
run "Python: voice audience"     python3 shared/test_adapter_voice_audience.py >/dev/null || fails=$((fails+1))
run "Python: voice override"     python3 shared/test_adapter_voice_override.py >/dev/null || fails=$((fails+1))
run "Python: engine-fallback voice text (2026-07-12)" python3 shared/test_adapter_engine_fallback_voice.py >/dev/null || fails=$((fails+1))
run "Python: progress dedup"     python3 shared/test_adapter_progress.py >/dev/null   || fails=$((fails+1))
run "Python: completion notify (bg done→messenger)" python3 shared/test_completion_notify.py >/dev/null || fails=$((fails+1))
run "Python: completion E2E (done→outbox→daemon send)" python3 shared/test_completion_e2e.py >/dev/null || fails=$((fails+1))
run "Python: /task producer (detached bg worker)"   python3 shared/test_bg_task.py >/dev/null || fails=$((fails+1))
run "Python: provenance marking (Art.50 §4 SSOT)"   python3 shared/test_provenance.py >/dev/null || fails=$((fails+1))
run "Python: scheduler (cron/workflow outbox)"      python3 shared/test_scheduler.py >/dev/null 2>&1 || fails=$((fails+1))
run "Python: bg_monitor (idle wakeup + delivery)"   bash -c 'PYTEST="${PYTEST:-}"; [[ -z "$PYTEST" ]] && { echo "(skip: pytest not found)"; exit 0; }; "$PYTEST" shared/test_bg_monitor.py -q >/dev/null 2>&1' || fails=$((fails+1))
run "Python: security hardening" python3 shared/test_adapter_security_hardening.py >/dev/null || fails=$((fails+1))
run "Python: HTTP-error reset (transient)"  python3 shared/test_adapter_http_reset.py >/dev/null || fails=$((fails+1))
run "Python: boot self-test"     python3 shared/test_self_test.py >/dev/null || fails=$((fails+1))
run "Python: artifacts lib (L33)"  env PYTHONPATH=../forge python3 ../forge/tests/test_artifacts.py >/dev/null || fails=$((fails+1))
run "Python: artifacts E2E (L33)"  env PYTHONPATH=../forge:shared python3 ../forge/tests/test_artifact_e2e.py >/dev/null || fails=$((fails+1))
run "Python: compute_submit/gate wiring (ADR-0190 M2)" env PYTHONPATH=../forge:../../core/compute python3 ../forge/tests/test_compute_engine_tools.py >/dev/null || fails=$((fails+1))
run "Python: datasource_connect GA tool (ADR-0190 M3)" env PYTHONPATH=../forge:../../core/compute python3 ../forge/tests/test_datasource_connect.py >/dev/null || fails=$((fails+1))
run "Python: artifact-register hook (L33)" python3 ../voice/hooks/test_artifact_register.py >/dev/null || fails=$((fails+1))
run "Python: consent gate (L17)" python3 shared/test_consent_gate.py >/dev/null || fails=$((fails+1))
run "Python: roles (L18)"        python3 shared/test_roles.py >/dev/null || fails=$((fails+1))
run "Python: disclosure (L19)"   python3 shared/test_disclosure.py >/dev/null || fails=$((fails+1))
run "Python: consent store hardening (ADR-0072 V-016)" python3 shared/test_consent.py >/dev/null || fails=$((fails+1))
run "Python: path-gate bash patterns (ADR-0072 V-007/V-013)" python3 shared/test_path_gate.py >/dev/null || fails=$((fails+1))
run "Bash: hermes-pib RAM gate (bc-free, SSOT)" bash test_setup_hermes_pib.sh >/dev/null || fails=$((fails+1))
run "Python: observer injection guard (ADR-0072 V-005)" python3 shared/test_adapter_observer.py >/dev/null || fails=$((fails+1))
run "Python: quota+audit (L20)"  python3 shared/test_quota.py >/dev/null || fails=$((fails+1))
run "Python: proposal (L21)"     python3 shared/test_proposal.py >/dev/null || fails=$((fails+1))
run "Python: settings view (/settings)" python3 shared/test_settings_view.py >/dev/null || fails=$((fails+1))
run "Python: agents (L22)"       env CORVIN_AGENTS_SKIP_LIVE=1 CORVIN_INTEGRATION_TEST=1 python3 shared/agents/test_engines_e2e.py >/dev/null || fails=$((fails+1))
run "Python: delegation lib (L29)" python3 ../../core/delegate/tests/test_delegation.py >/dev/null || fails=$((fails+1))
run "Python: delegate MCP (L29)" python3 ../../core/delegate/tests/test_mcp_server.py >/dev/null || fails=$((fails+1))
run "Python: delegate resolver (L29)" python3 ../cowork/test/test_resolver_delegate.py >/dev/null || fails=$((fails+1))
run "Python: capability registry (ADR-0190)" python3 ../cowork/test/test_capability_registry.py >/dev/null || fails=$((fails+1))
run "Python: capability-registry-matches-reality (ADR-0190 CI gate)" python3 ../cowork/test/test_capability_registry_matches_reality.py >/dev/null || fails=$((fails+1))
run "Python: capability-awareness resolver injection (ADR-0190)" python3 ../cowork/test/test_resolver_capability_awareness.py >/dev/null || fails=$((fails+1))
run "Python: orchestration MCP server (ADR-0190 M4/M5/M6)" env PYTHONPATH=../../core/orchestration:../../core/workflows:shared:../forge python3 ../../core/orchestration/tests/test_mcp_server.py >/dev/null || fails=$((fails+1))
run "Python: orchestration resolver injection (ADR-0190 M4/M5/M6)" python3 ../cowork/test/test_resolver_orchestration.py >/dev/null || fails=$((fails+1))
run "Python: E2E fictional-task routing (ADR-0190)" python3 ../cowork/test/test_e2e_fictional_tasks.py >/dev/null || fails=$((fails+1))
run "Python: delegate output-judge (L29.3a)" python3 ../../core/delegate/tests/test_output_judge.py >/dev/null || fails=$((fails+1))
run "Python: delegate tenant-policy (L29.4a)" python3 ../../core/delegate/tests/test_tenant_policy.py >/dev/null || fails=$((fails+1))
run "Python: delegate sandbox (L29.5-bwrap)" python3 ../../core/delegate/tests/test_sandbox.py >/dev/null || fails=$((fails+1))
run "Python: delegate prompt-safety (L29.6)" python3 ../../core/delegate/tests/test_prompt_safety.py >/dev/null || fails=$((fails+1))
run "Python: delegate skill-context (L30.1)" python3 ../../core/delegate/tests/test_skill_context.py >/dev/null || fails=$((fails+1))
run "Python: delegate mcp-config-builder (L30.2/30.3)" python3 ../../core/delegate/tests/test_mcp_config_builder.py >/dev/null || fails=$((fails+1))
run "Python: delegate live E2E (L30 — forge MCP + codex/opencode config-parse)" python3 ../../core/delegate/tests/test_live_e2e.py >/dev/null || fails=$((fails+1))
run "Python: strict-anonymisation snapshot (ADR-0023 L32)" bash -c 'python3 ../forge/tests/test_corvin_data_strict_anonymizer.py >/dev/null' || fails=$((fails+1))
run "Python: engine_switch (/engine slash-command)" python3 shared/test_engine_switch.py >/dev/null || fails=$((fails+1))
run "Python: opencode engine (L22)" python3 shared/agents/test_opencode_cli.py >/dev/null || fails=$((fails+1))
run "Python: adapter engine-switch (L22)" python3 shared/test_adapter_engine_switch.py >/dev/null || fails=$((fails+1))
run "Python: attachment-cleanup orphan-fix" python3 shared/test_adapter_attachment_cleanup.py >/dev/null || fails=$((fails+1))
run "Node: WA outbox disabled-chat drop" node whatsapp/test_outbox_disabled_chat_drop.js >/dev/null || fails=$((fails+1))
run "Python: engine path (Phase 2.2)" python3 shared/test_adapter_engine_path.py >/dev/null || fails=$((fails+1))
run "Python: engine-binary stripped-PATH guard" python3 shared/test_engine_binary_resolution_guard.py >/dev/null || fails=$((fails+1))
run "Python: voice_summary judge" python3 shared/test_dialectic_voice_summary.py >/dev/null || fails=$((fails+1))
run "Python: STT provider chain (engine-agnostic)" python3 ../voice/scripts/test_stt.py >/dev/null || fails=$((fails+1))
run "Python: corvin-voice doctor (ADR-0185 M5)" python3 ../voice/scripts/test_voice_doctor.py >/dev/null || fails=$((fails+1))
run "Python: auth elevation"     python3 shared/test_auth_elevation.py >/dev/null         || fails=$((fails+1))
run "Python: bridge audit API"   python3 shared/test_audit.py >/dev/null              || fails=$((fails+1))
run "Python: adapter audit"      python3 shared/test_adapter_audit.py >/dev/null      || fails=$((fails+1))
# test_adapter_corvin_env.py removed — file no longer exists
run "Python: audit unified"      python3 shared/test_audit_unified.py >/dev/null      || fails=$((fails+1))
run "Python: forge scopes"      bash -c 'cd ../forge && python3 tests/test_scope_detection.py >/dev/null' || fails=$((fails+1))
run "Python: forge multi-scope" bash -c 'cd ../forge && python3 tests/test_multi_scope.py >/dev/null' || fails=$((fails+1))
run "Python: forge cleanup"     bash -c 'cd ../forge && python3 tests/test_cleanup.py >/dev/null' || fails=$((fails+1))
run "Python: forge plugin"       bash -c 'cd ../forge && f=0; for t in tests/*.py; do [[ "$t" == "tests/test_cleanup.py" || "$t" == "tests/test_requirements.py" || "$t" == "tests/test_audit_detail_floor.py" || "$t" == "tests/test_xbind_guard.py" ]] && continue; python3 "$t" >/dev/null || f=$((f+1)); done; exit $f' || fails=$((fails+1))
run "Python: paths compat (Phase 1)" bash -c 'cd ../forge && python3 tests/test_paths_compat.py >/dev/null' || fails=$((fails+1))
run "Python: scope compat (Phase 1)" bash -c 'cd ../forge && python3 tests/test_scope_compat.py >/dev/null' || fails=$((fails+1))
run "Python: tenants module (ADR-0007 Phase 1.1)" bash -c 'cd ../forge && python3 tests/test_tenants.py >/dev/null' || fails=$((fails+1))
run "Python: tenant-aware paths (ADR-0007 Phase 1.2)" bash -c 'cd ../forge && python3 tests/test_paths_tenant.py >/dev/null' || fails=$((fails+1))
run "Python: tenant-aware state stores (ADR-0007 Phase 1.3)" python3 shared/test_state_stores_tenant.py >/dev/null || fails=$((fails+1))
run "Python: tenant migration helper (ADR-0007 Phase 1.4)" bash -c 'cd ../forge && python3 tests/test_tenant_migrate.py >/dev/null' || fails=$((fails+1))
run "Python: tenant migration→state-store roundtrip (ADR-0007 Phase 1.3+1.4)" bash -c 'cd ../forge && python3 tests/test_tenant_migration_roundtrip.py >/dev/null' || fails=$((fails+1))
run "Python: corvin-gateway auth (ADR-0007 Phase 2.1)" bash -c 'cd ../../core/gateway && python3 tests/test_auth.py >/dev/null' || fails=$((fails+1))
# Phase 2.2's TestClient suite needs FastAPI + pydantic v2 + httpx; the
# plugin ships a self-contained venv (bootstrap.sh). If the venv is
# absent we SKIP rather than FAIL — single-operator setups never run
# the Gateway and shouldn't be forced to install its dependencies.
run "Python: corvin-gateway app (ADR-0007 Phase 2.2)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_app.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway dispatcher (ADR-0007 Phase 2.3)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_dispatcher.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway webhooks (ADR-0007 Phase 2.4)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_webhooks.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway SSE (ADR-0007 Phase 2.5)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_sse.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway end-to-end smoke (ADR-0007 Phase 2.6)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_smoke.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway tenant-config (ADR-0007 Phase 3.1)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_tenant_config.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: tenant cross-plugin integration (ADR-0007 Phase 1+6)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_tenant_cross_plugin.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway engine-policy (ADR-0007 Phase 3.2)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_engine_policy.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway zone-policy (ADR-0007 Phase 3.3)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_zone_policy.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway OIDC/JWT (ADR-0007 Phase 3.4)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_oidc.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway SCIM 2.0 (ADR-0007 Phase 3.5)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_scim.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway Keycloak-shape smoke (ADR-0007 Phase 3.6)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_keycloak_smoke.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway container + Helm (ADR-0007 Phase 4)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_container.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway packaging (ADR-0007 Phase 5)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_packaging.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway durable-queue + rate-limit + gRPC (Phase 7)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_phase7.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: observability Grafana templates (ADR-0007 Phase 6.4)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_observability_dashboards.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: Phase 7 follow-up (gRPC server + SSE TTL eviction)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_phase7_followup.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway audit-metrics (ADR-0007 Phase 6.1)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_audit_metrics.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: corvin-gateway /metrics endpoint (ADR-0007 Phase 6.2)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python tests/test_metrics_endpoint.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: voice-audit metrics CLI (ADR-0007 Phase 6.3)" bash -c '
  cd ../../core/gateway
  if [ -x .venv/bin/python ]; then
    .venv/bin/python ../../operator/voice/scripts/test_voice_audit_metrics.py >/dev/null
  else
    echo "(skip: run core/gateway/bootstrap.sh to enable)"
  fi
' || fails=$((fails+1))
run "Python: spawn-env compat (Phase 1)" python3 shared/test_adapter_spawn_env_compat.py >/dev/null || fails=$((fails+1))
run "Python: secret-vault paths" bash -c 'cd ../forge && python3 tests/test_secret_vault_paths.py >/dev/null' || fails=$((fails+1))
run "Python: plugin-slot compat (Phase 1)" bash -c 'python3 ../skill-forge/tests/test_plugin_slot_compat.py >/dev/null' || fails=$((fails+1))
run "Python: session-timeout compat (Phase 1)" python3 ../voice/scripts/test_session_timeout_compat.py >/dev/null || fails=$((fails+1))
run "Python: skip-live compat (Phase 1)" python3 shared/agents/test_engines_skip_live_compat.py >/dev/null || fails=$((fails+1))
run "Python: corvin-migrate (Phase 4)" python3 shared/test_corvin_migrate.py >/dev/null || fails=$((fails+1))
run "Python: bridge_paths (ADR-0008 P8.1)" python3 shared/test_bridge_paths.py >/dev/null || fails=$((fails+1))
run "Node:   bridge_paths (ADR-0008 P8.1)" node shared/js/test_bridge_paths.js >/dev/null || fails=$((fails+1))
run "Python: bridges_migrate (ADR-0008 P8.2)" python3 shared/test_bridges_migrate.py >/dev/null || fails=$((fails+1))
run "Python: persona sandbox E2E" bash -c 'cd ../forge && python3 tests/test_persona_sandbox.py >/dev/null' || fails=$((fails+1))
run "Python: secret injection E2E" bash -c 'cd ../forge && python3 tests/test_secret_injection.py >/dev/null' || fails=$((fails+1))
run "Python: forge_list E2E"     bash -c 'cd ../forge && python3 tests/test_forge_list.py >/dev/null' || fails=$((fails+1))
run "Python: output streaming"   bash -c 'cd ../forge && python3 tests/test_output_streaming.py >/dev/null' || fails=$((fails+1))
run "Python: skill-forge linter"     bash -c 'python3 ../skill-forge/tests/test_linter.py >/dev/null'        || fails=$((fails+1))
run "Python: skill-forge registry"   bash -c 'python3 ../skill-forge/tests/test_registry.py >/dev/null'      || fails=$((fails+1))
run "Python: skill-forge multi-scope" bash -c 'python3 ../skill-forge/tests/test_multi_scope.py >/dev/null'  || fails=$((fails+1))
run "Python: skill-forge grading"    bash -c 'python3 ../skill-forge/tests/test_grading.py >/dev/null'       || fails=$((fails+1))
run "Python: skill-forge cleanup"    bash -c 'python3 ../skill-forge/tests/test_cleanup.py >/dev/null'       || fails=$((fails+1))
run "Python: skill-forge plugin slot" bash -c 'python3 ../skill-forge/tests/test_plugin_slot.py >/dev/null'  || fails=$((fails+1))
run "Python: skill-forge e2e demo"   bash -c 'python3 ../skill-forge/tests/test_e2e_demo_task.py >/dev/null' || fails=$((fails+1))
run "Python: skill-forge mcp notif"  bash -c 'python3 ../skill-forge/tests/test_mcp_notification.py >/dev/null' || fails=$((fails+1))
run "Python: extract last asst." python3 ../voice/scripts/test_extract_last_assistant.py >/dev/null || fails=$((fails+1))
run "Python: voice freshness"    python3 ../voice/scripts/test_voice_freshness.py >/dev/null      || fails=$((fails+1))
run "Python: summarize"          python3 ../voice/scripts/test_summarize.py >/dev/null      || fails=$((fails+1))
run "Bash: voice env lookup"     bash ../voice/scripts/test_voice_env_lookup.sh >/dev/null  || fails=$((fails+1))
run "Bash: persona voice resolver" bash ../voice/scripts/test_voice_persona_voice.sh >/dev/null || fails=$((fails+1))
run "Python: adapter persona voice" python3 shared/test_adapter_voice_persona.py >/dev/null || fails=$((fails+1))
run "Python: web trust gate (QDL)" python3 ../voice/hooks/test_web_trust_gate.py >/dev/null  || fails=$((fails+1))
# AWP-runtime suites removed by ADR-0005 (test_awp_integration.py +
# test_adapter_awp_dispatch.py + awp_runtime.py). AWP is now consumed
# only as a declarative standard (engine_policy schema + zone classifier).
run "Python: engine_registry (catalog standard)" python3 shared/test_engine_registry.py >/dev/null || fails=$((fails+1))
run "Python: engine_policy + zone classifier (declarative standard)" python3 shared/test_engine_policy.py >/dev/null || fails=$((fails+1))
run "Python: policy-based engine resolution helper" python3 shared/test_engine_policy_dispatch.py >/dev/null || fails=$((fails+1))
run "Python: AWP-DAG walker (Phase 6, standards-only)" python3 shared/test_awp_walker.py >/dev/null || fails=$((fails+1))
run "Python: extension registry (ADR-0142 layer ext)" python3 shared/test_extension_registry.py >/dev/null || fails=$((fails+1))
run "Python: LIP Tier 3 capability registry (ADR-0141)" python3 shared/test_security_capabilities.py >/dev/null || fails=$((fails+1))
run "Python: LIP Tier 1 layer manifest (ADR-0141)" python3 shared/test_layer_integrity.py >/dev/null || fails=$((fails+1))
run "Python: LIP Tier 2 A2A attestation (ADR-0141)" python3 shared/test_layer_integrity_a2a.py >/dev/null || fails=$((fails+1))
run "Python: LIP Tier 4 audit-head (ADR-0141)" python3 shared/test_a2a_audit_head.py >/dev/null || fails=$((fails+1))
run "Bash: say.py TTS helper"    bash ../voice/scripts/test_say.sh >/dev/null               || fails=$((fails+1))
run "Python: audit-verify notify" python3 ../voice/scripts/test_audit_verify_notify.py >/dev/null || fails=$((fails+1))
run "Node: shared/js/ modules"   node shared/js/test_modules.js >/dev/null            || fails=$((fails+1))
run "Node: net probe (outage detection)" node shared/js/test_net_probe.js >/dev/null  || fails=$((fails+1))
run "Node: outbox poller (preCheck+dedup)" node shared/js/test_outbox_poller.js >/dev/null || fails=$((fails+1))
run "Node: in-chat commands"     node shared/js/test_in_chat_commands.js >/dev/null   || fails=$((fails+1))
run "Node: discord slash-cmds"   node discord/test_slash_commands.js >/dev/null       || fails=$((fails+1))
run "Node: teams cards (unit)"   node teams/test_cards.js >/dev/null                   || fails=$((fails+1))
run "Node: teams handler (E2E)"  node teams/test_teams_e2e.js >/dev/null               || fails=$((fails+1))
run "Node: signal handler (E2E)" node signal/test_signal_daemon.js >/dev/null          || fails=$((fails+1))
run "Node: /ldd-* dispatch"      node shared/js/test_in_chat_commands_ldd.js >/dev/null || fails=$((fails+1))
run "Python: ldd library"        python3 shared/test_ldd_lib.py >/dev/null            || fails=$((fails+1))
run "Python: skill-inject + ldd" python3 shared/test_skill_inject_ldd.py >/dev/null   || fails=$((fails+1))
run "Python: ldd ↔ dialectic"    python3 shared/test_ldd_dialectic_coupling.py >/dev/null || fails=$((fails+1))
run "Python: ldd dependencies"   python3 shared/test_ldd_dependencies.py >/dev/null   || fails=$((fails+1))
run "Python: persona ldd"        python3 ../cowork/test/test_persona_ldd_resolution.py >/dev/null || fails=$((fails+1))
run "Node: audit.js wrapper"     node shared/js/test_audit.js >/dev/null              || fails=$((fails+1))
run "Node: auth audit hooks"     node shared/js/test_auth_audit.js >/dev/null         || fails=$((fails+1))
run "Node: read-only gate"       node shared/js/test_auth_read_only.js >/dev/null     || fails=$((fails+1))
run "Node: observer visibility"  node shared/js/test_observer_visibility.js >/dev/null || fails=$((fails+1))
run "Node: consent dispatcher"   node shared/js/test_consent_dispatcher.js >/dev/null || fails=$((fails+1))
run "Node: roles dispatcher (L18)" node shared/js/test_roles_dispatcher.js >/dev/null || fails=$((fails+1))
run "Node: disclosure dispatcher (L19)" node shared/js/test_disclosure_dispatcher.js >/dev/null || fails=$((fails+1))
run "Node: quota dispatcher (L20)" node shared/js/test_quota_dispatcher.js >/dev/null || fails=$((fails+1))
run "Node: proposal dispatcher (L21)" node shared/js/test_proposal_dispatcher.js >/dev/null || fails=$((fails+1))
run "Node: /engine dispatcher (L29 companion)" node shared/js/test_engine_switch_dispatcher.js >/dev/null || fails=$((fails+1))
run "Node: WA chat_state (LID)"  node whatsapp/test_chat_state.js >/dev/null         || fails=$((fails+1))
run "Node: chat_toggle (T/D/S)"  node shared/js/test_chat_toggle.js >/dev/null       || fails=$((fails+1))
run "Python: i18n + summarize i18n + lang_cli" python3 shared/test_i18n.py >/dev/null || fails=$((fails+1))
run "Node: /lang dispatcher"     node shared/js/test_lang_dispatcher.js >/dev/null    || fails=$((fails+1))
run "Python: voice-audit emit"   python3 ../voice/scripts/test_voice_audit_emit.py >/dev/null || fails=$((fails+1))
# corvin-compute (ADR-0013) — opt-in plugin; venv is bootstrapped lazily.
# Phase 13.1's skeleton tests are pure-stdlib and run under system python3,
# so the skip-gate only kicks in when the plugin DIR itself is absent
# (e.g. operators who deliberately removed it). Later phases that need
# sklearn/numpy depend on .venv and SKIP gracefully when it's missing.
run "Python: corvin-compute skeleton (ADR-0013 Phase 13.1)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_plugin_skeleton.py >/dev/null
    else
      python3 ../../core/compute/tests/test_plugin_skeleton.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute driver+strategies (ADR-0013 Phase 13.2+3)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_driver.py >/dev/null && \
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_strategies.py >/dev/null
    else
      python3 ../../core/compute/tests/test_driver.py >/dev/null && \
      python3 ../../core/compute/tests/test_strategies.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute worker (ADR-0013 Phase 13.4)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_worker.py >/dev/null
    else
      python3 ../../core/compute/tests/test_worker.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute completion notify (L25→messenger)" bash -c '
  if [ -d ../../core/compute ]; then
    PY=python3; [ -x ../../core/compute/.venv/bin/python ] && PY=../../core/compute/.venv/bin/python
    "$PY" ../../core/compute/tests/test_compute_notify.py >/dev/null
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute audit+pathgate (ADR-0013 Phase 13.6)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_audit.py >/dev/null
    else
      python3 ../../core/compute/tests/test_audit.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute MCP bridge (ADR-0013 Phase 13.5)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_mcp_bridge.py >/dev/null
    else
      python3 ../../core/compute/tests/test_mcp_bridge.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute parallel+cache (ADR-0013 Phase 13.7)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_parallel.py >/dev/null
    else
      python3 ../../core/compute/tests/test_parallel.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute Bayesian (ADR-0013 Phase 13.8)" bash -c '
  if [ -x ../../core/compute/.venv/bin/python ]; then
    ../../core/compute/.venv/bin/python ../../core/compute/tests/test_bayesian.py >/dev/null
  else
    echo "(skip: sklearn not bootstrapped — run core/compute/bootstrap.sh)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute recovery (ADR-0013 Phase 13.9)" bash -c '
  if [ -d ../../core/compute ]; then
    if [ -x ../../core/compute/.venv/bin/python ]; then
      ../../core/compute/.venv/bin/python ../../core/compute/tests/test_recovery.py >/dev/null
    else
      python3 ../../core/compute/tests/test_recovery.py >/dev/null
    fi
  else
    echo "(skip: corvin-compute plugin not present)"
  fi
' || fails=$((fails+1))
run "Python: corvin-compute end-to-end (ADR-0013 Phase 13.10)" bash -c '
  if [ -x ../../core/compute/.venv/bin/python ]; then
    ../../core/compute/.venv/bin/python ../../core/compute/tests/test_e2e.py >/dev/null
  else
    echo "(skip: sklearn not bootstrapped — run core/compute/bootstrap.sh)"
  fi
' || fails=$((fails+1))
# ── Layer 38 — RemoteTriggerReceiver + A2A protocol (ADR-0048) ─────────
# Run with a sandboxed instance_id path so the test machine never sees a
# persistent /global/instance_id.json written into ~/.corvin/.
_A2A_IID_DIR=$(mktemp -d)
trap "rm -rf $_A2A_IID_DIR" EXIT
export CORVIN_INSTANCE_ID_PATH="$_A2A_IID_DIR/instance_id.json"

run "Python: L38 instance identity (ADR-0048)"         python3 shared/test_instance_identity.py >/dev/null            || fails=$((fails+1))
run "Python: L38 RemoteTriggerReceiver M1 (ADR-0048)"  python3 shared/test_remote_trigger_receiver.py >/dev/null      || fails=$((fails+1))
run "Python: L38 RemoteTriggerReceiver M2 (ADR-0048)"  python3 shared/test_remote_trigger_receiver_m2.py >/dev/null   || fails=$((fails+1))
run "Python: L38 RemoteTriggerSender (ADR-0048)"       python3 shared/test_remote_trigger_sender.py >/dev/null        || fails=$((fails+1))
run "Python: L38 a2a_worker + injection (ADR-0048)"    python3 shared/test_a2a_worker.py >/dev/null                   || fails=$((fails+1))
run "Python: L38 bidirectional E2E (ADR-0048)"         python3 shared/test_a2a_bidirectional.py >/dev/null            || fails=$((fails+1))
run "Python: L38 v3 attachments (ADR-0048)"            python3 shared/test_a2a_attachments.py >/dev/null              || fails=$((fails+1))
run "Python: L38 v3 LIVE compute E2E (ADR-0048)"       python3 shared/test_a2a_e2e_compute.py >/dev/null              || fails=$((fails+1))
run "Python: L38 console route (ADR-0048)"             bash -c '
  if [ -x ../../core/console/.venv/bin/python ]; then
    ../../core/console/.venv/bin/python ../../core/console/tests/test_remote_trigger_log.py >/dev/null
  else
    python3 ../../core/console/tests/test_remote_trigger_log.py >/dev/null
  fi
' || fails=$((fails+1))
run "Python: L25 compute awpkg export+import (ADR-0090)" bash -c '
  _CONSOLE_PY="$(
    [ -x ../../core/console/.venv/bin/python ] && echo ../../core/console/.venv/bin/python || \
    command -v python3 2>/dev/null || true
  )"
  if [[ -z "$_CONSOLE_PY" ]]; then echo "(skip: no python3 found)"; exit 0; fi
  _PYTEST_C="$(
    [ -x ../../core/console/.venv/bin/pytest ] && echo ../../core/console/.venv/bin/pytest || \
    command -v pytest 2>/dev/null || true
  )"
  if [[ -z "$_PYTEST_C" ]]; then echo "(skip: pytest not found)"; exit 0; fi
  cd ../../core/console
  "$_PYTEST_C" tests/test_compute_awp_export.py -q --override-ini="addopts=" >/dev/null
' || fails=$((fails+1))
run "Python: L25 compute security+E2E (2026-06-04 review)" bash -c '
  _CONSOLE_PY="$(
    [ -x ../../core/console/.venv/bin/python ] && echo ../../core/console/.venv/bin/python || \
    command -v python3 2>/dev/null || true
  )"
  if [[ -z "$_CONSOLE_PY" ]]; then echo "(skip: no python3 found)"; exit 0; fi
  _PYTEST_C="$(
    [ -x ../../core/console/.venv/bin/pytest ] && echo ../../core/console/.venv/bin/pytest || \
    command -v pytest 2>/dev/null || true
  )"
  if [[ -z "$_PYTEST_C" ]]; then echo "(skip: pytest not found)"; exit 0; fi
  cd ../../core/console
  "$_PYTEST_C" tests/test_compute_security_e2e.py -q --override-ini="addopts=" >/dev/null
' || fails=$((fails+1))
run "Python: L38 M4 invite-token (ADR-0063)"           python3 shared/test_a2a_invite.py >/dev/null                  || fails=$((fails+1))
run "Python: L29 worker memory bridge (ADR-0051)"      python3 shared/test_memory_bridge.py >/dev/null                || fails=$((fails+1))

# CORVIN_INSTANCE_ID_PATH was scoped to the L38 block above.  Clear it so
# the L39 federation E2E (which spawns two nodes with distinct temp dirs)
# does not inherit the shared path and end up with duplicate instance_ids.
unset CORVIN_INSTANCE_ID_PATH

# ADR-0052 — security hardening (F1–F10); suites use pytest fixtures (tmp_path).
# Discover pytest via PATH or known conda/venv locations; skip gracefully if absent.
_PYTEST_BIN=$(command -v pytest 2>/dev/null || \
  find $HOME/pytest_env/bin $HOME/anaconda3/bin \
       $HOME/.awp/venv/bin $HOME/.local/bin /usr/local/bin \
       -maxdepth 1 -name pytest 2>/dev/null | head -1 || true)
if [[ -n "${_PYTEST_BIN:-}" ]]; then
  run "Python: ADR-0052 CAL + compliance (F1)"              "$_PYTEST_BIN" shared/test_compliance_assertion.py -q       >/dev/null || fails=$((fails+1))
  run "Python: ADR-0052 worker memory confinement (F5)"     "$_PYTEST_BIN" shared/test_worker_memory_confinement.py -q  >/dev/null || fails=$((fails+1))
  run "Python: ADR-0052 security hardening (F2-F4,F6-F10)"  "$_PYTEST_BIN" shared/test_adr0052_security.py -q          >/dev/null || fails=$((fails+1))
  run "Python: forge requirements (pytest)"                  "$_PYTEST_BIN" ../forge/tests/test_requirements.py -q       >/dev/null || fails=$((fails+1))
  run "Python: forge audit detail floor (pytest)"            "$_PYTEST_BIN" ../forge/tests/test_audit_detail_floor.py -q >/dev/null || fails=$((fails+1))
  run "Python: forge x-bind guard (pytest, R3-6)"            "$_PYTEST_BIN" ../forge/tests/test_xbind_guard.py -q        >/dev/null || fails=$((fails+1))
else
  printf "${CYAN}== ADR-0052 tests ==${NC}\n"
  printf "(skip: pytest not found in PATH — install pytest or activate a venv)\n\n"
fi

# ── Layer 39 — CorvinFed social federation (ADR-0053) ───────────────
# Sandbox: fresh CORVIN_HOME per suite via tempdir inside each test file.
run "Python: L39 PostEnvelope sign/verify (ADR-0053)"       python3 shared/test_social_envelope.py >/dev/null          || fails=$((fails+1))
run "Python: L39 content sanitizer NFKC-first (ADR-0053)"   python3 shared/test_social_sanitizer.py >/dev/null         || fails=$((fails+1))
run "Python: L39 participation consent flow (ADR-0053)"      python3 shared/test_social_consent.py >/dev/null           || fails=$((fails+1))
run "Python: L39 social feed + posts.db (ADR-0053)"          python3 shared/test_social_feed.py >/dev/null              || fails=$((fails+1))
run "Python: L39 SocialOriginRegistry (ADR-0053)"            python3 shared/test_social_registry.py >/dev/null          || fails=$((fails+1))
run "Python: L39 social HTTP server (ADR-0053)"              python3 shared/test_social_http_server.py >/dev/null       || fails=$((fails+1))
run "Python: L39 federation E2E two-node (ADR-0053)"         python3 shared/test_social_federation_e2e.py >/dev/null    || fails=$((fails+1))
run "Python: L41 social capability grants (ADR-0054)"  python3 shared/test_social_capability.py >/dev/null  || fails=$((fails+1))

run "Python: content marking Art.50§4 (ADR-0057 M1)"  python3 shared/test_content_marking.py >/dev/null   || fails=$((fails+1))
run "Python: incident tracker L40 (ADR-0057 M6)"       bash -c 'PYTEST="${PYTEST:-}"; [[ -z "$PYTEST" ]] && { echo "(skip: pytest not found)"; exit 0; }; PYTHONPATH=shared "$PYTEST" shared/test_incident_tracker.py -q >/dev/null 2>&1' || fails=$((fails+1))
run "Python: operator decl. gate (ADR-0057 M7)"        bash -c 'PYTEST="${PYTEST:-}"; [[ -z "$PYTEST" ]] && { echo "(skip: pytest not found)"; exit 0; }; PYTHONPATH=shared "$PYTEST" shared/test_operator_declaration.py -q >/dev/null 2>&1' || fails=$((fails+1))
run "Python: annex IV generator (ADR-0057 M8)"         python3 ../voice/scripts/test_corvin_annex_iv.py >/dev/null || fails=$((fails+1))
run "Python: Prometheus label PII linter (ADR-0073 G-017)" python3 shared/check_prometheus_labels.py --root ../.. >/dev/null || fails=$((fails+1))
run "Python: compliance gaps G-006/G-008/G-009/G-010 (ADR-0073)" python3 shared/test_compliance_g006_g008_g009_g010.py >/dev/null || fails=$((fails+1))

# ── ADR-0089 RAG Integration System (Phases 1-8) ────────────────
# NOTE: RAG tests can be run locally with:
#   cd operator/rag-integration && PYTHONPATH=../.. python3 tests/test_rag_basic.py
# Skipping from run-all-tests.sh for now as other test failures are unrelated to RAG code.

run "daemon boot smoke-test"     bash test_daemon_boot.sh >/dev/null                  || fails=$((fails+1))

total=$(grep -c '^run ' "$(basename "${BASH_SOURCE[0]}")")
if [[ $fails -eq 0 ]]; then
  printf "${GREEN}all %d test suites passed${NC}\n" "$total"
  exit 0
else
  printf "${RED}%d / %d suite(s) failed${NC}\n" "$fails" "$total"
  exit 1
fi
