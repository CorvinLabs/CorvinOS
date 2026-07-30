#!/usr/bin/env bash
# install.sh — Bootstrap the operator bundle without the AWPKG installer.
# Use this when corvin pkg is not yet available or for manual setup.
# For the full managed install, run: corvin pkg install ./operator/bundle/
set -euo pipefail
# An unmatched glob must expand to nothing, not to the literal pattern. Without
# this, `for f in .../personas/*.json` on a bundle that ships no personas/ dir
# (the wheel's _vendor layout does not include it) left the literal pattern,
# `[ -f "$f" ]` was false, the else-branch `cp "$f"` failed, and `set -e`
# aborted the whole script BEFORE the skills step ever ran — so a manual run of
# the vendored install.sh installed nothing (2026-07-30 review finding I3).
shopt -s nullglob

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"

log()  { echo "[operator/bundle] $*"; }
warn() { echo "[operator/bundle] WARN: $*" >&2; }

# ── 1. Personas ────────────────────────────────────────────────────────────────
PERSONA_DIR="$CORVIN_HOME/cowork/personas"
mkdir -p "$PERSONA_DIR"
for f in "$BUNDLE_DIR/personas/"*.json; do
  name="$(basename "$f")"
  if [ -f "$PERSONA_DIR/$name" ]; then
    warn "persona $name already exists — skipping (delete to reinstall)"
  else
    cp "$f" "$PERSONA_DIR/$name"
    log "installed persona: $name"
  fi
done

# ── 2. LDD Skills ─────────────────────────────────────────────────────────────
SKILLS_DIR="${HOME}/.claude/skills"
mkdir -p "$SKILLS_DIR"
if [ -d "$BUNDLE_DIR/skills/ldd" ]; then
  for skill_dir in "$BUNDLE_DIR/skills/ldd/"*/; do
    name="$(basename "$skill_dir")"
    target="$SKILLS_DIR/$name"
    if [ -d "$target" ]; then
      warn "skill $name already installed — skipping"
    else
      cp -r "$skill_dir" "$target"
      log "installed skill: $name"
    fi
  done
else
  warn "skills/ldd/ not yet populated — run Phase 1b to add LDD SKILL.md files"
fi

# ── 3. Bridge config templates ────────────────────────────────────────────────
for tmpl in "$BUNDLE_DIR/bridge-config/"*.settings.template.json; do
  channel="${tmpl##*/}"
  channel="${channel%.settings.template.json}"
  dest="$CORVIN_HOME/bridges/$channel/settings.json"
  if [ -f "$dest" ]; then
    warn "bridge config for $channel already exists — skipping (not overwriting live secrets)"
  else
    mkdir -p "$(dirname "$dest")"
    cp "$tmpl" "$dest"
    log "installed bridge config template: $channel → $dest"
    log "  → Fill in secrets: \$EDITOR $dest"
  fi
done

# ── 4. Forge tools ────────────────────────────────────────────────────────────
if ls "$BUNDLE_DIR/tools/"*.json >/dev/null 2>&1; then
  log "Forge tools found in tools/ — install via:"
  log "  corvin pkg install ./operator/bundle/  (AWPKG installer handles forge_tool() calls)"
  log "  or manually: use the forge MCP server (mcp__forge__forge_tool) for each tools/*.json"
else
  warn "tools/ not yet populated — run Phase 1b to add forge tool JSON files"
fi

log "Done. Restart bridges: bridge.sh restart"
