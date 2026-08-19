# Config-Migration Playbook — Tenant-Native Skills v0.3

**Status:** Pre-Implementation (Phase 0.1)  
**Owner:** Operator + Platform Team  
**When:** Before Phase 1 starts  

---

## Overview

CorvinOS ist transitioning von **global Skills in `~/.claude/skills/`** zu **Tenant-scoped Skills in `~/.corvin/tenants/_default/_shared/`**.

Dieser Playbook dokumentiert **exakt was migriert wird**, **wie Rollback funktioniert**, und **was der Operator vor und nach tun muss**.

**Golden Rule:** Zero data loss. Operator kann jederzeit rollback.

---

## PRE-MIGRATION CHECKLIST (Operator führt das aus)

Bevor du Phase 1 startest, mache diese Schritte:

### **1. Backup erstellen** (5 min)

```bash
# Backup alles was wichtig ist
tar -czf ~/Desktop/corvinOS_backup_$(date +%Y%m%d).tar.gz \
  ~/.claude/skills/ \
  ~/.claude/settings.json \
  ~/.corvin/tenants/_default/ \
  ~/.corvin/audit.jsonl

# Verifiziere Backup
tar -tzf ~/Desktop/corvinOS_backup_*.tar.gz | head -20
echo "✅ Backup created and verified"
```

### **2. Inventur machen** (10 min)

```bash
# Zähle Skills in ~/.claude/
find ~/.claude/skills/ -maxdepth 1 -type d -not -name "skills" | wc -l

# Zähle Configs
ls -la ~/.claude/settings*.json

# Zähle alte Tenants
ls -la ~/.corvin/tenants/
```

**Dokumentiere:**
- Anzahl Skills in `.claude/skills/`
- Custom settings in `.claude/settings.json`
- Andere Tenants außer `_default`?

### **3. Abhängigkeiten prüfen** (5 min)

```bash
# Prüfe ob alte Skills Dependencies aufeinander haben
# (Das sind die wichtigen Skills zu behalten!)
grep -r "dependencies" ~/.claude/skills/*/meta.json 2>/dev/null || echo "No dependencies found"
```

---

## WHAT GETS MIGRATED

### **✅ This gets migrated to `_shared/`:**

| Source | Destination | Notes |
|--------|-------------|-------|
| `~/.claude/skills/*/` | `~/.corvin/tenants/_default/_shared/skills/` | Alle Skills |
| `~/.claude/skills/*/body.md` | `~/.corvin/tenants/_default/_shared/skills/{id}/body.md` | Skill-Text |
| `~/.claude/skills/*/tests/` | `~/.corvin/tenants/_default/_shared/skills/{id}/tests/` | Unit-Tests (if exist) |
| `~/.claude/skills/*/README.md` | `~/.corvin/tenants/_default/_shared/skills/{id}/README.md` | Docs (if exist) |

**New files generated during migration:**
| File | Notes |
|------|-------|
| `meta.json` | Generated per Skill (version, created, etc.) |
| `CHANGELOG.md` | Auto-generated (v1.0.0 — migrated from ~/.claude/) |

### **⚠️ This gets COPIED but marked as legacy:**

| Source | Destination | Notes |
|--------|-------------|-------|
| `~/.claude/settings.json` (skills section) | `~/.corvin/tenants/_default/config/tenant.corvin.yaml` | Config migriert, aber neu strukturiert |
| Skill-Preferences (falls existiert) | `~/.corvin/tenants/_default/config/skill-prefs.json` | Generiert basierend auf old settings |

### **❌ This does NOT get migrated (deprecated):**

| Item | Why | Recovery |
|------|-----|----------|
| `~/.claude/settings.json` (non-skill section) | Use new tenant config | Manual re-config in Console UI |
| Old worktrees in `~/.claude/worktrees/` | Use git worktrees in repo instead | Not needed in v0.3+ |
| Legacy plugin config | Plugins are re-registered | Check Console → Plugins after migration |

