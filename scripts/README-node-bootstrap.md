# Node.js Bootstrap Scripts

Local, self-contained Node.js management for CorvinOS.

## Scripts

### `ensure-node.sh` (Linux / macOS)

**Purpose:** Bootstrap Node.js to `~/.corvin/node/`

**Usage:**
```bash
bash scripts/ensure-node.sh
```

**What it does:**
1. Reads `.nvmrc` from repo root
2. Detects platform (Linux/macOS, x64/arm64)
3. Downloads Node.js tarball from nodejs.org
4. Extracts to `~/.corvin/node/`
5. Verifies installation
6. Exports `CORVIN_NODE_BIN` and updates `PATH`

**Environment variables:**
- `CORVIN_HOME` — custom home (default: `~/.corvin/`)
- Modifies: `PATH`, `CORVIN_NODE_BIN`

**Exit codes:**
- `0` — success (or already installed)
- `1` — error (permission, network, extraction, etc.)

**Idempotent:** Yes. Safe to call multiple times; skips if correct version already installed.

**Example:**
```bash
# Install v24.18.0 (from .nvmrc)
bash scripts/ensure-node.sh
# ✓ Node.js v24.18.0 installed to ~/.corvin/node
```

---

### `ensure-npm.sh` (Linux / macOS)

**Purpose:** Verify npm from local Node.js + configure cache

**Usage:**
```bash
bash scripts/ensure-npm.sh          # Verify + export env
bash scripts/ensure-npm.sh -- ci    # Run 'npm ci' with configured env
```

**What it does:**
1. Calls `ensure-node.sh` (if needed)
2. Updates `PATH` to use local node
3. Verifies npm is available
4. Creates `~/.corvin/npm-cache/`
5. Exports `npm_config_cache`
6. (Optional) Runs `npm` command with configured environment

**Environment variables:**
- `CORVIN_HOME` — custom home (default: `~/.corvin/`)
- Sets: `PATH`, `npm_config_cache`

**Examples:**
```bash
# Just verify
bash scripts/ensure-npm.sh

# Install dependencies
bash scripts/ensure-npm.sh -- ci
bash scripts/ensure-npm.sh -- install --production
bash scripts/ensure-npm.sh -- run build
```

**Or source it in a shell:**
```bash
source <(bash scripts/ensure-npm.sh)
npm ci
npm run test
```

**Exit codes:**
- `0` — success
- `1` — error (node bootstrap failed, npm not found, etc.)

---

## Integration with `install.sh` / `install.ps1`

### install.sh (Phase 1a)

```bash
# Automatic bootstrap in install.sh (Phase 1a)
echo "  Bootstrapping local Node.js runtime ..."
if ! bash "${REPO_DIR}/scripts/ensure-node.sh"; then
    echo "  Node.js bootstrap failed — falling back to system Node.js"
fi
```

If `ensure-node.sh` fails, install continues with system Node.js (if available).

### install.ps1 (Phase 1a)

```powershell
# Windows version (Phase 1a): download + extract Node.js to %USERPROFILE%\.corvin\node\
# PowerShell script (no external bash call)
$DownloadUrl = "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip"
Invoke-WebRequest ... -Uri $DownloadUrl -OutFile $NodeZip
Expand-Archive -Path $NodeZip -DestinationPath $NodeRoot
```

---

## Architecture

```
install.sh (POSIX)
  ├─ Phase 1a: bash scripts/ensure-node.sh
  │  ├─ Read .nvmrc
  │  ├─ Download Node.js tarball
  │  └─ Extract to ~/.corvin/node/
  ├─ Phase 1c: Compatibility check
  ├─ Phase 2: uv + CorvinOS (existing)
  └─ Phase 4: Launch console

install.ps1 (PowerShell)
  ├─ Phase 1a: [inline PowerShell]
  │  ├─ Read .nvmrc
  │  ├─ Download Node.js ZIP
  │  └─ Extract to %USERPROFILE%\.corvin\node\
  ├─ Phase 1: uv + CorvinOS (existing)
  └─ Phase 4: Launch console
```

---

## Verification

**Check local Node.js:**
```bash
ls -lh ~/.corvin/node/bin/node
~/.corvin/node/bin/node --version
```

**Check npm:**
```bash
~/.corvin/node/bin/npm --version
ls -lh ~/.corvin/npm-cache/
```

---

## Troubleshooting

### Node.js download fails

**Symptoms:** "Failed to download from https://nodejs.org/dist/..."

**Diagnosis:**
```bash
# Test connectivity
curl -fI https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz

# Check .nvmrc
cat .nvmrc
```

**Fix:**
- Check internet connection
- Try again (transient network issue)
- Edit `.nvmrc` to a different version
- Use system Node.js (not preferred, but works)

### Extraction fails

**Symptoms:** "tar extraction failed" or "Cannot move to ~/.corvin/node"

**Diagnosis:**
```bash
# Check disk space
df -h ~
ls -lh ~/.corvin/

# Check write permissions
touch ~/.corvin/test && rm $_
```

**Fix:**
- Free up disk space
- Check `~/.corvin/` permissions: `chmod u+w ~/.corvin/`
- Retry

### npm not found

**Symptoms:** "npm not found in ~/.corvin/node/bin/npm or on PATH"

**Diagnosis:**
```bash
ls ~/.corvin/node/bin/
file ~/.corvin/node/bin/npm
```

**Fix:**
- Delete and reinstall: `rm -rf ~/.corvin/node && bash scripts/ensure-node.sh`
- Corrupted download (rare)

### PATH not updated

**Symptoms:** `npm` or `node` still refers to system version after running scripts

**Diagnosis:**
```bash
echo $PATH
which node
type npm
```

**Fix:**
- Manually update PATH: `export PATH=~/.corvin/node/bin:$PATH`
- Source `ensure-npm.sh` output: `source <(bash scripts/ensure-npm.sh)`
- Open a new terminal (some shells don't inherit exported vars)

---

## Security

- **Pinned version:** `.nvmrc` prevents surprise updates
- **HTTPS download:** nodejs.org uses HTTPS; no unencrypted transfer
- **SHA-256 verification** (future): can add checksum check (see comments in script)
- **No sudo:** all operations in user home
- **No global install:** no system-wide side effects
- **Isolated cache:** npm never writes to system directories

---

## References

- [node-self-contained.md](../docs/node-self-contained.md) — Full technical reference
- [quick-start-node-setup.md](../docs/quick-start-node-setup.md) — User guide
- ADR-0666 — Installer supply-chain security (applies same rigor to Node.js pin)
- ADR-0303 — Parallel executor (Node.js loaded before any tools run)
- `.nvmrc` — Standard format (https://github.com/nvm-sh/nvm#nvmrc)
