"""The single, hash-pinned exception to the Python AST gate (scripts/kb.py of Corvin-Knowledge).

Drives the real ``path_gate.check`` (the hook's entry point). The pinned bundle is a TEMPORARY stand-in with the same
layout (a ``kb.py`` that imports ``subprocess`` plus the two sibling modules), pinned in the module under test via
monkeypatch, so these tests do not break whenever the real kb.py is edited (that friction is the point of the real
pin, not of the suite). Every "allowed" case has a "same input, not pinned" control that is denied, so a green test
cannot mean "the gate was never looking".
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent
if str(_HOOK) not in sys.path:
    sys.path.insert(0, str(_HOOK))

import path_gate as pg  # type: ignore  # noqa: E402

KB_SRC = "import subprocess\nimport kb_lib\nprint('kb')\n"
LIB_SRC = "VALUE = 1\n"
MODEL_SRC = "import kb_lib\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    d = tmp_path / "Corvin-Knowledge" / "scripts"
    d.mkdir(parents=True)
    (d / "kb.py").write_text(KB_SRC)
    (d / "kb_lib.py").write_text(LIB_SRC)
    (d / "kb_model.py").write_text(MODEL_SRC)
    pinned = {str(d / "kb.py"): {"kb.py": _sha(KB_SRC), "kb_lib.py": _sha(LIB_SRC), "kb_model.py": _sha(MODEL_SRC)}}
    monkeypatch.setattr(pg, "_PINNED_PYTHON_TOOLS", pinned)
    audits: list[tuple[str, str]] = []
    monkeypatch.setattr(pg, "_emit_code_exec_audit",
                        lambda payload, *, outcome, blocked_reason="": audits.append((outcome, blocked_reason)))
    return d, audits


def _check(cmd: str) -> tuple[bool, str]:
    return pg.check({"tool_name": "Bash", "tool_input": {"command": cmd}})


# ---------------------------------------------------------------------------------------------- allowed
def test_the_pinned_script_runs_when_every_file_matches_its_hash(bundle):
    d, audits = bundle
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert ok, reason
    assert ("allowed_pinned", "") in audits, audits


def test_control_the_same_script_unpinned_is_denied(bundle, monkeypatch):
    d, _ = bundle
    monkeypatch.setattr(pg, "_PINNED_PYTHON_TOOLS", {})
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert not ok and "forbidden import" in reason and "subprocess" in reason


def test_flags_before_the_script_are_fine(bundle):
    d, _ = bundle
    for flag in ("-u", "-B", "-I"):
        ok, reason = _check(f"python3 {flag} {d / 'kb.py'} check")
        assert ok, (flag, reason)


# ---------------------------------------------------------------------------------------------- denied: content
def test_a_changed_kb_py_is_denied(bundle):
    d, audits = bundle
    (d / "kb.py").write_text(KB_SRC + "# edited\n")
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert not ok and "kb.py" in reason and "changed" in reason, reason
    assert any(o == "blocked" for o, _ in audits)


@pytest.mark.parametrize("name", ["kb_lib.py", "kb_model.py"])
def test_a_changed_sibling_module_is_denied(bundle, name):
    """Python puts the script's directory first on sys.path: an unpinned sibling would run under the exception."""
    d, _ = bundle
    (d / name).write_text("import os\nos.getcwd()\n")
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert not ok and name in reason and "changed" in reason, reason


def test_a_missing_file_is_denied_fail_closed(bundle):
    d, _ = bundle
    (d / "kb_model.py").unlink()
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert not ok and "kb_model.py" in reason, reason


def test_a_missing_script_is_denied_fail_closed(bundle):
    d, _ = bundle
    (d / "kb.py").unlink()
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert not ok and "kb.py" in reason, reason


def test_a_symlinked_bundle_file_is_denied(bundle, tmp_path):
    d, _ = bundle
    real = tmp_path / "elsewhere.py"
    real.write_text(LIB_SRC)                               # same bytes, so the hash would match
    (d / "kb_lib.py").unlink()
    (d / "kb_lib.py").symlink_to(real)
    ok, reason = _check(f"python3 {d / 'kb.py'} check")
    assert not ok and "kb_lib.py" in reason, reason


