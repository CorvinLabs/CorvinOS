"""First-call permission layer.

Each tool's impl has a sha256. The first time a user runs a given (name, sha)
pair we ask for explicit consent and record it in ``approvals.json``. If the
impl changes (sha drift) we re-prompt. Consent files live inside the same
``.forge/`` workspace so wiping it resets all approvals.

Approval modes:
  - ``ask``         → interactive prompt (default for TTY)
  - ``yes``         → auto-approve everything (CI / scripted demos)
  - ``deny``        → fail closed (used in tests and for distrusted contexts)
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

Mode = Literal["ask", "yes", "deny"]


@dataclass
class Decision:
    approved: bool
    reason: str
    mode: Mode


class PermissionStore:
    FILENAME = "approvals.json"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / self.FILENAME
        self.lock_path = self.root / ".approvals.lock"
        if not self.path.exists():
            self._atomic_write(self.path, "{}\n")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        self._atomic_write(self.path, json.dumps(data, indent=2) + "\n")

    def is_approved(self, name: str, sha: str) -> bool:
        with self._locked():
            rec = self._load().get(name)
            return bool(rec and rec.get("sha") == sha)

    def record(self, name: str, sha: str, *, mode: Mode) -> None:
        with self._locked():
            data = self._load()
            data[name] = {"sha": sha, "approved_at": time.time(), "mode": mode}
            self._save(data)

    def revoke(self, name: str) -> None:
        with self._locked():
            data = self._load()
            if data.pop(name, None) is not None:
                self._save(data)


def decide(
    *,
    store: PermissionStore,
    name: str,
    sha: str,
    impl_text: str,
    mode: Mode = "ask",
) -> Decision:
    """Resolve a permission decision for one call. Caches yes-decisions."""
    if store.is_approved(name, sha):
        return Decision(True, "previously approved", mode)

    if mode == "yes":
        store.record(name, sha, mode="yes")
        return Decision(True, "auto-approved (mode=yes)", mode)

    if mode == "deny":
        return Decision(False, "denied (mode=deny)", mode)

    # mode == "ask"
    if not sys.stdin.isatty():
        return Decision(
            False,
            "non-interactive stdin — pass --yes to auto-approve",
            mode,
        )
    print(f"\n=== forge permission prompt =================================", file=sys.stderr)
    print(f"  tool : {name}", file=sys.stderr)
    print(f"  sha  : {sha}", file=sys.stderr)
    print(f"  size : {len(impl_text)} bytes", file=sys.stderr)
    preview = "\n".join(impl_text.splitlines()[:20])
    print(f"  --- first 20 lines ---", file=sys.stderr)
    print(preview, file=sys.stderr)
    if impl_text.count("\n") > 20:
        print("  --- (truncated) ---", file=sys.stderr)
    print(f"=============================================================", file=sys.stderr)
    answer = input("approve this tool? [y/N] ").strip().lower()
    if answer in ("y", "yes"):
        store.record(name, sha, mode="ask")
        return Decision(True, "user approved", mode)
    return Decision(False, "user declined", mode)
