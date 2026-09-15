#!/usr/bin/env bash
# install-desktop-launcher.sh — registers Corvin Bridge as a desktop
# app in Ubuntu/Gnome.
#
# What happens:
#   1. .desktop file in ~/.local/share/applications/ — shows up in
#      Activities / App-Grid, can be pinned to sidebar/dock.
#   2. Copy onto the user's desktop folder (auto-detected via
#      xdg-user-dir DESKTOP — handles "Desktop" and "Schreibtisch") for
#      a double-click icon.
#   3. Both copies are chmod +x'd and marked metadata::trusted so Gnome
#      doesn't whine about untrusted launchers.
#   4. update-desktop-database refreshes the app cache.
#   5. Stale "corvin-bridge.desktop" left over from the pre-rebrand
#      installer (paths point at <repo-root>/...
#      which no longer exists) is removed.

set -uo pipefail

BRIDGES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$BRIDGES/assets/corvin-bridge.desktop"
APPS_DIR="$HOME/.local/share/applications"

# Resolve user-facing desktop folder. xdg-user-dir respects the locale
# setting (Schreibtisch on de_DE, Bureau on fr_FR, etc.), with $HOME/Desktop
# as the fallback.
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null)"
fi
[[ -n "${DESKTOP_DIR:-}" && -d "$DESKTOP_DIR" ]] || DESKTOP_DIR="$HOME/Desktop"

[[ -f "$SRC" ]] || { echo "FATAL: $SRC missing"; exit 1; }

mkdir -p "$APPS_DIR"
mkdir -p "$DESKTOP_DIR"

# Cleanup: remove stale pre-rebrand launcher if present. Old file
# pointed at <repo-root>/plugins/voice/bridges/...
# which no longer exists post-rebrand (CLAUDE.md § rebrand Phase 5).
for stale_dir in "$DESKTOP_DIR" "$APPS_DIR" "$HOME/Desktop" "$HOME/Schreibtisch"; do
  stale_file="$stale_dir/corvin-bridge.desktop"
  if [[ -f "$stale_file" ]]; then
    rm -f "$stale_file" && echo "✓ removed stale $stale_file"
  fi
done

REPO_ROOT="$(cd "$BRIDGES/../.." && pwd)"
sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$SRC" > "$APPS_DIR/corvin-bridge.desktop"
sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$SRC" > "$DESKTOP_DIR/corvin-bridge.desktop"
chmod +x "$APPS_DIR/corvin-bridge.desktop" "$DESKTOP_DIR/corvin-bridge.desktop"

# Mark as trusted so Gnome doesn't show "Untrusted launcher".
for f in "$APPS_DIR/corvin-bridge.desktop" "$DESKTOP_DIR/corvin-bridge.desktop"; do
  if command -v gio >/dev/null 2>&1; then
    gio set "$f" "metadata::trusted" true 2>/dev/null || true
  fi
done

# Refresh the apps cache.
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi

# Pin to the Ubuntu sidebar (gnome-shell favorites). Append our entry
# only if it isn't already there.
if command -v gsettings >/dev/null 2>&1; then
  CURRENT="$(gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo '[]')"
  NEW="$(python3 -c "
import ast
cur = ast.literal_eval('''$CURRENT''')
target = 'corvin-bridge.desktop'
if target not in cur:
    cur.append(target)
print(repr(cur).replace(chr(34), chr(39)))
")"
  gsettings set org.gnome.shell favorite-apps "$NEW" 2>/dev/null \
    && echo "✓ pinned to Ubuntu sidebar"
fi

echo "✓ $DESKTOP_DIR/corvin-bridge.desktop"
echo "✓ $APPS_DIR/corvin-bridge.desktop"
echo
echo "Next steps:"
echo "  • Desktop: double-click 'Corvin Bridge' (if Gnome asks, right-click → 'Allow launching')"
echo "  • Activities (Super key): search 'Corvin Bridge' → right-click → 'Add to favourites' to pin to sidebar."
echo "  • Right-click the sidebar icon: Up / Down / Restart / Status / Doctor / Tail / Logs / Console as quick-actions."
