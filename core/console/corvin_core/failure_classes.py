"""Shared failure classifier for turns that were cut off or failed (CONCEPT-0112, C1-C9).

One function, two callers: the console (``corvin_console.turn_recovery.find_interrupted``)
and the bridge (``corvin_operator/bridges/shared/adapter.py::process_one``). The same signal
must yield the same class on both surfaces, so the classification lives here and nowhere else.

The inputs are facts the caller has already observed, not OS calls:

* ``exit_code`` - the child's exit status as Python reports it (``-N`` when a POSIX signal
  killed it) or the shell form ``128+N``. ``-1`` is the boot reaper's orphan marker.
* ``cancelled`` - an operator stop was requested for this turn. Always C3: operator intent
  is final, even though the child was killed by a signal.
* ``timed_out`` - the engine's own deadline fired (C4).
* ``reason`` - an explicit reason code from a caller that knows more (see ``_REASON_CLASS``).

Platform note: POSIX reports a signal death as a negative or ``128+N`` status, which is what
``RESTART_EXIT_CODES`` encodes. Windows has no signal exit codes; a terminated process there
reports an ordinary status, so a host restart is visible only through the reaper's orphan
marker (``-1``) and the console's shutdown window. A Windows restart therefore classifies as
C9 unless one of those markers is present. This is stated, not papered over.

Pure function: never raises. An unrecognised signal is C9 (fail-safe, normal backoff).
"""
from __future__ import annotations

import enum
import signal
from dataclasses import dataclass


class FailureClass(str, enum.Enum):
    """The nine failure classes of CONCEPT-0112's matrix."""

    C1 = "C1"  # host restart / signal death of the engine child
    C2 = "C2"  # client disconnect (tab closed, Discord socket dropped)
    C3 = "C3"  # operator cancel - never resumed
    C4 = "C4"  # worker timeout (engine deadline)
    C5 = "C5"  # wedge: no heartbeat while the process is alive
    C6 = "C6"  # budget / quota exhausted - a user decision
    C7 = "C7"  # gate refusal (L44, L34, consent) - never retried with the same content
    C8 = "C8"  # permanent (model_not_found, auth) - fail now
    C9 = "C9"  # unknown


@dataclass(frozen=True)
class Classification:
    failure_class: FailureClass
    reason_code: str

    @property
    def code(self) -> str:
        """The class as written in the matrix and in audit records, e.g. ``"C1"``."""
        return self.failure_class.value


# Exit statuses a signal death produces: -N (asyncio / subprocess) and 128+N (shell wrapper).
# SIGTERM and SIGKILL are the two a service stop sends (systemd: SIGTERM, then SIGKILL).
_SIGNAL_NUMBERS = (signal.SIGTERM, signal.SIGKILL)
RESTART_EXIT_CODES: frozenset[int] = frozenset(
    {-int(n) for n in _SIGNAL_NUMBERS} | {128 + int(n) for n in _SIGNAL_NUMBERS} | {-1}
)

# Explicit reasons a caller may pass when it has observed more than the exit status.
_REASON_CLASS: dict[str, FailureClass] = {
    "client_disconnected": FailureClass.C2,
    "wedged": FailureClass.C5,
    "budget_exhausted": FailureClass.C6,
    "gate_refused": FailureClass.C7,
    "permanent": FailureClass.C8,
}


def classify_failure(
    *,
    exit_code: int | None = None,
    cancelled: bool = False,
    timed_out: bool = False,
    reason: str | None = None,
) -> Classification:
    """Classify one interrupted or failed turn. Precedence: operator cancel, explicit reason,
    timeout, then the exit status."""
    try:
        if cancelled:
            return Classification(FailureClass.C3, "operator_cancel")
        if reason is not None:
            cls = _REASON_CLASS.get(reason)
            if cls is None:
                return Classification(FailureClass.C9, "unrecognised_reason")
            return Classification(cls, reason)
        if timed_out:
            return Classification(FailureClass.C4, "worker_timeout")
        if exit_code is not None and exit_code in RESTART_EXIT_CODES:
            return Classification(FailureClass.C1, "host_signal")
        return Classification(FailureClass.C9, "unrecognised")
    except Exception:  # noqa: BLE001 - a classifier must never break the turn it classifies
        return Classification(FailureClass.C9, "classifier_error")
