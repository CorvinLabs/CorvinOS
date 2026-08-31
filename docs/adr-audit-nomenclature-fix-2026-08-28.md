# ADR Nomenclature Fix — Audit Finding P2 (2026-08-28)

**Status:** ✅ RESOLVED  
**Audit Reference:** adr-audit-plugin-marketplace-2026-08-28.md, Finding P2  
**Commit:** Corvin-ADR repo, commit d350e64

## Problem

The ADR audit (2026-08-28) identified **8 ADR files missing the standard "ADR-" prefix** in their filenames:

| Old Name | New Name |
|----------|----------|
| `0262-plugin-builder-v2-idea-first-interview.md` | `ADR-0262-plugin-builder-v2-idea-first-interview.md` |
| `0263-plugin-builder-ideas-mode-co-ideation.md` | `ADR-0263-plugin-builder-ideas-mode-co-ideation.md` |
| `0350-configuration-driven-plugin-loading.md` | `ADR-0350-configuration-driven-plugin-loading.md` |
| `0351-tts-provider-priority-openai-tier-1.md` | `ADR-0351-tts-provider-priority-openai-tier-1.md` |
| `0352-corvin-headless-os-console-as-plugin.md` | `ADR-0352-corvin-headless-os-console-as-plugin.md` |
| `0353-frontend-plugin-architecture.md` | `ADR-0353-frontend-plugin-architecture.md` |
| `0354-vibe-inspector-first-external-panel.md` | `ADR-0354-vibe-inspector-first-external-panel.md` |
| `0355-frontendforge-in-browser-panel-authoring.md` | `ADR-0355-frontendforge-in-browser-panel-authoring.md` |

## Impact

- Batch indexing scripts using `ls ADR-*.md | sort` missed these 6 ADRs
- CI/CD gates that enforce `git add Corvin-ADR/decisions/ADR-XXXX-*.md` might reject them
- Documentation generators searching by prefix pattern failed
- ADR graph tools (`scripts/adr_graph.py`) had reduced coverage

## Resolution

**Date Fixed:** 2026-08-28  
**Repository:** /home/shumway/projects/Corvin-ADR  
**Commit:** d350e64 (fix(adr): rename ADR files to standard ADR-XXXX- naming convention)

All 8 files have been renamed to include the "ADR-" prefix. No content or frontmatter was changed — this was a pure nomenclature fix.

**Verification:**
```bash
$ ls -1 /home/shumway/projects/Corvin-ADR/decisions/ADR-0262-* \
         /home/shumway/projects/Corvin-ADR/decisions/ADR-0263-* \
         /home/shumway/projects/Corvin-ADR/decisions/ADR-0350-* | wc -l
8
```

## Remaining Audit Findings (by Priority)

See `adr-audit-plugin-marketplace-2026-08-28.md` for complete audit details.

### P0 IMMEDIATE (Blocks all downstream gates)

- [ ] Resolve ADR-0362 collision (3 variants)
- [ ] Resolve ADR-0363 collision (3 variants)
- [ ] Resolve ADR-0365 collision (2 variants)
- [ ] Update ADR-0443 & ADR-0444 status to ACCEPTED

### P1 BLOCKING (Breaks downstream gates if not resolved)

- [ ] Classify ADR-0350–0355 status: PROPOSED vs. IMPLEMENTED
- [ ] Create or clarify ADR-0262/0263 status (now properly named, status is already ACCEPTED)

### P2 QUALITY (Improves discoverability)

- [x] **Rename files: `0350-*.md` → `ADR-0350-*.md`** (COMPLETE)
- [ ] Add missing edges (ADR-0365 → ADR-0233, ADR-0385 → ADR-0249, clarify ADR-0356 → ADR-0352)
- [ ] Update Memory (2026-08-28 snapshot)

## Next Steps

1. **P0 Collisions:** Resolve ADR number collisions by renumbering 3 duplicate sets
2. **P1 Status:** Promote ADR-0443/0444 to ACCEPTED; clarify ADR-0350–0355
3. **P2 Edges:** Add missing cross-ADR references
4. **Verification:** Re-run ADR graph tools to confirm full coverage

---

**Audit Completed:** 2026-08-28  
**Next Checkpoint:** After P0 & P1 items (target 2026-08-31)
