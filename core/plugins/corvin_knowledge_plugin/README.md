# Corvin-Knowledge Claude Code Plugin

**Agentic Knowledge Mesh for Distributed Teams**

A Claude Code plugin that brings Git-backed, distributed knowledge management to your agent workflows. Query knowledge bases, sync without data loss, and propose new ideas — all from within Claude Code.

---

## ✨ Features

- 🔍 **Query Knowledge** — Search ADRs, Concepts, Ideas by tag, project, or status
- 🔄 **Smart Sync** — Pull/push to Git remotes with conflict detection (fail-closed)
- 💡 **Propose** — Submit new entities via GitHub PR (no manual Git needed)
- ⚙️ **Configure** — Switch between Corvin-Knowledge canonical repo or custom team repos
- 🛡️ **Consistency** — Automatic validation (circular deps, PII, duplicates detected)

---

## 🚀 Quick Start

### 1. Install Plugin

```bash
# From GitHub Releases (recommended)
claude code --install-plugin https://github.com/CorvinLabs/Corvin-Knowledge/releases/download/v1.0.0/corvin-knowledge-plugin.zip

# Or: Clone + install locally
git clone https://github.com/CorvinLabs/Corvin-Knowledge.git
cd Corvin-Knowledge
claude code --install-plugin .
```

### 2. Configure (One-Time)

```bash
# Use canonical Corvin-Knowledge repo (default)
mesh config

# Or: Point to your team's custom repo
mesh config --repo-path ~/my-team-knowledge --remote-url https://github.com/my-org/knowledge.git
```

### 3. Query Knowledge

```bash
# Find all skills in CorvinOS
mesh query --tag=skills --project=CorvinOS

# Find accepted decisions
mesh query --status=accepted

# Find all architecture ADRs
mesh query --tag=architecture
```

### 4. Sync

```bash
# Pull latest from remote
mesh sync --pull

# Pull + push (requires write access to remote)
mesh sync --pull --push

# Strict mode (fails on any validation error)
mesh sync --pull --consistency=strict
```

### 5. Propose New Entity

```bash
# Propose a new idea
mesh propose --type=idea \
  --title="Enhanced Learning Loop" \
  --body="This idea improves feedback mechanisms..." \
  --project=CorvinOS

# Propose a concept
mesh propose --type=concept \
  --title="Three-Layer Testing Pattern" \
  --body="Reusable pattern for E2E validation..." \
  --project=CorvinOS
```

---

## 📦 Use Cases

### For Individual Agents
```python
# Inside a Claude Code session:
mesh query --tag=skills --status=accepted
# Returns: List of accepted skill ADRs for reference
```

### For Teams
```bash
# Team A clones repo
mesh config --repo-path ~/team-a-knowledge --remote-url https://github.com/teams/knowledge.git
mesh sync --pull

# Team B syncs independently
mesh config --repo-path ~/team-b-knowledge --remote-url https://github.com/teams/knowledge.git
mesh sync --pull

# Both teams' changes converge via Git (3-way merge)
mesh sync --pull --push
```

### For Proposing New Ideas
```bash
# Write your idea locally
cat > my-idea.md << 'EOF'
# New Concept: Autonomous Session Management

This proposes a pattern for managing session state across distributed teams.

## Problem
Teams duplicate session state across instances.

## Solution
Use a shared, Git-backed session registry with per-tenant isolation.
EOF

# Propose it (creates GitHub PR)
mesh propose --type=concept --title="Autonomous Session Management" --body="$(cat my-idea.md)" --project=CorvinOS
# Output: https://github.com/CorvinLabs/Corvin-Knowledge/pull/123
```

---

## ⚙️ Configuration

### Default Configuration (First Run)

```bash
mesh config
# Creates ~/.claude/plugins/corvin-knowledge.json with:
# - repo_path: ~/.corvin-knowledge
# - remote_url: https://github.com/CorvinLabs/Corvin-Knowledge.git
# - auto_sync_on_query: true (pull latest before queries)
# - consistency_level: warn (log errors but continue)
```

### Custom Configuration

```bash
# Use your organization's knowledge repo
mesh config \
  --repo-path ~/my-org-knowledge \
  --remote-url https://github.com/my-org/knowledge.git
```

### Configuration File Location

```
~/.claude/plugins/corvin-knowledge.json
```

Edit directly for advanced options:

