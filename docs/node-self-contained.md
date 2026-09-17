# Self-Contained Node.js Setup

CorvinOS manages Node.js locally without system-wide dependencies.

## How It Works

- **Location:** `~/.corvin/node/` (local, user-writable)
- **Version:** specified in `.nvmrc` at repo root
- **No `sudo` required** — all operations within user home
- **Idempotent** — safe to run multiple times; skips if already installed
- **Platform support:** Linux (x64, arm64), macOS (x64, arm64), Windows (x64)

## Bootstrap Scripts

### Linux / macOS

**Phase 1a of `install.sh` automatically calls:**
```bash
bash scripts/ensure-node.sh    # Downloads + extracts Node.js to ~/.corvin/node/
bash scripts/ensure-npm.sh     # Configures npm with local cache
```

**Manual execution:**
```bash
# Install/verify local Node.js
bash scripts/ensure-node.sh

# Configure npm environment (sets PATH, npm cache)
source <(bash scripts/ensure-npm.sh)

# Use npm
npm ci       # (recommended for CI/reproducible installs)
npm install  # (general development)
```

### Windows

**Phase 1a of `install.ps1` automatically:**
1. Reads `.nvmrc`
2. Downloads `node-v<version>-win-x64.zip` from nodejs.org
3. Extracts to `%USERPROFILE%\.corvin\node\`
4. Updates `%PATH%` to prioritize local node
5. Proceeds with uv + CorvinOS install

**Manual execution (PowerShell):**
```powershell
# Set up local Node.js
$env:Path = "$env:USERPROFILE\.corvin\node\bin;$env:Path"
$env:npm_config_cache = "$env:USERPROFILE\.corvin\npm-cache"

# Use npm
npm ci
npm install
```

## Version Management

Node.js version is pinned in `.nvmrc`:
```
v24.18.0
```

**To update:**
1. Edit `.nvmrc` with new version (e.g., `v25.0.0`)
2. Delete `~/.corvin/node/` (or re-run installer)
3. Re-run `ensure-node.sh` or `install.sh`

## Cache Location

npm downloads are cached in `~/.corvin/npm-cache/` (user-local, not global).

**To clear cache:**
```bash
rm -rf ~/.corvin/npm-cache/*
```

Or via npm directly:
```bash
npm cache clean --force
```

## Fallback to System Node.js

If `ensure-node.sh` fails (e.g., disk full, no download access), the installer:
- Logs a warning
- Continues with system-wide Node.js (if available)
- Does not block the full CorvinOS install

This ensures the installer is **fail-graceful**, not fail-hard.

## Architecture

**uv Pattern (existing):**
```
uv (single static binary)
  → manages its own Python (no system Python needed)
  → runs `uv tool install corvinos`
  → installs CorvinOS to user home
```

**Node.js Pattern (new, mirrors uv):**
```
scripts/ensure-node.sh
  → downloads Node.js tarball/zip
  → extracts to ~/.corvin/node/ (idempotent)
  → no chmod -x needed (archive preserves executable bit)
  → exports PATH (node/bin first)
  → ~43 MB on disk (comparable to system npm)
```

**Benefits over system Node.js:**
- No `sudo` at any step
- Operator controls version (not system package manager)
- Multiple tenants can run different Node versions
- Offline installs possible (once tarball cached)
- Rollback simple: delete `~/.corvin/node/`

## Development Workflow

```bash
# Clone repo
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS

# Standard install (auto-bootstraps local Node.js)
bash install.sh --editable .

# Or manual steps:
bash scripts/ensure-node.sh
source <(bash scripts/ensure-npm.sh)
npm ci

# Local development (node/npm now in PATH)
npm run build
npm test
```

## Troubleshooting

### "Error: unsupported OS"
Node.js platform detection failed. Check:
```bash
uname -s   # Should be Linux, Darwin
uname -m   # Should be x86_64, arm64
```

### "Error: Cannot create ~/.corvin"
Permission issue. Check:
```bash
mkdir -p ~/.corvin && chmod 755 ~/.corvin
```

### "Error: Failed to download"
Network issue. Check:
```bash
curl -fI https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz
```

### npm not found after extraction
Rare case: corrupted download. Delete and retry:
```bash
rm -rf ~/.corvin/node
bash scripts/ensure-node.sh
```

## Compliance

- **No sudo** — all operations in `~/.corvin/` (user-writable)
- **POSIX bash** — Linux, macOS, WSL
- **PowerShell 5.1+** — Windows (same as uv installer)
- **Idempotent** — safe to re-run
- **Immutable versions** — pins via `.nvmrc`, not latest
- **Audit trail** — npm cache isolated per user/tenant
- **No global side effects** — `node`/`npm` only on PATH during execution

## Security

- **Pinned version** — `.nvmrc` prevents surprise updates
- **Checksum validation** (future) — SHA-256 verify on download
- **Isolated cache** — npm never writes to system directories
- **Arch-specific binaries** — no cross-compilation risk
- **Transparent download** — URL in script, not hidden in bootstrap

## Reference

- **ADR-0666:** Installer supply-chain security (applies same rigor as uv pin)
- **ADR-0303:** Parallel executor (Node.js is loaded before any tools run)
- **.nvmrc standard:** https://github.com/nvm-sh/nvm#nvmrc
