# Skill Distribution Guide (ADR-0674)

**Version:** 1.0.0  
**Last Updated:** 2026-09-17  
**Status:** PRODUCTION

This guide covers how to package, distribute, and install Skills using CorvinOS Skill Forge v2.0.

## Quick Start

### Package a Skill

Package a generated Skill into a distributable ZIP:

```bash
# Via HTTP API
curl -X POST http://localhost:8765/v1/skill-forge/package \
  -d "skill_id=my_awesome_skill" \
  -d "skill_path=~/.corvin/skills_gen/my_awesome_skill"

# Response
{
  "zip_url": "/v1/skill-forge/download/my_awesome_skill_1.0.0.zip",
  "zip_hash": "sha256:xyz789...",
  "metadata": {
    "skill_id": "my_awesome_skill",
    "version": "1.0.0",
    "packaged_at": "2026-09-17T12:34:56Z",
    "checksum_count": 42,
    "audit_trail_events": 8
  }
}
```

### Download a Packaged Skill

Download a Skill package for distribution:

```bash
curl -O http://localhost:8765/v1/skill-forge/download/my_awesome_skill_1.0.0.zip
```

The response includes the package hash:

```
X-Skill-Hash: sha256:xyz789...
```

### Install a Skill

Install a Skill from a ZIP file:

```bash
# From local file
curl -X POST http://localhost:8765/v1/skill-forge/install \
  -d "zip_path=/path/to/my_awesome_skill_1.0.0.zip"

# From URL
curl -X POST http://localhost:8765/v1/skill-forge/install \
  -d "url=https://marketplace.corvin.ai/skills/my_awesome_skill_1.0.0.zip"

# Response
{
  "installed_path": "/home/user/.corvin/skills/custom/my_awesome_skill",
  "skill_id": "my_awesome_skill",
  "version": "1.0.0",
  "status": "success",
  "errors": []
}
```

### List Installed Skills

View all installed Skills:

```bash
curl http://localhost:8765/v1/skill-forge/installed

# Response
{
  "skills": [
    {
      "skill_id": "my_awesome_skill",
      "version": "1.0.0",
      "installed_at": "2026-09-17T12:34:56Z",
      "path": "/home/user/.corvin/skills/custom/my_awesome_skill",
      "status": "healthy",
      "boot_layer": "installed"
    }
  ]
}
```

## Package Structure

A packaged Skill ZIP has the following structure:

```
my_awesome_skill_1.0.0.zip
│
└── my_awesome_skill/
    ├── skill.json                    # Manifest (required)
    ├── README.md                     # Documentation
    ├── src/
    │   ├── __init__.py
    │   ├── skill.py                  # Skill implementation
    │   └── validators.py
    ├── hooks/
    │   ├── on_load.py                # Lifecycle hooks
    │   ├── on_execute.py
    │   ├── on_feedback.py
    │   └── on_unload.py
    ├── tests/
    │   ├── test_skill.py
    │   ├── test_edge_cases.py
    │   └── conftest.py
    ├── scripts/
    │   ├── install.py                # Dependency installation
    │   ├── test_runner.py
    │   ├── packager.py
    │   └── integrator.py
    ├── docs/
    │   ├── README.md                 # Additional docs
    │   ├── API.md
    │   └── EXAMPLES.md
    ├── references/
    │   ├── dependencies.txt          # Python dependencies
    │   ├── system_requirements.md
    │   └── external_data.md
    │
    └── .forge/                       # Forge metadata
        ├── generation_context.json   # Generation record
        ├── audit_trail.jsonl         # Generation events
        └── checksum.sha256           # File hashes
```

### Metadata Files

#### generation_context.json

Records when and how the Skill was generated:

```json
{
  "generated_at": "2026-09-17T12:34:56Z",
  "generated_by": "skill-forge-v2.0",
  "generator_version": "2.0.0",
  "user_prompt": "A skill that routes requests to the best LLM",
  "domain": "routing",
  "phases_completed": [
    "phase1_skeleton_generated",
    "phase2_llm_code_generated",
    "phase3_packaging_complete"
  ],
  "manifest_hash": "sha256:abc123..."
}
```

#### audit_trail.jsonl

Append-only log of generation events (one JSON per line):

```jsonl
{"timestamp":"2026-09-17T12:34:56Z","event":"scaffold_created","phase":1}
{"timestamp":"2026-09-17T12:35:01Z","event":"code_generated","tokens_used":2048}
{"timestamp":"2026-09-17T12:35:15Z","event":"tests_generated","coverage":87.5}
{"timestamp":"2026-09-17T12:35:25Z","event":"package_created","zip_hash":"sha256:xyz789..."}
```