```json
{
  "repo_path": "~/.corvin-knowledge",
  "remote_url": "https://github.com/CorvinLabs/Corvin-Knowledge.git",
  "auto_sync_on_query": true,
  "consistency_level": "warn"
}
```

---

## 🔒 Safety Guarantees

### Consistency Checks (Always On)

Every sync validates:

| Check | Error Level | Description |
|---|---|---|
| **Duplicate IDs** | ERROR | Two entities with same `id:` |
| **Circular Dependencies** | ERROR | Circular `depends_on` relationships |
| **Invalid Frontmatter** | ERROR | YAML parse errors |
| **Dangling Links** | WARN | References to non-existent entities |
| **PII Detection** | REJECT | Secrets/emails/SSNs in body (fail-closed) |
| **Checksum Mismatch** | WARN | File changed externally |

### Consistency Levels

```bash
# strict: Abort sync on any error
mesh sync --consistency=strict

# warn: Log errors but continue (default)
mesh sync --consistency=warn

# ignore: Skip validation (dev only)
mesh sync --consistency=ignore
```

### Conflict Resolution

When two teams edit the same entity:

```bash
mesh sync --pull
# Output: Merge conflict detected. Resolve manually via Git:
#   1. git status
#   2. Edit conflicted file
#   3. git add .
#   4. git commit
#   5. mesh sync --push
```

---

## 🐛 Troubleshooting

### Plugin Not Found After Install

```bash
# Verify installation
claude code --list-plugins | grep corvin-knowledge

# Reinstall if missing
claude code --uninstall-plugin corvin-knowledge
claude code --install-plugin https://github.com/CorvinLabs/Corvin-Knowledge/releases/download/v1.0.0/corvin-knowledge-plugin.zip
```

### Query Returns No Results

```bash
# Check configuration
cat ~/.claude/plugins/corvin-knowledge.json

# Verify repository exists
ls -la ~/.corvin-knowledge/graph/entities.jsonl

# Force sync to rebuild index
mesh sync --pull
```

### Sync Fails with "Git not found"

```bash
# Install Git
# macOS:
brew install git

# Ubuntu/Debian:
sudo apt-get install git

# Windows:
# Download from https://git-scm.com/download/win
```

### Consistency Check Blocks Sync

```bash
# Check errors
mesh sync --consistency=warn  # See warnings/errors

# If errors are acceptable, ignore them
mesh sync --consistency=ignore

# Or: Fix manually (e.g., remove duplicate entities)
# Then sync again
mesh sync --consistency=strict
```

---

## 📚 Architecture

### Three-Layer Design

1. **Layer 1: Manifest** (`manifest.json`)
   - Claude Code discovers plugin capabilities
   - Declares commands (query, sync, propose, config)

2. **Layer 2: Entry Point** (`plugin.py`)
   - Runtime invokes `execute(command, args, config)`
   - All commands run in-process (no subprocess)

3. **Layer 3: Consistency** (Validators)
   - Every sync runs consistency checks
   - Fail-closed: errors block sync (unless `consistency=ignore`)

### Data Flow

```
Claude Code
    ↓
mesh query [--tag=skills]
    ↓
plugin.py::execute("query", ...)
    ↓
KnowledgeMeshSDK::query()
    ↓
Read: ~/.corvin-knowledge/graph/entities.jsonl
    ↓
Return: [Entity, Entity, ...]
    ↓
Display in Claude Code
```

---

## 🤝 Contributing

To extend or contribute to the plugin:

1. **Fork** https://github.com/CorvinLabs/Corvin-Knowledge
2. **Edit** `sync/plugin.py` or `sync/manifest.json`
3. **Test** via `pytest tests/test_plugin.py`
4. **Submit PR** with description

---

## 📄 License

Apache-2.0 (same as Corvin-Knowledge)

---

## 🔗 Related

- **Corvin-Knowledge** — https://github.com/CorvinLabs/Corvin-Knowledge
- **ADR-MESH-002** — Plugin Contract & Distribution
- **ADR-0884** — CorvinOS Integration (this plugin)
- **CorvinOS Docs** — https://github.com/CorvinLabs/CorvinOS/docs

---

## 💬 Support

- **Issues** — https://github.com/CorvinLabs/Corvin-Knowledge/issues
- **Discussions** — https://github.com/CorvinLabs/Corvin-Knowledge/discussions
- **Docs** — `/home/shumway/projects/Corvin-Knowledge/README.md`
