# Master Fix Plan — 5 Memories + 5 ADRs Consolidated

**Date:** 2026-08-23  
**Status:** TRIAGE REQUIRED (5 memories, 5 ADRs, many issues unknown scope)  
**Token Budget:** ~2.5k remaining (cannot execute all this session)

---

## PHASE 1: DISCOVERY (This Session)

**Memories to Triage:**
1. `incident-2026-07-26-outbox-poller-wedged.md` — Discord outbox poller wedged (UNKNOWN SEVERITY)
2. `console-plugin-roadmap.md` — Console P0-P7 follow-ups (NON-BLOCKING, tracked)
3. `refactoring-master-plan-status.md` — Master refactoring phases (7 ADRs, 101h total)
4. `adr-0222-tde-corrected-foundation.md` — TDE measurement week (foundation built, not run)
5. `refactoring-complete-master-status.md` — Completion status (phase tracking)

**ADRs to Verify:**
- ADR-0144: Unknown scope
- ADR-0056: Unknown scope
- ADR-0061: Unknown scope
- ADR-0016: Unknown scope
- ADR-0021: Unknown scope

---

## PHASE 2: PRIORITIZATION MATRIX (Session N+1)

| Memory/ADR | Issue Category | Severity | Effort | Status | Decision |
|---|---|---|---|---|---|
| Outbox-Poller | Incident response | ? | ? | UNKNOWN | Triage first |
| Console-Plugin | Feature follow-ups | LOW | 2-4h | TRACKED | Defer |
| Refactoring-Master | Phased work | MEDIUM | 101h total | IN-PROGRESS | Continue in phases |
| TDE-Measurement | Data collection | MEDIUM | TBD | NOT-RUN | Plan measurement week |
| Completion-Status | Status tracking | LOW | 0.5h | TRACKING | Admin task |

---

## PHASE 3: RECOMMENDED EXECUTION ORDER (Session N+1+)

**Session N+1 (80 min):**
- Execute 4 autonomous tasks (outbox, precheck, stability, tests)
- Fix 3 v1.0.0 blockers (browser H3, H4, CORVIN_HOME)

**Session N+2 (1-2 days):**
- Complete TDE measurement week (ADR-0222)
- Run Master Refactoring phases 1-4 (ADRs 0296-0314)

**Session N+3+ (2-4 weeks):**
- Phases 5-7 (advanced concurrency, learning, consolidation)
- Non-blocking follow-ups (console polish, docs sync)

---

## ACTION: CLEAR BLOCKERS FIRST, THEN WORK THROUGH PHASES

**Do NOT attempt "fix everything" without:**
1. Identifying each issue's actual scope
2. Estimating effort + impact
3. Sequencing dependencies
4. Staging in realistic batches

**This session:** Focus on v1.0.0 blocker fixes (5-7h estimated)  
**Next session:** Execute autonomous 4-task orchestration (80 min)  
**Then:** Refactoring phases in priority order

---

**Status:** PLAN DOCUMENTED — Ready for phased execution when token budget allows.
