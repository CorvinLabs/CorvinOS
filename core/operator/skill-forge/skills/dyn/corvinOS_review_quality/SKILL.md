---
name: corvinOS_review_quality
description: CorvinOS Code Review: Docs, Diagrams, Git Hygiene, Final Decision Tree
---

# CorvinOS Review: Quality & Final Gate

## PHASE 12: DOCUMENTATION & DIAGRAMS (Load-Bearing)

### 12a. Docs Sync (Hard Constraint)
- [ ] Feature-change → corresponding `docs/claude-ref/layer-N-*.md` updated?
- [ ] Protocol/wire-format change → protocol-ref updated?
- [ ] CLI-flag/command → all `docs/` & diagrams updated?
- [ ] Compliance-mechanism → compliance-audit-doc + CLAUDE.md updated?
- [ ] No "I'll do it later" (load-bearing violation)?

### 12b. Diagram Sync (Hard Constraint)
- [ ] New architecture feature → SVG in `docs/diagrams/`?
- [ ] Sequence/flow diagram for new protocol?
- [ ] Module-map updated?
- [ ] Text descriptions match current code-state?
- [ ] No stale placeholders?

### 12c. Code Comments
- [ ] Only WHY-comments, not WHAT?
- [ ] No references to "issue #123" or "used by X flow"?
- [ ] No multi-paragraph docstrings?
- [ ] Constraint/invariant comments only?

### 12d. CHANGELOG
- [ ] User-facing changes logged?
- [ ] Breaking changes flagged?
- [ ] Version bump justified?

---

## PHASE 13: GIT HYGIENE

### 13a. Commit Quality
- [ ] Commits atomic/logical units?
- [ ] Message format: `<type>(<scope>): <subject>` (lowercase)?
- [ ] No WIP/temp/fixup without rebase?
- [ ] Diff-size reasonable (< 500 lines preferred)?

### 13b. Branch & Merge Strategy
- [ ] Fast-forward merge possible (no merge-commits)?
- [ ] Beta-tester PR? → One `.md` under `docs/issues/` only?
- [ ] Maintainer session? → Direct push to main OK?

### 13c. Version Bumping (SemVer)
- [ ] Major for breaking changes?
- [ ] Minor for new features?
- [ ] Patch for bugfixes?
- [ ] Tag created for release?

---

## PHASE 14: FINAL DECISION TREE

### Decision 1: Red-Lines OK?
```
Compliance red-line? → REJECT (audit-first, no exception)
Licensing red-line?  → REJECT (CLA/§3 non-negotiable)
Security red-line?   → REJECT (path-gate/secrets/chain)
         ↓ NO
     CONTINUE
```

### Decision 2: Structural OK?
```
ADR needed & missing? → REJECT (design-choice without decision)
Layer invariant broken? → REJECT (Corvin contract)
Audit-chain corrupted?  → REJECT (hash-integrity)
         ↓ NO
     CONTINUE
```

### Decision 3: Docs Synced?
```
Feature-change without docs-update? → REQUEST CHANGES (hard constraint)
Diagram stale?                      → REQUEST CHANGES (hard constraint)
         ↓ YES
     CONTINUE
```

### Decision 4: Testing Adequate?
```
Critical code untested?  → REJECT
Coverage < 80%?          → REQUEST CHANGES
Security-code untested?  → REJECT
E2E failing?             → REJECT
         ↓ NO
     CONTINUE
```

### Decision 5: Code Quality?
```
Build fails?        → REQUEST CHANGES
Lint errors?        → REQUEST CHANGES (style/naming)
Performance drop?   → REQUEST CHANGES (with metrics)
         ↓ NO
     CONTINUE
```

### Final Gate: Skip Reasons Named?
```
If any ADR skipped → 1-line skip reason documented?
If tests skipped   → justification documented?
If docs deferred   → when & who responsible?
         ↓ ALL NAMED
     APPROVE ✅
```

---

## SEVERITY ESCALATION

### **REJECT (Stop All)**
- Compliance red-line
- Licensing red-line
- Security red-line (path-gate bypass, secret leak)
- Audit-chain corruption
- ADR required but missing
- Tests failing for critical code
- Build fails

### **REQUEST CHANGES (Conditional Approval)**
- Docs/diagrams not synced (with date + expectation)
- Coverage < 80%
- E2E for security-code missing
- Code-quality issues (style, naming)
- Performance concerns (with metrics)
- Minor compliance concerns

### **APPROVE (With Notes)**
- All gates green
- Skip reasons named
- Docs synced
- Testing adequate
- ADR-gate completed

---

## TEMPLATES FOR FINDINGS

### Template: Compliance Finding
```
**[Layer-NN]** <finding>
- Mechanism: <mechanism affected>
- Impact: <potential consequence>
- Fix: <specific action>
- Reference: docs/claude-ref/layer-NN-*.md
```

### Template: Security Finding
```
**[Security]** <finding>
- Attack surface: <what's exposed>
- Severity: CRITICAL / HIGH / MEDIUM
- Mitigation: <specific fix>
- Test: <how to verify fix>
```

### Template: Testing Gap
```
**[Testing]** <gap>
- Code affected: <file:function>
- Coverage: <current>% → <needed>%
- Scenarios: <happy-path> / <edge-cases> / <error-paths>
```

