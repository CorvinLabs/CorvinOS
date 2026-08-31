"""Tenant Sync (G5, ADR-0369) — the type-specific merge engine for cross-device learning.

CorvinOS runs on the operator's own machine. Cross-device learning means a tenant's
*learnable state* — CEL stage grades, learning-event JSONL, skills, memory — is shared
across the operator's instances (laptop, server) through a Git remote. Git is the
**transport + history** ONLY; the merge is done HERE, structurally, per data type — never
by `git merge` on working files (which would hand an end-user a text conflict they will
never resolve).

Merge rules (see ADR-0369, and the plan's LDD review F1/F4):
  * Grade store (``ce_stage_grades.json``): **union of the per-stage ``grades[]`` arrays**,
    then recompute ``n_grades``/``mean_score`` from the union. NOT "sum n_grades" — that
    double-counts a grade already present on both sides.
  * Learning events (``*.jsonl``): union of lines, de-duplicated, sorted. Append-only logs
    merge losslessly.
  * Free-form files (skills, memory): last-write-wins by mtime, with a collision report so
    the loser is never silently dropped without the operator seeing it.

Security (fail-closed intent, honestly bounded):
  * ``assert_no_raw_pii`` scans a payload for PII/secret shapes and raises before a push.
    It is **best-effort over free text**, NOT the structural fail-closed guarantee that
    telemetry's ``_assert_safe`` gives over a closed enum allowlist — learning state is
    free-form (memory prose, skill bodies, grade notes). The load-bearing protections are
    the mandatory GPG encryption + explicit consent + default-off flag; the scanner is a
    second line, not the guarantee. Do not describe it as fail-closed-equivalent.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PiiLeak(Exception):
    """Raised by ``assert_no_raw_pii`` when a payload carries a PII/secret shape."""


# ── PII backstop (best-effort; GPG + consent carry the real guarantee) ─────────
# Shapes that must never leave the machine in cleartext. Deliberately conservative
# (false positives are cheap: they just refuse a sync until the operator looks).
_PII_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"\b(?:sk-|ghp_|xoxb-)[A-Za-z0-9]{16,}")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?\d{4}){3,}")),
)


def scan_pii(text: str) -> list[str]:
    """Return the names of every PII shape found in ``text`` (empty = clean)."""
    return [name for name, pat in _PII_PATTERNS if pat.search(text or "")]


def assert_no_raw_pii(payload: str | bytes) -> None:
    """Raise ``PiiLeak`` if the payload carries a PII/secret shape. Best-effort — see
    the module docstring: this is a second line, not a fail-closed guarantee."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    hits = scan_pii(text)
    if hits:
        raise PiiLeak(f"payload carries PII/secret shape(s): {', '.join(sorted(set(hits)))}")


# ── merge primitives ───────────────────────────────────────────────────────────
def merge_jsonl(local: list[str], remote: list[str]) -> list[str]:
    """Union of two append-only JSONL logs: de-duplicated, stable-sorted. Blank lines
    dropped. A line present on both sides appears once (lossless, no double-count)."""
    seen: dict[str, None] = {}
    for line in [*local, *remote]:
        s = (line or "").strip()
        if s:
            seen.setdefault(s, None)
    return sorted(seen.keys())


