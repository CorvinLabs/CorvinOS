#!/usr/bin/env python3
"""voice-audit — verify hash-chain integrity of the voice bridge audit log.

Usage:
    python3 voice_audit.py verify [--path PATH]
    python3 voice_audit.py tail   [--path PATH] [--limit N]

The default audit file is ``~/.config/corvin-voice/forge/audit.jsonl``; an
explicit ``--path`` (or the ``VOICE_AUDIT_PATH`` env var) overrides.

Exit codes:
    0   chain intact (or audit file empty / missing — nothing to verify)
    1   integrity violation found — affected lines listed on stderr
    2   IO error (file unreadable, etc.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))

import audit  # noqa: E402  bridges/shared/audit.py


def _cross_peer_verify(
    local_path: Path,
    peer_path: Path,
    *,
    task_id_filter: str | None = None,
    strict: bool = False,
) -> tuple[bool, list[dict], list[dict]]:
    """ADR-0116 M5: verify A2A chain anchors across two chain files.

    Reads both chains, matches chain_anchor_sent / chain_anchor_received pairs
    by task_id, and checks that the attested chain tail hashes exist in the
    referenced chains.

    Returns (ok, problems).  ok=True means all verifiable pairs pass.
    """
    def _read_chain(path: Path) -> list[dict]:
        records = []
        if not path.is_file():
            return records
        with path.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _chain_hashes(records: list[dict]) -> set[str]:
        return {r["hash"] for r in records if isinstance(r.get("hash"), str)}

    local_records = _read_chain(local_path)
    peer_records = _read_chain(peer_path)
    local_hashes = _chain_hashes(local_records)
    peer_hashes = _chain_hashes(peer_records)

    problems: list[dict] = []

    # Collect anchor events from both chains.
    local_sent: dict[str, dict] = {}     # task_id → chain_anchor_sent event
    local_received: dict[str, dict] = {} # task_id → chain_anchor_received event
    peer_sent: dict[str, dict] = {}
    peer_received: dict[str, dict] = {}

    for rec in local_records:
        evt = rec.get("event_type", "")
        d = rec.get("details") or {}
        tid = str(d.get("task_id") or "")
        if not tid:
            continue
        if task_id_filter and tid != task_id_filter:
            continue
        if evt == "A2A.chain_anchor_sent":
            local_sent[tid] = d
        elif evt == "A2A.chain_anchor_received":
            local_received[tid] = d

    for rec in peer_records:
        evt = rec.get("event_type", "")
        d = rec.get("details") or {}
        tid = str(d.get("task_id") or "")
        if not tid:
            continue
        if task_id_filter and tid != task_id_filter:
            continue
        if evt == "A2A.chain_anchor_sent":
            peer_sent[tid] = d
        elif evt == "A2A.chain_anchor_received":
            peer_received[tid] = d

    all_task_ids = (set(local_sent) | set(local_received)
                    | set(peer_sent) | set(peer_received))

    results = []
    ok = True

    for tid in sorted(all_task_ids):
        # Case: local sent, peer received — verify peer_chain_tail in peer chain.
        ls = local_sent.get(tid)
        pr = peer_received.get(tid)
        if ls or pr:
            our_tail = (ls or {}).get("our_chain_tail", "")
            peer_tail = (pr or {}).get("peer_chain_tail", "")
            if ls and not pr:
                if strict:
                    problems.append({"task_id": tid, "issue": "peer_chain_anchor_received_missing"})
                    ok = False
                results.append({"task_id": tid, "verdict": "UNVERIFIABLE",
                                 "reason": "peer_chain_anchor_received_missing"})
                continue
            if pr and not ls:
                if strict:
                    problems.append({"task_id": tid, "issue": "local_chain_anchor_sent_missing"})
                    ok = False
                results.append({"task_id": tid, "verdict": "UNVERIFIABLE",
                                 "reason": "local_chain_anchor_sent_missing"})
                continue
            # Both present: check that our_chain_tail prefix matches something
            # in the local hash set.
            if our_tail and not any(h.startswith(our_tail) for h in local_hashes):
                problems.append({"task_id": tid, "issue": "local_chain_tail_not_found",
                                  "our_chain_tail_prefix": our_tail})
                ok = False
                results.append({"task_id": tid, "verdict": "FAIL",
                                 "reason": "local_chain_tail_not_found"})
                continue
            # peer_chain_tail in chain_anchor_received stores the SENDER's chain
            # tail prefix (from the receiver's perspective, the sender is "peer").
            # So peer_tail = local's tail prefix → verify against local_hashes.
            if peer_tail and not any(h.startswith(peer_tail) for h in local_hashes):
                problems.append({"task_id": tid, "issue": "peer_chain_tail_not_found",
                                  "peer_chain_tail_prefix": peer_tail})
                ok = False
                results.append({"task_id": tid, "verdict": "FAIL",
                                 "reason": "peer_chain_tail_not_found"})
                continue
            results.append({"task_id": tid, "verdict": "PASS",
                             "our_chain_tail": our_tail, "peer_chain_tail": peer_tail})

    return ok, problems, results


def _nbac_cross_genesis_check(
    local_path: Path,
    peer_path: Path,
) -> tuple[bool, str]:
    """ADR-0117 M4: verify genesis block signatures + network_id compatibility.

    Returns (ok, message). ok=True iff:
    - Both chains have a genesis block.
    - Both genesis blocks have valid Network Root Key signatures.
    - Both chains share the same network_id.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))
        from nbac import (  # noqa: PLC0415
            get_genesis_block as _get_gb,
            verify_genesis_block as _verify_gb,
        )
    except ImportError:
        return False, "nbac module not importable (check dependencies)"

    def _load_and_verify(chain_path: Path, label: str) -> tuple[bool, str, dict | None]:
        block = _get_gb(chain_path)
        if block is None:
            return False, f"{label}: no genesis block found", None
        if not _verify_gb(block):
            return False, f"{label}: genesis block signature INVALID", None
        # Block may have details nested (write_event wraps into details)
        details = block.get("details") or block
        return True, "ok", details

    local_ok, local_msg, local_block = _load_and_verify(local_path, "local")
    if not local_ok:
        return False, local_msg

    peer_ok, peer_msg, peer_block = _load_and_verify(peer_path, "peer")
    if not peer_ok:
        return False, peer_msg

    local_net = (local_block or {}).get("network_id", "")
    peer_net = (peer_block or {}).get("network_id", "")
    if local_net != peer_net:
        return False, (f"network_id mismatch: local={local_net!r} vs peer={peer_net!r} "
                       f"— chains are from different networks")

    return True, f"both chains on network_id={local_net!r} with valid signatures"


