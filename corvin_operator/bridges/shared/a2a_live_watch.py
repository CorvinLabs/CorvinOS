"""Continuous A2A health watch — "is the running host healthy, and will a fresh peer's task go through?"

Runs on a timer (``ops/systemd/corvin-a2a-watch.{service,timer}``), one pass per run, and answers with a
verdict plus a line in ``<CORVIN_HOME>/logs/a2a_watch.jsonl`` and an up-to-date
``<CORVIN_HOME>/logs/a2a_watch.status.json``. Exit status: 0 = healthy or degraded (warnings only),
1 = something CRITICAL is wrong with the HOST (``systemctl --user --failed`` then shows it), 2 = the watch
itself could not run.

What it measures (the goal: A2A just works on every freshly installed instance):

  CRITICAL — the host is wrong
    service_fresh   the serving process started AFTER the newest A2A source file on disk. A service keeps
                    the code it started with; this is the failure class that left the quota fast-fail and the
                    capacity signal undeployed for hours.
    ordinary_ping / pong_capacity / task_status_unknown / reaimed_ignored / forged_refused / feed_shape
                    the contract of ``a2a_live_probe`` over the host's real HTTP boundary.

  WARN — look at it, the host itself is fine
    peer_offline        a paired peer is not reachable (measured by the host's own probe)
    peer_probe_stale    the host has not probed a peer for >5 min (its connectivity manager is stuck)
    peer_limit_reached  the peer reports its daily compute pool as spent (free tier: 10 agent tasks/day)
    peer_pool_pressure  we alone already used >=8 of those 10 units on the peer today (UTC)
    peer_refusing       the peer's last >=3 answers to us were refusals — and what it said about why

Nothing here raises, spends a peer's quota, or sends a task: only pings and reads. Concurrent runs are
serialised by a lock (the second one exits 0 without doing anything).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from a2a_live_probe import Check, probe_host  # noqa: E402

try:
    import fcntl  # POSIX only; without it concurrent runs are simply not serialised
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

POOL_LIMIT = 10                 # free tier: compute_units_per_day (limits.FREE_TIER)
POOL_WARN_AT = 8
REFUSAL_STREAK = 3
PROBE_STALE_S = 300.0
LOG_MAX_BYTES = 1_000_000
LOG_KEEP_LINES = 2000
#: A2A source whose change means "a running host may now be behind the disk".
SOURCE_GLOBS = (
    "corvin_operator/bridges/shared/remote_trigger_*.py",
    "corvin_operator/bridges/shared/a2a_*.py",
    "corvin_operator/license/compute_quota.py",
    "core/console/corvin_console/routes/a2a_*.py",
    "core/console/corvin_console/routes/federation_routes.py",
    "core/federation/*.py",
    "core/gateway/corvin_gateway/app.py",
)
#: Files the glob matches that the HOST never loads (the watch and its probe run beside it): a change to
#: them says nothing about what the running process is missing.
NOT_LOADED_BY_HOST = ("a2a_live_",)
_RAN = ("ok", "filtered", "timeout")   # an answer that means the peer's worker ran (a unit was spent)


# ── pure analysis (unit-tested) ───────────────────────────────────────────

def source_is_newer(started_ts: float | None, newest_src_ts: float | None, newest_name: str = "",
                    *, slack_s: float = 5.0) -> Check:
    """The serving process must have started after the newest A2A source file."""
    if started_ts is None:
        return Check("service_fresh", True, "skipped — start time of the host is not known here", "warn")
    if newest_src_ts is None:
        return Check("service_fresh", True, "skipped — no A2A source files found", "warn")
    if newest_src_ts > started_ts + slack_s:
        lag = int(newest_src_ts - started_ts)
        return Check("service_fresh", False,
                     f"the host started {_hm(started_ts)} but {newest_name or 'an A2A source file'} changed "
                     f"{_hm(newest_src_ts)} ({lag // 60} min later) — it serves OLD code; restart it")
    return Check("service_fresh", True, f"started {_hm(started_ts)}, newest A2A source {_hm(newest_src_ts)}")


def _hm(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _num(x: Any) -> float:
    """A finite float, else 0.0 — a feed is data from another process, never trusted to be numeric."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return 0.0
    return float(x) if x == x and abs(x) != float("inf") else 0.0


