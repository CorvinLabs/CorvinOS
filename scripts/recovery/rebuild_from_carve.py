#!/usr/bin/env python3
"""Rebuild lost CorvinOS audit history from a raw-device carve (ADR-2058).

Input: the candidate lines produced by ``carve_corvin_records.sh`` (default
``/dev/shm/corvin-carve/raw.jsonl``). The device also holds deleted TEST data
(pytest homes under /tmp, throwaway chains) and torn fragments, so nothing is
trusted by shape:

* An audit record is admitted only if its ``hash`` recomputes AND its ``mac``
  verifies under the out-of-tree anchor key (``security_events`` primitives —
  no second implementation), and it belongs to the LOST chain's lineage: it is
  reachable from the lost genesis through ``prev_hash`` links, or it sits in a
  mac-verified segment whose records carry this instance's ``instance_id``.
* A learning EventStore line is admitted only if its ``event_id`` appears in an
  admitted audit record (the store is audit-first: every event has one).

Output (with ``--apply``), never touching the live chain:

* ``<forge>/recovered/audit.carved-<stamp>.jsonl`` — admitted records in chain
  order (linked segments, gaps where blocks were overwritten), plus a
  ``.manifest.json`` with segments, gaps and coverage; linked to the live chain
  with an ``audit.chain_supersedes`` seam (reason ``chain_loss_carve``).
* learning events merged (deduplicated by event_id) into
  ``<tenant>/learning/events/YYYY-MM-DD.jsonl``.

Default is a dry run that only reports.

  python3 scripts/recovery/rebuild_from_carve.py [--carve PATH] [--apply]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "forge"))
from forge import security_events as se  # noqa: E402

TENANT = "_default"


def corvin_home() -> Path:
    return Path(os.environ.get("CORVIN_HOME") or REPO / ".corvin")


def lost_identity() -> dict:
    """The retired identity record(s) name the lost chain(s)."""
    d = se._mac_sentinel_path().parent / "chain_ids_retired"
    out = {}
    for p in sorted(d.glob("*")) if d.is_dir() else []:
        try:
            rec = json.loads(p.read_text())
            out[p.name] = rec
        except (OSError, ValueError):
            continue
    return out


def verify_record(rec: dict, key: bytes) -> bool:
    if not isinstance(rec.get("hash"), str) or not isinstance(rec.get("mac"), str):
        return False
    prev = rec.get("prev_hash", "")
    if not isinstance(prev, str):
        return False
    body = {k: v for k, v in rec.items() if k not in se.CHAIN_HASH_EXCLUDED_FIELDS}
    try:
        canon = se._canonical(body).encode("utf-8")
    except (TypeError, ValueError):
        return False
    h = hashlib.sha256(prev.encode("utf-8") + b"\n" + canon).hexdigest()[:16]
    if h != rec["hash"]:
        return False
    m = hmac.new(key, prev.encode("utf-8") + b"\n" + canon, hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(m, rec["mac"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--carve", default="/dev/shm/corvin-carve/raw.jsonl", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--instance-id", default="", help="this instance's id (default: <home>/instance_id)")
    args = ap.parse_args(argv)

    key = se._anchor_key()
    if key is None:
        print("anchor key unavailable — cannot authenticate anything; abort")
        return 2
    home = corvin_home()
    iid = args.instance_id or (home / "instance_id").read_text().strip()
    ids = lost_identity()
    lost_geneses = {r.get("genesis") for r in ids.values() if r.get("genesis")}
    print(f"lost chain genesis: {sorted(lost_geneses)}  instance: {iid[:8]}")

    audit: dict[str, dict] = {}
    learning_lines: list[dict] = []
    total = bad_json = unverified = 0
    with args.carve.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            total += 1
            line = line.strip()
            try:
                rec = json.loads(line)
            except ValueError:
                bad_json += 1
                continue
            if not isinstance(rec, dict):
                continue
            if "hash" in rec and "prev_hash" in rec and "ts" in rec:
                if rec["hash"] in audit:
                    continue
                if verify_record(rec, key):
                    audit[rec["hash"]] = rec
                else:
                    unverified += 1
            elif "event_type" in rec:
                learning_lines.append(rec)
    print(f"candidates={total} bad_json={bad_json} audit_verified={len(audit)} audit_rejected={unverified} learning_candidates={len(learning_lines)}")

    # ── order into segments by prev_hash links ──────────────────────────
    by_prev: dict[str, list[dict]] = collections.defaultdict(list)
    for r in audit.values():
        by_prev[r.get("prev_hash", "")].append(r)
    heads = [r for r in audit.values() if r.get("prev_hash", "") not in audit]
    segments = []
    for head in sorted(heads, key=lambda r: r["ts"]):
        seg, cur, seen = [head], head, {head["hash"]}
        while True:
            nxt = [n for n in by_prev.get(cur["hash"], []) if n["hash"] not in seen]
            if not nxt:
                break
            cur = min(nxt, key=lambda r: r["ts"])  # a fork is impossible in one chain
            seen.add(cur["hash"])
            seg.append(cur)
        segments.append(seg)

    def belongs(seg: list[dict]) -> bool:
        if seg[0].get("prev_hash", "") == "" and seg[0]["hash"] in lost_geneses:
            return True
        if seg[0]["hash"] in lost_geneses:
            return True
        own = sum(1 for r in seg if r.get("instance_id") == iid)
        return own >= max(1, len(seg) // 2)

    kept = [s for s in segments if belongs(s)]
    dropped = [s for s in segments if not belongs(s)]
    kept.sort(key=lambda s: s[0]["ts"])
    n_kept = sum(len(s) for s in kept)
    print(f"segments: kept={len(kept)} ({n_kept} records) dropped={len(dropped)} ({sum(len(s) for s in dropped)} records, foreign lineage/test data)")
    for s in kept[:40]:
        print(f"  {time.strftime('%F %T', time.localtime(s[0]['ts']))} → {time.strftime('%F %T', time.localtime(s[-1]['ts']))}  {len(s):>7} rec  head.prev={s[0].get('prev_hash','')[:8] or 'GENESIS'}")
    if len(kept) > 40:
        print(f"  … {len(kept) - 40} more")

    kept_event_ids = set()
    for s in kept:
        for r in s:
            eid = (r.get("details") or {}).get("event_id")
            if isinstance(eid, str):
                kept_event_ids.add(eid)
    learn_ok: dict[str, dict] = {}
    for rec in learning_lines:
        eid = rec.get("event_id")
        if isinstance(eid, str) and eid in kept_event_ids and rec.get("tenant_id", TENANT) == TENANT:
            learn_ok[eid] = rec
    print(f"learning events admitted (event_id bound to a recovered chain record): {len(learn_ok)}")

    if not args.apply:
        print("dry run — nothing written (use --apply)")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    forge_dir = home / "tenants" / TENANT / "global" / "forge"
    rec_dir = forge_dir / "recovered"
    rec_dir.mkdir(parents=True, exist_ok=True)
    out = rec_dir / f"audit.carved-{stamp}.jsonl"
    with out.open("x", encoding="utf-8") as fh:
        for s in kept:
            for r in s:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.chmod(out, 0o600)
    manifest = {
        "source": str(args.carve), "created": stamp, "instance_id": iid,
        "lost_geneses": sorted(lost_geneses), "records": n_kept,
        "segments": [{"first_ts": s[0]["ts"], "last_ts": s[-1]["ts"], "records": len(s),
                      "head_prev_hash": s[0].get("prev_hash", ""), "tail_hash": s[-1]["hash"]}
                     for s in kept],
        "note": "Records authenticated individually (hash + anchor-key MAC). Segments are "
                "separated by gaps where the original blocks were overwritten before the carve.",
    }
    (rec_dir / f"audit.carved-{stamp}.manifest.json").write_text(json.dumps(manifest, indent=2))
    seam = se.record_chain_supersession(forge_dir / "audit.jsonl", out, reason="chain_loss_carve")
    print(f"wrote {out} ({n_kept} records); seam {'written' if seam else 'NOT written'}")

    ev_dir = home / "tenants" / TENANT / "learning" / "events"
    ev_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = collections.defaultdict(list)
    for rec in learn_ok.values():
        ts = str(rec.get("timestamp", ""))[:10]
        if len(ts) == 10:
            by_day[ts].append(rec)
    added = 0
    for day, recs in sorted(by_day.items()):
        p = ev_dir / f"{day}.jsonl"
        have = set()
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    have.add(json.loads(ln).get("event_id"))
                except ValueError:
                    pass
        with p.open("a", encoding="utf-8") as fh:
            for rec in sorted(recs, key=lambda r: str(r.get("timestamp", ""))):
                if rec["event_id"] not in have:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
                    added += 1
    print(f"learning events restored: {added} into {ev_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
