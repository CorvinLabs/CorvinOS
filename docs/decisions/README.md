# Architectural Decision Records (DEPRECATED)

**⚠️ DEPRECATED (2026-09-17, ADR-0862)**

This directory is **no longer the canonical location** for ADRs.

## New Location

All ADRs have been migrated to a git submodule:
- **Path:** `corvin_decisions/decisions/` (in this repository)
- **Canonical Source:** `/home/shumway/projects/Corvin-ADR/decisions/`
- **Sync:** Automatically via git submodule (read-only)

## Migration Rationale

| Issue | Solution |
|---|---|
| ADRs duplicated locally | Submodule keeps single canonical copy in sync |
| Fragmentation across repos | External Corvin-ADR repo owns all core ADRs |
| Manual sync burden | Git handles submodule updates automatically |

## How to Access ADRs

**From CorvinOS repo:**
```bash
# After clone
git submodule update --init --recursive

# Read any ADR
cat corvin_decisions/decisions/ADR-XXXX-slug.md
```

**From external repo (if needed):**
```bash
cd /home/shumway/projects/Corvin-ADR
ls decisions/ADR-*.md
```

## No Longer Here

- ❌ Do NOT create ADR files in `docs/decisions/` (this directory)
- ❌ Do NOT modify ADRs locally and expect them to sync
- ❌ Do NOT reference old paths in code (`docs/decisions/ADR-XXXX`)

**New Pattern in Code:**
```python
# ✅ Correct
# See corvin_decisions/decisions/ADR-0862-adr-submodule-integration.md

# ❌ Wrong (deprecated)
# See docs/decisions/ADR-0862.md
```

## Questions?

See:
- `CorvinOS/CLAUDE.md` → "ADR Submodule Integration" section
- `corvin_decisions/decisions/ADR-0862-adr-submodule-integration.md` → Full details

---

**Decision:** ADR-0862 (2026-09-17)  
**Effective:** Immediately  
**Previous Location:** `docs/decisions/` (archived, not actively used)
