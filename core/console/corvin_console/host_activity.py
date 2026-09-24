"""Host-level activity on this machine — interactive Claude Code sessions and git commits (ADR-2060).

Two readers, both read-only and in place (nothing is copied):

* :func:`agent_sessions` — the operator's interactive Claude Code sessions.
  Live ones come from the CLI's session registry (``<claude_home>/sessions/<pid>.json``,
  the pid checked alive), finished ones from transcripts under
  ``<claude_home>/projects/`` touched within :data:`WINDOW_S`.
* :func:`git_commits` — non-merge commits of a repository within the window.

Privacy boundary: ONLY ``entrypoint == "cli"`` sessions (a person at a terminal).
``sdk-cli`` sessions are CorvinOS's own bridge / ACS workers; their transcripts
hold other people's messages and the ``chat`` source already covers them. A
session whose cwd lies under ``CORVIN_HOME`` is excluded too. A session is
titled by its own ``ai-title`` — never by prompt text.

Tenancy: this is data of the OS user running the console (one ``~/.claude``,
one checkout), so it belongs to :data:`HOST_TENANT` only — callers return
nothing for any other tenant.

Cost: a transcript is re-read only when its (mtime, size) changed, and then only
its head (entrypoint, cwd, start) and its tail (latest ``ai-title``). Only the
sync job (:func:`session_commit_subjects`) reads whole transcripts.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

HOST_TENANT = "_default"
WINDOW_S = 7 * 86400
HOST_NOTE = "Host-level source (this machine's Claude Code sessions and git history) — shown to the default tenant only."

_HEAD_BYTES = 64 * 1024
_TAIL_BYTES = 256 * 1024
_TITLE_MAX = 120

_lock = threading.Lock()
_meta_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
_title_cache: dict[str, str] = {}
_git_cache: dict[str, tuple[tuple, float, list[dict[str, Any]]]] = {}
_commit_cmd_cache: dict[str, tuple[int, int, list[str]]] = {}


# ── paths ────────────────────────────────────────────────────────────────────

def claude_home() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".claude"


def repo_root() -> Path:
    """The checkout this console runs from (core/console/corvin_console → repo)."""
    return Path(__file__).resolve().parents[3]


def _corvin_home() -> Path | None:
    try:
        from core.paths.tenant import corvin_home  # noqa: PLC0415

        return Path(corvin_home()).resolve()
    except Exception:  # noqa: BLE001
        env = os.environ.get("CORVIN_HOME", "").strip()
        return Path(env).resolve() if env else None


def _encode_cwd(cwd: str) -> str:
    """Claude Code's project-directory name for a cwd."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _is_worker_cwd(cwd: str, corvin: Path | None) -> bool:
    if not cwd:
        return True
    if corvin is None:
        return False
    try:
        return Path(cwd).resolve().is_relative_to(corvin)
    except (OSError, ValueError):
        return False


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    if isinstance(value, str) and value:
        from datetime import datetime, timezone  # noqa: PLC0415

        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None


# ── transcripts ──────────────────────────────────────────────────────────────

def _read_head(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"entrypoint": None, "cwd": None, "started": None, "session_id": path.stem}
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_HEAD_BYTES)
    except OSError:
        return out
    for raw in chunk.split(b"\n"):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue  # the last line of a truncated head
        if not isinstance(d, dict):
            continue
        if out["entrypoint"] is None and d.get("entrypoint"):
            out["entrypoint"] = d["entrypoint"]
        if out["cwd"] is None and d.get("cwd"):
            out["cwd"] = d["cwd"]
        if out["started"] is None and d.get("timestamp"):
            out["started"] = _ts(d["timestamp"])
        if out["entrypoint"] and out["cwd"] and out["started"]:
            break
    return out


def _title_from(chunk: bytes) -> str | None:
    title = None
    for raw in chunk.split(b"\n"):
        if b'"ai-title"' not in raw and b'"custom-title"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        t = d.get("customTitle") or d.get("aiTitle") if isinstance(d, dict) else None
        if t:
            title = str(t)
    return title