#### checksum.sha256

SHA256 hashes for integrity verification:

```
sha256:abc123... my_awesome_skill/src/skill.py
sha256:def456... my_awesome_skill/tests/test_skill.py
sha256:ghi789... my_awesome_skill/skill.json
...
```

## Installation Workflow

### Atomic Installation

Installation is **atomic**: either fully succeeds or fully fails. No partial states.

1. **Verify ZIP integrity** (optional): Compute checksums against manifest
2. **Extract to temp directory**: Avoid corrupting installed versions
3. **Validate manifest**: Ensure skill.json is valid
4. **Check dependencies**: Verify required Skills are installed
5. **Move to final location**: Atomic rename (temp → ~/.corvin/skills/custom/)
6. **Install dependencies**: Run scripts/install.py
7. **Register in registry**: Add to ~/.corvin/.registry/installed.json
8. **Emit audit event**: Log skill_installed to audit trail

### Backup & Rollback

If a Skill is already installed, the previous version is automatically backed up:

```bash
# Original installation
~/.corvin/skills/custom/my_awesome_skill/  # v1.0.0

# After installing v2.0.0
~/.corvin/skills/custom/my_awesome_skill/              # v2.0.0
~/.corvin/skills/custom/my_awesome_skill.backup.1.0.0/ # v1.0.0 (backup)
```

To rollback:

```bash
curl -X POST http://localhost:8765/v1/skill-forge/rollback \
  -d "skill_id=my_awesome_skill" \
  -d "version=1.0.0"
```

## Verification

### Checksum Verification

Before installation, verify package integrity:

```bash
# Download package
curl -O http://localhost:8765/v1/skill-forge/download/my_awesome_skill_1.0.0.zip

# Extract and verify (package includes checksums in .forge/checksum.sha256)
unzip my_awesome_skill_1.0.0.zip
cd my_awesome_skill
sha256sum -c .forge/checksum.sha256

# Output: "file.py: OK" for each file
```

### Installation Status

Check health of installed Skills:

```bash
curl http://localhost:8765/v1/skill-forge/installed

# Look for "status": "healthy" (path exists and is readable)
# "status": "unhealthy" means installation is corrupted or missing
```

### Package Metadata

Retrieve generation context and audit trail:

```bash
curl http://localhost:8765/v1/skill-forge/packages/my_awesome_skill/1.0.0/metadata

# Response includes:
# - generation_context.json (when/where/how it was generated)
# - audit_trail.jsonl (all generation events)
# - checksums (file hashes)
```

## Dependency Installation

### Automatic Dependency Installation

When a Skill is installed, CorvinOS automatically runs dependency installation:

1. **Python dependencies** (if `references/dependencies.txt` exists):
   ```bash
   pip install -r ~/.corvin/skills/custom/my_awesome_skill/references/dependencies.txt
   ```

2. **Custom installation script** (if `scripts/install.py` exists):
   ```bash
   python ~/.corvin/skills/custom/my_awesome_skill/scripts/install.py
   ```

### Manual Dependency Installation

If automatic installation fails, install manually:

```bash
# Extract the Skill
unzip my_awesome_skill_1.0.0.zip -d ~/.corvin/skills/custom/

# Install dependencies
cd ~/.corvin/skills/custom/my_awesome_skill
pip install -r references/dependencies.txt
python scripts/install.py

# Run tests
pytest tests/ -v
```

## Distribution via Marketplace

### Publishing a Skill

To publish a packaged Skill to the marketplace:

1. **Package the Skill**:
   ```bash
   curl -X POST http://localhost:8765/v1/skill-forge/package \
     -d "skill_id=my_awesome_skill"
   ```

2. **Upload to marketplace**:
   ```bash
   curl -X POST https://marketplace.corvin.ai/upload \
     -F "package=@my_awesome_skill_1.0.0.zip" \
     -H "Authorization: Bearer $MARKETPLACE_TOKEN"
   ```

3. **Verify in marketplace**:
   ```bash
   curl https://marketplace.corvin.ai/skills/my_awesome_skill/1.0.0
   ```

### Installing from Marketplace

Install directly from marketplace URL:

```bash
curl -X POST http://localhost:8765/v1/skill-forge/install \
  -d "url=https://marketplace.corvin.ai/skills/my_awesome_skill/1.0.0/download"
```

## Troubleshooting

### "Package not found" (404)

The ZIP file doesn't exist at the specified path. Check:

