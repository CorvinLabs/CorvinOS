# Skill Marketplace Integration Architecture (ADR-0674)

**Version:** 1.0.0  
**Last Updated:** 2026-09-17  
**Status:** PRODUCTION  
**Related ADRs:** ADR-0511 (Marketplace), ADR-0672 (Skill Forge v2.0), ADR-0673 (Skill Folder Structure)

## Overview

This document describes how Skill Forge v2.0 integrates with the Corvin Marketplace using the ZIP packaging and distribution format defined in ADR-0674.

## Architecture Layers

### Layer 1: Skill Generation (Skill Forge v2.0)

Generates Skills in a standard folder structure with all required files:

```
skill_gen/
├── phase1_skeleton_generated/
├── phase2_code_generated/
└── Phase 3: Ready for packaging
```

**Output:** Complete Skill folder at `~/.corvin/skills_gen/{skill_id}/`

### Layer 2: Skill Packaging (ADR-0674)

Packages a generated Skill into a distributable ZIP:

```
SkillPackager.package(skill_folder, manifest)
  ├── Validate folder structure
  ├── Generate .forge/generation_context.json
  ├── Generate .forge/audit_trail.jsonl
  ├── Compute .forge/checksum.sha256
  ├── Create ZIP archive
  └── Return (zip_path, zip_hash, metadata)
```

**Output:** `~/.corvin/skills_packages/{skill_id}_{version}.zip`

### Layer 3: Distribution

Two distribution channels:

#### 3a. HTTP Download Endpoint (Local)

```
GET /v1/skill-forge/download/{filename}
  → FileResponse(path=zip_path, media_type="application/zip")
  → X-Skill-Hash: sha256:...
```

#### 3b. Marketplace Upload

```
POST https://marketplace.corvin.ai/upload
  ← (ZIP file)
  → Marketplace indexes Skill
  → GET https://marketplace.corvin.ai/skills/{skill_id}/{version}/download
```

### Layer 4: Installation

Atomic Skill installation from ZIP or URL:

```
SkillInstaller.install_skill(zip_path, verify_checksum=True)
  ├── Extract manifest from ZIP
  ├── Check if already installed
  ├── Verify checksums (optional)
  ├── Check dependencies
  ├── Extract to temp directory
  ├── Move to ~/.corvin/skills/custom/{skill_id}
  ├── Install dependencies
  ├── Register in ~/.corvin/.registry/installed.json
  └── Emit audit event
```

**Output:** Installed Skill at `~/.corvin/skills/custom/{skill_id}/`

### Layer 5: Registry & Discovery

Central registry tracks all installed Skills:

```
~/.corvin/.registry/installed.json
{
  "registry_version": "2.0.0",
  "last_updated": "2026-09-17T12:34:56Z",
  "installed_skills": [
    {
      "skill_id": "my_awesome_skill",
      "version": "1.0.0",
      "installed_at": "2026-09-17T12:34:56Z",
      "path": "/home/user/.corvin/skills/custom/my_awesome_skill",
      "status": "healthy",
      "boot_layer": "installed",
      "dependencies": [["dependent_skill", ">=0.9.0"]]
    }
  ]
}
```

**Discovery:** Registry is scanned at boot and on demand via GET /v1/skill-forge/installed

## Data Flow

### Packaging Flow

```
┌─────────────────────────────────┐
│  Skill Forge Phase 1-2 Output   │
│  ~/.corvin/skills_gen/{id}/     │
└────────────────┬────────────────┘
                 │
                 v
┌─────────────────────────────────┐
│   SkillPackager.package()       │
│  - Validate structure           │
│  - Generate metadata (.forge)   │
│  - Compute checksums           │
│  - Create ZIP                  │
└────────────────┬────────────────┘
                 │
                 v
┌─────────────────────────────────┐
│  Packaged Skill ZIP             │
│  ~/.corvin/skills_packages/     │
│  {id}_{version}.zip            │
└─────────────────────────────────┘
```

### Distribution Flow

```
┌─────────────────────────────────┐
│  Packaged Skill ZIP             │
│  ~/.corvin/skills_packages/     │
└────────────────┬────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
        v                 v
┌──────────────┐    ┌──────────────────────┐
│   HTTP API   │    │  Marketplace Upload  │
│  /download   │    │  POST /upload        │
└──────┬───────┘    └──────┬───────────────┘
       │                   │
       └───────┬───────────┘
               v
     ┌─────────────────────┐
     │  Downloaded/Shared  │
     │  by Users/Systems   │
     └────────────┬────────┘
                  │
                  v
      ┌──────────────────────┐
      │  SkillInstaller.     │
      │  install_skill()     │
      └────────────┬─────────┘
                   │
                   v
      ┌──────────────────────┐
      │  Installed Skill     │
      │  ~/.corvin/skills/   │
      │  custom/{id}/        │
      └──────────────────────┘
```

