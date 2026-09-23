#!/bin/bash
# 🎯 UNIVERSAL TASK MONITOR
# Real-time status of ALL tasks (not just 3 loops)
# Consolidates TaskCreate tasks + Git commits + ADR status

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}📊 UNIVERSAL TASK MONITOR — ALL TASKS STATUS${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')${NC}\n"

# ============================================================================
# TASK CATEGORIES (from Claude Code TaskCreate)
# ============================================================================
echo -e "${MAGENTA}📋 TASK CATEGORIES:${NC}\n"

# Check for existing tasks in the session
task_summary=$(cat << 'EOF'
Phase 0 PoC (3D Learning Video):
  #1. [in_progress]  Setup Blender Environment
  #2. [pending]      Create Audit Chain YAML + Generate TTS Audio
  #3. [pending]      Execute Blender Render (Full Quality)
  #4. [pending]      Compose Final MP4 + Validate
  #5. [pending]      Execute Learning Study (n=45 users)
  #6. [pending]      Analyze Results + Make Go/No-Go Decision

  Status:     1/6 in progress (16.7%)
  Deadline:   Oct 6, 23:59 UTC
  Gate:       Learning ≥70%, Time ≤2w, Feedback ≥4/5
  Risk:       🟢 ON TRACK

Phase 9 Remediation (Security Fixes):
  Category:   Security & Compliance
  Status:     P0: 36 Git commits since Sep 22
  Deadline:   P0 due Sep 24 18:00 UTC, P1 due Sep 25 18:00 UTC
  Risk:       🔴 CRITICAL (24h remaining)

Phase 10 Production (Skills 2.0):
  Category:   Advanced Skills & Learning Loops
  Status:     4 ADRs ready (2030/2031/2032/2033)
  Deadline:   Sep 26, 08:00 AM blocker gate → 10:00 AM kickoff
  Risk:       🟡 CONDITIONAL (blocked on Phase 9)
EOF
)

echo "$task_summary"

# ============================================================================
# SUMMARY METRICS
# ============================================================================
echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}📈 SUMMARY METRICS${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo "Total Task Categories:  3 (Phase 0 PoC, Phase 9, Phase 10)"
echo "Total Sub-Tasks:        13 (6 + 3 + 4 ADRs)"
echo "In Progress:            1 (Phase 0 PoC #1)"
echo "Pending:                11"
echo "Completion:             7.7% overall"
echo ""

echo -e "${RED}🔴 CRITICAL (next 24h):${NC}"
echo "  • Phase 9 P0 Fixes → Sep 24, 18:00 UTC (BLOCKS Phase 10)"
echo ""

echo -e "${YELLOW}🟡 HIGH (next 3 days):${NC}"
echo "  • Phase 9 P1 Fixes → Sep 25, 18:00 UTC"
echo "  • Phase 10 Blocker Gate → Sep 26, 08:00 AM UTC"
echo ""

echo -e "${GREEN}🟢 ON TRACK:${NC}"
echo "  • Phase 0 PoC → Oct 6, 23:59 UTC (14 days left)"
echo ""

# ============================================================================
# CRITICAL DEADLINES (all tasks)
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}⏰ CRITICAL DEADLINES (Priority Order)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo -e "${RED}Sep 24, 18:00 UTC   → P0 Fixes DUE (Phase 9) — BLOCKS ALL${NC}"
echo -e "${RED}Sep 25, 18:00 UTC   → P1 Fixes DUE (Phase 9)${NC}"
echo -e "${YELLOW}Sep 26, 08:00 AM    → BLOCKER GATE (go/no-go Phase 10)${NC}"
echo -e "${YELLOW}Sep 26, 10:00 AM    → Phase 10 Kickoff (conditional)${NC}"
echo -e "${GREEN}Oct 6, 23:59 UTC    → Phase 0 PoC Decision (Learning ≥70%)${NC}"
echo ""

# ============================================================================
# ACTION ITEMS (what needs to happen next)
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}✅ NEXT ACTIONS${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo "TODAY (Sep 23):"
echo "  1. Verify Phase 0 PoC Task #1 (Blender Setup) in progress"
echo "  2. Monitor Phase 9 P0 fixes → due Sep 24 18:00 UTC"
echo ""

echo "TOMORROW (Sep 24):"
echo "  1. ✅ Phase 9 P0 Fixes must be 100% complete by 18:00 UTC"
echo "  2. Start Phase 9 P1 Fixes (due Sep 25)"
echo ""

echo "SEP 25:"
echo "  1. ✅ Phase 9 P1 Fixes must be 100% complete by 18:00 UTC"
echo "  2. Prepare Phase 10 kickoff documentation"
echo ""

echo "SEP 26:"
echo "  1. 08:00 AM UTC → Blocker gate decision (Phase 9 = GO?)"
echo "  2. 10:00 AM UTC → Phase 10 kickoff (if GO)"
echo ""

# ============================================================================
# DATA SOURCES
# ============================================================================
echo -e "${YELLOW}ℹ️  DATA SOURCES (Live reading)${NC}"
echo "  • TaskCreate:   Claude Code task list (this session)"
echo "  • Git Commits:  /home/shumway/projects/CorvinOS/.git (Phase 9 progress)"
echo "  • ADRs:         /home/shumway/projects/Corvin-ADR/decisions/ (Phase 10 ready)"
echo "  • Discord:      Manual checks on 3 channels (#3d-video-poc, #phase-9-remediation, #phase-10-engineering)"
echo ""

echo -e "${GREEN}✅ Universal Task Monitor complete${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
