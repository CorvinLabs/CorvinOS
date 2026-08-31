"""ACO support-bundle exporter (ADR-0178 follow-up).

Collects EVERYTHING healing/logging-related into ONE timestamped folder + a .zip
that a user can send to the maintainer to get their errors fixed. Read-only: it
NEVER moves the live log writers (that would break the reader≠writer contract) —
it COPIES into the bundle.

SECRET-SAFE BY CONSTRUCTION: an explicit ALLOWLIST of artifact kinds is collected
(logs, audit metadata, ACO diagnoses, a fresh nerve scan, system info). Files are
also passed through a denylist guard so a key / env / vault / token can never end
up in a bundle that gets emailed around. Audit logs are metadata-only by design
(GDPR Art. 5 — no prompt/output text, fingerprinted ids).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

# Never copy a file whose name matches these — defense-in-depth on top of the
# allowlist (a stray secret in a logs dir must still never leave the machine).
_SECRET_DENY = (
    ".key", ".pem", ".env", ".pfx", ".p12", "service.env", "maintainer.env",
    "maintainer.key", "identity_registry.json", "vault", "secret", "token",
    ".ssh", "id_rsa", "private",
)
_AUDIT_TAIL_LINES = 5000        # keep bundles small + recent
_MAX_COPY_BYTES = 8 * 1024 * 1024


def _is_secret(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in _SECRET_DENY)


def _safe_copy(src: Path, dst: Path) -> bool:
    if _is_secret(src.name):
        return False
    try:
        if not src.is_file() or src.is_symlink():
            return False
        if src.stat().st_size > _MAX_COPY_BYTES:
            return _copy_tail(src, dst, _AUDIT_TAIL_LINES)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except OSError:
        return False


def _copy_tail(src: Path, dst: Path, lines: int) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with src.open("r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
        with dst.open("w", encoding="utf-8") as out:
            out.write(f"# ...truncated to last {lines} lines...\n")
            out.writelines(tail)
        return True
    except OSError:
        return False


def _system_info(home: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "ts": int(time.time()),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "os_name": os.name,
        "corvin_home": str(home),
    }
    try:
        import importlib.metadata as _md  # noqa: PLC0415
        info["corvinos_version"] = _md.version("corvinos")
    except Exception:  # noqa: BLE001
        info["corvinos_version"] = "unknown"
    try:
        du = shutil.disk_usage(str(home))
        info["disk_free_mb"] = du.free // (1024 * 1024)
        info["disk_total_mb"] = du.total // (1024 * 1024)
    except OSError:
        pass
    return info


def _nerve_snapshot() -> list[dict]:
    try:
        from .nerve import NerveRegistry  # noqa: PLC0415
        signals = NerveRegistry.scan_all()
        out = []
        for s in signals:
            d = s.to_dict() if hasattr(s, "to_dict") else {}
            out.append(d)
        return out
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"nerve scan failed: {exc}"}]


def create_bundle(corvin_home: str | Path, out_dir: str | Path | None = None, *,
                  include_audit: bool = True, run_nerve_scan: bool = True,
                  stamp: str | None = None) -> Path:
    """Build the support bundle folder + zip. Returns the .zip path. Best-effort:
    a missing/unreadable artifact is skipped, never fatal."""
    home = Path(corvin_home)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    base = Path(out_dir) if out_dir else (home / "aco" / "support-bundles")
    bdir = base / f"support-bundle-{stamp}"
    bdir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"stamp": stamp, "collected": {}}

    # 1) the debug log(s)
    logs = home / "logs"
    n = 0
    if logs.is_dir():
        for lf in logs.glob("corvin.log*"):
            if _safe_copy(lf, bdir / "logs" / lf.name):
                n += 1
    manifest["collected"]["debug_logs"] = n

    # 2) the actuating-repair journal (L5) + ACO diagnoses/nightly
    _safe_copy(home / "aco_repair.jsonl", bdir / "aco_repair.jsonl")
    diag = home / "aco" / "diagnoses"
    dn = 0
    if diag.is_dir():
        for p in [diag / "nightly.log", *diag.glob("*/*.json")]:
            if _safe_copy(p, bdir / "diagnoses" / p.relative_to(diag)):
                dn += 1
    manifest["collected"]["diagnoses"] = dn

    # 3) per-session Observable-Chat logs (L1)
    cn = 0
    for cd in home.rglob("chat_debug.jsonl*"):
        rel = cd.relative_to(home)
        if _safe_copy(cd, bdir / "chat_debug" / rel):
            cn += 1
    manifest["collected"]["chat_debug_logs"] = cn

    # 4) audit chains — metadata only (GDPR Art. 5), tailed to keep size sane
    an = 0
    if include_audit:
        for a in home.rglob("audit.jsonl"):
            if _is_secret(a.name):
                continue
            rel = a.relative_to(home)
            if _copy_tail(a, bdir / "audit" / rel, _AUDIT_TAIL_LINES):
                an += 1
    manifest["collected"]["audit_chains"] = an

    # 5) a FRESH nerve scan + system info (the detailed live probe)
    if run_nerve_scan:
        (bdir / "nerve_signals.json").write_text(
            json.dumps(_nerve_snapshot(), indent=2, ensure_ascii=False), encoding="utf-8")
        manifest["collected"]["nerve_scan"] = True
    (bdir / "system_info.json").write_text(
        json.dumps(_system_info(home), indent=2, ensure_ascii=False), encoding="utf-8")

    # 6) manifest + README
    (bdir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (bdir / "README.txt").write_text(
        "CorvinOS support bundle (ACO).\n\n"
        "This folder contains ONLY logs + metadata for debugging — no secrets,\n"
        "keys, tokens, prompts or message text (audit logs are metadata-only).\n"
        "Zip it (already zipped alongside) and send it to the maintainer to get\n"
        "your errors fixed.\n", encoding="utf-8")

    # 7) zip the folder
    zip_path = base / f"support-bundle-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in bdir.rglob("*"):
            if f.is_file() and not _is_secret(f.name):
                zf.write(f, f.relative_to(base))
    return zip_path


def create_default(out_dir: str | Path | None = None) -> Path:
    from forge import paths as _paths  # type: ignore  # noqa: PLC0415
    return create_bundle(_paths.corvin_home(), out_dir)
