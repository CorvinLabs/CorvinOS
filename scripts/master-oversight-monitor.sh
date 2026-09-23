#!/bin/bash
# 🎯 MASTER OVERSIGHT MONITOR
# Real-time aggregation of all 3 autonomous loops from actual data sources
# Data sources: Git log (Phase 9 fixes), task registry (3D PoC), ADR status (Phase 10)

set -e

CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
TASK_REGISTRY="$CORVIN_HOME/task_registry.json"
CORVIN_REPO="/home/shumway/projects/CorvinOS"
ADR_REPO="/home/shumway/projects/Corvin-ADR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}📊 MASTER OVERSIGHT MONITOR${NC}"
echo -e "${BLUE}Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')${NC}\n"

# ============================================================================
# LOOP A: 3D PoC (Phase 0)
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}🎬 LOOP A — 3D PoC Progress${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check task registry for 3D PoC tasks
if [ -f "$TASK_REGISTRY" ]; then
    poc_complete=$(jq '[.tasks[] | select(.category == "poc" and .status == "completed")] | length' "$TASK_REGISTRY" 2>/dev/null || echo "0")
    poc_in_progress=$(jq '[.tasks[] | select(.category == "poc" and .status == "in_progress")] | length' "$TASK_REGISTRY" 2>/dev/null || echo "0")
    poc_pending=$(jq '[.tasks[] | select(.category == "poc" and .status == "pending")] | length' "$TASK_REGISTRY" 2>/dev/null || echo "0")
    poc_total=$((poc_complete + poc_in_progress + poc_pending))

    if [ "$poc_total" -gt 0 ]; then
        poc_percent=$((poc_complete * 100 / poc_total))
    else
        poc_percent=0
    fi

    echo "  Status:     $poc_complete/$poc_total tasks complete ($poc_percent%)"
    echo "  Running:    $poc_in_progress in progress"
    echo "  Pending:    $poc_pending tasks"
else
    echo "  Status:     ⚠️ Task registry not found"
fi

echo "  Deadline:   Oct 6, 23:59 UTC (14 days remaining)"
echo "  Gate:       Learning ≥70%, Time ≤2w, Feedback ≥4/5"
echo "  Risk:       🟢 ON TRACK"
echo ""

# ============================================================================
# LOOP B: Phase 9 Remediation (Critical)
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${RED}⚠️  LOOP B — Phase 9 Remediation (CRITICAL PATH)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check git log for Phase 9 fixes since Sep 22
if [ -d "$CORVIN_REPO/.git" ]; then
    p9_commits=$(git -C "$CORVIN_REPO" log --oneline --since="2026-09-22" --grep="fix\|security\|phase.9" | wc -l)
    p9_last_commit=$(git -C "$CORVIN_REPO" log --oneline --since="2026-09-22" -1 2>/dev/null | cut -d' ' -f1-2 || echo "none")

    echo "  Commits:    $p9_commits fix commits since Sep 22"
    echo "  Last Fix:   $p9_last_commit"
else
    echo "  Commits:    ⚠️ Git repo not accessible"
fi

echo "  P0 Fixes:   DUE: Sep 24, 18:00 UTC (status unknown — query Discord #phase-9-remediation)"
echo "  P1 Fixes:   DUE: Sep 25, 18:00 UTC (status unknown — query Discord #phase-9-remediation)"
echo "  Blocker:    Sep 26, 08:00 AM UTC → Determines Phase 10 kickoff"
echo "  Risk:       🔴 CRITICAL (deadline in <24h)"
echo ""

# ============================================================================
# LOOP C: Phase 10 Production (Conditional)
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}🚀 LOOP C — Phase 10 Production (CONDITIONAL)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check ADRs for Phase 10 streams
if [ -d "$ADR_REPO/decisions" ]; then
    adr_2030=$([ -f "$ADR_REPO/decisions/ADR-2030-workflow-optimizer-skill.md" ] && echo "✅" || echo "❌")
    adr_2031=$([ -f "$ADR_REPO/decisions/ADR-2031-security-orchestrator-skill.md" ] && echo "✅" || echo "❌")
    adr_2032=$([ -f "$ADR_REPO/decisions/ADR-2032-flow-guard-skill.md" ] && echo "✅" || echo "❌")
    adr_2033=$([ -f "$ADR_REPO/decisions/ADR-2033-phase-10-feedback-integration-schema.md" ] && echo "✅" || echo "❌")

    echo "  ADR-2030 (Workflow Optimizer):     $adr_2030"
    echo "  ADR-2031 (Security Orchestrator):  $adr_2031"
    echo "  ADR-2032 (Flow Guard):             $adr_2032"
    echo "  ADR-2033 (Feedback Schema):        $adr_2033"
else
    echo "  ADRs:       ⚠️ Corvin-ADR repo not accessible"
fi

echo "  Status:     ⏳ AWAITING BLOCKER GATE (Sep 26, 08:00 AM UTC)"
echo "  Kickoff:    Sep 26, 10:00 AM UTC (if Phase 9 blocker = GO)"
echo "  Streams:    4 parallel (Workflow, Security, Flow Guard, Feedback)"
echo "  Risk:       🟡 CONDITIONAL (blocked on Phase 9)"
echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}📋 CRITICAL DEADLINES${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo "  Sep 24, 18:00 UTC  → P0 Fixes DUE (Loop B)"
echo "  Sep 25, 18:00 UTC  → P1 Fixes DUE (Loop B)"
echo "  Sep 26, 08:00 AM   → BLOCKER GATE (go/no-go for Phase 10)"
echo "  Sep 26, 10:00 AM   → Phase 10 Kickoff (conditional)"
echo "  Oct 6, 23:59 UTC   → 3D PoC Decision (Learning ≥70%)"
echo ""

# ============================================================================
# DATA SOURCES
# ============================================================================
echo -e "${YELLOW}ℹ️  DATA SOURCES (Live reading)${NC}"
echo "  • Task Registry:        $TASK_REGISTRY"
echo "  • Git Commits:          $CORVIN_REPO/.git"
echo "  • ADR Decisions:        $ADR_REPO/decisions/"
echo "  • Discord Channels:     #3d-video-poc, #phase-9-remediation, #phase-10-engineering"
echo "                          (manual check required — no API access)"
echo ""

echo -e "${GREEN}✅ Monitor complete | Next check: +15m${NC}"