def _session_title(path: Path, size: int) -> str | None:
    """Latest ai-title: from the tail; one full read only if the tail never had one."""
    key = str(path)
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, size - _TAIL_BYTES))
            t = _title_from(fh.read())
            if t is None and key not in _title_cache and size > _TAIL_BYTES:
                fh.seek(0)
                t = _title_from(fh.read())
    except OSError:
        t = None
    if t:
        _title_cache[key] = t
    return _title_cache.get(key)


def _transcript_meta(path: Path) -> dict[str, Any] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _lock:
        hit = _meta_cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        head = hit[2] if hit else _read_head(path)
    meta = {**head, "mtime": st.st_mtime, "title": _session_title(path, st.st_size), "path": key}
    with _lock:
        _meta_cache[key] = (st.st_mtime_ns, st.st_size, meta)
    return meta


def _transcripts(root: Path, since: float, corvin: Path | None) -> Iterator[Path]:
    projects = root / "projects"
    if not projects.is_dir():
        return
    worker_prefix = _encode_cwd(str(corvin)) if corvin else None
    for d in projects.iterdir():
        # Bridge/ACS workers run under CORVIN_HOME: thousands of transcripts
        # skipped here by name, before a single stat.
        if not d.is_dir() or (worker_prefix and d.name.startswith(worker_prefix)):
            continue
        for f in d.glob("*.jsonl"):
            try:
                if f.stat().st_mtime >= since:
                    yield f
            except OSError:
                continue


# ── live registry ────────────────────────────────────────────────────────────