def _corvin_root_from(path: Path):
    """Derive the ``.corvin`` tree root that contains an audit chain path.

    Returns None when no safe ``.corvin``-style root can be found — the
    caller MUST refuse to walk in that case (never fall back to ``/`` or
    ``$HOME``, which would rglob the entire filesystem / home dir).
    """
    p = path.resolve()
    for anc in p.parents:
        if anc.name == ".corvin":
            return anc
    # No `.corvin` ancestor: accept only if the immediate tree looks like a
    # corvin home (has global/ + tenants/) — i.e. <root>/global/forge/audit.jsonl.
    if len(p.parents) >= 3:
        cand = p.parents[2]
        if (cand / "global").is_dir() and (cand / "tenants").is_dir():
            return cand
    return None


def _is_safe_walk_root(root: Path) -> bool:
    """A root is safe to rglob iff it is a ``.corvin`` dir or clearly a corvin
    home tree — NEVER ``/``, ``$HOME``, or ``$HOME/.config``."""
    r = root.resolve()
    if r == Path("/") or r == Path.home() or r == (Path.home() / ".config"):
        return False
    return r.name == ".corvin" or ((r / "global").is_dir() and (r / "tenants").is_dir())


def cmd_verify_all(args) -> int:
    """Verify EVERY audit.jsonl chain under the corvin_home tree.

    Closes the coverage gap where only the single default chain was ever
    verified. Exit 1 if ANY chain is broken (rc=2 if IO errors but no
    integrity violations). Walk is symlink-safe (no followlinks) and refuses
    dangerous roots (``/``, ``$HOME``) so it can never rglob the whole FS.
    """
    base = (Path(args.path).expanduser() if args.path else audit.audit_path())
    root = base if base.is_dir() else _corvin_root_from(base)
    if root is None or not _is_safe_walk_root(root):
        print(f"refusing to verify-all: {base} does not resolve to a safe "
              f".corvin tree root (set --path to a .corvin dir or chain)",
              file=sys.stderr)
        return 2
    # os.walk(followlinks=False) — do NOT follow dir symlinks (Python 3.13's
    # rglob would, double-counting the forge/skill-forge symlink trees and
    # risking escape/cycles). Dedupe by resolved real path.
    import os as _os
    seen: set = set()
    chains: list[Path] = []
    for dirpath, _dirs, files in _os.walk(root, followlinks=False):
        if "audit.jsonl" in files:
            real = (Path(dirpath) / "audit.jsonl").resolve()
            if real not in seen:
                seen.add(real)
                chains.append(real)
    chains.sort()
    if not chains:
        # FND-15: zero chains is not necessarily a clean "all OK" — it can mean
        # the verifier resolved a DIFFERENT CORVIN_HOME than the writer
        # (env unset / cwd-walk divergence), so the real chain with its events
        # is never visited. Surface the expected writer path so a home mismatch
        # is diagnosable rather than silently passing. (rc stays 0 so a genuine
        # fresh install / nightly timer with no audit yet isn't a false alarm.)
        _writer = audit.audit_path()
        print(f"WARNING: no audit chains found under {root}. If this host has "
              f"written audit events, the verifier may be resolving a different "
              f"CORVIN_HOME than the writer — expected writer chain: {_writer}",
              file=sys.stderr)
        print(f"no audit chains found under {root}")
        return 0
    n_ok = 0
    broken: list[Path] = []
    io_errors: list[Path] = []
    for chain in chains:
        try:
            ok, problems = audit.verify_audit(chain)
        except OSError as e:
            print(f"  IO-ERROR  {chain}: {e}", file=sys.stderr)
            io_errors.append(chain)
            continue
        # FND-16: run the SAME signed segment-manifest continuity check that
        # cmd_verify runs, per chain, so the nightly `verify --all` timer
        # (which routes here) actually catches a deleted/swapped L37-sealed
        # segment. The live chain resets to a fresh, internally-valid segment
        # on rotation and verifies clean, so verify_audit() alone would pass
        # while sealed history is orphaned — exactly the gap this closes. The
        # manifest failure feeds the SAME `broken`/`ok` aggregation below, so a
        # tampered manifest forces the process exit code non-zero. Fail-closed
        # on the check's own error, mirroring cmd_verify (an integrity verifier
        # must never treat its own crash as "pass").
        try:
            man_ok, man_problems, man_warnings = _verify_segment_manifest(
                chain.parent, _first_prev_hash(chain))
            for w in man_warnings:
                print(f"  manifest  {chain}: {w}", file=sys.stderr)
            if not man_ok:
                ok = False
                problems = list(problems) + man_problems
        except Exception as e:  # noqa: BLE001
            print(f"  manifest  {chain}: segment-manifest check ERRORED — "
                  f"failing closed: {e}", file=sys.stderr)
            ok = False
            problems = list(problems) + [{"issue": "manifest_check_errored",
                                          "detail": type(e).__name__}]
        if ok:
            n_ok += 1
            print(f"  OK        {chain}")
        else:
            broken.append(chain)
            first = problems[0] if problems else {}
            print(f"  BROKEN    {chain}  line {first.get('line','?')}: "
                  f"{first.get('issue','?')}", file=sys.stderr)
    total = len(chains)
    if broken:
        print(f"\naudit ALL: {n_ok}/{total} OK — {len(broken)} BROKEN, "
              f"{len(io_errors)} IO-error", file=sys.stderr)
        return 1
    if io_errors:
        print(f"\naudit ALL: {n_ok}/{total} OK — {len(io_errors)} IO-error "
              f"(unreadable, not an integrity violation)", file=sys.stderr)
        return 2
    print(f"\naudit ALL OK  ({n_ok}/{total} chains under {root})")
    return 0


