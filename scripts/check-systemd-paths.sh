#!/usr/bin/env bash
# check-systemd-paths.sh — do the installed systemd units still point at real paths?
#
# The corvin-*.service user units live in ~/.config/systemd/user/ and hold
# ABSOLUTE paths into the repo (WorkingDirectory=, ExecStart=, PYTHONPATH=).
# They are not tracked by git, so no `git mv`, no rename sweep and no commit
# hook ever touches them.
#
# On 2026-09-15 that gap took the Discord bridge down: a rename moved
# operator/ -> corvin_operator/ and all 18 units kept pointing at the old
# tree. Nothing looked broken — systemd still reported `active`, because the
# running processes held open cwd handles to directories that no longer
# existed. The failure surfaced sideways, as the bridge adapter silently
# refusing every engine spawn (`manifest-missing`) and answering Discord with
# a canned fallback instead of a real reply.
#
# Exit 0 = every referenced path exists. Exit 1 = at least one is dangling.
#
# Run it after ANY directory rename in the repo, and from CI where the units
# are installed. See ADR-0730 / PLAN-0730 Phase 6.
#
# LIMITATION, measured 2026-09-15: a leftover shell of the OLD directory masks
# the gap. While operator/ still exists holding only runtime queues, paths like
# operator/forge and operator/bridges/shared resolve and this script reports OK
# even though no Python lives there any more. Removing that shell (PLAN-0730
# Phase 5) is what makes this check fully effective.
set -uo pipefail

UNIT_DIR="${SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"

if [ ! -d "$UNIT_DIR" ]; then
  echo "skip: no unit directory at $UNIT_DIR"
  exit 0
fi

shopt -s nullglob
units=("$UNIT_DIR"/corvin-*.service)
if [ ${#units[@]} -eq 0 ]; then
  echo "skip: no corvin-*.service units installed"
  exit 0
fi

dangling=0
checked=0

for unit in "${units[@]}"; do
  # Pull absolute paths out of the directives that must resolve. Template
  # units (@.service) and __REPO_ROOT__ placeholders are expanded by the
  # installer, not by systemd, so skip unexpanded ones.
  while IFS= read -r path; do
    case "$path" in
      *'%'*|*'__REPO_ROOT__'*|*'$'*) continue ;;   # unexpanded specifier
    esac
    checked=$((checked + 1))
    if [ ! -e "$path" ]; then
      printf 'DANGLING  %-38s  %s\n' "$(basename "$unit")" "$path"
      dangling=$((dangling + 1))
    fi
  done < <(
    # Comment lines are stripped FIRST. Units routinely document a previous
    # broken path in a comment ("the old ExecStart=... resolved to a
    # non-existent path"), and flagging those is a false positive — the fast
    # way to make a guard get ignored.
    directives=$(sed -E 's/^[[:space:]]*#.*$//' "$unit" 2>/dev/null)
    printf '%s\n' "$directives" \
      | grep -hoE '(WorkingDirectory|ExecStart|ExecStartPre|ExecStop|EnvironmentFile)=[^ ]*' \
      | sed -E 's/^[A-Za-z]+=-?//' \
      | grep -E '^/'
    # PYTHONPATH= is colon-separated and holds several paths at once
    printf '%s\n' "$directives" \
      | grep -hoE 'PYTHONPATH=[^ ]*' \
      | sed 's/^PYTHONPATH=//' | tr ':' '\n' | grep -E '^/'
  )
done

echo "checked $checked path(s) across ${#units[@]} unit(s) in $UNIT_DIR"

if [ "$dangling" -gt 0 ]; then
  cat <<EOF

$dangling dangling path(s). The units were not carried along by a repo change.
Fix, then reload and restart the affected services:

  grep -rl '/CorvinOS/<old-path>/' "$UNIT_DIR"/*.service \\
    | xargs sed -i 's|/CorvinOS/<old-path>/|/CorvinOS/<new-path>/|g'
  systemctl --user daemon-reload
  systemctl --user restart corvin-voice-bridge-adapter corvin-voice-bridge-discord

Verify the processes really moved — the unit file alone does not prove it:

  readlink /proc/\$(systemctl --user show -p MainPID --value <unit>)/cwd
EOF
  exit 1
fi

echo "OK: every referenced path exists"
exit 0