---

## MIGRATION PROCESS (Phase 1.1 Implementation)

### **Step 0: Validation (before any changes)**

```python
# corvinOS/core/skill_management/migration_validator.py
class MigrationValidator:
    def pre_migration_check(self) -> ValidationReport:
        """Check if migration is safe"""
        return {
            "skills_count": len(list_skills_in_claude()),
            "broken_skills": check_for_broken_skills(),
            "missing_dependencies": check_for_unresolvable_deps(),
            "config_issues": check_config_compatibility(),
            "warnings": [],
            "can_proceed": True/False
        }

# CLI
corvinOS skill migrate --dry-run
# Output: "Found 5 skills, 0 broken, 0 missing deps. Safe to proceed."
```

**Operator sees:**
```
Pre-migration validation:
✅ Found 5 skills in ~/.claude/skills/
✅ All skills have valid structure
✅ No circular dependencies
✅ No missing dependencies
✅ Safe to proceed with migration

Next: corvinOS skill migrate --confirm
```

### **Step 1: Create Backup (automatic)**

```python
# Migration automatically backs up ~/.claude/skills/
backup_path = f"~/.corvin/tenants/_default/backups/pre_migration_{timestamp}.tar.gz"
tar_all_skills_and_configs(backup_path)
audit_log("pre_migration_backup_created", backup_path)
```

### **Step 2: Create Tenant Structure (automatic)**

```python
# Create missing directories
mkdir -p ~/.corvin/tenants/_default/_shared/skills/
mkdir -p ~/.corvin/tenants/_default/_local/skills/
mkdir -p ~/.corvin/tenants/_default/_platform/
mkdir -p ~/.corvin/tenants/_default/config/
mkdir -p ~/.corvin/tenants/_default/exports/
```

### **Step 3: Migrate Skills (automatic)**

For each Skill in `~/.claude/skills/`:

```python
def migrate_single_skill(skill_dir: Path, tenant_id: str) -> MigrationResult:
    skill_id = skill_dir.name
    
    # Copy files
    copy_tree(skill_dir, 
              f"~/.corvin/tenants/{tenant_id}/_shared/skills/{skill_id}/")
    
    # Generate meta.json if missing
    if not (skill_dir / "meta.json").exists():
        meta = {
            "id": skill_id,
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "migration_from": "~/.claude/skills/",
            "dependencies": []  # Will be auto-detected later
        }
        write_json(
            f"~/.corvin/tenants/{tenant_id}/_shared/skills/{skill_id}/meta.json",
            meta
        )
    
    # Generate CHANGELOG.md
    changelog = f"""# Changelog — {skill_id}

## v1.0.0 (Migration)
- Migrated from ~/.claude/skills/ to tenant-scoped structure
- Original location: ~/.claude/skills/{skill_id}/
- Migration date: {datetime.now().isoformat()}
"""
    write_file(
        f"~/.corvin/tenants/{tenant_id}/_shared/skills/{skill_id}/CHANGELOG.md",
        changelog
    )
    
    # Log
    audit_log("skill_migrated", skill_id, 
        f"from ~/.claude/skills/{skill_id}/ to _shared/")
    
    return MigrationResult(skill_id=skill_id, status="success")
```

### **Step 4: Migrate Config (automatic)**

```python
def migrate_config(tenant_id: str) -> ConfigMigrationResult:
    # Load old config
    old_config = load_json(Path.home() / ".claude/settings.json")
    
    # Transform to new tenant config
    new_config = {
        "spec": {
            "skills": {
                "auto_cleanup_local": True,
                "cleanup_ttl_days": 90,
                "github_sync": {
                    "enabled": False,  # User enables manually
                    "repo": None,
                    "branch": "main"
                }
            },
            "telemetry": {
                "skills_tracking": True
            }
        }
    }
    
    # Write new config
    write_yaml(
        f"~/.corvin/tenants/{tenant_id}/config/tenant.corvin.yaml",
        new_config
    )
    
    # Migrate skill preferences
    skill_prefs = {
        "enabled_skills": get_enabled_skills_from_old_config(old_config),
        "disabled_skills": get_disabled_skills_from_old_config(old_config),
        "skill_aliases": {}
    }
    write_json(
        f"~/.corvin/tenants/{tenant_id}/config/skill-prefs.json",
        skill_prefs
    )
    
    audit_log("config_migrated", tenant_id, "to new tenant structure")
```

