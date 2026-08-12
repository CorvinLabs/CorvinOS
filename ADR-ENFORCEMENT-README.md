# ADR-Code Sync Enforcement

**Status:** LIVE (2026-08-12)  
**Purpose:** Ensure Code and Architecture Decision Records never diverge again

---

## Problem

**Phase 5 shipped:** 0296–0301 implemented in code  
**Documentation:** Completely missing from Corvin-ADR  
**Root Cause:** No technical enforcement of Code/ADR sync

## Solution

5-layer enforcement stack makes Code-ADR sync **non-negotiable**:

| Layer | Component | Trigger | Enforcement |
|---|---|---|---|
| 1 | Pre-commit hook | Local commit | Blocks commits to `core/` without ADR |
| 2 | CI/CD gate | GitHub push/PR | Blocks PR merge |
| 3 | Code review checklist | Human review | Requires ADR validation |
| 4 | CLAUDE.md rules | Every session | Clear written policy |
| 5 | Auto-sync | Post-merge + cron | Updates memory automatically |

---

## Quick Start

### For Developers

No special action needed — the hooks auto-install with git clone.

**When you commit code to `core/`:**

```bash
# 1. Make your change
# 2. Create an ADR (or skip with valid reason)
# 3. Stage both
git add core/my_module/*.py
git add Corvin-ADR/decisions/ADR-0XXX-*.md

# 4. Commit (hook validates automatically)
git commit -m "feat(my_module): description

ADR-0XXX documents this design.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

**If hook rejects (no ADR found):**

```bash
# Option 1: Add the ADR
# Option 2: Skip (for tests/docs/hotfixes only)
git commit --no-verify -m "fix: urgent hotfix [skip-adr-check]"
```

### For Maintainers

**Install hooks once per repo clone:**

```bash
bash operator/scripts/install_adr_hooks.sh
```

**Run tests:**

```bash
bash operator/scripts/test_adr_hooks.sh
```

---

## Components

### 1. Pre-Commit Hook (`.git/hooks/pre-commit`)

**What it does:**
- Detects staged changes to `core/` (excluding tests)
- Checks for `Corvin-ADR/decisions/ADR-*.md` in same commit
- Rejects if missing (unless exempt)

**What triggers it:**
- Every `git commit` in CorvinOS

**Bypass:**
```bash
git commit --no-verify
```

**Exempt files:**
- Tests (`core/*/tests/`, `conftest.py`)
- Fixtures
- `*.pyc` files

### 2. CI/CD Gate (`.github/workflows/adr-sync-check.yml`)

**What it does:**
- Runs on every PR to `main`
- Compares base..HEAD for code vs ADR changes
- Blocks merge if out of sync
- Posts PR comment with guidance

**What triggers it:**
- GitHub push to main
- Pull request against main
- Paths filter: `core/**` (not tests)

### 3. Code Review Checklist (`CONTRIBUTING.md`)

**Required checks:**
- [ ] ADR exists (or skip reason documented)
- [ ] ADR.paths matches changed files
- [ ] ADR.commits lists this PR's commits
- [ ] ADR title accurately describes change

### 4. Policy (CLAUDE.md + CONTRIBUTING.md)

**Clear rules:**

| Change Type | ADR Required | Example |
|---|---|---|
| New module/API | ✅ YES | `core/newmodule/__init__.py` |
| Compliance change | ✅ YES | Change to audit trail |
| Bug fix | ❌ NO | Off-by-one error |
| Refactor | ❌ NO | Rename variable, same behavior |
| Test change | ❌ NO | But document in commit msg |

### 5. Auto-Sync Script (`operator/scripts/sync_memory_from_adrs.py`)

**What it does:**
- Reads all ADRs from Corvin-ADR/decisions/
- Generates `~/.claude/projects/CorvinOS/memory/ADR-INDEX.md`
- Commits to git automatically

**What triggers it:**
- Post-merge hook (if ADRs changed)
- Nightly cron (to be added)
- Manual: `python3 operator/scripts/sync_memory_from_adrs.py --commit`

**Generated index includes:**
- All active ADRs with status + paths
- Deprecated/superseded ADRs
- Module-to-ADR cross-reference
- Regulation annotations

---

## Decision Record: When ADR is Required

### ✅ YES — Create an ADR if:

- **New module** in `core/` — `core/myfeature/__init__.py`
- **New public API** — exported function, endpoint, CLI command
- **Protocol change** — wire format, message schema, contract
- **Compliance mechanism** — audit, consent, disclosure
- **Layer-level contract** — changes contract with downstream
- **Irreversible decision** — fails open or closed, licensing tier

**Example:**
```python
# core/myfeature/__init__.py — NEW MODULE
def my_feature():  # <- Needs ADR
    pass
```

### ❌ NO — Skip ADR if:

- **Bug fix** with no behavior change outside the bug
- **Refactor** — same behavior, different code
- **Test/fixture** — no production code change
- **Documentation** — no code change
- **Parameter tuning** — thresholds, timeouts
- **Comment/docstring** — no logic change

**Example:**
```python
# core/existing/__init__.py — EXISTING MODULE
def my_function():
    # Fixed off-by-one error
    return value - 1  # <- No ADR needed
```

### ⚠️ MAYBE — Ask if:

- **Feature flag** — if it gates major behavior: YES
- **Performance opt** — if it changes contract: YES
- **Config schema** — if it adds new layer: YES

---

## Files

| File | Purpose | Edited |
|---|---|---|
| `.git/hooks/pre-commit` | Local enforcement | Yes |
| `.git/hooks/post-merge` | Auto-sync trigger | Yes |
| `.github/workflows/adr-sync-check.yml` | CI/CD gate | Yes |
| `operator/scripts/sync_memory_from_adrs.py` | Memory updater | Yes |
| `operator/scripts/install_adr_hooks.sh` | Setup script | Yes |
| `operator/scripts/test_adr_hooks.sh` | Test suite | Yes |
| `CLAUDE.md` | Policy rules | Updated |
| `CONTRIBUTING.md` | Developer guide | Updated |
| `ADR-ENFORCEMENT-README.md` | This file | Yes |

---

## Testing

### Manual Test

```bash
# Install hooks
bash operator/scripts/install_adr_hooks.sh

# Run test suite
bash operator/scripts/test_adr_hooks.sh

# Expected output:
#   ✓ Pre-commit hook installed
#   ✓ Rejects code without ADR
#   ✓ Allows --no-verify bypass
#   ✓ Exception flag works
```

### Integration Test (Real Commit)

```bash
# Create test branch
git checkout -b test/adr-enforcement

# Make a code change
echo "# Test" > core/test/demo.py

# Try to commit WITHOUT ADR
git add core/test/demo.py
git commit -m "test: demo"
# Should FAIL with:
#   ERROR: Code-Änderungen erfordern ADR-Dokumentation

# Create ADR
cat > Corvin-ADR/decisions/ADR-9999-demo.md << 'EOF'
---
id: ADR-9999
status: PROPOSED
paths:
  - core/test/demo.py
---
# Demo ADR
EOF

# Try again WITH ADR
git add Corvin-ADR/decisions/ADR-9999-demo.md
git commit -m "test: demo

ADR-9999 documents this.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
# Should SUCCEED with:
#   ✓ ADR-Dokumentation gefunden
```

---

## Troubleshooting

### Hook Rejected My Commit (No ADR)

**Solution:** Create an ADR or skip with valid reason.

```bash
# Option 1: Create ADR
# (Edit Corvin-ADR/decisions/ADR-XXXX-*.md)
git add Corvin-ADR/decisions/ADR-XXXX-*.md
git commit

# Option 2: Skip (for tests/docs/hotfixes only)
git commit --no-verify -m "fix: hotfix [skip-adr-check: reason]"
```

### Hook is Not Running

**Debug:**
```bash
# Check if installed
ls -la .git/hooks/pre-commit
# Should show: -rwxr-xr-x (executable)

# Check permissions
chmod +x .git/hooks/pre-commit

# Re-install
bash operator/scripts/install_adr_hooks.sh
```

### CI/CD Gate is Blocking My PR

**Solution:** Same as hook — add ADR or document skip reason in commit message.

**CI gate checks:**
- Are there code changes in `core/`?
- Are there matching ADR changes?
- Is there a valid skip flag in commit message?

### Memory Not Syncing

**Debug:**
```bash
# Run sync manually
python3 operator/scripts/sync_memory_from_adrs.py

# Check output
cat ~/.claude/projects/CorvinOS/memory/ADR-INDEX.md
```

---

## Maintenance

### Updating This System

When changes are needed:

1. **Hook changes** → Update `.git/hooks/pre-commit` and test
2. **CI changes** → Update `.github/workflows/adr-sync-check.yml`
3. **Parser changes** → Update `operator/scripts/sync_memory_from_adrs.py`
4. **Policy changes** → Update `CLAUDE.md` and `CONTRIBUTING.md`

All changes should flow through PR with CI validation.

### Cron Job (Future)

To add nightly sync (currently post-merge only):

```bash
# ~/.crontab entry (example)
0 2 * * * cd /path/to/CorvinOS && python3 operator/scripts/sync_memory_from_adrs.py --commit
```

---

## References

- **Policy:** `CLAUDE.md` § Code/Docs Sync
- **Developer Guide:** `CONTRIBUTING.md` § ADR Requirements
- **ADR Standard:** `docs/claude-ref/adr-gate.md`
- **ADR Examples:** `Corvin-ADR/decisions/` (all files)

---

**Last Updated:** 2026-08-12  
**Enforced Since:** 2026-08-12 (Phase 5 Resolution)
