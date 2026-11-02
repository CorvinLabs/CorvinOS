"""Platform probes: does the chat's persistence actually work on THIS host? (PLAN-0969 WP-2)

Each probe writes, reads and deletes one throw-away record through the same primitive the
chat relies on, and reports pass or fail. Nothing real is touched: probes work in a scratch
directory under ``<corvin_home>/.platform_probe`` (session memory uses a dedicated fake session
that is removed again). A failure carries the exception TYPE only, never a message or a path,
so a report can be pasted from a foreign machine without leaking anything (ADR-0179 floor).

Why this exists (CONCEPT-0125): two defects survived on Windows because nothing exercised the
path there. ``sessions/<channel>:<chat>`` is illegal in a Windows path (WinError 123), and the
no-follow open flag read straight off the ``os`` module is an ``AttributeError`` there, swallowed
by a broad ``except``. A probe turns that silent loss into a signal on the host where it happens.

The probes are pure functions with injectable seams so tests can run each against a deliberately
broken store. :func:`run_all` is what the nerve fiber (``platform.probe``) and the read-only
report route call.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: Probe names, in report order. Part of the report contract.
PROBE_NAMES = ("session_memory", "recall_store", "run_log", "voice_summary_store")

_UMLAUT_TEXT = "Käse — üßö \U0001f600"  # umlauts, em dash, a non-BMP char


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    #: Exception class name on failure, "" on success. Never a message (it can hold a path).
    error_type: str = ""
    #: Short, fixed vocabulary describing what failed, e.g. "write", "read", "fts5".
    stage: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "error_type": self.error_type, "stage": self.stage}


@dataclass
class _Stage:
    """Tracks which stage a probe reached, so a failure names it."""
    current: str = "setup"
    log: list[str] = field(default_factory=list)

    def at(self, stage: str) -> None:
        self.current = stage


def _fail(name: str, stage: _Stage, exc: BaseException) -> ProbeResult:
    return ProbeResult(name=name, ok=False, error_type=type(exc).__name__, stage=stage.current)


def _ok(name: str) -> ProbeResult:
    return ProbeResult(name=name, ok=True)


def scratch_root(home: Path) -> Path:
    """A fresh scratch directory under ``<home>/.platform_probe`` (never reused)."""
    root = Path(home) / ".platform_probe" / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=False)
    return root


def _remove_scratch(root: Path, home: Path) -> None:
    """Remove ``root`` only if it is a probe scratch dir inside ``home``."""
    try:
        resolved, base = root.resolve(), (Path(home) / ".platform_probe").resolve()
        if base in resolved.parents:
            shutil.rmtree(resolved, ignore_errors=True)
        base.rmdir()  # only succeeds when empty; harmless otherwise
    except OSError:
        pass


# ── probe: session memory (the WinError 123 class) ───────────────────────────

def probe_session_memory(
    tenant_id: str = "_default",
    *,
    write_files: Callable[..., None] | None = None,
) -> ProbeResult:
    """Create a session-memory dir through the REAL writer, read a file back, remove it.

    Goes through ``memory_bridge._session_root`` and ``_write_cc_memory_files``, so a bare
    ``<channel>:<chat>`` path (illegal on Windows) fails here, on the host it breaks.
    """
    stage = _Stage()
    session_root: Path | None = None
    existed = False
    try:
        stage.at("import")
        shared = Path(__file__).resolve().parents[4] / "corvin_operator" / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import memory_bridge as mb  # type: ignore

        stage.at("resolve")
        session_root = mb._session_root(tenant_id, "platform-probe", f"probe:{uuid.uuid4().hex[:8]}")
        existed = session_root.exists()
        mem_dir = session_root / ".claude" / "memory"

        stage.at("write")
        (write_files or mb._write_cc_memory_files)(mem_dir, _UMLAUT_TEXT, "", "")

        stage.at("read")
        written = list(mem_dir.glob("corvin_*.md"))
        if not written or _UMLAUT_TEXT not in written[0].read_text(encoding="utf-8"):
            raise OSError("session memory file missing or altered")
        return _ok("session_memory")
    except Exception as exc:  # noqa: BLE001 — a probe must never raise into the healer
        return _fail("session_memory", stage, exc)
    finally:
        if session_root is not None and not existed:
            shutil.rmtree(session_root, ignore_errors=True)


# ── probe: recall store (SQLite FTS5 + WAL) ──────────────────────────────────

def probe_recall_store(
    scratch: Path,
    *,
    connect: Callable[..., sqlite3.Connection] = sqlite3.connect,
) -> ProbeResult:
    """The recall DB needs FTS5 with the recall tokenizer and WAL on this filesystem.

    Probed on a scratch database, never on ``recall.db``: no real row is written.
    """
    stage = _Stage()
    con: sqlite3.Connection | None = None
    try:
        stage.at("open")
        con = connect(str(scratch / "probe_recall.db"), timeout=5.0, isolation_level=None)
        stage.at("wal")
        con.execute("PRAGMA journal_mode=WAL")
        stage.at("fts5")
        con.execute(
            "CREATE VIRTUAL TABLE probe_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2')")
        con.execute("INSERT INTO probe_fts(body) VALUES (?)", (_UMLAUT_TEXT,))
        stage.at("query")
        hit = con.execute("SELECT count(*) FROM probe_fts WHERE probe_fts MATCH 'kase'").fetchone()
        if not hit or hit[0] != 1:
            raise sqlite3.OperationalError("fts5 query returned no hit")
        return _ok("recall_store")
    except Exception as exc:  # noqa: BLE001
        return _fail("recall_store", stage, exc)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


# ── probe: append-only run log (the bare O_NOFOLLOW class) ───────────────────

def _append_flags() -> int:
    """The exact flag expression the run-event / feedback / voice-history writers use."""
    return (os.O_WRONLY | os.O_APPEND | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))


def probe_run_log(
    scratch: Path,
    *,
    os_open: Callable[..., int] = os.open,
) -> ProbeResult:
    """Append one JSONL line through ``os.open`` and read it back byte-exact."""
    stage = _Stage()
    path = scratch / "probe_run.jsonl"
    line = b'{"probe":"run_log"}\n'
    try:
        stage.at("open")
        fd = os_open(path, _append_flags(), 0o600)
        try:
            stage.at("write")
            os.write(fd, line)
        finally:
            os.close(fd)
        stage.at("read")
        if path.read_bytes() != line:  # a Windows text-mode fd would have written \r\n
            raise OSError("append log not byte-exact")
        return _ok("run_log")
    except Exception as exc:  # noqa: BLE001
        return _fail("run_log", stage, exc)


# ── probe: voice-summary store (UTF-8 + atomic replace over an existing file) ─

def probe_voice_summary_store(
    scratch: Path,
    *,
    replace: Callable[[Path, Path], object] | None = None,
) -> ProbeResult:
    """Write the recap metadata the way ``routes/voice.py`` does: tmp file, UTF-8, atomic
    replace over an existing file (the step an antivirus or a player can block on Windows)."""
    stage = _Stage()
    d = scratch / "voice-summary"
    dest = d / "summary.json"
    do_replace = replace or (lambda src, dst: src.replace(dst))
    try:
        stage.at("mkdir")
        d.mkdir(parents=True, exist_ok=True)
        for generation in (1, 2):  # the second pass replaces an EXISTING file
            tmp = dest.with_suffix(f".json.tmp{generation}")
            stage.at("write")
            tmp.write_text(f'{{"text": "{_UMLAUT_TEXT}", "n": {generation}}}', encoding="utf-8")
            stage.at("replace")
            do_replace(tmp, dest)
        stage.at("read")
        body = dest.read_text(encoding="utf-8")
        if _UMLAUT_TEXT not in body or '"n": 2' not in body:
            raise OSError("voice summary file missing or altered")
        return _ok("voice_summary_store")
    except Exception as exc:  # noqa: BLE001
        return _fail("voice_summary_store", stage, exc)


# ── runner + host facts ───────────────────────────────────────────────────────

def host_facts() -> dict:
    """Coarse, closed-vocabulary host description for a pasted report. No paths, no names."""
    try:
        con = sqlite3.connect(":memory:")
        fts5 = bool(con.execute(
            "SELECT 1 FROM pragma_compile_options WHERE compile_options='ENABLE_FTS5'").fetchone())
        con.close()
    except Exception:  # noqa: BLE001
        fts5 = False
    return {
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "sqlite": sqlite3.sqlite_version,
        "fts5_compiled": fts5,
        "has_o_nofollow": hasattr(os, "O_NOFOLLOW"),
        "has_o_binary": hasattr(os, "O_BINARY"),
        "has_real_fcntl": _has_real_fcntl(),
    }


def _has_real_fcntl() -> bool:
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:
        return False
    return not getattr(fcntl, "_corvin_win_shim", False)


def run_all(home: Path, tenant_id: str = "_default") -> list[ProbeResult]:
    """Run every probe once. Never raises; the scratch directory is always removed."""
    results: list[ProbeResult] = []
    scratch: Path | None = None
    try:
        scratch = scratch_root(home)
    except Exception as exc:  # noqa: BLE001 — cannot even create a dir under the home
        failed = ProbeResult(name="scratch", ok=False, error_type=type(exc).__name__, stage="mkdir")
        return [failed]
    try:
        results.append(probe_session_memory(tenant_id))
        results.append(probe_recall_store(scratch))
        results.append(probe_run_log(scratch))
        results.append(probe_voice_summary_store(scratch))
    finally:
        _remove_scratch(scratch, home)
    return results