### **Step 5: Verify Migration (automatic)**

```python
def verify_migration(tenant_id: str) -> VerificationResult:
    errors = []
    warnings = []
    
    # Check all skills were copied
    old_count = count_skills(Path.home() / ".claude/skills/")
    new_count = count_skills(Path.home() / f".corvin/tenants/{tenant_id}/_shared/skills/")
    
    if old_count != new_count:
        errors.append(f"Skill count mismatch: {old_count} old vs {new_count} new")
    
    # Check no skills are missing dependencies
    for skill_id in list_skills(tenant_id, "_shared"):
        manifest = load_skill_manifest(skill_id, tenant_id)
        for dep in manifest.get("dependencies", []):
            if not skill_exists(dep["id"], tenant_id, dep["scope"]):
                warnings.append(f"{skill_id} depends on missing {dep['id']}")
    
    # Check audit trail
    if not audit_trail_has_migration_entries(tenant_id):
        errors.append("Audit trail incomplete")
    
    return VerificationResult(
        success=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        migrated_count=new_count,
        backup_location=backup_path
    )
```

**Operator sees:**
```
Migration verification:
✅ 5 skills migrated successfully
✅ All metadata generated
⚠️  Warning: skill_A depends on skill_B (version mismatch 1.0 vs 1.2)
✅ Audit trail complete
✅ Config migrated
✅ Backup at ~/.corvin/tenants/_default/backups/pre_migration_20260819.tar.gz

Status: ✅ MIGRATION SUCCESSFUL
```

---

## POST-MIGRATION CHECKLIST (Operator)

### **1. Verify Skills Work (5 min)**

```bash
# List migrated skills
corvinOS skill list --tenant _default --scope _shared

# Check a specific skill
corvinOS skill info academic-paper-generation --tenant _default

# Run a test to make sure it works
# (depends on your skill, but should still function)
```

### **2. Update GitHub-Sync Config (optional, 5 min)**

```bash
# If you want GitHub backups:
corvinOS skill-sync configure \
  --tenant _default \
  --repo github:shumway/dotcloud-backup \
  --branch main \
  --enable-sync

# First sync (optional)
corvinOS skill-sync --tenant _default --push --dry-run
```

### **3. Clean Up Old Files (optional, 5 min)**

```bash
# Only after verifying migration succeeded:
# Backup ~/.claude/skills/ to archive
tar -czf ~/Archive/claude_skills_legacy_$(date +%Y%m%d).tar.gz ~/.claude/skills/

# Or keep it as fallback for 30 days, then delete
# (your choice)
```

---

## ROLLBACK PROCEDURE (If Migration Fails)

**If anything goes wrong:**

```bash
# Step 1: Restore from backup
tar -xzf ~/.corvin/tenants/_default/backups/pre_migration_*.tar.gz -C ~/

# Step 2: Restore ~/.claude/skills/
cp -r ~/.corvin/tenants/_default/backups/skills/* ~/.claude/skills/

# Step 3: Remove partially-migrated _shared/ (if needed)
rm -rf ~/.corvin/tenants/_default/_shared/skills/

# Step 4: Contact support if issues persist
```

**Automatic rollback in migration (if validation fails):**

