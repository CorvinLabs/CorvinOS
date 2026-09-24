"""Operator acknowledgement of a LOST audit chain (ADR-2058).

When the live chain file is destroyed (2026-09-24: ``git clean -fxd`` in the
repo checkout that holds ``CORVIN_HOME`` deleted ``audit.jsonl`` together with
every other ignored file), the out-of-tree chain identity record still names the
lost chain. Every later write is stamped ``_chain_replaced_from`` and the boot
tripwire refuses — correctly: from the files alone a destroyed chain is
indistinguishable from a replaced one. The documented remedy is "restore the
chain backup". When no backup of the lost tail exists, the ONLY honest way
forward is an explicit, recorded operator acknowledgement. This module is that
act, and it is deliberately not reachable from any env var, flag or boot path.

What it does — nothing is deleted, merged, rewritten or reordered:

1. Freezes the post-loss file (records written after the loss, each stamped
   ``_chain_replaced_from``) by RENAMING it next to the canonical path, under the
   chain's own write lock.
2. Retires the lost chain's PATH-keyed identity record and prefix witness into
   ``<key dir>/{chain_ids,chain_witness}_retired/`` — moved, byte-identical. The
   genesis-keyed tail anchor and mac markers of the lost chain stay in place.
3. Starts a fresh chain whose FIRST record is ``audit.chain_loss_acknowledged``,
   naming the lost chain's genesis and last anchored tail, the cause and the
   retired record location; then links the frozen post-loss file and any
   supplied backup chains with the existing ``audit.chain_supersedes`` seam.

An auditor reading the new chain therefore learns, in-chain and hash-linked,
that a chain was lost, which one (genesis + tail from the out-of-tree anchor the
attacker/accident could not reach), when, why, and where every surviving
fragment is.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import security_events as se

LOSS_EVENT = "audit.chain_loss_acknowledged"
_CAUSE_MAX = 200


class ChainLossRefused(RuntimeError):
    """The acknowledgement preconditions do not hold; nothing was changed."""


def _open_by_other_process(path: Path) -> list[int]:
    """PIDs (other than ours) holding *path* open — Linux /proc scan, best-effort."""
    target = str(path.resolve())
    me = os.getpid()
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    for p in proc.iterdir():
        if not p.name.isdigit() or int(p.name) == me:
            continue
        try:
            for fd in (p / "fd").iterdir():
                try:
                    if os.readlink(fd) == target:
                        pids.append(int(p.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return pids


def _retire(src: Path, retired_dir: Path, stamp: str) -> str:
    if not src.exists():
        return ""
    retired_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(retired_dir, 0o700)
    dst = retired_dir / f"{src.name}.{stamp}"
    shutil.move(str(src), str(dst))
    return str(dst)


def acknowledge_chain_loss(
    canonical: Path, *, cause: str, backups: list[Path] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Perform the acknowledgement. Raises :class:`ChainLossRefused` (and changes
    nothing) unless the canonical chain is currently reported ``chain_replaced``
    against an anchored identity — i.e. unless there really is a lost chain."""
    canonical = Path(canonical)
    cause = " ".join(str(cause).split())[:_CAUSE_MAX]
    if not cause:
        raise ChainLossRefused("a cause is required")

    record = se._read_chain_path_record(canonical)
    if not record or not record.get("genesis"):
        raise ChainLossRefused("no anchored chain identity for this path — nothing was lost")
    live_genesis = se._chain_identity(canonical) if canonical.exists() else ""
    if live_genesis and live_genesis == record["genesis"]:
        raise ChainLossRefused("the anchored chain is still the live chain — nothing was lost")

    holders = _open_by_other_process(canonical) if canonical.exists() else []
    if holders:
        raise ChainLossRefused(f"chain file is open by pid(s) {holders} — stop every writer first")

    ts = time.time() if now is None else now
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))
    key_dir = se._mac_sentinel_path().parent

    # 1. Freeze the post-loss file, under the chain's own write lock so no
    #    writer can append between our check and the rename.
    frozen: Path | None = None
    if canonical.exists() and canonical.stat().st_size > 0:
        frozen = canonical.with_name(f"{canonical.name}.postloss-{stamp}")
        with canonical.open("a") as fh:
            se._lock_chain(fh)
            try:
                os.replace(canonical, frozen)
            finally:
                se._unlock_chain(fh)

    # 2. Retire the lost chain's path-keyed identity + witness (moved, not deleted).
    id_path = se._chain_path_record_path(canonical)
    retired_id = _retire(id_path, key_dir / "chain_ids_retired", stamp)
    legacy_id = se._legacy_chain_path_record_path(canonical)
    if legacy_id != id_path:
        _retire(legacy_id, key_dir / "chain_ids_retired", stamp)
    retired_witness = _retire(
        se._chain_witness_path(canonical), key_dir / "chain_witness_retired", stamp)

    # 3. Fresh chain: the acknowledgement is its first record.
    canonical.parent.mkdir(parents=True, exist_ok=True)
    ack = se.write_event(
        canonical, LOSS_EVENT, severity="CRITICAL",
        details={
            "lost_genesis": str(record.get("genesis", ""))[:32],
            "lost_tail": str(record.get("tail", ""))[:32],
            "lost_anchor_ts": record.get("ts"),
            "lost_chain_key": se.chain_path_key(canonical),
            "cause": cause,
            "retired_identity": bool(retired_id),
            "retired_witness": bool(retired_witness),
            "postloss_frozen": frozen is not None,
            "backups_linked": len(backups or []),
        },
    )
    seams = []
    for sibling in ([frozen] if frozen else []) + [Path(b) for b in (backups or [])]:
        rec = se.record_chain_supersession(canonical, sibling, reason="chain_loss_recovery")
        if rec:
            seams.append(str(rec.get("hash", "")))

    return {
        "ack_hash": ack.get("hash", ""),
        "frozen_postloss": str(frozen) if frozen else "",
        "retired_identity": retired_id,
        "retired_witness": retired_witness,
        "seams": seams,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Acknowledge a LOST audit chain (ADR-2058). Stop every "
                    "CorvinOS service first. Nothing is deleted.")
    ap.add_argument("--chain", required=True, type=Path, help="canonical chain path")
    ap.add_argument("--cause", required=True, help="why the chain was lost (recorded in-chain)")
    ap.add_argument("--backup", action="append", type=Path, default=[],
                    help="surviving chain copy to link with a seam (repeatable)")
    ap.add_argument("--i-understand-the-chain-is-lost", action="store_true", dest="confirm",
                    help="required: this records a permanent loss of audit history")
    args = ap.parse_args(argv)
    if not args.confirm:
        ap.error("refusing without --i-understand-the-chain-is-lost")
    try:
        out = acknowledge_chain_loss(args.chain, cause=args.cause, backups=args.backup)
    except ChainLossRefused as exc:
        print(f"refused: {exc}")
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
