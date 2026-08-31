"""ulo_schema.py — User-Defined Learning Objectives: dataclass + validation.

Part of ADR-0163 (ULO M1).  Imported by ulo.py and any future ULO consumer.
Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

# ── Constants ─────────────────────────────────────────────────────────────

MAX_TEXT_LEN: int = 200
MAX_OBJECTIVES: int = 20       # per chat; operator can override via env
_ULO_ID_RE = re.compile(r"^ulo_[a-z0-9]{8}$")

Priority     = Literal["low", "medium", "high"]
Scope        = Literal["session", "chat", "all"]
CheckTrigger = Literal["always", "code", "review", "commit"]

_PRIORITY_LABEL = {"low": "low", "medium": "med", "high": "high"}

# ── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class UserObjective:
    """One learnable behavioural constraint authored by the user."""

    id:                      str
    text:                    str
    priority:                Priority     = "medium"
    scope:                   Scope        = "chat"
    active:                  bool         = True
    created_at:              float        = field(default_factory=time.time)
    updated_at:              float        = field(default_factory=time.time)
    compliance_window:       int          = 50
    compliance_rate:         float | None = None
    reinforcement_threshold: float        = 0.75
    turns_checked:           int          = 0
    consecutive_failures:    int          = 0
    check_trigger:           CheckTrigger = "always"

    # ── Serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":                      self.id,
            "text":                    self.text,
            "priority":                self.priority,
            "scope":                   self.scope,
            "active":                  self.active,
            "created_at":              self.created_at,
            "updated_at":              self.updated_at,
            "compliance_window":       self.compliance_window,
            "compliance_rate":         self.compliance_rate,
            "reinforcement_threshold": self.reinforcement_threshold,
            "turns_checked":           self.turns_checked,
            "consecutive_failures":    self.consecutive_failures,
            "check_trigger":           self.check_trigger,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserObjective":
        return cls(
            id=str(d["id"]),
            text=str(d["text"]),
            priority=d.get("priority", "medium"),        # type: ignore[arg-type]
            scope=d.get("scope", "chat"),                # type: ignore[arg-type]
            active=bool(d.get("active", True)),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
            compliance_window=int(d.get("compliance_window", 50)),
            compliance_rate=(
                float(d["compliance_rate"])
                if d.get("compliance_rate") is not None else None
            ),
            reinforcement_threshold=float(d.get("reinforcement_threshold", 0.75)),
            turns_checked=int(d.get("turns_checked", 0)),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
            check_trigger=d.get("check_trigger", "always"),  # type: ignore[arg-type]
        )

    # ── Display helpers ──────────────────────────────────────────────────

    def summary_line(self) -> str:
        """One-line human-readable summary for list displays."""
        status = "✅" if self.active else "⏸"
        prio   = _PRIORITY_LABEL.get(self.priority, self.priority)
        cr_str = (
            f"  compliance: {self.compliance_rate:.0%}"
            if self.compliance_rate is not None else ""
        )
        return f"{status} [{self.id}] [{prio}] {self.text!r}{cr_str}"


# ── Validation helpers ────────────────────────────────────────────────────

def make_id() -> str:
    """Return a fresh collision-safe objective id."""
    return "ulo_" + secrets.token_hex(4)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RUN_RE = re.compile(r"\s+")


def sanitize_text(text: str) -> str:
    """Neutralise prompt-injection vectors in a user-supplied objective.

    Objective text is interpolated verbatim into the ``<learning_objectives>``
    system-prompt block (ulo.render_block / ulo_compliance.get_reinforcement_block).
    Without sanitisation a value like ``</learning_objectives>\\n<system>…``
    breaks out of the block and steers the model (security review 2026-06-27).

    Steps: NFKC-normalise (matches the compliance path), strip control chars,
    collapse every whitespace run — including newlines/tabs — to a single
    space (kills multi-line breakout structure), and drop angle brackets so no
    pseudo-tag can close the wrapper. This is applied at the input boundary
    (validate_text) and defensively again at render time for legacy stores.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS_RE.sub(" ", text)
    text = text.replace("<", "").replace(">", "")
    text = _WS_RUN_RE.sub(" ", text)
    return text.strip()


def validate_text(text: str) -> str:
    text = sanitize_text(text)
    if not text:
        raise ValueError("objective text must not be empty")
    if len(text) > MAX_TEXT_LEN:
        raise ValueError(
            f"objective text exceeds {MAX_TEXT_LEN} characters "
            f"({len(text)} given)"
        )
    return text


def validate_priority(p: str) -> Priority:
    if p not in ("low", "medium", "high"):
        raise ValueError(f"priority must be low|medium|high, got {p!r}")
    return p  # type: ignore[return-value]


def validate_scope(s: str) -> Scope:
    if s not in ("session", "chat", "all"):
        raise ValueError(f"scope must be session|chat|all, got {s!r}")
    return s  # type: ignore[return-value]


def validate_check_trigger(t: str) -> CheckTrigger:
    if t not in ("always", "code", "review", "commit"):
        raise ValueError(
            f"check_trigger must be always|code|review|commit, got {t!r}"
        )
    return t  # type: ignore[return-value]


__all__ = [
    "UserObjective",
    "MAX_TEXT_LEN",
    "MAX_OBJECTIVES",
    "Priority",
    "Scope",
    "CheckTrigger",
    "make_id",
    "validate_text",
    "validate_priority",
    "validate_scope",
    "validate_check_trigger",
]