def utc_midnight(now: float) -> float:
    d = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def analyse_peers(feed: dict | None, now: float) -> list[Check]:
    """Warnings about peers, from the host's OWN measurements (feed peers + history). Never raises."""
    if not isinstance(feed, dict):
        return []
    out: list[Check] = []
    peers = [p for p in feed.get("peers") or [] if isinstance(p, dict) and p.get("peer_id")]
    msgs = [m for m in feed.get("messages") or [] if isinstance(m, dict)]
    day0 = utc_midnight(now)
    for p in peers:
        pid, label = str(p["peer_id"]), str(p.get("label") or p["peer_id"])[:40]
        presence = p.get("presence")
        if presence in ("disabled", "removed"):
            continue
        if presence == "offline":
            out.append(Check("peer_offline", False, f"{label}: not reachable", "warn"))
        lc = _num(p.get("last_check_at"))
        if lc and now - lc > PROBE_STALE_S:
            out.append(Check("peer_probe_stale", False,
                             f"{label}: the host last probed it {int((now - lc) // 60)} min ago", "warn"))
        if p.get("task_capacity") == "limit_reached":
            out.append(Check("peer_limit_reached", False,
                             f"{label}: daily compute limit reached — it refuses tasks until 00:00 UTC", "warn"))
        mine = sorted((m for m in msgs if m.get("peer_id") == pid and m.get("direction") == "in"
                       and m.get("kind") == "response"), key=lambda m: _num(m.get("ts")))
        used = sum(1 for m in mine if _num(m.get("ts")) >= day0 and m.get("status") in _RAN)
        if used >= POOL_WARN_AT:
            out.append(Check("peer_pool_pressure", False,
                             f"{label}: {used} of {POOL_LIMIT} daily units on a free-tier peer already used by us today (UTC)",
                             "warn"))
        streak = 0
        for m in reversed(mine):
            if m.get("status") == "rejected":
                streak += 1
            else:
                break
        if streak >= REFUSAL_STREAK:
            why = str(next((m.get("error") for m in reversed(mine) if m.get("status") == "rejected"), "") or "")
            out.append(Check("peer_refusing", False,
                             f"{label}: its last {streak} answers were refusals — "
                             f"{why[:160] or 'no reason named (an older build?)'}", "warn"))
    return out


def verdict(checks: list[Check]) -> str:
    if any(not c.ok and c.severity == "critical" for c in checks):
        return "broken"
    if any(not c.ok for c in checks):
        return "degraded"
    return "healthy"


# ── environment: process start, source freshness ──────────────────────────

def service_started_ts(unit: str | None) -> float | None:
    """Wall-clock start of a systemd --user unit (Linux). None where it cannot be told."""
    if not unit or not sys.platform.startswith("linux"):
        return None
    try:
        env = dict(os.environ)
        # A timer/cron/bare-shell run may lack the user-manager coordinates systemctl --user needs; without
        # them the freshness check would silently skip — on exactly the hosts it exists to catch.
        rt = f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else ""
        if rt and "XDG_RUNTIME_DIR" not in env and Path(rt).is_dir():
            env["XDG_RUNTIME_DIR"] = rt
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={rt}/bus")
        out = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestampMonotonic", "--value"],
                             capture_output=True, text=True, timeout=10, env=env).stdout.strip()
        mono = int(out)
        if mono <= 0:
            return None
        boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
        return boot + mono / 1_000_000
    except Exception:  # noqa: BLE001
        return None


def newest_source(repo: Path) -> tuple[float | None, str]:
    best, name = None, ""
    for pattern in SOURCE_GLOBS:
        for f in repo.glob(pattern):
            if f.name.startswith(("test_", *NOT_LOADED_BY_HOST)) or not f.is_file():
                continue
            with contextlib.suppress(OSError):
                m = f.stat().st_mtime
                if best is None or m > best:
                    best, name = m, f.name
    return best, name