def cmd_verify(args) -> int:
    if getattr(args, "all", False):
        return cmd_verify_all(args)
    path = Path(args.path).expanduser() if args.path else audit.audit_path()
    if not path.exists():
        print(f"audit file does not exist (nothing to verify): {path}")
        return 0
    # ADR-0153 M3: enable per-record instance_sig verification when requested.
    # Best-effort import — the signing layer lives in forge.security_events.
    if getattr(args, "with_signatures", False):
        try:
            try:
                from forge.security_events import set_verify_sigs  # type: ignore
            except ImportError:
                from security_events import set_verify_sigs  # type: ignore
            set_verify_sigs(True)
            print("instance_sig verification: ENABLED (ADR-0153)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"--with-signatures requested but unavailable: {e}", file=sys.stderr)
    try:
        ok, problems = audit.verify_audit(path)
    except OSError as e:
        print(f"audit IO error: {e}", file=sys.stderr)
        return 2

    # FND-14: signed segment-manifest continuity check. Runs on EVERY verify
    # (incl. the daily timer) without decrypting anything — catches a deleted
    # or swapped sealed segment and a live chain unlinked from the newest tail,
    # which the live-only chain check and the opt-in --include-sealed walk both
    # miss on the default path.
    try:
        man_ok, man_problems, man_warnings = _verify_segment_manifest(
            path.parent, _first_prev_hash(path))
        for w in man_warnings:
            print(f"manifest: {w}", file=sys.stderr)
        if not man_ok:
            ok = False
            problems = list(problems) + man_problems
    except Exception as e:  # noqa: BLE001
        # FAIL-CLOSED (R1 finding): an integrity verifier must not treat its own
        # error as "pass". A crash in the manifest check (e.g. crafted manifest
        # content) previously left exit 0; now it fails the verify so the
        # operator/timer surfaces it instead of a false all-clear.
        print(f"segment-manifest check ERRORED — failing closed: {e}", file=sys.stderr)
        ok = False
        problems = list(problems) + [{"issue": "manifest_check_errored",
                                      "detail": type(e).__name__}]

    # ADR-0044 / Layer 37 cross-segment verification.
    # When --include-sealed is set, walk all rotated + sealed segments
    # chronologically, unseal each, verify its internal chain, and
    # confirm chain continuity across segment boundaries.
    sealed_problems: list[dict] = []
    if getattr(args, "include_sealed", False):
        try:
            sealed_ok, sealed_problems = _verify_sealed_segments(
                path.parent,
                live_first_prev_hash=_first_prev_hash(path),
                identity_file=Path(args.identity).expanduser() if getattr(args, "identity", None) else None,
                live_audit_path=path,
            )
            if not sealed_ok:
                ok = False
                problems = list(problems) + sealed_problems
        except RuntimeError as e:
            print(f"sealed-segment verification failed: {e}", file=sys.stderr)
            return 2

    # ADR-0116 M5: cross-peer chain anchor verification.
    cross_peer_results = []
    if getattr(args, "cross_peer", None):
        peer_path = Path(args.cross_peer).expanduser()
        if not peer_path.exists():
            print(f"cross-peer chain file not found: {peer_path}", file=sys.stderr)
            return 2
        try:
            _cp_ok, _cp_problems, cross_peer_results = _cross_peer_verify(
                path, peer_path,
                task_id_filter=getattr(args, "task_id", None) or None,
                strict=getattr(args, "strict", False),
            )
            if not _cp_ok:
                ok = False
                problems = list(problems) + _cp_problems
        except Exception as exc:
            print(f"cross-peer verification failed: {exc}", file=sys.stderr)
            return 2

        # ADR-0117 M4: genesis block + network compatibility check.
        if getattr(args, "peer_genesis_check", False):
            try:
                _gc_ok, _gc_msg = _nbac_cross_genesis_check(path, peer_path)
                if not _gc_ok:
                    ok = False
                    problems = list(problems) + [{"issue": "nbac_genesis_mismatch",
                                                   "detail": _gc_msg}]
                    print(f"NBAC genesis check FAIL: {_gc_msg}", file=sys.stderr)
                else:
                    print(f"NBAC genesis check OK: {_gc_msg}")
            except Exception as exc:
                print(f"NBAC genesis check error: {exc}", file=sys.stderr)
                # Not fatal — genesis check failure does not block other results.

    if ok:
        suffix = " + sealed" if getattr(args, "include_sealed", False) else ""
        if cross_peer_results:
            suffix += f" + cross-peer({len(cross_peer_results)} task(s))"
        print(f"audit OK{suffix}  ({path})")
        for r in cross_peer_results:
            verdict = r.get("verdict", "?")
            tid = r.get("task_id", "?")
            detail = r.get("reason", "") or (
                f"our_tail={r.get('our_chain_tail','?')[:8]}…  "
                f"peer_tail={r.get('peer_chain_tail','?')[:8]}…"
            )
            print(f"  task_id={tid:36s}  {verdict:14s}  {detail}")
        return 0
    print(f"audit INTEGRITY VIOLATION  ({path})", file=sys.stderr)
    try:
        try:
            from forge.clag import explain_reason_code as _explain  # type: ignore
        except ImportError:
            from clag import explain_reason_code as _explain  # type: ignore
    except Exception:  # noqa: BLE001
        def _explain(code):
            return ""
    for p in problems:
        _issue = p.get("issue", "?")
        print(f"  line {p.get('line', '?')}: {_issue}  {p}", file=sys.stderr)
        _why = _explain(_issue)
        if _why:
            print(f"      → {_why}", file=sys.stderr)
    if cross_peer_results:
        print("cross-peer results:", file=sys.stderr)
        for r in cross_peer_results:
            print(f"  task_id={r.get('task_id','?')}  {r.get('verdict','?')}  "
                  f"{r.get('reason','')}", file=sys.stderr)
    if getattr(args, "notify_bridge", False):
        try:
            n = _notify_chain_break(path, problems, args)
            if n > 0:
                print(f"chain-break notification: forwarded to "
                      f"{n} bridge user(s)", file=sys.stderr)
            else:
                print("chain-break notification: relay not configured "
                      "or no targets — skip", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            # Notification is best-effort. The exit code still reflects
            # the chain integrity, not the notify success.
            print(f"chain-break notification failed: {exc}", file=sys.stderr)
    return 1


def _first_prev_hash(path: Path) -> str:
    """Return the ``prev_hash`` of the first chain entry in *path*, or
    "" if the file is empty or unreadable. Used to confirm that the
    live segment's first entry references the last sealed segment's
    tail hash."""
    if not path.is_file():
        return ""
    try:
        import json as _json
        with path.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                ph = rec.get("prev_hash")
                if isinstance(ph, str):
                    return ph
                return ""
    except OSError:
        return ""
    return ""


def _verify_segment_manifest(audit_dir: Path, live_first_prev_hash: str):
    """FND-14: cross-segment continuity check on the DEFAULT verify path.

    The full --include-sealed walk decrypts every segment (emits
    unseal_requested, needs the key) so it cannot run on the daily timer.
    This check works on plaintext metadata only — no unseal — and detects:
      * a deleted or swapped sealed/rotated segment (existence + sha256),
      * a tampered manifest (per-entry HMAC with the out-of-tree anchor key),
      * a broken cross-segment link (entry[i].last == entry[i+1].first_prev),
      * a live chain no longer linked to the newest sealed tail.

    Returns ``(ok, problems, warnings)``. ``ok`` is False only for genuine
    tamper. A legacy install that rotated before manifests existed yields a
    WARNING (segments present, no manifest), never a hard failure."""
    import hashlib
    import hmac as _hmac
    import json as _json
    try:
        from audit_sealer import (
            segment_manifest_path, list_sealed_segments,
            _manifest_anchor_key, _manifest_canonical, _sha256_file,
            manifest_mac_active,
        )
    except ImportError:
        try:
            from operator.bridges.shared.audit_sealer import (  # type: ignore
                segment_manifest_path, list_sealed_segments,
                _manifest_anchor_key, _manifest_canonical, _sha256_file,
                manifest_mac_active,
            )
        except ImportError:
            return True, [], ["audit_sealer unavailable — manifest check skipped"]

    problems: list[dict] = []
    warnings: list[str] = []
    mpath = segment_manifest_path(audit_dir)
    entries: list[dict] = []
    if mpath.is_file():
        try:
            with mpath.open("r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = _json.loads(line)
                    except _json.JSONDecodeError:
                        problems.append({"issue": "manifest_corrupt_line"})
                        continue
                    # A line that parses but isn't a dict (e.g. `[]`, `5`, `"x"`)
                    # would later AttributeError on .get() and, via the caller's
                    # broad except, silently disable the whole continuity check
                    # (R1 finding). Record it as tamper and skip — never feed a
                    # non-dict into the continuity logic.
                    if not isinstance(obj, dict):
                        problems.append({"issue": "manifest_corrupt_line"})
                        continue
                    entries.append(obj)
        except OSError as e:
            return True, [], [f"manifest unreadable: {e}"]

    on_disk = [p.name for p in list_sealed_segments(audit_dir)]
    if not entries:
        # R1 finding: whole-manifest deletion/emptying must be caught here. If
        # the host has the manifest MAC active (out-of-tree sentinel) and the
        # anchor key is available and sealed segments exist on disk, an
        # empty/absent manifest is a full-strip of the continuity record — a
        # hard problem, NOT a legacy warning. (Mirrors the R3-03 per-entry
        # full-strip defense; that one only fires when entries remain.)
        try:
            _key_present = _manifest_anchor_key() is not None
        except Exception:  # noqa: BLE001
            _key_present = False
        if on_disk and _key_present and manifest_mac_active(audit_dir):
            problems.append({
                "issue": "manifest_full_strip",
                "detail": "sealed segments present + manifest MAC active on host, "
                          "but the segment manifest is empty/absent",
            })
            return False, problems, warnings
        if on_disk:
            warnings.append(
                f"{len(on_disk)} rotated/sealed segment(s) present but no signed "
                f"manifest — cross-segment continuity unverifiable without "
                f"--include-sealed (legacy install? run a rotation to start the manifest)"
            )
        return True, problems, warnings

    # Per-entry MAC. R2-FND-05: the MAC is MANDATORY once the manifest has begun
    # using it. The sha256/last_hash are NOT covered by the entry's own MAC-free
    # hash anywhere, so an attacker who deletes the `mac` field could otherwise
    # rewrite an entry's sha256 to match a swapped segment and slip past as an
    # "unsigned legacy entry". Mirror the chain MAC-epoch: once a mac'd entry is
    # seen with the key available, every later entry MUST carry a verifiable mac
    # (a missing one → manifest_mac_missing). Entries before the first mac (the
    # legacy prefix from before the key existed) stay exempt. A present mac that
    # can't be verified because the key is gone is surfaced (not silently passed)
    # unless CORVIN_AUDIT_VERIFY_NO_KEY_OK=1.
    import os as _os
    _no_key_ok = _os.environ.get("CORVIN_AUDIT_VERIFY_NO_KEY_OK", "").strip() in ("1", "true", "yes")
    ak = _manifest_anchor_key()
    mac_required = False
    mac_seen = 0
    for e in entries:
        if "mac" in e:
            if not ak:
                if not _no_key_ok:
                    problems.append({"issue": "manifest_mac_unverifiable_key_absent",
                                     "segment": e.get("segment")})
                continue
            mac_required = True
            mac_seen += 1
            body = {k: v for k, v in e.items() if k != "mac"}
            expect = _hmac.new(ak, _manifest_canonical(body).encode("utf-8"),
                               hashlib.sha256).hexdigest()[:16]
            if not _hmac.compare_digest(expect, str(e.get("mac", ""))):
                problems.append({"issue": "manifest_mac_tampered",
                                 "segment": e.get("segment")})
        elif ak and mac_required:
            # Epoch started, key available, but this entry lost its mac → strip.
            problems.append({"issue": "manifest_mac_missing",
                             "segment": e.get("segment")})

    # R3-03 manifest full-strip detection: the per-entry MAC-epoch above only
    # fires once a mac'd entry is SEEN, so stripping the `mac` field from EVERY
    # entry would otherwise pass as an all-legacy manifest, letting an attacker
    # swap a sealed segment (rewriting its sha256) undetected. Same structural
    # argument as the live chain (R3-02): the out-of-tree manifest-MAC sentinel
    # exists IFF a manifest mac has been written, and that same write appended a
    # mac'd entry — so a manifest with entries, the sentinel present, the key
    # available, and ZERO mac'd entries is only reachable by stripping every mac.
    # Legacy manifests (written before the key existed) never set the sentinel.
    if ak is not None and mac_seen == 0 and manifest_mac_active(audit_dir):
        problems.append({"issue": "manifest_mac_stripped",
                         "detail": "manifest MAC active on host but no entry carries a mac"})

    # Cross-segment contiguity.
    for i in range(len(entries) - 1):
        if entries[i].get("last_hash") != entries[i + 1].get("first_prev_hash"):
            problems.append({"issue": "manifest_discontinuity",
                             "between": entries[i].get("segment"),
                             "and": entries[i + 1].get("segment")})

    # Each recorded segment still on disk with a matching sha256 (no unseal).
    for e in entries:
        name = e.get("on_disk") or e.get("segment")
        seg = audit_dir / name if name else None
        if seg is None or not seg.is_file():
            problems.append({"issue": "sealed_segment_missing", "segment": name})
            continue
        want = e.get("sha256")
        if want and _sha256_file(seg) != want:
            problems.append({"issue": "sealed_segment_tampered", "segment": name})

    # Live chain links to the newest sealed tail. R3 finding: the old
    # `and live_first_prev_hash` short-circuit let a fresh-GENESIS replacement of
    # the live chain (first record prev_hash="") orphan all sealed history
    # undetected — verify passed because the genesis chain is internally valid
    # and the sealed segments still match their manifest. If sealed segments
    # exist (newest_tail set), the live chain MUST link to the newest tail; a
    # genesis live chain (prev="") legitimately occurs only when there is no
    # sealed history, in which case newest_tail is empty and this never fires.
    newest_tail = entries[-1].get("last_hash", "")
    if newest_tail and newest_tail != live_first_prev_hash:
        problems.append({"issue": "live_unlinked_from_sealed",
                         "expected_prev_hash": newest_tail,
                         "live_first_prev_hash": live_first_prev_hash or "<genesis/empty>"})

    return (len(problems) == 0), problems, warnings


def _verify_sealed_segments(
    audit_dir: Path,
    *,
    live_first_prev_hash: str = "",
    identity_file: Path | None = None,
    live_audit_path: Path | None = None,
) -> tuple[bool, list[dict]]:
    """Walk every sealed audit segment in ``audit_dir`` and verify its
    internal hash chain + cross-segment link.

    Returns ``(ok, problems)``. Problems describe per-segment failures.

    Implementation: imports the Layer-37 module lazily so this script
    stays runnable when audit_sealer / sealer binaries aren't installed.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))
        from audit_sealer import (  # type: ignore
            last_hash_of_segment,
            list_sealed_segments,
            make_forge_audit_writer,
            unseal_to_temp,
        )
    except ImportError as e:
        raise RuntimeError(f"audit_sealer module unavailable: {e}") from e

    # L37 invariant: audit.unseal_requested (WARNING) must be emitted
    # BEFORE every unseal — even automated --include-sealed verification.
    # Resolve the live audit path: use the caller-supplied path when
    # available (preferred), fall back to the module default.
    _live_audit_path = live_audit_path if live_audit_path is not None else audit.audit_path()
    _audit_writer = make_forge_audit_writer(_live_audit_path)

    segments = list_sealed_segments(audit_dir)
    problems: list[dict] = []
    if not segments:
        return True, []

    prev_tail = ""
    for seg in segments:
        # Plaintext vs sealed: plaintext segments verify directly;
        # sealed segments need an unseal step first.
        is_sealed = seg.suffix in (".age", ".gpg")
        plaintext = seg
        cleanup = False
        if is_sealed:
            try:
                plaintext = unseal_to_temp(
                    seg,
                    identity_file=identity_file,
                    audit_writer=_audit_writer,
                    requester="verify --include-sealed",
                )
                cleanup = True
            except (RuntimeError, FileNotFoundError) as e:
                problems.append({
                    "segment": seg.name,
                    "issue": "unseal_failed",
                    "reason": str(e)[:200],
                })
                continue

        try:
            # ADR-0044 / Layer 37: pass the previous segment's tail as
            # initial_prev so the first entry's prev_hash (typically
            # an audit.rotation_link) is checked against the cross-
            # segment boundary, not the legacy "" default.
            #
            # Lazy-import forge.security_events.verify_chain directly
            # because audit.verify_audit doesn't surface initial_prev yet.
            try:
                import sys as _sys
                _sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))
                from forge.security_events import verify_chain  # type: ignore
                ok, segment_problems = verify_chain(
                    plaintext, initial_prev=prev_tail,
                )
            except ImportError:
                ok, segment_problems = audit.verify_audit(plaintext)
            if not ok:
                for p in segment_problems:
                    p["segment"] = seg.name
                    problems.append(p)
            from audit_sealer import last_hash_of_segment as _tail  # type: ignore
            prev_tail = _tail(plaintext)
        finally:
            if cleanup:
                try:
                    plaintext.unlink()
                except OSError:
                    pass

    # Live segment continuity check: live audit.jsonl's first prev_hash
    # must match the last sealed segment's tail hash (if any sealed
    # exists).
    if segments and live_first_prev_hash != prev_tail:
        problems.append({
            "segment": "<live>",
            "issue": "live_link_break",
            "expected_prev": prev_tail,
            "actual_prev": live_first_prev_hash,
        })

    return (len(problems) == 0), problems


def cmd_unseal(args) -> int:
    """DPO inspection path. Decrypt a sealed segment to a tmpdir with
    mode 0600. Emits ``audit.unseal_requested`` (WARNING) into the
    LIVE chain *before* decryption — the request is audited regardless
    of what happens to the plaintext."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))
        from audit_sealer import (  # type: ignore
            unseal_to_temp,
            make_forge_audit_writer,
        )
    except ImportError as e:
        print(f"audit_sealer module unavailable: {e}", file=sys.stderr)
        return 2

    sealed = Path(args.segment).expanduser()
    if not sealed.is_file():
        print(f"sealed segment not found: {sealed}", file=sys.stderr)
        return 2

    identity_file = Path(args.identity).expanduser() if args.identity else None
    live_audit_path = (Path(args.path).expanduser() if args.path
                       else audit.audit_path())
    audit_writer = make_forge_audit_writer(live_audit_path)

    try:
        plaintext = unseal_to_temp(
            sealed,
            identity_file=identity_file,
            audit_writer=audit_writer,
            requester=args.requester or "",
        )
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"unseal failed: {e}", file=sys.stderr)
        return 2

    if args.output:
        # Move the tmpdir plaintext into the operator-specified path.
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        plaintext.rename(out)
        os_chmod = __import__("os").chmod
        os_chmod(out, 0o600)
        print(str(out))
    else:
        # Stream to stdout, then remove the tmpdir copy.
        try:
            with plaintext.open("rb") as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    sys.stdout.buffer.write(chunk)
        finally:
            try:
                plaintext.unlink()
            except OSError:
                pass
    return 0


def _notify_chain_break(path: Path, problems: list,
                        args) -> int:
    """Post one outbox envelope per relay target so the bridge daemon
    forwards a CRITICAL warning to the operator. Returns the number of
    envelopes written.

    Reads relay config from ``--relay-config`` or
    ``$VOICE_RELAY_CONFIG`` / ``~/.config/corvin-voice/relay.json``.
    Outbox dir defaults to
    ``<repo>/operator/bridges/shared/outbox/`` but can be overridden
    via ``--outbox-dir`` (used in tests).

    Both single-target legacy schema and a `targets` list are supported:

      Legacy: {channel, to, prefix?}
      Multi : {targets: [{channel, to, prefix?}, ...]}
    """
    import time as _t
    cfg_path = (Path(args.relay_config).expanduser()
                if getattr(args, "relay_config", None)
                else Path(os.environ.get("VOICE_RELAY_CONFIG")
                          or "~/.config/corvin-voice/relay.json"
                          ).expanduser())
    if not cfg_path.exists():
        return 0
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return 0

    targets = cfg.get("targets")
    if not isinstance(targets, list) or not targets:
        # Legacy single-target form.
        if cfg.get("channel") and cfg.get("to"):
            targets = [{"channel": cfg["channel"], "to": cfg["to"],
                        "prefix": cfg.get("prefix", "")}]
        else:
            return 0

    outbox_dir = (Path(args.outbox_dir).expanduser()
                  if getattr(args, "outbox_dir", None)
                  else REPO_ROOT / "operator" / "bridges"
                       / "shared" / "outbox")
    outbox_dir.mkdir(parents=True, exist_ok=True)

    # Plain-language explainer for each issue code so the notified operator/user
    # understands WHICH check broke and WHY (metadata-only — codes, never
    # content). Single source of truth lives in clag.explain_reason_code.
    try:
        try:
            from forge.clag import explain_reason_code as _explain  # type: ignore
        except ImportError:
            from clag import explain_reason_code as _explain  # type: ignore
    except Exception:  # noqa: BLE001
        def _explain(code):  # fallback
            return f"integrity check failed (code: {code})"

    summary_lines = [
        f"🔒 AUDIT CHAIN BROKEN — {len(problems)} problem(s) detected.",
        f"file: {path}",
        "CorvinOS halts on a broken chain (tamper-evident audit is a GDPR "
        "Art. 30/32 guarantee). Reasons:",
    ]
    _seen_codes: set[str] = set()
    for p in problems[:5]:
        _issue = p.get("issue", "unknown")
        _line = p.get("line", "?")
        summary_lines.append(f"  • line {_line}: {_issue}"[:160])
        if _issue not in _seen_codes:
            _seen_codes.add(_issue)
            summary_lines.append(f"    ↳ {_explain(_issue)}"[:300])
    summary_lines.append("Run `voice-audit verify` / `bridge.sh doctor` to inspect.")
    text = "\n".join(summary_lines)

    written = 0
    for tgt in targets:
        if not isinstance(tgt, dict):
            continue
        ch = tgt.get("channel")
        to = tgt.get("to")
        if not ch or not to:
            continue
        prefix = tgt.get("prefix") or "🚨 audit-verify:"
        envelope = {
            "channel": ch,
            "to":      to,
            "text":    f"{prefix} {text}",
            "_relay":  True,
            "_audit_chain_break": True,
        }
        if ch in ("telegram", "discord"):
            try:
                envelope["chat_id"] = (int(to)
                                       if str(to).lstrip("-").isdigit()
                                       else to)
            except (ValueError, TypeError):
                envelope["chat_id"] = to
        msg_id = f"audit-verify_{int(_t.time() * 1000)}_{written}"
        try:
            (outbox_dir / f"{msg_id}.json").write_text(
                json.dumps(envelope, ensure_ascii=False)
            )
            written += 1
        except OSError:
            continue
    return written


def cmd_tail(args) -> int:
    path = Path(args.path).expanduser() if args.path else audit.audit_path()
    if not path.exists():
        print(f"audit file does not exist: {path}")
        return 0
    lines = path.read_text().splitlines()[-args.limit:]
    for raw in lines:
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  [malformed]  {raw[:80]}")
            continue
        ts = rec.get("ts", 0)
        sev = rec.get("severity", "INFO").ljust(8)
        evt = rec.get("event_type", "?").ljust(28)
        d = rec.get("details") or {}
        ch = d.get("channel", "")
        chat = d.get("chat_key", "")
        user = d.get("user", "")
        persona = d.get("persona", "")
        print(f"  {ts:.3f}  {sev}  {evt}  ch={ch:8s}  chat={chat:14s}  "
              f"user={user:12s}  persona={persona}")
    return 0


def cmd_metrics(args) -> int:
    """Phase 6.3 — observability projection over the audit chain.

    Renders the same aggregate the gateway's /metrics endpoint serves,
    but for single-operator deployments without the gateway venv. The
    tenant defaults to ``_default`` (the single-operator implicit
    tenant); --tenant overrides.
    """
    # Try to import the gateway's audit_metrics module. It lives in
    # core/gateway, which is NOT on PYTHONPATH by default.
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "core" / "gateway"))
    sys.path.insert(0, str(repo / "operator" / "forge"))
    try:
        from corvin_gateway import audit_metrics as _am
    except ImportError as exc:
        print(f"audit_metrics module not available: {exc}", file=sys.stderr)
        print("(install corvin-gateway plugin venv to enable metrics)",
              file=sys.stderr)
        return 3
    since: float | None = None
    if args.since:
        try:
            seconds = _am.parse_duration(args.since)
        except ValueError as e:
            print(f"--since: {e}", file=sys.stderr)
            return 4
        import time as _t
        since = _t.time() - seconds
    snap = _am.aggregate(args.tenant, since=since)
    fmt = args.format
    if fmt == "prom":
        print(_am.render_prometheus(snap))
    elif fmt == "json":
        print(json.dumps(_am.snapshot_to_dict(snap), indent=2, sort_keys=True))
    else:  # table (default)
        print(_am.render_table(snap), end="")
    return 0