### Installation Flow

```
ZIP Package
    │
    ├─ Extract manifest
    │   └─ Get skill_id, version, dependencies
    │
    ├─ Check if already installed
    │   └─ If yes, create backup
    │
    ├─ Verify checksums (optional)
    │   └─ Hash each file against .forge/checksum.sha256
    │
    ├─ Check dependencies
    │   └─ Verify all dependent Skills are installed
    │
    ├─ Extract to temp (atomic)
    │   └─ Isolated from live installations
    │
    ├─ Move to final location
    │   └─ Atomic rename (temp → ~/.corvin/skills/custom/{id}/)
    │
    ├─ Install dependencies
    │   ├─ Run scripts/install.py (if exists)
    │   └─ pip install -r references/dependencies.txt (if exists)
    │
    ├─ Register in registry
    │   └─ Add entry to ~/.corvin/.registry/installed.json
    │
    ├─ Emit audit event
    │   └─ skill_installed → audit chain
    │
    └─ Return success (with errors if any non-fatal failures)
```

## Key Components

### SkillPackager

**File:** `core/skills/skill_packager.py`

```python
class SkillPackager:
    def __init__(self, output_dir: Optional[Path] = None)
    def package(self, skill_folder: Path, manifest: SkillManifestV2) -> Tuple[Path, str, Dict]
    def verify_package(self, zip_path: Path) -> bool
    
    # Private methods
    def _validate_skill_folder(self, skill_folder: Path, manifest: SkillManifestV2) -> None
    def _create_generation_context(self, skill_folder: Path, manifest: SkillManifestV2) -> Dict
    def _create_audit_trail(self, skill_folder: Path, manifest: SkillManifestV2) -> List[Dict]
    def _compute_checksums(self, skill_folder: Path) -> Dict[str, str]
    def _compute_file_hash(file_path: Path) -> str
    def _add_folder_to_zip(folder: Path, zf: zipfile.ZipFile, arcname: Path) -> None
```

**Key Features:**
- Validates Skill folder structure
- Generates immutable metadata (generation context + audit trail)
- Computes SHA256 checksums for all files
- Creates ZIP with deflate compression
- Returns (zip_path, zip_hash, metadata)

### SkillInstaller

**File:** `core/skills/skill_installer.py`

```python
class SkillInstaller:
    def __init__(self, corvin_home_path: Optional[str] = None, audit_backend=None)
    async def install_skill(self, package_path: str, verify_checksum: bool = True) -> Dict
    
    # Private methods
    async def _extract_manifest(self, package_path: Path) -> SkillManifest
    async def _verify_checksum(self, package_path: Path) -> None
    async def _check_dependencies(self, manifest: SkillManifest) -> None
    async def _install_dependencies(self, skill_dir: Path, manifest: SkillManifest) -> None
    async def _unzip_package(self, package_path: Path, extract_to: Path) -> None
    async def _load_registry(self) -> dict
    async def _update_registry(self, record: InstalledSkillRecord) -> None
    def _emit_audit_event(self, event: Dict[str, Any]) -> None
```

**Key Features:**
- Atomic extraction (temp → final)
- Checksum verification
- Dependency resolution
- Automatic backup of previous versions
- Registry management
- Audit logging

### Distribution Routes

**File:** `core/console/corvin_console/routes/skill_forge_distribution_routes.py`

```python
# Packaging endpoints
POST   /v1/skill-forge/package              # Package a Skill
GET    /v1/skill-forge/packages             # List packaged Skills
GET    /v1/skill-forge/download/{filename}  # Download a package

# Installation endpoints
POST   /v1/skill-forge/install              # Install from ZIP or URL
GET    /v1/skill-forge/installed            # List installed Skills

# Metadata endpoints
GET    /v1/skill-forge/packages/{id}/{ver}/metadata  # Get package metadata
```

## Integration Points

### With Skill Forge v2.0 (ADR-0672)

- **Input:** Phase 1-2 generated Skill folder
- **Output:** Packaged ZIP ready for distribution
- **Format:** SkillManifestV2 (skill.json)

### With Marketplace (ADR-0511)

- **Upload:** Package ZIP to marketplace
- **Download:** Install from marketplace URL
- **Metadata:** Generate context + audit trail used for discovery

### With Learning Infrastructure (ADR-0314)

- **Feedback:** Installed Skill receives feedback events
- **Optimization:** Skill learns from feedback
- **Versioning:** Each version has independent learning state

### With Audit Trail (ADR-0232)

- **Events:** skill_packaged, skill_downloaded, skill_installed
- **Hash-chaining:** All events are hash-linked
- **Immutability:** ZIP checksums verify content wasn't tampered

## Security & Compliance

### Checksum Verification