# ---------------------------------------------------------------------------------------------- denied: path
def test_the_same_bytes_at_another_path_get_no_exception(bundle, tmp_path):
    d, _ = bundle
    other = tmp_path / "copy"
    other.mkdir()
    for n in ("kb.py", "kb_lib.py", "kb_model.py"):
        (other / n).write_text((d / n).read_text())
    ok, reason = _check(f"python3 {other / 'kb.py'} check")
    assert not ok and "forbidden import" in reason, reason


@pytest.mark.parametrize("spell", [
    lambda d: f"{d}/../{d.name}/kb.py",                    # ".." detour to the very same file
    lambda d: f"{d}//kb.py",                               # doubled separator
    lambda d: f"{d}/./kb.py",                              # dot segment
])
def test_no_path_normalisation_other_spellings_are_not_exempt(bundle, spell):
    d, _ = bundle
    ok, reason = _check(f"python3 {spell(d)} check")
    assert not ok and "forbidden import" in reason, reason


def test_a_relative_spelling_gets_no_exception(bundle, monkeypatch):
    d, _ = bundle
    monkeypatch.chdir(d)
    ok, reason = _check("python3 kb.py check")
    assert not ok and "forbidden import" in reason, reason


def test_a_quoted_path_gets_no_exception(bundle):
    d, _ = bundle
    # the quote is part of the captured token, which is not the pinned string: the exemption must not fire.
    # (Whether the gate then analyses that token is the pre-existing behaviour and not what this test pins.)
    exempt, deny = pg._pinned_tool_gate(f"python3 '{d / 'kb.py'}' check")
    assert exempt == frozenset() and deny == ""
    assert pg._pinned_tool_gate(f"python3 {d / 'kb.py'} check")[0] == frozenset({str(d / "kb.py")})   # control


# ---------------------------------------------------------------------------------------------- denied: options
@pytest.mark.parametrize("extra", [
    "review calibrate X --lead-question q --reviewer r --reviewer-cmd 'sh -c id'",
    "orchestrate X --drafter-cmd 'sh -c id'",
])
def test_options_that_run_other_commands_are_refused(bundle, extra):
    d, _ = bundle
    ok, reason = _check(f"python3 {d / 'kb.py'} {extra}")
    assert not ok and "refused" in reason, reason


@pytest.mark.parametrize("prefix", ["PYTHONPATH=/tmp/x ", "PYTHONSTARTUP=/tmp/x ", "LD_PRELOAD=/tmp/x.so ", "BASH_ENV=/tmp/x "])
def test_interpreter_rerouting_environment_is_refused(bundle, prefix):
    d, _ = bundle
    ok, reason = _check(f"{prefix}python3 {d / 'kb.py'} check")
    assert not ok and "refused" in reason, reason


# ---------------------------------------------------------------------------------- nothing else is relaxed
def test_a_second_python_snippet_in_the_same_command_is_still_checked(bundle):
    d, _ = bundle
    ok, reason = _check(f"python3 {d / 'kb.py'} check && python3 -c 'import socket'")
    assert not ok and "socket" in reason, reason


def test_another_script_next_to_the_pinned_one_is_still_checked(bundle):
    d, _ = bundle
    (d / "evil.py").write_text("import subprocess\n")
    ok, reason = _check(f"python3 {d / 'kb.py'} check && python3 {d / 'evil.py'}")
    assert not ok and "forbidden import" in reason, reason


def test_write_target_protection_still_applies_to_a_pinned_command(bundle, tmp_path):
    d, _ = bundle
    ledger = tmp_path / ".corvin-ledger" / "ledger.jsonl"
    ok, reason = _check(f"python3 {d / 'kb.py'} check > {ledger}")
    assert not ok, "a redirect into a protected ledger path must still be refused"
    ok2, reason2 = _check(f"python3 {d / 'kb.py'} check > {tmp_path / 'out.txt'}")       # control: harmless target
    assert ok2, reason2


# ---------------------------------------------------------------------------------- the shipped table itself
def test_the_shipped_pin_table_is_one_exact_absolute_path_with_no_wildcards():
    table = pg._PINNED_PYTHON_TOOLS        # this test uses no fixture: monkeypatches of other tests are undone
    assert len(table) == 1
    (script, bundle), = table.items()
    assert Path(script).is_absolute() and script.endswith("/Corvin-Knowledge/scripts/kb.py")
    assert not any(c in script for c in "*?[]$~") and ".." not in script
    assert set(bundle) == {"kb.py", "kb_lib.py", "kb_model.py"}
    assert all(len(h) == 64 and set(h) <= set("0123456789abcdef") for h in bundle.values())