def _proc_start(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # comm may contain spaces/parens: fields after the LAST ')'; starttime is field 22.
    rest = stat[stat.rfind(")") + 2:].split()
    return rest[19] if len(rest) > 19 else None


def _alive(entry: dict[str, Any]) -> bool:
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        return False
    if Path("/proc").is_dir():
        start = _proc_start(pid)
        if start is None:
            return False
        want = entry.get("procStart")
        return want is None or str(want) == start  # a reused pid is not the session
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_registry(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    reg = root / "sessions"
    if not reg.is_dir():
        return out
    for f in reg.glob("*.json"):
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(e, dict) or not e.get("sessionId"):
            continue
        if e.get("kind") != "interactive" or e.get("entrypoint", "cli") != "cli":
            continue
        if _alive(e):
            out[str(e["sessionId"])] = e
    return out


# ── public: sessions ─────────────────────────────────────────────────────────

def agent_sessions(now: float | None = None, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Interactive Claude Code sessions: live + finished within the window.

    Each: ``{session_id, live, state (busy|idle|ended), title, project, cwd,
    started, last_active, name, pid}``. ``cwd`` is for matching only — callers
    must not render it.
    """
    now = time.time() if now is None else now
    root = root or claude_home()
    corvin = _corvin_home()
    live = _live_registry(root)
    out: dict[str, dict[str, Any]] = {}
    for path in _transcripts(root, now - WINDOW_S, corvin):
        m = _transcript_meta(path)
        if not m or m["entrypoint"] != "cli" or _is_worker_cwd(m["cwd"] or "", corvin):
            continue
        sid = m["session_id"]
        prev = out.get(sid)
        if prev and prev["last_active"] >= m["mtime"]:
            continue
        out[sid] = {
            "session_id": sid, "live": False, "state": "ended",
            "title": (m["title"] or "")[:_TITLE_MAX] or None,
            "project": Path(m["cwd"]).name if m["cwd"] else None, "cwd": m["cwd"],
            "started": m["started"], "last_active": m["mtime"], "name": None, "pid": None,
            "transcript": m["path"],
        }
    for sid, e in live.items():
        cwd = str(e.get("cwd") or "")
        if _is_worker_cwd(cwd, corvin):
            continue
        rec = out.get(sid)
        if rec is None:
            path = root / "projects" / _encode_cwd(cwd) / f"{sid}.jsonl"
            m = _transcript_meta(path) if path.exists() else None
            rec = {
                "session_id": sid, "title": ((m or {}).get("title") or "")[:_TITLE_MAX] or None,
                "project": Path(cwd).name if cwd else None, "cwd": cwd,
                "started": (m or {}).get("started"), "last_active": (m or {}).get("mtime"),
                "transcript": (m or {}).get("path"),
            }
            out[sid] = rec
        rec.update({
            "live": True, "state": "busy" if e.get("status") == "busy" else "idle",
            "name": e.get("name"), "pid": e.get("pid"),
            "started": rec.get("started") or _ts(e.get("startedAt")),
            "last_active": max(x for x in (rec.get("last_active"), _ts(e.get("updatedAt")), 0.0) if x is not None),
        })
    return sorted(out.values(), key=lambda r: -(r.get("last_active") or 0))


def session_commit_commands(transcript: str) -> list[str]:
    """Every ``git commit`` command text the session ran (whole-file read, cached)."""
    path = Path(transcript)
    try:
        st = path.stat()
    except OSError:
        return []
    with _lock:
        hit = _commit_cmd_cache.get(transcript)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    cmds: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "git commit" not in line or '"tool_use"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                content = (d.get("message") or {}).get("content") if isinstance(d, dict) else None
                for c in content if isinstance(content, list) else []:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        cmd = str((c.get("input") or {}).get("command") or "")
                        if "git commit" in cmd:
                            cmds.append(cmd)
    except OSError:
        return []
    with _lock:
        _commit_cmd_cache[transcript] = (st.st_mtime_ns, st.st_size, cmds)
    return cmds


def sessions_for_commits(commits: list[dict[str, Any]], sessions: list[dict[str, Any]],
                         repo: Path) -> dict[str, list[str]]:
    """``{sha: [session_id, …]}`` — a session made a commit when one of its
    ``git commit`` commands carries the commit's subject, it ran in this repo,
    and the commit time lies inside the session's lifetime."""
    out: dict[str, list[str]] = {}
    repo_r = repo.resolve()
    for s in sessions:
        if not s.get("transcript") or not s.get("cwd"):
            continue
        try:
            if not Path(s["cwd"]).resolve().is_relative_to(repo_r):
                continue
        except (OSError, ValueError):
            continue
        cmds = session_commit_commands(s["transcript"])
        if not cmds:
            continue
        lo = (s.get("started") or 0) - 60
        hi = (s.get("last_active") or time.time()) + 60
        for c in commits:
            if len(c["subject"]) >= 12 and lo <= c["ts"] <= hi and any(c["subject"] in cmd for cmd in cmds):
                out.setdefault(c["sha"], []).append(s["session_id"])
    return out


# ── public: git ──────────────────────────────────────────────────────────────

def repo_slug(repo: Path) -> str:
    """The repository's name as it appears in run ids (``commit:<slug>:<sha>``)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", repo.name) or "repo"


def _git_key(repo: Path) -> tuple:
    g = repo / ".git"
    parts = []
    for p in (g / "logs" / "HEAD", g / "packed-refs", g / "HEAD"):
        try:
            parts.append(p.stat().st_mtime_ns)
        except OSError:
            parts.append(0)
    return tuple(parts)


def git_commits(repo: Path | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """``[{sha, short, ts, subject, repo (slug)}]`` newest first, non-merge, within the window."""
    repo = repo or repo_root()
    now = time.time() if now is None else now
    if not (repo / ".git").exists():
        return []
    key = _git_key(repo)
    ck = str(repo)
    with _lock:
        hit = _git_cache.get(ck)
        # Keyed on the reflog: a new commit / pull invalidates; else a minute's reuse.
        if hit and hit[0] == key and now - hit[1] < 60:
            return hit[2]
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo), "log", f"--since={int(now - WINDOW_S)}", "--no-merges",
             "-n", "2000", "--pretty=format:%H%x1f%ct%x1f%s%x1e"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if cp.returncode != 0:
        return []
    out = []
    for rec in cp.stdout.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) != 3:
            continue
        sha, ct, subject = parts
        try:
            ts = float(ct)
        except ValueError:
            continue
        # Fixed 12 chars, not git's auto-abbrev: the short sha is part of a stored
        # run link and must not change length as the repository grows.
        out.append({"sha": sha, "short": sha[:12], "ts": ts, "subject": subject, "repo": repo_slug(repo)})
    with _lock:
        _git_cache[ck] = (key, now, out)
    return out


ADR_RE = re.compile(r"\bADR-(\d{3,4})\b")


def adr_refs(subject: str) -> list[str]:
    seen: list[str] = []
    for n in ADR_RE.findall(subject):
        a = f"ADR-{int(n):04d}"
        if a not in seen:
            seen.append(a)
    return seen
