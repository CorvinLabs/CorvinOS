"""Canonical task lifecycle states and the transition table (PLAN-0958 P1).

This module is the ONE declaration of the task states. ``TaskManager`` (the
chat task log) and ``TaskQueue`` (the tenant-global queue behind
``/v1/console/tasks``) both alias :class:`TaskState`; the guard test
``core/console/tests/test_task_state_enum_guard.py`` fails on any new
task-lifecycle enum that is not registered there.

Transitions are enforced by :func:`check_transition`. Forbidden examples:
``cancelled -> anything``, ``completed -> anything``, ``failed -> resuming``,
``interrupted -> running`` (an interrupted task must pass ``resuming``).
A same-state write is an idempotent no-op and always allowed.

Stored events are append-only and are never rewritten. A task that the
pre-P1 boot reaper finalized as ``task.failed`` with
``error == "orphaned_on_restart"`` is READ as :attr:`TaskState.INTERRUPTED`
(see :func:`is_legacy_orphan_failure`); the event itself stays as written.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class TaskState(str, Enum):
    """Every lifecycle state a task can be in."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    WAITING_RETRY = "waiting_retry"
    RESUMING = "resuming"
    NEEDS_USER = "needs_user"
    ABANDONED = "abandoned"


TERMINAL_STATES: frozenset[TaskState] = frozenset({
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.ABANDONED,
})

#: States from which a user or operator can still cancel the task.
CANCELLABLE_STATES: frozenset[TaskState] = frozenset({
    TaskState.PENDING,
    TaskState.RUNNING,
    TaskState.INTERRUPTED,
    TaskState.WAITING_RETRY,
    TaskState.RESUMING,
    TaskState.NEEDS_USER,
})

_S = TaskState

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    # PENDING -> COMPLETED: inline paths finish a task without a distinct start (the guard raises in
    # production, so refusing a flow that exists would turn a finished task into an exception).
    _S.PENDING: frozenset({_S.RUNNING, _S.COMPLETED, _S.FAILED, _S.CANCELLED, _S.INTERRUPTED}),
    _S.RUNNING: frozenset({_S.COMPLETED, _S.FAILED, _S.CANCELLED, _S.INTERRUPTED}),
    _S.INTERRUPTED: frozenset({_S.WAITING_RETRY, _S.RESUMING, _S.NEEDS_USER,
                               _S.ABANDONED, _S.CANCELLED}),
    _S.WAITING_RETRY: frozenset({_S.RESUMING, _S.NEEDS_USER, _S.ABANDONED, _S.CANCELLED}),
    _S.RESUMING: frozenset({_S.RUNNING, _S.COMPLETED, _S.FAILED, _S.INTERRUPTED,
                            _S.CANCELLED}),
    _S.NEEDS_USER: frozenset({_S.RESUMING, _S.ABANDONED, _S.CANCELLED}),
    _S.COMPLETED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
    _S.ABANDONED: frozenset(),
}

#: Audit / log event name -> the state it moves a task into.
EVENT_TARGET_STATE: dict[str, TaskState] = {
    "task.started": TaskState.RUNNING,
    "task.completed": TaskState.COMPLETED,
    "task.failed": TaskState.FAILED,
    "task.cancelled": TaskState.CANCELLED,
    "task.interrupted": TaskState.INTERRUPTED,
}

#: Reason string the pre-P1 boot reaper wrote into ``task.failed.error``.
LEGACY_ORPHAN_REASON = "orphaned_on_restart"


class InvalidTransitionError(ValueError):
    """A task was asked to move along a transition the table forbids."""

    def __init__(self, old: TaskState | str, new: TaskState | str):
        self.old = TaskState(old)
        self.new = TaskState(new)
        super().__init__(
            f"forbidden task state transition: {self.old.value} -> {self.new.value}"
        )


def check_transition(old: TaskState | str, new: TaskState | str) -> None:
    """Raise :class:`InvalidTransitionError` unless ``old -> new`` is allowed."""
    old_s, new_s = TaskState(old), TaskState(new)
    if old_s == new_s:
        return
    if new_s not in ALLOWED_TRANSITIONS[old_s]:
        raise InvalidTransitionError(old_s, new_s)


def is_legacy_orphan_failure(event: Mapping[str, Any]) -> bool:
    """True for a pre-P1 ``task.failed`` written by the boot reaper."""
    return (
        event.get("event") == "task.failed"
        and event.get("error") == LEGACY_ORPHAN_REASON
    )
