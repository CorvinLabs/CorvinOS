# Installation Guide: Corvin-Knowledge Claude Code Plugin

Complete guide for installing, configuring, and using the Corvin-Knowledge plugin in Claude Code.

---

## Prerequisites

- **Claude Code** (latest version)
- **Python 3.9+**
- **Git 2.25+** (for repository operations)

### Verify Prerequisites

```bash
# Check Claude Code
claude code --version

# Check Python
python3 --version

# Check Git
git --version
```

---

## Installation Methods

### Method 1: GitHub Release (Recommended)

Download and install the latest plugin release:

```bash
# Download from GitHub Releases
wget https://github.com/CorvinLabs/Corvin-Knowledge/releases/download/v1.0.0/corvin-knowledge-plugin.zip

# Install in Claude Code
claude code --install-plugin ./corvin-knowledge-plugin.zip

# Verify installation
claude code --list-plugins | grep corvin-knowledge
```

### Method 2: Clone + Install Locally

```bash
# Clone the repository
git clone https://github.com/CorvinLabs/Corvin-Knowledge.git
cd Corvin-Knowledge/sync

# Install plugin
claude code --install-plugin .

# Verify
claude code --list-plugins | grep corvin-knowledge
```

### Method 3: From PyPI (Future)

```bash
# Install via pip (when available)
pip install corvin-knowledge-plugin

# Then register with Claude Code
claude code --install-plugin ~/.local/lib/python3.11/site-packages/corvin_knowledge_plugin
```

---

## Configuration

### Initial Setup (One-Time)

After installation, configure the plugin:

```bash
# Use default (Corvin-Knowledge canonical repo)
mesh config
# This creates ~/.claude/plugins/corvin-knowledge.json

# Verify configuration
cat ~/.claude/plugins/corvin-knowledge.json
```

### Configuration for Teams

If your team has a custom knowledge repository:

```bash
# Configure for your team's repo
mesh config \
  --repo-path ~/my-team-knowledge \
  --remote-url https://github.com/my-org/knowledge.git

# Verify
cat ~/.claude/plugins/corvin-knowledge.json
```

### Configuration File Reference

The plugin stores configuration in `~/.claude/plugins/corvin-knowledge.json`:

```json
{
  "repo_path": "~/.corvin-knowledge/",
  "remote_url": "https://github.com/CorvinLabs/Corvin-Knowledge.git",
  "auto_sync_on_query": true,
  "consistency_level": "warn"
}
```

**Fields:**
- `repo_path`: Local directory where knowledge repo is stored
- `remote_url`: Git remote URL (GitHub, GitLab, etc.)
- `auto_sync_on_query`: Whether to pull latest before queries (default: true)
- `consistency_level`: Error handling (strict/warn/ignore, default: warn)

---

## First Use

### Step 1: Verify Plugin is Working

```bash
# Run a simple query
mesh query

# Should return: List of ADRs/Concepts from the knowledge base
```

### Step 2: Explore Available Commands

```bash
# List all available commands
mesh --help

# Get help for a specific command
mesh query --help
mesh sync --help
mesh propose --help
mesh config --help
```

### Step 3: Query Your Knowledge

```bash
# Find skills
mesh query --tag=skills

# Find accepted decisions
mesh query --status=accepted

# Find your project's documentation
mesh query --project=MyProject
```

---

## Common Tasks

### Sync with Remote

```bash
# Pull latest changes from remote
mesh sync --pull

# Pull + push (requires write access)
mesh sync --pull --push

# Pull with strict validation (fail on errors)
mesh sync --pull --consistency=strict
```

### Propose a New Idea

```bash
# Create a new idea file
cat > my-idea.md << 'EOF'
# Proposal: Better Skill Composition

## Problem
Current skill composition is rigid.

## Solution
Introduce declarative skill DAGs.
EOF

# Propose it (creates GitHub PR)
mesh propose \
  --type=idea \
  --title="Better Skill Composition" \
  --body="$(cat my-idea.md)" \
  --project=CorvinOS

# Output will show the GitHub PR URL for review
```

### Search Knowledge

```bash
# By tag
mesh query --tag=compliance

# By project
mesh query --project=CorvinOS

# By status
mesh query --status=accepted

# Combine filters
mesh query --tag=security --status=accepted --project=CorvinOS
```

---

## Troubleshooting

### Issue: Plugin Not Found

**Symptom:** `mesh: command not found`

**Solution:**
```bash
# Verify installation
claude code --list-plugins | grep corvin-knowledge

# If missing, reinstall
claude code --install-plugin https://github.com/CorvinLabs/Corvin-Knowledge/releases/download/v1.0.0/corvin-knowledge-plugin.zip

# Restart Claude Code
```

### Issue: Query Returns No Results

**Symptom:** `count: 0, entities: []`

