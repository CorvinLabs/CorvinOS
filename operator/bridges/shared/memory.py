"""memory.py — episodic, lazy-loaded user memory (Tier 2 of the memory layer).

Stores longer-form notes the user (or Claude) wants to keep around across
chats and bridges. Each topic is one Markdown file:

    ~/.config/corvin-voice/memory/
    ├── MEMORY.md            ← index, always shown to Claude
    ├── travel.md
    ├── coding_style.md
    └── work_email.md

The adapter injects only `MEMORY.md` into the system prompt — about 200-500
characters of "what topics exist and what each is about". Claude then reads
specific topic files via the standard `Read` tool when the conversation
calls for it. Cheap on tokens, scales to many topics, and keeps the secrets
out of the prompt.

Self-extending: the adapter system prompt (item 8) tells Claude to offer
"should I remember this?" when the user mentions a stable fact. Claude
then uses Write to create / append a topic file and re-runs `rebuild_index()`
so the new topic shows up in the next turn's system prompt.

Tier 2 — sensitive notes can land here (e.g. "user prefers du-form"); for
real secrets (credentials, API keys) use the Vault (Tier 3).
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path


def _memory_root() -> Path:
    """Canonical topic-memory root: ``<XDG_CONFIG_HOME or ~/.config>/corvin-voice/memory``.

    XDG Base Directory spec: default to ``$HOME/.config`` when ``XDG_CONFIG_HOME``
    is unset — NOT ``voice_dir()``. The old voice_dir() fallback flipped the topic
    store between the XDG location (console / interactive shells, XDG set) and the
    tenant-home location (systemd --user bridges, XDG unset), so a note written via
    one was invisible to the other (same reader!=writer split as the voice profile).

    VOICE_CONFIG_DIR (highest priority) mirrors forge.paths.voice_config_dir /
    say.py's SSOT — see vault.py::_vault_root (V-2 audit finding)."""
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override))) / "memory"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(os.path.expanduser(xdg)) / "corvin-voice" / "memory"


MEMORY_DIR = _memory_root()
INDEX_FILE = MEMORY_DIR / "MEMORY.md"

# Topic file names: lowercase, hyphenated, .md suffix, no path traversal.
_VALID_TOPIC = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _ensure_dir() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        MEMORY_DIR.chmod(0o700)
    except OSError:
        pass


def _normalise_topic(topic: str) -> str:
    """Strip extension if present, lowercase, replace spaces with `-`.
    Raises ValueError on anything that doesn't match the safe pattern."""
    if not topic:
        raise ValueError("empty topic name")
    t = topic.strip().lower()
    if t.endswith(".md"):
        t = t[:-3]
    t = t.replace(" ", "-")
    if not _VALID_TOPIC.match(t):
        raise ValueError(
            f"invalid topic name: {topic!r} (use letters, digits, _ and -; up to 40 chars)"
        )
    return t


def topic_path(topic: str) -> Path:
    return MEMORY_DIR / f"{_normalise_topic(topic)}.md"


# ─── public API ────────────────────────────────────────────────────────────

def list_topics() -> list[str]:
    """Return sorted topic names (without .md)."""
    if not MEMORY_DIR.exists():
        return []
    out = []
    for p in MEMORY_DIR.iterdir():
        if p.is_file() and p.name.endswith(".md") and p.name != "MEMORY.md":
            out.append(p.stem)
    return sorted(out)


def read_topic(topic: str) -> str:
    """Return the full body of a topic file, or "" if it doesn't exist."""
    p = topic_path(topic)
    try:
        return p.read_text()
    except FileNotFoundError:
        return ""


def write_topic(topic: str, content: str, *, append: bool = False) -> Path:
    """Create or update a topic file. Re-builds the index afterwards.
    Returns the topic path."""
    _ensure_dir()
    p = topic_path(topic)
    body = (content or "").rstrip() + "\n"
    if append and p.exists():
        existing = p.read_text().rstrip()
        body = existing + "\n\n" + body
    # Atomic write: tmp + rename.
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(body)
    shutil.move(str(tmp), str(p))
    try:
        p.chmod(0o600)
    except OSError:
        pass
    rebuild_index()
    return p


def forget_topic(topic: str) -> bool:
    """Delete a topic file. Returns True iff it existed and was removed."""
    p = topic_path(topic)
    if not p.exists():
        return False
    p.unlink()
    rebuild_index()
    return True


def first_line_summary(content: str, *, max_chars: int = 80) -> str:
    """Pick a one-line summary for the index. Strategy:
      - first non-empty, non-heading line
      - else first heading content
      - capped at `max_chars` with an ellipsis.
    """
    head_line = ""
    body_line = ""
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if not head_line:
                head_line = line.lstrip("# ").strip()
            continue
        body_line = line
        break
    summary = body_line or head_line or ""
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


def rebuild_index() -> Path:
    """Regenerate MEMORY.md from the existing topic files. The file format
    is intentionally tight — every line is a hyphen-bullet so the system
    prompt costs ~50 chars per topic at most.
    """
    _ensure_dir()
    topics = list_topics()
    lines = ["# Memory index", ""]
    if not topics:
        lines.append("(no topics yet — use `/memory write <topic> <text>` to add one)")
    else:
        lines.append(f"_Updated {time.strftime('%Y-%m-%d %H:%M')}; {len(topics)} topic(s)._")
        lines.append("")
        for t in topics:
            try:
                summary = first_line_summary(topic_path(t).read_text())
            except OSError:
                summary = ""
            lines.append(f"- `{t}` — {summary}" if summary else f"- `{t}`")
    body = "\n".join(lines).rstrip() + "\n"
    tmp = INDEX_FILE.with_suffix(".md.tmp")
    tmp.write_text(body)
    shutil.move(str(tmp), str(INDEX_FILE))
    try:
        INDEX_FILE.chmod(0o600)
    except OSError:
        pass
    return INDEX_FILE


def for_system_prompt() -> str:
    """Render the index as a short paragraph appendable to the system prompt.
    Returns "" when there are no topics, so a fresh install costs nothing."""
    topics = list_topics()
    if not topics:
        return ""
    lines = []
    for t in topics:
        try:
            summary = first_line_summary(topic_path(t).read_text())
        except OSError:
            summary = ""
        lines.append(f"  - `{t}`" + (f" — {summary}" if summary else ""))
    return (
        "\n\nLong-term memory (lazy-loaded — read a topic file via the Read tool "
        "when relevant):\n"
        f"  Path: {MEMORY_DIR}\n"
        + "\n".join(lines)
    )