def cmd_emit(args) -> int:
    """Append a single hash-chained event. Used by the JS daemons:
    they shell out to `voice-audit emit ...` instead of re-implementing
    the chain logic. Falls back to a no-op if the forge plugin (which
    provides forge.security_events.write_event) is missing."""
    path = Path(args.path).expanduser() if args.path else audit.audit_path()
    if audit._se is None:
        print("forge plugin missing — cannot emit (no chain library)",
              file=sys.stderr)
        return 3
    details = {}
    if args.details:
        try:
            parsed = json.loads(args.details)
            if not isinstance(parsed, dict):
                raise ValueError("--details must be a JSON object")
            details.update(parsed)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"--details: invalid JSON: {e}", file=sys.stderr)
            return 4
    if args.channel:
        details["channel"] = args.channel
    if args.chat_key:
        details["chat_key"] = args.chat_key
    if args.user:
        details["user"] = args.user
    if args.persona:
        details["persona"] = args.persona
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        audit._se.write_event(
            path, args.event_type,
            severity=audit._VOICE_EVENT_SEVERITY.get(args.event_type),
            tool=args.tool or "",
            run_id=args.run_id or "",
            details=details,
            hash_chain=True,
        )
    except OSError as e:
        print(f"audit IO error: {e}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voice-audit")
    p.add_argument("--path", default=None,
                   help="audit file path (default: VOICE_AUDIT_PATH or "
                        "~/.config/corvin-voice/forge/audit.jsonl)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("verify", help="verify hash-chain integrity")
    pv.add_argument("--all", action="store_true",
        help="verify EVERY audit.jsonl chain under the corvin_home tree "
             "(global, tenant, forge, skill-forge, per-session) — not just "
             "the single default chain. Exit 1 if ANY chain is broken. "
             "Closes the 'only one chain is ever verified' coverage gap.")
    pv.add_argument("--notify-bridge", action="store_true",
        help="on chain break, write a CRITICAL warning envelope into the "
             "shared outbox so the bridge daemons forward it to all "
             "configured relay targets (Roadmap L)")
    pv.add_argument("--relay-config", default=None,
        help="path to relay.json (default: $VOICE_RELAY_CONFIG or "
             "~/.config/corvin-voice/relay.json)")
    pv.add_argument("--outbox-dir", default=None,
        help="bridge outbox dir for the warning envelope (default: "
             "<repo>/operator/bridges/shared/outbox/)")
    pv.add_argument("--include-sealed", action="store_true",
        help="ADR-0044 / Layer 37: walk every rotated + sealed audit "
             "segment in the audit dir, unseal each, verify its internal "
             "chain, AND confirm cross-segment chain link continuity. "
             "Requires the sealer binary (age / gpg) to be available "
             "for sealed segments.")
    pv.add_argument("--identity", default=None,
        help="path to an age identity file (or gpg key id) used by "
             "--include-sealed to unseal segments. If unset, age uses "
             "the default keyring location.")
    # ADR-0116 M5: cross-peer chain anchor verification.
    pv.add_argument("--cross-peer", default=None, metavar="PEER_CHAIN",
        help="ADR-0116 M5: path to a peer instance's audit.jsonl. "
             "Reads both chains and verifies that A2A.chain_anchor_sent / "
             "chain_anchor_received events reference valid chain tail hashes "
             "in each respective chain. Prints per-task-id PASS / FAIL / "
             "UNVERIFIABLE. Exit 1 if any FAIL.")
    pv.add_argument("--task-id", default=None,
        help="filter cross-peer verification to a single task_id")
    pv.add_argument("--strict", action="store_true",
        help="treat UNVERIFIABLE as FAIL (for regulated-sector tooling "
             "where 'could not verify' is not acceptable)")
    # ADR-0117 M4: verify genesis block signature + network compatibility
    pv.add_argument("--peer-genesis-check", action="store_true",
        help="ADR-0117 M4: when --cross-peer is set, also verify the peer's "
             "genesis block signature (must be signed by the Network Root Key) "
             "and that both chains share the same network_id. Exit 1 on mismatch.")
    # ADR-0153 M3: verify the per-record Ed25519 instance_sig at rest (the
    # externally-attestable instance-binding layer). Off by default to keep the
    # daily timer cheap; the signing side always runs, so this is the production
    # entry point that was specified but never wired (R3 finding).
    pv.add_argument("--with-signatures", action="store_true", dest="with_signatures",
        help="ADR-0153: also verify each record's Ed25519 instance_sig "
             "(instance attestation). Missing sigs (pre-M3 records) stay non-fatal; "
             "a present-but-invalid sig fails the verify.")
    pv.set_defaults(fn=cmd_verify)

    # ADR-0044 / Layer 37 — operator-initiated unseal for DPO / legal hold.
    pu = sub.add_parser("unseal",
        help="decrypt a sealed audit segment for DPO inspection. "
             "Emits audit.unseal_requested (WARNING) into the live "
             "chain BEFORE decryption regardless of outcome.")
    pu.add_argument("segment",
        help="path to a sealed segment, e.g. "
             "audit.2026-04-15T120000Z.jsonl.age")
    pu.add_argument("--identity", default=None,
        help="age identity file or gpg key id (defaults to the "
             "sealer's default keyring location)")
    pu.add_argument("--output", default=None,
        help="write plaintext here (mode 0600); default: stream to "
             "stdout and discard")
    pu.add_argument("--requester", default="",
        help="free-form requester string recorded in the unseal audit "
             "event (e.g. 'dpo@example.com / legal hold #1234')")
    pu.set_defaults(fn=cmd_unseal)

    pt = sub.add_parser("tail", help="show last N events")
    pt.add_argument("--limit", type=int, default=20)
    pt.set_defaults(fn=cmd_tail)

    pm = sub.add_parser("metrics",
        help="audit-chain projection in Prometheus / JSON / table format "
             "(Phase 6.3)")
    pm.add_argument("--tenant", default="_default",
        help="tenant id (default: _default = single-operator)")
    pm.add_argument("--since", default=None,
        help="time window — 30s / 5m / 2h / 7d, or bare seconds")
    pm.add_argument("--format", choices=("table", "prom", "json"),
        default="table",
        help="output format (default: table)")
    pm.set_defaults(fn=cmd_metrics)

    pe = sub.add_parser("emit",
        help="append one hash-chained event (used by the JS daemons)")
    pe.add_argument("event_type",
        help="e.g. bridge.whitelist_deny, bridge.pin_failure, "
             "bridge.rate_limit_exceeded, daemon.started, daemon.stopped")
    pe.add_argument("--channel",  default="")
    pe.add_argument("--chat-key", default="")
    pe.add_argument("--user",     default="")
    pe.add_argument("--persona",  default="")
    pe.add_argument("--tool",     default="")
    pe.add_argument("--run-id",   default="")
    pe.add_argument("--details",  default="",
        help='extra fields as a JSON object, e.g. \'{"reason":"not in whitelist"}\'')
    pe.set_defaults(fn=cmd_emit)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