Every file in a Skill is hashed with SHA256:

```
.forge/checksum.sha256:
sha256:abc123... src/skill.py
sha256:def456... tests/test_skill.py
sha256:ghi789... skill.json
...
```

Installer verifies all files before installation:

```python
with zipfile.ZipFile(zip_path) as zf:
    for arcname in zf.namelist():
        file_data = zf.read(arcname)
        computed = sha256(file_data)
        assert computed == checksums[arcname]  # Must match
```

### Atomic Installation

Installation is all-or-nothing:

```python
# Extract to temp first
with tempfile.TemporaryDirectory() as temp:
    zf.extractall(temp)
    try:
        # Validate...
        # Move to final location (atomic)
        shutil.move(temp_path, final_path)
    except:
        # On error, nothing changed on disk
        raise
```

### Dependency Isolation

Each installed Skill has its own dependencies directory:

```
~/.corvin/skills/custom/my_awesome_skill/
├── src/
├── tests/
└── references/dependencies.txt  # Installed here, not globally
```

Dependencies are installed with `pip -r references/dependencies.txt --target`, not globally.

### Audit Trail

All operations logged:

```python
_emit_audit_event({
    "event_type": "skill_installed",
    "skill_id": "my_awesome_skill",
    "version": "1.0.0",
    "source_zip": "/path/to/zip",
    "install_path": "/home/user/.corvin/skills/custom/...",
    "lom": "core.skills.skill_installer:SkillInstaller.install_skill:L95"
})
```

Events are hash-chained in `~/.corvin/audit.jsonl`.

## Rollback Strategy

### Version Backup

When installing a new version, the old one is backed up:

```
Installation of v2.0.0:
  ~/.corvin/skills/custom/my_awesome_skill/              (NEW: v2.0.0)
  ~/.corvin/skills/custom/my_awesome_skill.backup.1.0.0/ (OLD: v1.0.0)
```

### Rollback

Restore previous version:

```python
installer.rollback_skill("my_awesome_skill", "1.0.0")
```

This:
1. Unloads current Skill (v2.0.0)
2. Swaps directories (v2.0.0 ← → v1.0.0 backup)
3. Reloads previous version
4. Emits audit event

## Performance Considerations

### ZIP Compression

Skills are compressed with ZIP_DEFLATED:

- Typical Skill: 5-10 MB (uncompressed) → 1-3 MB (compressed)
- Large Skill (with models): 50-100 MB (uncompressed) → 20-40 MB (compressed)
- Network transfer: ~5-30 seconds at 1 Mbps

### Installation Time

- Extract: ~1-2 seconds (depends on file count)
- Checksum verify: ~2-5 seconds (all files)
- Dependency install: ~10-60 seconds (depends on dependencies)
- **Total:** ~15-65 seconds typical

### Registry Lookup

- Registry size: ~1 KB per installed Skill
- Lookup time: O(n) where n = number of installed Skills
- Caching: Optional in-memory cache

## Testing Strategy

### Unit Tests

- Package creation + validation
- Checksum computation + verification
- Metadata generation
- Registry management

### E2E Tests

- Download → Install → List → Verify workflow
- Backup + rollback scenarios
- Corrupted ZIP handling
- Missing dependency detection

### Adversarial Tests

- Symlink attacks
- Path traversal attempts
- Large file handling
- Special characters in filenames
- Corrupted checksums

## Monitoring & Observability

### Audit Events

Track all distribution operations:

```
skill_packaged         → Skill converted to ZIP
skill_downloaded       → ZIP downloaded from endpoint
skill_installed        → ZIP extracted and installed
skill_install_failed   → Installation error
skill_install_skipped  → Already installed
```

### Metrics

Collect for observability:

- Package size distribution
- Installation success rate
- Installation duration (p50, p95, p99)
- Registry size growth
- Backup/rollback frequency

### Logs

Structured logging at each step:

```
INFO: Packaged Skill: my_awesome_skill v1.0.0 → /home/user/.corvin/skills_packages/my_awesome_skill_1.0.0.zip
INFO: Verified 42 checksums in package
INFO: Skill installation completed: my_awesome_skill v1.0.0 (success)
```

## Future Enhancements

1. **Differential packaging:** Only package changed files (delta updates)
2. **Compression options:** Let users choose compression level
3. **Signing:** GPG sign packages for authenticity
4. **Incremental installation:** Merge new version with existing (non-atomic)
5. **Analytics:** Track popular Skills, version adoption rates

## References

- ADR-0674: Skill Package & ZIP Distribution Format
- ADR-0672: Skill Forge v2.0 Generator Architecture
- ADR-0673: Skill Standard Folder Structure & Hook Contracts
- ADR-0511: Corvin Marketplace
- ADR-0314: Learning Infrastructure
- ADR-0232: Audit Chain & Security
