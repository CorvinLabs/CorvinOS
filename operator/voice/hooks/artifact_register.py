#!/usr/bin/env python3
"""Layer 33 — Auto-register PostToolUse hook.

Fires after every successful ``Write`` / ``Edit`` / ``NotebookEdit``.
Two predicates decide whether the file becomes an artifact:

1. Output path is inside ``<session>/artifacts/``, OR
2. The file's MIME type is in ``auto_register_mimes`` (PDFs, images,
   CSVs, OOXML, etc.).

Hooks must return fast (Claude-Code budget ~3 s). The slow work
(Haiku-4.5 description + manifest write + recall.db indexing) runs
in a **detached background process** via ``os.fork()`` + ``setsid()``
so the hook returns to Claude Code in <50 ms.

Privacy: the audit event ``artifact.auto_registered`` carries
``name``, ``sha256``, ``mime``, ``size``, ``by_tool`` only. The
description text and the file content NEVER enter the audit chain.

Failure policy: any exception is swallowed silently — auto-register
is best-effort. Operators get observability through the audit chain
and the bridge log, not through hook failures.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── Path bootstrap ─────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # operator/voice/hooks/ → repo root

for p in (
    REPO_ROOT / "operator" / "forge",
    REPO_ROOT / "operator" / "bridges" / "shared",
):
    s = str(p)
    if p.is_dir() and s not in sys.path:
        sys.path.insert(0, s)


# ── Triggers ───────────────────────────────────────────────────────────────

_AUTO_REGISTER_TOOLS = {"Write", "Edit", "NotebookEdit"}

# MIME types that trigger auto-register even when the path is outside the
# session artifact tree. Resolved at runtime via the config layer so the
# operator can extend it without code edits.
_DEFAULT_MIMES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/webp", "image/svg+xml",
    "text/csv", "text/html",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# ── Payload parsing ────────────────────────────────────────────────────────


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _extract_output_path(tool: str, payload: dict) -> Path | None:
    """Return the (absolute) path the tool wrote to, or None."""
    inp = payload.get("tool_input") or {}
    if tool == "Write":
        p = inp.get("file_path")
    elif tool == "Edit":
        p = inp.get("file_path")
    elif tool == "NotebookEdit":
        p = inp.get("notebook_path") or inp.get("file_path")
    else:
        p = None
    if not p:
        return None
    try:
        return Path(p).resolve()
    except (OSError, RuntimeError):
        return None


# ── MIME detection (mirror of forge.artifacts._detect_mime, kept here so
# the hook stays self-contained and the user-tier bridge.sh install can
# omit the forge package without breaking the hook) ────────────────────────


def _detect_mime(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        head = b""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF":
        try:
            with path.open("rb") as fh:
                tail = fh.read(12)
            if tail[8:12] == b"WEBP":
                return "image/webp"
        except OSError:
            pass
    if head[:5] == b"<?xml" or head[:4] == b"<svg":
        return "image/svg+xml"
    if head[:2] == b"PK":
        ext_guess = mimetypes.guess_type(str(path))[0]
        if ext_guess:
            return ext_guess
        return "application/zip"
    guessed = mimetypes.guess_type(str(path))[0]
    return guessed or "application/octet-stream"


# ── Session-key resolution ─────────────────────────────────────────────────


def _resolve_session_root() -> Path | None:
    """Locate the active session artifact root.

    Priority:
      1. ``CORVIN_SESSION_KEY`` env (adapter-set, format ``<bridge>:<chat>``).
      2. None — caller may still trigger via MIME match into a fresh path.
    """
    sk = os.environ.get("CORVIN_SESSION_KEY") \
        or os.environ.get("CORVIN_SESSION_KEY") \
        or ""
    if not sk or "/" in sk or ".." in sk:
        return None
    try:
        from forge import artifacts as _art  # type: ignore
        return _art.session_artifacts_dir(sk)
    except Exception:
        return None


def _should_auto_register(path: Path, session_root: Path | None) -> tuple[bool, str]:
    """Return ``(yes, reason)``.

    Two predicates apply, in order:
      - path-convention: file already lives under ``<session>/artifacts/``.
      - mime-detect: file MIME type is in the configured allow-list.
    """
    # Predicate 1 — path convention.
    if session_root is not None:
        try:
            path.relative_to(session_root)
            return True, "path-convention"
        except ValueError:
            pass

    # Predicate 2 — MIME detection.
    if not path.is_file():
        return False, "missing"
    mime = _detect_mime(path)
    allowed = _load_allowed_mimes()
    if mime in allowed:
        return True, f"mime:{mime}"
    return False, f"skip:{mime}"


def _load_allowed_mimes() -> set[str]:
    """Read ``<tenant>/global/artifacts.config.json::auto_register_mimes``.

    Falls back to the built-in default set when the config is absent.
    Never raises — config errors degrade to defaults.
    """
    try:
        from forge import artifacts as _art  # type: ignore
        cfg = _art.load_config()
        mimes = cfg.get("auto_register_mimes")
        if isinstance(mimes, list) and mimes:
            return {str(m) for m in mimes}
    except Exception:
        pass
    return set(_DEFAULT_MIMES)


# ── Description generation (Haiku-4.5, runs in detached child) ────────────


def _generate_description(path: Path, mime: str) -> str:
    """Return a one-sentence description or empty string on failure.

    Uses the Layer-29.5 helper-model contract: `claude -p --max-turns 1
    --no-tools` with the model selected by `helper_model.claude_args()`.
    """
    try:
        from helper_model import claude_args, resolve_helper_model  # type: ignore
    except Exception:
        return ""

    # Helper opted out (CORVIN_HELPER_MODEL in {off,none,default,""}) → don't
    # invoke the helper at all. "off" means no helper, not "use claude's default
    # model for the description" — otherwise a description-only call still spawns
    # an ~8 s `claude -p` even when the operator disabled helper models.
    if resolve_helper_model("artifact_describe") is None:
        return ""

    # Read first 4 KB. For binary MIMEs the bytes are useless to the LLM;
    # we send a metadata-only prompt instead.
    if mime.startswith("text/") or mime in ("application/json",
                                            "application/xml"):
        try:
            with path.open("r", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            head = ""
        prompt = (
            "Beschreibe diesen Inhalt in einem einzigen Satz auf der Sprache "
            "des Inhalts. Sei spezifisch (Thema, Format, Sprache). "
            "Antworte mit GENAU einem Satz, ohne Anführungszeichen.\n\n"
            f"FILE: {path.name}\nMIME: {mime}\n\n--- CONTENT (first 4KB) ---\n"
            f"{head}\n--- END ---"
        )
    else:
        # Binary: metadata-only summary.
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        prompt = (
            "Generate a one-sentence description for this binary artifact. "
            "Answer in English unless the filename hints otherwise. "
            "Respond with EXACTLY one sentence, no quotes.\n\n"
            f"FILE: {path.name}\nMIME: {mime}\nSIZE: {size} bytes"
        )

    try:
        argv = ["claude", "-p", "--max-turns", "1", "--tools", ""]
        argv.extend(claude_args("artifact_describe"))
        r = subprocess.run(
            argv, input=prompt, capture_output=True, text=True,
            timeout=20,
        )
        if r.returncode != 0:
            return ""
        line = (r.stdout or "").strip().split("\n", 1)[0]
        # Cap to 200 chars; full content stays only in source.
        return line[:200]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


# ── recall.db cross-indexing (best-effort) ─────────────────────────────────


def _index_in_recall(*, channel: str, chat_key: str, name: str,
                     description: str) -> None:
    """Insert an ``artifact_summary`` row in ``recall.db`` so the same
    FTS5 index Layer 28 maintains can be queried by ``artifact_search``.

    Sentinel ``persona='_artifact'`` distinguishes artifact-summary
    rows from real conversation turns. ``msg_id`` is ``artifact:<name>``.
    """
    if not channel or not chat_key or not description:
        return
    try:
        from conversation_recall import index_turn  # type: ignore
        index_turn(
            channel=channel,
            chat_key=chat_key,
            user_text=name,
            assistant_text=description,
            msg_id=f"artifact:{name}",
            persona="_artifact",
        )
    except Exception:
        pass


# ── Audit emission ────────────────────────────────────────────────────────


def _emit(event_type: str, *, severity: str, details: dict) -> None:
    """Append one event to the unified hash chain. Never raises."""
    try:
        from forge.security_events import write_event  # type: ignore
        from forge.paths import tenant_global_dir  # type: ignore

        audit = tenant_global_dir() / "forge" / "audit.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        write_event(audit, event_type, severity=severity,
                    tool="hook.artifact_register", run_id="",
                    details=details)
    except Exception:
        pass


# ── Background worker (runs after fork) ───────────────────────────────────


def _do_register(*, source: Path, session_root: Path | None,
                 by_tool: str, reason: str) -> None:
    """Slow path: detect MIME, generate description, register, cross-index.

    Runs in the detached child process so the hook itself returns in
    <50 ms. Every failure path is swallowed — this thread is best-effort
    by design.
    """
    try:
        from forge import artifacts as _art  # type: ignore
    except Exception:
        return

    if session_root is None:
        # No session context — register into the active tenant's session
        # tree at a synthetic ``adhoc:<pid>`` key. This is rare; usually
        # the adapter sets CORVIN_SESSION_KEY before the engine spawns.
        try:
            from forge.tenants import current_tenant  # type: ignore
            from forge.paths import tenant_sessions_dir  # type: ignore
            session_root = (tenant_sessions_dir(current_tenant())
                            / f"adhoc:{os.getpid()}" / "artifacts")
        except Exception:
            return

    try:
        mime = _detect_mime(source)
        description = _generate_description(source, mime)

        # File is outside the artifact tree → move it in via register(move=True).
        # File already inside → register(move=False) is a no-op move.
        already_inside = False
        try:
            source.relative_to(session_root)
            already_inside = True
        except ValueError:
            pass

        entry = _art.register(
            source_path=source,
            artifacts_root=session_root,
            description=description,
            by_tool=by_tool,
            move=not already_inside,
        )
    except Exception as e:  # noqa: BLE001
        _emit("artifact.auto_register_failed",
              severity="WARNING",
              details={"reason": f"{type(e).__name__}: {e}"[:200],
                       "source_basename": source.name[:80]})
        return

    _emit("artifact.auto_registered",
          severity="INFO",
          details={"name": entry.name, "sha256": entry.sha256,
                   "size": entry.size, "mime": entry.mime,
                   "by_tool": entry.by_tool,
                   "trigger": reason})

    channel = os.environ.get("CORVIN_BRIDGE") or os.environ.get("CORVIN_BRIDGE") or ""
    chat_key = os.environ.get("CORVIN_CHAT_KEY") or os.environ.get("CORVIN_CHAT_KEY") or ""
    _index_in_recall(channel=channel, chat_key=chat_key,
                     name=entry.name, description=description)


# ── Detached fork wrapper ─────────────────────────────────────────────────


def _spawn_detached(callable_, *args, **kwargs) -> None:
    """Fork+setsid the slow worker so the hook returns immediately.

    The child closes stdin/stdout/stderr and runs to completion in the
    background. Parent returns within microseconds.

    Security note (ADR-0144 / fork-review 2026-06-20): the fork child runs
    OUTSIDE the L10 path-gate enforcement perimeter — path_gate.py is a
    PreToolUse hook for Claude Code tool calls, not a filesystem syscall
    interceptor.  Callables passed here must NOT write to license paths
    (global/license.key, ~/.config/corvin-voice/session.key) or to the
    audit chain (audit.jsonl).  Verify any changes to _do_register() against
    this constraint.
    """
    try:
        pid = os.fork()
    except (OSError, AttributeError):
        # No fork available — degrade to synchronous. On Windows os.fork does NOT
        # exist (AttributeError, not OSError), so this hook must catch both or it
        # crashes on every Write/Edit tool call. Hook latency goes up, but the
        # auto-register contract is preserved.
        try:
            callable_(*args, **kwargs)
        except Exception:
            pass
        return
    if pid != 0:
        # Parent — wake up later only to reap the (already detached) child.
        return
    # Child — detach.
    try:
        os.setsid()
    except OSError:
        pass
    # Close inherited file descriptors so the parent can exit cleanly.
    try:
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            try:
                os.dup2(null, fd)
            except OSError:
                pass
        if null > 2:
            os.close(null)
    except OSError:
        pass
    try:
        callable_(*args, **kwargs)
    except Exception:
        pass
    finally:
        os._exit(0)


# ── Hook entry-point ──────────────────────────────────────────────────────


def main() -> int:
    payload = _read_payload()
    tool = str(payload.get("tool_name") or "")
    if tool not in _AUTO_REGISTER_TOOLS:
        return 0

    # Tool-response success check — Claude Code populates `tool_response`
    # on PostToolUse. If the upstream tool errored, we skip.
    resp = payload.get("tool_response") or {}
    if isinstance(resp, dict) and resp.get("error"):
        return 0

    out = _extract_output_path(tool, payload)
    if out is None or not out.is_file():
        return 0

    session_root = _resolve_session_root()
    should, reason = _should_auto_register(out, session_root)
    if not should:
        return 0

    _spawn_detached(_do_register,
                    source=out,
                    session_root=session_root,
                    by_tool=f"tool.{tool}",
                    reason=reason)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Hooks MUST return rc=0 on internal errors so Claude Code's
        # tool flow is never blocked by auto-register issues.
        sys.exit(0)
