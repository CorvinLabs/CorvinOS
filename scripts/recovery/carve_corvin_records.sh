#!/usr/bin/env bash
# Carve CorvinOS JSON records (audit-chain lines + learning EventStore lines)
# out of the RAW block device after an accidental deletion (ADR-2058 incident,
# 2026-09-24). READ-ONLY on the device; output goes to RAM (/dev/shm) so the
# carve itself cannot overwrite the free blocks it is trying to read.
#
# Nothing carved is trusted as-is: scripts/recovery/rebuild_from_carve.py keeps
# only audit records whose hash AND per-record MAC verify under the out-of-tree
# anchor key, so a recovered record is provably an original.
#
#   sudo bash scripts/recovery/carve_corvin_records.sh [/dev/<device>]
set -euo pipefail
dev="${1:-$(findmnt -no SOURCE /)}"
out="/dev/shm/corvin-carve"
mkdir -p "$out"
chmod 700 "$out"
echo "device: $dev  ->  $out   (read-only scan, this takes a while)"
start=$(date +%s)
# One JSON object per line, no control characters inside a record.
#   audit chain:     {"ts": 1790..., "event_type": ...   "prev_hash": ..., "hash": ...}
#   learning events: {"event_id":"...","event_type":"...","skill_id":...}
#                    {"event_type": "learning.<kind>", "tenant_id": ...}
LC_ALL=C /usr/bin/grep -a -o -E \
  '\{"ts": [0-9]{10}\.[0-9]+, "event_type": "[^[:cntrl:]]*"hash": "[0-9a-f]{16}"[^[:cntrl:]]*\}|\{"event_id":"[0-9a-f-]{36}","event_type":"[^[:cntrl:]]*\}|\{"event_type": "learning\.[^[:cntrl:]]*\}' \
  "$dev" > "$out/raw.jsonl" || true
chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$out" "$out/raw.jsonl" 2>/dev/null || true
echo "done in $(( $(date +%s) - start ))s: $(wc -l < "$out/raw.jsonl") candidate lines, $(du -h "$out/raw.jsonl" | cut -f1)"
