# Quick Wins Cleanup — 3 Tasks, <2h Total

**Status:** Ready to execute in parallel with ADR-0538 amendments  
**Risk:** LOW (test-only or unused code, no production impact)  
**Reversibility:** 100% (git revert if needed)

---

## Task 1: Delete Dead Feature Flag Resolver (0.5h)

**What:** `core/vibe_engineering/feature_flags.py` — 180 LoC, 0 production usage

**Why:**
- Defined with default OFF (never enabled in production)
- Only imported by tests + doc comments
- Canonical registry exists in `core/console/corvin_core/feature_flags.py` (already used)

**Steps:**

```bash
cd /home/shumway/projects/CorvinOS

# 1. Verify zero production imports
grep -r "from core.vibe_engineering.feature_flags" core/ --include="*.py" \
  | grep -v "test_" | grep -v "#.*import"
# Expected: 0 results

# 2. Find test files to update
grep -r "from core.vibe_engineering.feature_flags\|import.*feature_flags" \
  --include="test_*.py" | cut -d: -f1 | sort -u
# Expected: 1–3 test files

# 3. Update those test files (replace import with canonical registry)
# Example: grep "from core.vibe_engineering.feature_flags import X" tests/ 
#          → replace with "from core.console.corvin_core.feature_flags import X"

# 4. Delete file
git rm core/vibe_engineering/feature_flags.py

# 5. Update __init__.py if it re-exports
grep "feature_flags" core/vibe_engineering/__init__.py && \
  sed -i '/feature_flags/d' core/vibe_engineering/__init__.py

# 6. Test
cd core/vibe_engineering && python -c "import *" 2>&1 | grep -i error || echo "✓ OK"

# 7. Commit
git add . && git commit -m "cleanup: remove dead feature_flags resolver [test-only]

This resolver was defined with default OFF and never enabled in production.
Canonical registry exists in core/console/corvin_core/feature_flags.py.

Removes 180 LoC from dead code, simplifies vibe_engineering module.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

**Verification:**
```bash
# Confirm file is gone
ls -la core/vibe_engineering/feature_flags.py 2>&1 | grep "No such"

# Confirm tests still pass
pytest core/vibe_engineering/tests/ -v --tb=short
```

---

## Task 2: Remove Stale `vibe_engineering_v0_2` Flag (1h)

**What:** Feature flag that default-OFF, never enabled, but still has 8+ test cases

**Why:**
- Never enabled in production config (no pathway to turn it on)
- Tests still validate the flag logic (dead test paths)
- ~50 LoC removed, test suite shrinks

**Steps:**

```bash
cd /home/shumway/projects/CorvinOS

# 1. Locate the flag definition
grep -r "vibe_engineering_v0_2" core/ --include="*.yaml" --include="*.py" --include="*.json"
# Expected output shows: feature_config.yaml (default: false), test_feature_flags.py (8 tests)

# 2. Verify NO production config enables it
grep -r "vibe_engineering_v0_2.*true" . --include="*.yaml" --include="*.json"
# Expected: 0 results

# 3. Remove from feature_config.yaml
# Locate line like: "vibe_engineering_v0_2: false"
# Delete the line (or entire section if small)

# 4. Find and remove test cases
grep -n "vibe_engineering_v0_2\|@parameterize.*v0_2\|test.*v0_2" \
  core/vibe_engineering/tests/test_feature_flags.py
# Count the lines; should be ~8 test cases

# Delete those test functions/cases from test file
# (Use editor or sed to remove the @test blocks)

# 5. Verify feature_flags.py DEFAULTS dict if it exists
grep -A 5 "^DEFAULTS = {" core/vibe_engineering/feature_flags.py || echo "No DEFAULTS"
# If it has "vibe_engineering_v0_2", remove it

# 6. Test
pytest core/vibe_engineering/tests/test_feature_flags.py -v --tb=short
# Should have 8 fewer test cases than before

# 7. Commit
git add . && git commit -m "cleanup: remove dead vibe_engineering_v0_2 flag [test-only]

Flag was defined with default OFF and never enabled in any production config.
Removed flag definition + 8 test cases that validated dead code path.

Removes 50 LoC, simplifies feature flag maintenance.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

**Verification:**
```bash
# Confirm flag is gone from config
grep "vibe_engineering_v0_2" core/vibe_engineering/feature_config.yaml && echo "FAIL" || echo "✓ OK"

# Confirm test count decreased
pytest core/vibe_engineering/tests/test_feature_flags.py --collect-only -q | wc -l
# Should be ~8 fewer than before this task
```

---

## Task 3: Cleanup Unused Imports (Automation, 1h)

**What:** Scan for unused imports, remove them (low-risk automation)

**Why:**
- Dead imports clutter code + confuse readers
- Can be automated with linting tools
- No behavior change

**Steps:**

```bash
cd /home/shumway/projects/CorvinOS

# 1. Install/run autoflake (or similar)
# If not installed: pip install autoflake

# 2. Dry-run: see what it would remove
autoflake --remove-all-unused-imports --recursive core/vibe_engineering/ --check

# 3. Apply changes (with caution: review output first!)
autoflake --remove-all-unused-imports --recursive core/vibe_engineering/ --in-place

# 4. Manual review (some tools remove false positives)
git diff core/vibe_engineering/
# Verify no legitimate imports were removed (e.g., TYPE_CHECKING imports)

# 5. Test
pytest core/vibe_engineering/tests/ -v --tb=short

# 6. Commit
git add . && git commit -m "cleanup: remove unused imports in vibe_engineering [maintenance]

Ran autoflake to remove unused imports. Maintains code cleanliness.
No behavior change; all tests pass.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

**Verification:**
```bash
# Confirm no regressions
pytest core/vibe_engineering/tests/ -q
# All tests should pass

# Confirm import removals are safe (run linter)
pylint core/vibe_engineering/ --disable=all --enable=unused-import
# Should have fewer or same number of warnings as before
```

---

## Combined Impact (All 3 Quick Wins)

| Task | LoC | Test Cases | Effort | Status |
|---|---|---|---|---|
| 1. Delete feature_flags.py | -180 | -? | 0.5h | 🟢 Ready |
| 2. Remove v0_2 flag | -50 | -8 | 1h | 🟢 Ready |
| 3. Unused imports | -~30 | 0 | 1h | 🟢 Ready |
| **TOTAL** | **-260** | **-8** | **<2.5h** | **🟢 Ready** |

**Expected outcome:**
- Cleaner codebase
- Test suite shrinks by 8 cases (less maintenance)
- No production impact
- Full reversibility (git revert if needed)

---

## Execution Order

**Sequential (safer):**
1. Task 1 (feature_flags.py delete) → test → commit
2. Task 2 (v0_2 flag) → test → commit
3. Task 3 (unused imports) → test → commit

**Or in parallel** (if high confidence):
- Start all 3; review changes; commit together

**Dependency check:** None (fully independent)

---

## Rollback (if needed)

```bash
# Revert all 3 quick wins
git revert HEAD~3..HEAD    # Revert last 3 commits
# OR
git reset --hard HEAD~3    # Hard reset to before quick wins

# Restore files
git checkout HEAD@{1} -- core/vibe_engineering/
```

---

## Notes

- ADR-0538 amendments do NOT block these quick wins (independent code paths)
- Phase A can start before/during/after quick wins (no interaction)
- Quick wins are purely maintenance (test-only + unused code cleanup)
- Zero risk of production impact
