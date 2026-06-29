"""ACO Layer 4+ — diagnosis synthesizer (ADR-0179).

The missing seam that closes the loop: turn LOGS into a queued DIAGNOSIS, so the
L6 maintenance loop can act on real, recurring, localized bugs — never on noise.

Sources fused per run (maintainer side):
  * local logs            ``<home>/logs/corvin.log*``
  * mirrored remote logs  ``<home>/aco/remote/<name>/logs/corvin.log*`` (rsync, ADR-0178)
  * ingested telemetry    scrubbed signatures submitted by opted-in foreign users
                          (ADR-0179 telemetry channel) — already PII-free.

Precision rules (so we NEVER auto-patch noise):
  1. **Recurring only** — a signature must appear ≥ ``min_occurrences`` times across
     all sources. One-off errors (a network blip, a user mistake) are ignored.
  2. **Localized only** — a code-patch diagnosis requires a repo-relative file from
     a traceback frame. A bare ``ERROR`` line with no frame can never become a
     patch; it is written report-only.
  3. **Deduplicated** — a signature already queued (pending) or handled (done) is
     skipped, so the nightly loop doesn't re-open the same fix.

A qualifying signature → a diagnosis JSON in ``pending/`` carrying
``requires_repro_test: true`` (the reproduction gate then demands a red→green
proof before any commit). Everything else → ``reports/`` for human eyes only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from .error_signature import ErrorSignature, extract_signatures


def _diag_root(home: Path) -> Path:
    return Path(home) / "aco" / "diagnoses"


def _log_sources(home: Path) -> list[tuple[str, str]]:
    """(label, text) for every readable corvin.log across local + mirrored remotes."""
    home = Path(home)
    out: list[tuple[str, str]] = []
    globs = [
        ("local", home / "logs"),
    ]
    for name, d in globs:
        for p in sorted(d.glob("corvin.log*")) if d.is_dir() else []:
            try:
                out.append((f"{name}:{p.name}", p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    remote_root = home / "aco" / "remote"
    if remote_root.is_dir():
        for inst in sorted(remote_root.iterdir()):
            logs = inst / "logs"
            for p in sorted(logs.glob("corvin.log*")) if logs.is_dir() else []:
                try:
                    out.append((f"remote:{inst.name}:{p.name}",
                                p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
    return out


def _existing_ids(home: Path) -> set[str]:
    ids: set[str] = set()
    root = _diag_root(home)
    for sub in ("pending", "done", "failed"):
        d = root / sub
        if d.is_dir():
            for f in d.glob("*.json"):
                ids.add(f.stem)
    return ids


def aggregate(sources: Iterable[tuple[str, str]],
              telemetry_sigs: Optional[list[dict]] = None) -> dict[str, dict]:
    """signature → {sig: ErrorSignature, count: int, sources: set, localized: bool}."""
    agg: dict[str, dict] = {}

    def _add(sig: ErrorSignature, src: str, n: int = 1) -> None:
        slot = agg.get(sig.signature)
        if slot is None:
            agg[sig.signature] = {"sig": sig, "count": n, "sources": {src},
                                  "localized": sig.localized}
        else:
            slot["count"] += n
            slot["sources"].add(src)

    for label, text in sources:
        for sig in extract_signatures(text):
            _add(sig, label)
    # foreign-user telemetry: pre-scrubbed signature dicts with their own counts
    for t in (telemetry_sigs or []):
        if not isinstance(t, dict) or not t.get("signature"):
            continue
        try:
            n = int(t.get("count", 1))
        except (TypeError, ValueError):
            n = 1                              # malformed count → 1, don't drop the signal
        sig = ErrorSignature(
            signature=str(t["signature"]), exc_type=str(t.get("exc_type", "?")),
            message_template=str(t.get("message_template", "")),
            top_repo_file=t.get("top_repo_file"), func=str(t.get("func", "?")),
            frames=list(t.get("frames", []) or []), localized=bool(t.get("top_repo_file")))
        _add(sig, f"telemetry:{t.get('instance', 'anon')}", n)
    return agg


def _build_diagnosis(slot: dict, *, now: int) -> dict:
    sig: ErrorSignature = slot["sig"]
    return {
        "id": sig.signature,
        "schema": "aco.diagnosis/1",
        "anomaly_class": "recurring_exception",
        "summary": f"{sig.exc_type} in {sig.top_repo_file} ({sig.func}) "
                   f"— {slot['count']}× across {len(slot['sources'])} source(s)",
        "file": sig.top_repo_file,
        "files": [sig.top_repo_file],
        "root_cause": f"{sig.exc_type}: {sig.message_template}",
        "repro": {
            "signature": sig.signature, "exc_type": sig.exc_type,
            "message_template": sig.message_template, "frames": sig.frames,
            "occurrences": slot["count"], "sources": sorted(slot["sources"]),
        },
        "confidence": "high",
        "requires_repro_test": True,
        "ts": now,
    }


def synthesize(home: str | Path, *, min_occurrences: int = 3,
               telemetry_sigs: Optional[list[dict]] = None,
               now: Optional[int] = None) -> dict:
    """Fuse all sources → write qualifying diagnoses to pending/, the rest to
    reports/. Returns a summary. Idempotent: skips already-known signatures."""
    import time
    home = Path(home)
    now = int(now or time.time())
    root = _diag_root(home)
    pending, reports = root / "pending", root / "reports"
    pending.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    # De-dup the two channels for the SAME instance: if a remote is rsync-mirrored
    # (its logs already feed _log_sources) AND it also submits telemetry, counting
    # both would double-count the same occurrences and could falsely cross the
    # recurrence threshold (review MED-HIGH). Drop telemetry whose pseudonym matches
    # a mirrored remote dir name. (Residual: pseudonyms that don't match the mirror
    # dir name can't be reconciled — document, accept.)
    mirrored = {p.name for p in (home / "aco" / "remote").iterdir()} \
        if (home / "aco" / "remote").is_dir() else set()
    tsigs = [t for t in (telemetry_sigs or [])
             if not (isinstance(t, dict) and t.get("instance") in mirrored)]

    agg = aggregate(_log_sources(home), tsigs)
    known = _existing_ids(home)
    queued, reported, skipped = [], [], 0

    for sigid, slot in agg.items():
        if sigid in known:
            skipped += 1
            continue
        recurring = slot["count"] >= min_occurrences
        localized = slot["localized"] and slot["sig"].top_repo_file
        if recurring and localized:
            diag = _build_diagnosis(slot, now=now)
            (pending / f"{sigid}.json").write_text(
                json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
            queued.append(sigid)
        else:
            # report-only: not enough evidence OR not localizable → never a patch.
            reason = ("below recurrence threshold" if not recurring
                      else "not localizable to a repo file")
            rec = _build_diagnosis(slot, now=now) if localized else {
                "id": sigid, "summary": slot["sig"].message_template[:160],
                "occurrences": slot["count"], "ts": now}
            rec["confidence"] = "low"
            rec["report_reason"] = reason
            rec.pop("requires_repro_test", None)
            rp = reports / f"{sigid}.json"
            is_new = not rp.exists()
            rp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
            if is_new:                      # only count NEW reports (overwrites are idempotent)
                reported.append(sigid)

    return {"queued": queued, "reported": reported, "skipped_known": skipped,
            "signatures_seen": len(agg), "min_occurrences": min_occurrences}