# ── the run ───────────────────────────────────────────────────────────────

def run_once(base_url: str, *, unit: str | None, repo: Path, origins_dir: Path | None,
             timeout_s: float, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    checks: list[Check] = []
    started = service_started_ts(unit)
    src_ts, src_name = newest_source(repo)
    checks.append(source_is_newer(started, src_ts, src_name))
    probe_checks, feed = probe_host(base_url, origins_dir=origins_dir, timeout_s=timeout_s)
    checks.extend(probe_checks)
    checks.extend(analyse_peers(feed, now))
    return {
        "ts": now, "at": _dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "url": base_url, "verdict": verdict(checks),
        "checks": [c.as_dict() for c in checks],
        "peers": [{"peer_id": str(p.get("peer_id"))[:36], "label": str(p.get("label") or "")[:40],
                   "presence": p.get("presence"), "task_capacity": p.get("task_capacity")}
                  for p in ((feed or {}).get("peers") or []) if isinstance(p, dict)],
    }


# ── persistence ───────────────────────────────────────────────────────────

def persist(report: dict, home: Path) -> None:
    """Append one JSON line (capped) and replace the status file atomically. Best effort."""
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    jl = logs / "a2a_watch.jsonl"
    with contextlib.suppress(OSError):
        if jl.exists() and jl.stat().st_size > LOG_MAX_BYTES:
            keep = jl.read_text(encoding="utf-8").splitlines()[-LOG_KEEP_LINES:]
            tmp = jl.with_suffix(".tmp")
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            os.replace(tmp, jl)
    with open(jl, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, separators=(",", ":")) + "\n")
    st = logs / "a2a_watch.status.json"
    tmp = st.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1), encoding="utf-8")
    os.replace(tmp, st)


@contextlib.contextmanager
def _single_instance(home: Path):
    """Non-blocking lock: yields True when this is the only run, False when another holds it."""
    if fcntl is None:
        yield True
        return
    try:
        (home / "logs").mkdir(parents=True, exist_ok=True)
        fd = os.open(home / "logs" / "a2a_watch.lock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        # No usable log dir: the lock is a convenience, the VERDICT is the point — run unserialised.
        yield True
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _render(report: dict) -> str:
    lines = [f"A2A watch {report['at']} — {report['verdict'].upper()} ({report['url']})"]
    for c in report["checks"]:
        mark = "ok  " if c["ok"] else ("FAIL" if c["severity"] == "critical" else "WARN")
        lines.append(f"  [{mark}] {c['name']}: {c['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Continuous A2A health watch (one pass).")
    ap.add_argument("--url", default=os.environ.get("CORVIN_LIVE_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--unit", default="corvin-webui", help="systemd --user unit serving the host ('' = skip freshness)")
    ap.add_argument("--home", default=os.environ.get("CORVIN_HOME") or str(Path.home() / ".corvin"))
    ap.add_argument("--origins-dir", default=None)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--quiet", action="store_true", help="print only when not healthy")
    a = ap.parse_args(argv)
    home = Path(a.home)
    repo = Path(__file__).resolve().parents[3]
    try:
        with _single_instance(home) as only:
            if not only:
                print("A2A watch: another run is in progress — skipped")
                return 0
            report = run_once(a.url, unit=a.unit or None, repo=repo,
                              origins_dir=Path(a.origins_dir) if a.origins_dir else None, timeout_s=a.timeout)
            with contextlib.suppress(Exception):
                persist(report, home)
    except Exception as e:  # noqa: BLE001 — the watch itself must say so, loudly
        print(f"A2A watch: could not run ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr)
        return 2
    if not (a.quiet and report["verdict"] == "healthy"):
        print(_render(report))
    return 1 if report["verdict"] == "broken" else 0


if __name__ == "__main__":
    raise SystemExit(main())
