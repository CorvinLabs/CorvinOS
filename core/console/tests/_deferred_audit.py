"""Wait for the deferred instance-identity audit write before reading a sandbox chain.

The first chain write in a fresh ``CORVIN_HOME`` mints that sandbox's instance key
and records ``instance.key_rotated`` from a short-lived ``ibc-audit-deferred``
thread (``instance_identity._audit_ibc``): it cannot take the chain lock the outer
write still holds. Since d0e049466 the record is bound to its chain and key when
the event is raised. Correct product behaviour, but a test that

* counts chain records right after its first write counts that record against
  whatever it does next (test_learner_audit_first, test_webhook_hardening_r2), or
* removes the sandbox while the thread is still writing loses the race
  (``OSError: [Errno 39] Directory not empty``)

must wait for it first. ``conftest.py`` does the second for every
``TemporaryDirectory`` cleanup; the first needs an explicit call.
"""
from __future__ import annotations

import threading

THREAD_NAME = "ibc-audit-deferred"


def join_deferred_identity_writes(timeout: float = 10.0) -> None:
    for th in threading.enumerate():
        if th.name == THREAD_NAME and th is not threading.current_thread():
            th.join(timeout=timeout)