```bash
# Verify file exists
ls -lh /path/to/my_awesome_skill_1.0.0.zip

# Download from console
curl http://localhost:8765/v1/skill-forge/packages
```

### "Checksum verification failed"

Files were corrupted during download or storage. Re-download the package:

```bash
# Delete corrupted package
rm my_awesome_skill_1.0.0.zip

# Re-download
curl -O http://localhost:8765/v1/skill-forge/download/my_awesome_skill_1.0.0.zip

# Verify
sha256sum -c .forge/checksum.sha256
```

### "Dependency missing"

A required Skill is not installed. Check dependencies:

```bash
# View required dependencies
unzip -p my_awesome_skill_1.0.0.zip my_awesome_skill/skill.json | jq .dependencies

# Install missing dependencies
curl -X POST http://localhost:8765/v1/skill-forge/install \
  -d "zip_path=/path/to/dependency_skill.zip"
```

### "Installation incomplete" (partial status)

Some parts of installation failed (e.g., dependency installation), but the Skill is functional. Check errors:

```bash
curl -X POST http://localhost:8765/v1/skill-forge/install \
  -d "zip_path=/path/to/skill.zip" \
  | jq .errors
```

## Audit Trail

All packaging and installation operations are logged to the audit trail:

```bash
# View audit events for Skill operations
grep "skill_packaged\|skill_installed\|skill_downloaded" ~/.corvin/audit.jsonl

# Output:
# {"timestamp":"2026-09-17T12:34:56Z","event_type":"skill_packaged","skill_id":"my_awesome_skill",...}
# {"timestamp":"2026-09-17T12:35:00Z","event_type":"skill_downloaded","skill_id":"my_awesome_skill",...}
# {"timestamp":"2026-09-17T12:35:10Z","event_type":"skill_installed","skill_id":"my_awesome_skill",...}
```

## Security Considerations

- **Checksums**: Verify package integrity using SHA256 hashes
- **Atomic operations**: Installation is all-or-nothing (no partial states)
- **Backup & rollback**: Previous versions are automatically backed up
- **Audit trail**: All operations are logged and hash-chained
- **Access control**: Installation requires appropriate permissions
- **Dependency isolation**: Dependencies are installed per Skill directory

## API Reference

### POST /v1/skill-forge/package

Package a Skill folder into a ZIP archive.

**Parameters:**
- `skill_id` (string, required): Skill identifier
- `skill_path` (string, optional): Custom path to Skill folder

**Response:**
```json
{
  "zip_url": "/v1/skill-forge/download/...",
  "zip_hash": "sha256:...",
  "metadata": {...}
}
```

### GET /v1/skill-forge/packages

List all packaged Skills.

**Response:**
```json
{
  "packages": [
    {
      "filename": "my_awesome_skill_1.0.0.zip",
      "skill_id": "my_awesome_skill",
      "version": "1.0.0",
      "created_at": "2026-09-17T12:34:56Z",
      "size_bytes": 45678,
      "download_url": "..."
    }
  ]
}
```

### GET /v1/skill-forge/download/{filename}

Download a packaged Skill ZIP.

**Parameters:**
- `filename` (string, required): ZIP filename

**Response:** Binary ZIP file

### POST /v1/skill-forge/install

Install a Skill from ZIP or URL.

**Parameters:**
- `zip_path` (string, optional): Local path to ZIP
- `url` (string, optional): URL to download ZIP from
- `verify_checksum` (boolean, optional): Verify checksums (default: true)

**Response:**
```json
{
  "installed_path": "...",
  "skill_id": "my_awesome_skill",
  "version": "1.0.0",
  "status": "success|already_installed|partial",
  "errors": []
}
```

### GET /v1/skill-forge/installed

List all installed Skills.

**Response:**
```json
{
  "skills": [
    {
      "skill_id": "my_awesome_skill",
      "version": "1.0.0",
      "installed_at": "2026-09-17T12:34:56Z",
      "path": "...",
      "status": "healthy|unhealthy",
      "boot_layer": "installed"
    }
  ]
}
```

### GET /v1/skill-forge/packages/{skill_id}/{version}/metadata

Get metadata for a packaged Skill.

**Response:**
```json
{
  "generation_context": {...},
  "audit_trail": [...],
  "checksums": {...}
}
```

## References

- ADR-0674: Skill Package & ZIP Distribution Format
- ADR-0672: Skill Forge v2.0 Generator Architecture
- ADR-0673: Skill Standard Folder Structure & Hook Contracts
- ADR-0511: Corvin Marketplace