```python
def migrate_with_rollback(tenant_id: str) -> MigrationResult:
    try:
        # Pre-check
        validation = pre_migration_check()
        if not validation.can_proceed:
            raise MigrationValidationError(validation.warnings)
        
        # Create backup BEFORE any changes
        backup_path = create_backup(tenant_id)
        
        # Migrate
        results = migrate_all_skills(tenant_id)
        
        # Verify
        verification = verify_migration(tenant_id)
        if not verification.success:
            # Automatic rollback
            restore_from_backup(backup_path)
            raise MigrationVerificationError(verification.errors)
        
        return MigrationResult(success=True, backup_location=backup_path)
    
    except Exception as e:
        # Rollback automatically
        restore_from_backup(backup_path)
        audit_log("migration_failed_rollback", str(e))
        raise
```

---

## MIGRATION COMMANDS (CLI)

**Operator runs these:**

```bash
# Phase 0: Pre-check (no changes)
corvinOS skill migrate --dry-run --tenant _default
# Output: "5 skills ready to migrate. Run --confirm to proceed."

# Phase 0: Backup & Validate (creates backup, validates)
corvinOS skill migrate --validate --tenant _default
# Output: "Backup created. Validation passed. Safe to migrate."

# Phase 1: Execute Migration (actually migrates)
corvinOS skill migrate --confirm --tenant _default
# Output: "Migrating... ✅ 5 skills migrated successfully"

# Phase 2: Verify
corvinOS skill migrate --verify --tenant _default
# Output: "✅ Migration verified. All skills working."

# Rollback (if needed)
corvinOS skill migrate --rollback --tenant _default
# Output: "Rolled back to backup. All data restored."
```

---

## WHAT CHANGES FOR THE OPERATOR

### **Before (v0.2):**
- Skills in: `~/.claude/skills/{id}/body.md`
- Config in: `~/.claude/settings.json`
- Backup: Manual (scary!)
- GitHub: Not supported

### **After (v0.3):**
- Skills in: `~/.corvin/tenants/_default/_shared/skills/{id}/body.md`
- Config in: `~/.corvin/tenants/_default/config/tenant.corvin.yaml`
- Backup: Automatic + versioned
- GitHub: `corvinOS skill-sync --push`

### **Operator UX:**

| Action | Before | After |
|--------|--------|-------|
| List Skills | Edit folder by hand | `corvinOS skill list` |
| Add Skill | Copy to ~/.claude/skills/ | `corvinOS skill add` or UI |
| Enable/Disable | Edit settings.json | `corvinOS skill enable/disable` or UI |
| Backup | Manual zip | Automatic before migration |
| Export | Not supported | `corvinOS skill-sync --push` |
| Multiple Tenants | Not supported | Separate `_default/`, `work/`, etc. |

---

## COMMON ISSUES & FIXES

### **Q: What if migration fails halfway?**
A: Automatic rollback kicks in. Restore from backup if needed.

### **Q: What if I have custom skill dirs outside ~/.claude/skills/?**
A: Manual migration playbook provided. Contact support.

### **Q: What if I want to keep ~/.claude/skills/ as fallback?**
A: Keep the backup. You can restore anytime. But Phase 1 onwards, use tenant-scoped skills only.

### **Q: What about old worktrees in ~/.claude/worktrees/?**
A: They're deprecated. Use git worktrees in the repo instead.

### **Q: What if a skill breaks after migration?**
A: Compare `meta.json` (new) vs old structure. Usually missing dependency or version mismatch.

---

## TIMELINE

- **Week 0 Day 1:** Operator reads this playbook
- **Week 0 Day 1–2:** Operator runs `--dry-run` to validate
- **Week 0 Day 2:** Operator runs `--confirm` to execute migration
- **Week 0 Day 2–3:** Operator verifies skills work
- **Week 1+:** Phase 1 implementation begins

---

## SIGN-OFF

**Operator Confirmation Needed:**

```
☐ I have read and understand this playbook
☐ I have created a backup
☐ I have checked my skills inventory
☐ I am ready to proceed with migration
```

When ready, Operator says: **"Go ahead with Phase 1"**