**Solution:**
```bash
# Check configuration
cat ~/.claude/plugins/corvin-knowledge.json

# Verify repository exists and has data
ls -la ~/.corvin-knowledge/graph/entities.jsonl

# Force sync to clone/update repository
mesh sync --pull

# Try query again
mesh query
```

### Issue: Git Clone Fails

**Symptom:** `Git clone failed: ...`

**Causes:**
- Git not installed
- Network unreachable
- Invalid `remote_url` configuration

**Solution:**
```bash
# Verify Git is installed
git --version

# Test network connectivity
ping github.com

# Verify remote URL
mesh config --remote-url https://github.com/CorvinLabs/Corvin-Knowledge.git

# Try sync again
mesh sync --pull
```

### Issue: Sync Blocked by Consistency Errors

**Symptom:** `status: validation_failed, errors: [...]`

**Solution:**
```bash
# See warnings/errors (don't fail)
mesh sync --consistency=warn

# Or ignore validation (development only)
mesh sync --consistency=ignore

# For strict mode, fix errors manually
# (Edit conflicting entities in the repo)
```

### Issue: Merge Conflict on Sync

**Symptom:** `status: conflict`

**Solution:**
```bash
# Resolve manually via Git
cd ~/.corvin-knowledge
git status  # See conflicted files
# Edit conflicted entities
git add .
git commit -m "Resolve merge conflict"

# Retry sync
mesh sync --push
```

---

## Updating the Plugin

### Check Version

```bash
mesh --version  # Shows plugin version
```

### Update to Latest

```bash
# Download new version
wget https://github.com/CorvinLabs/Corvin-Knowledge/releases/download/latest/corvin-knowledge-plugin.zip

# Reinstall
claude code --uninstall-plugin corvin-knowledge
claude code --install-plugin ./corvin-knowledge-plugin.zip
```

---

## Uninstalling

```bash
# Remove plugin
claude code --uninstall-plugin corvin-knowledge

# Clean up configuration (optional)
rm ~/.claude/plugins/corvin-knowledge.json

# Clean up local repository (optional)
rm -rf ~/.corvin-knowledge
```

---

## Advanced Configuration

### Custom Knowledge Repository

If you want to run your own knowledge repository:

```bash
# 1. Create a new Git repository
mkdir my-team-knowledge
cd my-team-knowledge
git init

# 2. Create graph directory structure
mkdir -p graph
touch graph/entities.jsonl
touch graph/relations.jsonl
echo '{"version": "1.0.0"}' > graph/graph-meta.json

# 3. Commit
git add .
git commit -m "Initial knowledge repository"

# 4. Push to GitHub (create repo first on GitHub)
git remote add origin https://github.com/my-org/knowledge.git
git push -u origin main

# 5. Configure plugin
mesh config \
  --repo-path ~/my-team-knowledge \
  --remote-url https://github.com/my-org/knowledge.git

# 6. Verify
mesh query
```

### Offline Use

The plugin supports offline operation (after initial sync):

```bash
# 1. Sync once to clone repository
mesh sync --pull

# 2. Go offline (disable network)
# Network not required for queries after this point

# 3. Query works offline
mesh query --tag=skills

# 4. When back online, sync again
mesh sync --pull --push
```

---

## Support & Issues

- **Bug Reports** — https://github.com/CorvinLabs/Corvin-Knowledge/issues
- **Discussions** — https://github.com/CorvinLabs/Corvin-Knowledge/discussions
- **Documentation** — https://github.com/CorvinLabs/Corvin-Knowledge/blob/main/README.md

---

## Security Considerations

- **Credentials**: Plugin stores config in `~/.claude/plugins/` (same as Claude Code)
- **Git Access**: Remote repository access uses SSH or HTTPS (depends on Git config)
- **Data**: Knowledge entities stored locally in `~/.corvin-knowledge/`
- **Validation**: PII detection runs on every sync (email, SSN, API keys blocked)

---

## Performance

### Repository Size

- Typical repository: ~50 MB (1000+ entities + full Git history)
- Sync time: ~2-5 seconds (full pull/merge/rebuild)
- Query time: <100ms (JSON Lines scan)

### Optimization Tips

```bash
# Shallow clone for faster initial sync (saves ~80% bandwidth)
# (Not yet implemented, consider for future)

# Selective sync (sync specific projects only)
# (Not yet implemented, consider for future)
```

---

## Roadmap

Planned features for future versions:

- [ ] Shallow clone support
- [ ] Selective project sync
- [ ] Built-in merge conflict resolution
- [ ] Web-based dashboard
- [ ] API endpoint for third-party tools
- [ ] Real-time collaboration (websocket sync)

---

## Contributing

Want to improve the plugin? Contributions welcome!

```bash
# Fork repository
# Create feature branch
# Make changes
# Run tests: pytest tests/
# Submit PR
```

---

**Last Updated:** 2026-09-18  
**Version:** 1.0.0  
**License:** Apache-2.0
