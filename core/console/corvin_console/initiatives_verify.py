"""Verify initiative evidence and record the results — keeps the board's facts current.

    python -m corvin_console.initiatives_verify [--tenant _default]

For every task / precondition in ``<tenant>/global/initiatives.json`` that
declares ``evidence`` it

* runs the listed pytest targets (repo-relative, must resolve INSIDE the repo —
  the file cannot make this run anything else) with the repo's interpreter, and
* checks that each listed path exists (existence only, nothing is read),

then writes ``verification: {at, passed, failed, errors, skipped,
paths_present, paths_total, missing_paths, summary, first_ok_at}`` back into
the item. The board (``initiatives.py``) derives status and progress from it.

Concurrency: results are merged into a FRESH read of the file right before the
atomic write, keyed by initiative/item id, so an operator edit made while the
tests ran is not lost. One content-free audit record per run
(``initiatives.verified``: counts only).

Run by ``corvin-initiatives-verify.timer`` every 5 min with ``--if-changed``:
the run is skipped unless the repo state or the evidence spec changed since the
last run, or that run is older than :data:`MAX_AGE_S` (30 min). So evidence
follows a code change within ~5 min without re-running the suites every tick.
Safe to run by hand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import initiatives as board_mod

REPO = Path(__file__).resolve().parents[3]
_TIMEOUT_S = 900
#: Even with nothing changed, re-verify at least this often.
MAX_AGE_S = 30 * 60
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped)")


def _safe_test_target(target: str) -> str | None:
    """Repo-relative pytest target that stays inside the repo, or None."""
    if not isinstance(target, str) or not target or target.startswith("-"):
        return None
    file_part = target.split("::", 1)[0]
    resolved = (REPO / file_part).resolve()
    try:
        resolved.relative_to(REPO)
    except ValueError:
        return None
    return target if resolved.exists() else None


def _python() -> str:
    for cand in (REPO / "core/console/.venv/bin/python", REPO / ".venv/bin/python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def run_tests(targets: list[str]) -> dict[str, Any]:
    safe = [t for t in (_safe_test_target(t) for t in targets) if t]
    rejected = len(targets) - len(safe)
    if not safe:
        return {"passed": 0, "failed": 0, "errors": len(targets), "skipped": 0,
                "summary": f"{rejected} test target(s) missing or outside the repo"}
    # Tests run against a THROWAWAY home: never the live install, its audit
    # chain or its tenant files.
    sandbox = tempfile.mkdtemp(prefix="corvin-initiatives-verify-")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CORVIN_HOME": sandbox,
           "VOICE_AUDIT_PATH": os.path.join(sandbox, "audit.jsonl"), "CORVIN_TENANT_ID": "_default"}
    try:
        proc = subprocess.run(
            [_python(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:logging",
             "--continue-on-collection-errors", *safe],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        tail = next((ln for ln in reversed(proc.stdout.splitlines()) if _SUMMARY_RE.search(ln)), "")
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 1, "skipped": 0,
                "summary": f"timed out after {_TIMEOUT_S}s"}
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for n, kind in _SUMMARY_RE.findall(tail):
        counts["errors" if kind.startswith("error") else kind] += int(n)
    counts["errors"] += rejected
    if not tail:
        counts["errors"] += 1
    summary = tail.strip("= ").split(" in ")[0] if tail else f"pytest exit {proc.returncode}, no summary"
    summary = ", ".join(x for x in summary.split(", ") if "warning" not in x)
    if rejected:
        summary += f"; {rejected} target(s) missing/outside repo"
    return {**counts, "summary": summary}


def check_paths(paths: list[str]) -> dict[str, Any]:
    missing = []
    for p in paths:
        path = Path(p) if os.path.isabs(p) else REPO / p
        if not path.exists():
            missing.append(p)
    return {"paths_present": len(paths) - len(missing), "paths_total": len(paths),
            "missing_paths": missing}


def verify_item(item: dict[str, Any], cache: dict[tuple, dict], now_iso: str) -> dict[str, Any] | None:
    ev = item.get("evidence")
    if not isinstance(ev, dict) or not (ev.get("tests") or ev.get("paths")):
        return None
    tests = list(ev.get("tests") or [])
    key = tuple(tests)
    if tests and key not in cache:
        cache[key] = run_tests(tests)
    t = cache.get(key, {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "summary": ""})
    p = check_paths(list(ev.get("paths") or []))
    ok = (t["failed"] == 0 and t["errors"] == 0 and p["paths_present"] == p["paths_total"]
          and (t["passed"] + p["paths_total"]) > 0)
    prev = item.get("verification") if isinstance(item.get("verification"), dict) else {}
    parts = [t["summary"]] if tests else []
    if p["paths_total"]:
        parts.append(f"{p['paths_present']}/{p['paths_total']} paths present")
    return {
        "at": now_iso,
        **{k: t[k] for k in ("passed", "failed", "errors", "skipped")},
        **p,
        "summary": "; ".join(x for x in parts if x),
        # When it FIRST went green — kept across runs, cleared when it breaks.
        "first_ok_at": (prev.get("first_ok_at") or now_iso) if ok else None,
    }


def _items(ini: dict[str, Any]):
    for t in ini.get("tasks") or []:
        yield "task", t
    for c in ini.get("preconditions") or []:
        yield "pre", c


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                              text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return ""


def fingerprint(raw: dict[str, Any]) -> str:
    """Hash of everything a verification result depends on.

    Git HEAD + working-tree status (tracked changes AND untracked files) +
    the evidence specs + existence/mtime of every evidence path. Any change
    → new fingerprint → the next ``--if-changed`` tick re-verifies.
    """
    h = hashlib.sha256()
    h.update(_git("rev-parse", "HEAD").encode())
    h.update(_git("status", "--porcelain", "--untracked-files=all").encode())
    h.update(_git("diff", "HEAD").encode())
    for ini in raw.get("initiatives", []):
        for _kind, item in _items(ini):
            ev = item.get("evidence")
            if not isinstance(ev, dict):
                continue
            h.update(json.dumps(ev, sort_keys=True).encode())
            for p in ev.get("paths") or []:
                path = Path(p) if os.path.isabs(p) else REPO / p
                try:
                    h.update(f"{p}:{path.stat().st_mtime_ns}".encode())
                except OSError:
                    h.update(f"{p}:missing".encode())
    return h.hexdigest()


def _state_path(tenant_id: str) -> Path | None:
    p = board_mod.board_path(tenant_id)
    return None if p is None else p.parent / ".initiatives_verify.state.json"


def needs_run(tenant_id: str, now: float | None = None) -> tuple[bool, str]:
    """(run?, reason) for ``--if-changed``."""
    now = time.time() if now is None else now
    raw, path = board_mod._load_raw(tenant_id)
    if path is None or not path.is_file():
        return False, "no initiatives file"
    sp = _state_path(tenant_id)
    try:
        state = json.loads(sp.read_text()) if sp and sp.is_file() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    if now - float(state.get("at", 0)) > MAX_AGE_S:
        return True, "last run older than max age"
    if state.get("fingerprint") != fingerprint(raw):
        return True, "repo or evidence changed"
    return False, "unchanged"


def lock_path(tenant_id: str) -> Path | None:
    p = board_mod.board_path(tenant_id)
    return None if p is None else p.parent / ".initiatives_verify.lock"


def is_running(tenant_id: str) -> bool:
    """True while a verification holds the lock (pid alive)."""
    lp = lock_path(tenant_id)
    if lp is None or not lp.exists():
        return False
    try:
        pid = int(lp.read_text().strip() or 0)
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def verify(tenant_id: str) -> dict[str, Any]:
    lp = lock_path(tenant_id)
    if lp is None:
        return {"verified": 0, "reason": "tenant home unresolvable"}
    if is_running(tenant_id):
        return {"verified": 0, "reason": "already running"}
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(str(os.getpid()))
    try:
        return _verify(tenant_id)
    finally:
        try:
            lp.unlink()
        except OSError:
            pass


def start_background(tenant_id: str) -> bool:
    """Spawn a detached verification run. False if one is already running."""
    if is_running(tenant_id):
        return False
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(REPO / "core/console"), str(REPO), os.environ.get("PYTHONPATH", "")])}
    subprocess.Popen(
        [_python(), "-m", "corvin_console.initiatives_verify", "--tenant", tenant_id],
        cwd=str(REPO), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    return True


def _verify(tenant_id: str) -> dict[str, Any]:
    raw, path = board_mod._load_raw(tenant_id)
    if path is None or not path.is_file():
        return {"verified": 0, "reason": "no initiatives file"}
    now_iso = board_mod._iso(time.time())
    cache: dict[tuple, dict] = {}
    results: dict[tuple[str, str, str], dict] = {}
    for ini in raw["initiatives"]:
        for kind, item in _items(ini):
            v = verify_item(item, cache, now_iso)
            if v is not None:
                key = str(item.get("id") or item.get("label"))
                results[(str(ini.get("id")), kind, key)] = v

    # Merge into a fresh read so concurrent operator edits survive.
    fresh, _ = board_mod._load_raw(tenant_id)
    for ini in fresh["initiatives"]:
        for kind, item in _items(ini):
            key = (str(ini.get("id")), kind, str(item.get("id") or item.get("label")))
            if key in results:
                item["verification"] = results[key]
    board_mod._write_raw(path, fresh)

    sp = _state_path(tenant_id)
    if sp is not None:
        sp.write_text(json.dumps({"at": time.time(), "fingerprint": fingerprint(fresh)}))
    green = sum(1 for v in results.values() if v["first_ok_at"])
    summary = {"verified": len(results), "green": green, "red": len(results) - green,
               "test_runs": len(cache)}
    _audit(tenant_id, summary)
    return summary


def _audit(tenant_id: str, summary: dict[str, Any]) -> None:
    """One content-free chained record per run. Never fails the verification."""
    try:
        from core.paths import tenant_audit_chain  # noqa: PLC0415
        from forge import security_events  # type: ignore  # noqa: PLC0415

        security_events.write_event(
            tenant_audit_chain(tenant_id), "initiatives.verified", severity="INFO",
            details={"tenant_id": tenant_id, **summary},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"initiatives_verify: audit write failed: {type(exc).__name__}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", default=os.environ.get("CORVIN_TENANT_ID", "_default"))
    ap.add_argument("--if-changed", action="store_true",
                    help="skip unless repo/evidence changed or the last run is older than 30 min")
    args = ap.parse_args(argv)
    try:
        if args.if_changed:
            run, reason = needs_run(args.tenant)
            if not run:
                print(json.dumps({"skipped": True, "reason": reason}))
                return 0
        summary = verify(args.tenant)
    except board_mod.InitiativeError as exc:
        print(f"initiatives_verify: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
