# Quick Start: Self-Contained Node.js Setup

## Installation (Automatic)

```bash
# Linux / macOS
bash install.sh

# Windows
.\install.ps1
```

Both installers automatically:
1. **Phase 1a:** Bootstrap local Node.js to `~/.corvin/node/`
2. **Phase 2:** Install CorvinOS via uv
3. **Phase 3:** Setup wizard (optional)

No `sudo`, no system dependencies, no package manager.

## Manual Setup (Development)

### Linux / macOS

```bash
# 1. Ensure local Node.js
bash scripts/ensure-node.sh

# 2. Configure npm
source <(bash scripts/ensure-npm.sh)

# 3. Install dependencies
npm ci   # or: npm install

# 4. Build / develop
npm run build
npm test
npm start
```

### Windows (PowerShell)

```powershell
# 1. Read .nvmrc and setup node
$NodeVersion = (Get-Content .nvmrc).Trim() -replace '^v', ''
$env:Path = "$env:USERPROFILE\.corvin\node\bin;$env:Path"
$env:npm_config_cache = "$env:USERPROFILE\.corvin\npm-cache"

# 2. Verify npm
npm --version

# 3. Install dependencies
npm ci   # or: npm install

# 4. Build / develop
npm run build
npm test
npm start
```

## What Gets Installed

```
~/.corvin/
├── node/                    # 43 MB — Node.js v24.18.0 (binaries)
├── npm-cache/              # npm download cache
└── ... (other CorvinOS data)
```

**No system-wide changes** — everything in `~/.corvin/`.

## Version Control

Edit `.nvmrc` to update Node.js:
```
v24.18.0    ← Change this
```

Then delete and re-install:
```bash
rm -rf ~/.corvin/node
bash scripts/ensure-node.sh
```

## Troubleshooting

**node/npm not found:**
```bash
# Manually update PATH
export PATH=~/.corvin/node/bin:$PATH
node --version
npm --version
```

**Permission denied error:**
```bash
# Check ~/.corvin is writable
chmod u+w ~/.corvin
mkdir -p ~/.corvin/node ~/.corvin/npm-cache
```

**Download fails:**
```bash
# Check network
curl -fI https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz

# Or fall back to system Node.js (if available)
which node
npm --version
```

## Architecture

| Layer | What | Where |
|-------|------|-------|
| **CorvinOS** | Python + CLI tools | Installed via `uv` (existing) |
| **Node.js** | JavaScript runtime | `~/.corvin/node/` (new) |
| **npm** | JavaScript package manager | Bundled with Node.js |
| **npm cache** | Downloaded packages | `~/.corvin/npm-cache/` |

Each is self-contained; no interaction needed.

## FAQ

**Q: Why not use system Node.js?**
A: No sudo needed, version control is simpler, compatible with multi-tenant setups.

**Q: Can I use a different version?**
A: Edit `.nvmrc`, delete `~/.corvin/node/`, re-run installer.

**Q: Does it slow down startup?**
A: No; binaries are cached. First download (~30s) happens once.

**Q: What if the download fails?**
A: Installer falls back to system Node.js (if available) and continues. Manual retry is always possible.

**Q: Can I uninstall it?**
A: Yes: `rm -rf ~/.corvin/node ~/.corvin/npm-cache`

## References

- Full details: [node-self-contained.md](node-self-contained.md)
- ADR-0666: Installer supply-chain security
- ADR-0303: Parallel executor
