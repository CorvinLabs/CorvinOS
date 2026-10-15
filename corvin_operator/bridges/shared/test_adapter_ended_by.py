"""PLAN-0959 P0: the task record says WHO ended a failed turn (``ended_by``).

Unit-level on the pure mapping ``adapter.ended_by_label``. The kill-attribution end-to-end
path (a real process group signalled by ``terminate_process_tree``) is covered in
``agents/test_terminate_process_tree.py``.

The adapter is imported directly, not through ``test_bg_scope_completion._adapter()``: that
helper purges ``agents`` from ``sys.modules`` and would break the Windows-simulation tests that
run later in the same session.
"""
from __future__ import annotations


def _label(**kw) -> str:
    import adapter  # noqa: PLC0415 — pure function, no stubs needed
    base = dict(user_cancel=False, kill_caller=None, scope_end=None, timed_out=False, rc=1)
    base.update(kw)
    return adapter.ended_by_label(**base)


def test_an_operator_cancel_wins_over_every_other_cause():
    assert _label(user_cancel=True, kill_caller="_cancel_chat", rc=143) == "operator_cancel"


def test_a_kill_from_this_process_names_its_caller():
    assert _label(kill_caller="_cleanup_proc", rc=143) == "python_kill:_cleanup_proc"


def test_a_scope_cap_is_named_by_the_cap():
    assert _label(scope_end="child_cap", rc=143) == "scope_end:child_cap"


def test_an_idle_kill_is_named_as_the_stream_idle_timeout():
    assert _label(timed_out=True, rc=-15) == "stream_idle_timeout"


def test_a_signal_exit_nobody_here_sent_is_unattributed():
    # The case the production logs could not explain: 143 with no kill from this process.
    for rc in (143, 137, -15, -9):
        assert _label(rc=rc) == "signal_unattributed", rc


def test_a_plain_error_exit_is_just_exit():
    assert _label(rc=1) == "exit"
