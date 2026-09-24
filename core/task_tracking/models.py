"""Request models + vocabularies for the Task-Tracking SSOT (ADR-2051, ADR-2056).

Pydantic v2. The enums are the single definition of every closed vocabulary —
the store, the service and the console route all import them from here.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

KINDS = ("initiative", "epic", "story", "task", "subtask", "issue", "proposal")
STATUSES = ("open", "in_progress", "blocked", "complete", "archived")
PRIORITIES = ("critical", "high", "medium", "low")
APPROVAL_STATES = ("none", "suggested", "pending", "approved", "rejected")
DEPENDENCY_TYPES = ("blocks", "depends_on", "related", "duplicates")

Kind = Literal["initiative", "epic", "story", "task", "subtask", "issue", "proposal"]
Status = Literal["open", "in_progress", "blocked", "complete", "archived"]
Priority = Literal["critical", "high", "medium", "low"]
ApprovalState = Literal["none", "suggested", "pending", "approved", "rejected"]
Decision = Literal["pending", "approved", "rejected"]

#: Which kinds may sit directly under which parent kind. ``None`` = top level.
PARENT_RULES: dict[str, tuple[Optional[str], ...]] = {
    "initiative": (None,),
    "epic": ("initiative",),
    "story": ("initiative", "epic"),
    "task": (None, "initiative", "epic", "story", "issue", "proposal"),
    "issue": (None, "initiative", "epic", "story"),
    "proposal": (None, "initiative", "epic", "story"),
    "subtask": ("epic", "story", "task", "issue", "proposal"),
}

_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Assignee / owner: a handle, not a person's contact data — no "@", no digits-only phone shape.
_HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,47}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$")


def _check_iso(v: Optional[str]) -> Optional[str]:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _ISO_RE.match(v):
        raise ValueError("expected an ISO-8601 date or timestamp")
    return v


def _check_handle(v: Optional[str]) -> Optional[str]:
    if v in (None, ""):
        return None
    v = v.strip()
    if not _HANDLE_RE.match(v):
        raise ValueError("a handle: letters, digits, space, '.', '_' or '-' (max 48), no e-mail or phone")
    return v


def _check_token(v: Optional[str]) -> Optional[str]:
    if v in (None, ""):
        return None
    if not _TOKEN_RE.match(v):
        raise ValueError("lowercase token [a-z0-9_-], max 32")
    return v


class _Fields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = Field(default=None, max_length=8000)
    priority: Optional[Priority] = None
    owner: Optional[str] = None
    assignee: Optional[str] = None
    start_at: Optional[str] = None
    deadline: Optional[str] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    work_estimate: Optional[float] = Field(default=None, ge=0, le=100000)
    work_actual: Optional[float] = Field(default=None, ge=0, le=100000)
    target_milestone: Optional[str] = Field(default=None, max_length=64)
    category: Optional[str] = None
    labels: Optional[list[str]] = Field(default=None, max_length=12)

    @field_validator("start_at", "deadline")
    @classmethod
    def _v_iso(cls, v: Optional[str]) -> Optional[str]:
        return _check_iso(v)

    @field_validator("owner", "assignee")
    @classmethod
    def _v_handle(cls, v: Optional[str]) -> Optional[str]:
        return _check_handle(v)

    @field_validator("category")
    @classmethod
    def _v_token(cls, v: Optional[str]) -> Optional[str]:
        return _check_token(v)

    @field_validator("labels")
    @classmethod
    def _labels(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        out = []
        for x in v:
            t = _check_token(x)
            if t and t not in out:
                out.append(t)
        return out


class ItemCreate(_Fields):
    kind: Kind
    title: str = Field(min_length=1, max_length=200)
    parent_id: Optional[str] = Field(default=None, max_length=64)
    status: Status = "open"
    approval_state: ApprovalState = "none"


class ItemPatch(_Fields):
    """Every field optional; ``version`` is the one the client read (409 on mismatch)."""

    version: int = Field(ge=1)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    kind: Optional[Kind] = None
    parent_id: Optional[str] = Field(default=None, max_length=64)
    status: Optional[Status] = None
    status_reason: Optional[str] = Field(default=None, max_length=500)
    approval_state: Optional[ApprovalState] = None


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    version: int = Field(ge=1)


class DependencyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    depends_on_id: str = Field(min_length=1, max_length=64)
    # "blocks" is reserved in the vocabulary but not accepted: stored in the same
    # direction as depends_on it would read inverted. Model "A blocks B" as
    # "B depends_on A".
    dep_type: Literal["depends_on", "related", "duplicates"] = "depends_on"


_RUN_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/#-]{0,199}$")


class RunLinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_type: str = Field(min_length=1, max_length=32)
    run_ref: str = Field(min_length=1, max_length=200)

    @field_validator("run_type")
    @classmethod
    def _rt(cls, v: str) -> str:
        return _check_token(v) or ""

    @field_validator("run_ref")
    @classmethod
    def _rr(cls, v: str) -> str:
        if not _RUN_REF_RE.match(v):
            raise ValueError("run_ref: [A-Za-z0-9_.:/#-], max 200")
        return v