def merge_grade_store(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Merge two ``ce_stage_grades.json`` dicts. For each stage, UNION the ``grades[]``
    arrays (a grade is identified by its full record; identical records collapse), then
    recompute ``n_grades``/``mean_score`` from the union. Never sums n_grades."""
    out: dict[str, Any] = {}
    for stage in set(local) | set(remote):
        l_entry = local.get(stage) or {}
        r_entry = remote.get(stage) or {}
        l_grades = l_entry.get("grades") or []
        r_grades = r_entry.get("grades") or []
        # union by canonical JSON of each grade record (order-insensitive, lossless)
        merged: dict[str, dict] = {}
        for g in [*l_grades, *r_grades]:
            merged.setdefault(json.dumps(g, sort_keys=True, default=str), g)
        grades = list(merged.values())
        scores = [g.get("score") for g in grades if isinstance(g.get("score"), (int, float))]
        out[stage] = {
            **{k: v for k, v in {**l_entry, **r_entry}.items() if k != "grades"},
            "grades": grades,
            "n_grades": len(grades),
            "mean_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
        }
    return out


@dataclass
class Collision:
    path: str
    reason: str
    kept: str  # "local" | "remote"


@dataclass
class SyncReport:
    merged_files: list[str] = field(default_factory=list)
    jsonl_lines_added: int = 0
    grade_stages_merged: int = 0
    collisions: list[Collision] = field(default_factory=list)
    pii_blocked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged_files": self.merged_files,
            "jsonl_lines_added": self.jsonl_lines_added,
            "grade_stages_merged": self.grade_stages_merged,
            "collisions": [c.__dict__ for c in self.collisions],
            "pii_blocked": self.pii_blocked,
            "ok": not self.pii_blocked,
        }


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def merge_tenant_dirs(local_dir: Path, remote_dir: Path) -> SyncReport:
    """Merge ``remote_dir`` INTO ``local_dir`` in place, type-specifically. Returns a
    report. Raises ``PiiLeak`` (via the caller's push-time assert) is NOT done here —
    this only merges local state; the PII assert runs on the OUTBOUND payload before push.

    File-type routing:
      * ``ce_stage_grades.json``  → grade-store union
      * ``*.jsonl``               → line union
      * everything else           → last-write-wins by mtime, collision recorded
    """
    local_dir = Path(local_dir)
    remote_dir = Path(remote_dir)
    report = SyncReport()

    for rpath in sorted(remote_dir.rglob("*")):
        if not rpath.is_file():
            continue
        rel = rpath.relative_to(remote_dir)
        lpath = local_dir / rel
        lpath.parent.mkdir(parents=True, exist_ok=True)

        if rpath.name == "ce_stage_grades.json":
            l = json.loads(_read(lpath) or "{}") if lpath.exists() else {}
            r = json.loads(_read(rpath) or "{}")
            merged = merge_grade_store(l, r)
            lpath.write_text(json.dumps(merged, indent=2, default=str), encoding="utf-8")
            report.grade_stages_merged += len(merged)
            report.merged_files.append(str(rel))
        elif rpath.suffix == ".jsonl":
            before = [ln for ln in _read(lpath).splitlines() if ln.strip()]
            merged = merge_jsonl(before, _read(rpath).splitlines())
            lpath.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")
            report.jsonl_lines_added += max(0, len(merged) - len(before))
            report.merged_files.append(str(rel))
        else:
            if not lpath.exists():
                lpath.write_bytes(rpath.read_bytes())
                report.merged_files.append(str(rel))
            else:
                l_mtime = lpath.stat().st_mtime
                r_mtime = rpath.stat().st_mtime
                if r_mtime > l_mtime and _read(rpath) != _read(lpath):
                    lpath.write_bytes(rpath.read_bytes())
                    report.collisions.append(Collision(str(rel), "mtime LWW", "remote"))
                    report.merged_files.append(str(rel))
                elif _read(rpath) != _read(lpath):
                    report.collisions.append(Collision(str(rel), "mtime LWW", "local"))
    return report


# ── live transport: GPG (mandatory) + git (transport) — G5 ─────────────────────
# The remote git repo only ever holds CIPHERTEXT: the tenant learning bundle is tarred
# and GPG-symmetric-encrypted before it is written into the clone and pushed. On pull it
# is decrypted into a temp dir, merged into the local state, re-encrypted, and pushed.
# So third-party PII (ADR-0369 C14) never lands on the remote in cleartext, and the
# assert_no_raw_pii backstop runs on the OUTBOUND bytes: they are ciphertext, so it
# passes — and would fire (drop the push) only if encryption silently produced plaintext.
import subprocess  # noqa: E402
import tarfile  # noqa: E402
import io  # noqa: E402

_ENC_NAME = "learning.tar.gz.gpg"


class SyncError(Exception):
    """A live-sync step (gpg/git) failed. The turn/route degrades, never crashes."""


def gpg_available() -> bool:
    try:
        subprocess.run(["gpg", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _gpg(args: list[str], *, data: bytes, passphrase: str) -> bytes:
    """Run gpg with the passphrase on fd 3 (never argv/env) and ``data`` on stdin."""
    import os  # noqa: PLC0415
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, (passphrase + "\n").encode("utf-8"))
        os.close(w_fd)
        w_fd = -1
        # pass_fds keeps r_fd at its own number in the child — reference that number,
        # not a hardcoded 3 (subprocess does not renumber passed fds).
        proc = subprocess.run(
            ["gpg", "--batch", "--yes", "--quiet", "--passphrase-fd", str(r_fd),
             "--pinentry-mode", "loopback", *args],
            input=data, capture_output=True, pass_fds=(r_fd,), timeout=120)
    finally:
        os.close(r_fd)
        if w_fd != -1:
            os.close(w_fd)
    if proc.returncode != 0:
        raise SyncError(f"gpg failed: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def gpg_encrypt(data: bytes, passphrase: str) -> bytes:
    """Symmetric AES-256 encryption (no keyring needed — passphrase from the Vault)."""
    return _gpg(["--symmetric", "--cipher-algo", "AES256", "-o", "-"],
                data=data, passphrase=passphrase)


def gpg_decrypt(blob: bytes, passphrase: str) -> bytes:
    return _gpg(["--decrypt", "-o", "-"], data=blob, passphrase=passphrase)


def bundle_dir(path: Path) -> bytes:
    """Deterministic-ish tar.gz of a directory tree (sorted names)."""
    path = Path(path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(path.rglob("*")):
            if f.is_file():
                tar.add(f, arcname=str(f.relative_to(path)))
    return buf.getvalue()


def unbundle(blob: bytes, dest: Path) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            # contain extraction to dest (no absolute paths / .. traversal)
            tgt = (dest / m.name).resolve()
            if not str(tgt).startswith(str(dest.resolve())):
                continue
            try:
                tar.extract(m, dest, filter="data")  # Py 3.12+: reject unsafe members
            except TypeError:  # older Python without the filter kwarg
                tar.extract(m, dest)


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=180,
                          env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true",
                               "HOME": str(cwd), "PATH": "/usr/bin:/bin"})
    if proc.returncode != 0:
        raise SyncError(f"git {args[0]} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def run_git_sync(local_dir: Path, remote_url: str, cache_dir: Path, passphrase: str,
                 *, pat: "str | None" = None,
                 author: str = "Corvin <corvin@localhost>") -> SyncReport:
    """The full live sync (G5): pull → decrypt → merge remote INTO local → re-encrypt →
    push. Git is transport+history only; the remote holds ONLY the GPG ciphertext blob.
    Raises SyncError on any gpg/git failure (the caller degrades, never crashes)."""
    if not gpg_available():
        raise SyncError("gpg not available — mandatory encryption cannot run")
    local_dir = Path(local_dir)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    clone = cache_dir / "clone"

    # PAT is injected into the URL for https remotes; never logged (SyncError truncates).
    url = remote_url
    if pat and remote_url.startswith("https://"):
        url = remote_url.replace("https://", f"https://{pat}@", 1)

    if (clone / ".git").is_dir():
        _git(["pull", "--ff-only", "origin", "HEAD"], clone)
    else:
        _git(["clone", "--depth", "1", url, str(clone)], cache_dir)
        _git(["config", "user.email", "corvin@localhost"], clone)
        _git(["config", "user.name", "Corvin"], clone)

    # decrypt the remote bundle (if any) and merge it INTO the local state
    remote_state = cache_dir / "remote_state"
    if remote_state.exists():
        for f in sorted(remote_state.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
    enc = clone / _ENC_NAME
    if enc.is_file():
        try:
            unbundle(gpg_decrypt(enc.read_bytes(), passphrase), remote_state)
        except SyncError:
            raise  # wrong passphrase / corrupt remote → surface, do not merge garbage
    report = merge_tenant_dirs(local_dir, remote_state)

    # re-bundle the merged local state, encrypt, and stage the ciphertext ONLY
    blob = gpg_encrypt(bundle_dir(local_dir), passphrase)
    assert_no_raw_pii(blob)  # ciphertext → passes; cleartext leak → drop the push
    (clone / _ENC_NAME).write_bytes(blob)

    _git(["add", _ENC_NAME], clone)
    status = _git(["status", "--porcelain"], clone)
    if status.strip():
        _git(["commit", "-m", "corvin: tenant learning sync", "--author", author], clone)
        _git(["push", "origin", "HEAD"], clone)
    else:
        report.collisions.append(Collision(_ENC_NAME, "no change to push", "local"))
    return report
