"""Smoke + robustness tests. Run as ``python3 tests/test_forge.py``.

Covers:
  - happy path (create, list, call, delete)
  - schema validation (missing, type, enum)
  - tamper detection (sha drift on disk)
  - permission gate (deny / yes / drift re-prompt)
  - concurrency (parallel bump_call under flock)
  - output cap (oversized stdout truncated, not OOM)
  - timeout (wall clock + cleanup)
  - manifest corruption (raises, doesn't silently lose data)
  - audit log integrity
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge.permissions import PermissionStore, decide
from forge.registry import Registry
from forge.runner import (
    PermissionDenied,
    SchemaError,
    TamperError,
    ToolError,
    run_tool,
)


# ---------- helpers ---------------------------------------------------------

ECHO_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"ok": True, "echo": p}))
'''

ECHO_SCHEMA = {
    "type": "object",
    "required": ["msg"],
    "properties": {"msg": {"type": "string"}},
}

SLOW_IMPL = '''#!/usr/bin/env python3
import time, sys, json
json.loads(sys.stdin.read())
time.sleep(60)
print("never")
'''

NOOP_SCHEMA = {"type": "object", "properties": {}}

OVERSIZED_IMPL = '''#!/usr/bin/env python3
import json, sys
json.loads(sys.stdin.read())
# Print ~8 MiB; runner caps at 4 MiB by default.
sys.stdout.write("x" * (8 * 1024 * 1024))
'''


PASS = 0
FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    print(f"  {mark}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- tests -----------------------------------------------------------

def test_happy_path():
    print("\n[happy path]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        spec = reg.create("echo", "echo tool", ECHO_SCHEMA, ECHO_IMPL)
        t("create returns spec", spec.name == "echo")
        t("manifest persisted", reg.get("echo") is not None)
        t("impl in tools/", Path(spec.impl_path).parent.name == "tools")
        r = run_tool(reg, "echo", {"msg": "hi"}, permission_mode="yes")
        t("call ok", r.ok)
        # _artifacts_dir is auto-injected by the runner; only assert msg=hi
        t("payload echoed",
          r.data and r.data.get("ok") is True and
          r.data.get("echo", {}).get("msg") == "hi")
        t("call_count bumped", reg.get("echo").call_count == 1)
        t("sandbox label set", r.sandbox in ("bwrap", "rlimits"))
        reg.delete("echo")
        t("delete clears entry", reg.get("echo") is None)


def test_schema_violations():
    print("\n[schema violations]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        try:
            run_tool(reg, "echo", {}, permission_mode="yes")
            t("missing required raises", False)
        except SchemaError:
            t("missing required raises", True)
        try:
            run_tool(reg, "echo", {"msg": 42}, permission_mode="yes")
            t("type mismatch raises", False)
        except SchemaError:
            t("type mismatch raises", True)


def test_tamper_detection():
    print("\n[tamper detection]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        spec = reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        # Mutate the impl file out from under the manifest.
        Path(spec.impl_path).write_text(ECHO_IMPL + "\n# evil\n")
        try:
            run_tool(reg, "echo", {"msg": "hi"}, permission_mode="yes")
            t("sha drift refused", False)
        except TamperError:
            t("sha drift refused", True)


def test_permission_gate():
    print("\n[permission gate]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        spec = reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        # mode=deny fails closed
        try:
            run_tool(reg, "echo", {"msg": "x"}, permission_mode="deny")
            t("deny mode fails closed", False)
        except PermissionDenied:
            t("deny mode fails closed", True)
        # mode=yes records approval
        run_tool(reg, "echo", {"msg": "x"}, permission_mode="yes")
        store = PermissionStore(reg.root)
        t("approval recorded", store.is_approved("echo", spec.sha256))
        # subsequent deny still wins because we already approved (cached)
        # ...but if we revoke + deny we get PermissionDenied
        store.revoke("echo")
        try:
            run_tool(reg, "echo", {"msg": "x"}, permission_mode="deny")
            t("revoke + deny denied", False)
        except PermissionDenied:
            t("revoke + deny denied", True)
        # forging a NEW sha should re-prompt (deny here)
        reg.delete("echo")
        spec2 = reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL + "\n# v2\n")
        store.record("echo", "00deadbeef00", mode="yes")  # stale approval
        try:
            run_tool(reg, "echo", {"msg": "x"}, permission_mode="deny")
            t("sha drift re-prompts", False)
        except PermissionDenied:
            t("sha drift re-prompts", True, detail=f"new sha={spec2.sha256}")


def test_concurrency():
    print("\n[concurrency]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        N = 16

        def one():
            return run_tool(reg, "echo", {"msg": "c"}, permission_mode="yes").ok

        with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
            results = list(ex.map(lambda _: one(), range(N)))
        t(f"all {N} parallel calls ok", all(results))
        final = reg.get("echo").call_count
        t(f"call_count == {N} (no lost updates)", final == N,
          detail=f"got {final}")


def test_output_cap():
    print("\n[output cap]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("flood", "floods stdout", NOOP_SCHEMA, OVERSIZED_IMPL)
        r = run_tool(reg, "flood", {}, permission_mode="yes",
                     output_cap=1024 * 1024)
        t("call survives oversized stdout", r.ok)
        t("stdout flagged truncated", r.stdout_truncated is True)


def test_timeout():
    print("\n[timeout]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("slow", "sleeps", NOOP_SCHEMA, SLOW_IMPL)
        t0 = time.monotonic()
        try:
            run_tool(reg, "slow", {}, permission_mode="yes", timeout=0.5)
            t("timeout raises", False)
        except ToolError as e:
            elapsed = time.monotonic() - t0
            ok = "timed out" in str(e) and elapsed < 5.0
            t("timeout raises and returns quickly", ok,
              detail=f"{elapsed:.2f}s")


def test_manifest_corruption():
    print("\n[manifest corruption]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        # Corrupt the manifest.
        reg.manifest_path.write_text("{ this is not json")
        try:
            reg.list()
            t("corruption raises", False)
        except RuntimeError as e:
            t("corruption raises", "corrupted" in str(e))


def test_audit_integrity():
    print("\n[audit integrity]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        reg.delete("echo")
        reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        reg.promote("echo")
        events = (reg.root / "audit.jsonl").read_text().splitlines()
        # Audit format is now structured (event_type instead of action)
        types = [json.loads(line)["event_type"] for line in events]
        t("4 events recorded", len(types) == 4,
          detail=f"got {len(types)}")
        t("event_type sequence matches",
          types == ["tool.created", "tool.deleted", "tool.created", "tool.promoted"])


def test_invalid_runtime():
    print("\n[invalid runtime]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        try:
            reg.create("x", "x", NOOP_SCHEMA, "echo hi", runtime="ruby")
            t("rejects unknown runtime", False)
        except ValueError:
            t("rejects unknown runtime", True)


def test_windows_preexec_fn_skipped():
    """Adversarial-review regression (fresh Windows 11 install): `preexec_fn`
    is a POSIX-only Popen kwarg — CPython raises ValueError synchronously
    inside Popen.__init__ on Windows, before any process is spawned, if it's
    not None. bwrap is unavailable on Windows too, so every forge tool call
    fell through to the "rlimits" sandbox_label, which unconditionally
    passed `preexec_fn=lambda: apply_rlimits(limits)` — crashing every
    single Windows forge invocation (mcp__forge__forge_exec), not just
    intermittently.

    subprocess.mswindows is fixed at import time from the REAL sys.platform,
    so monkeypatching sys.platform here can't make a genuine Popen(..,
    preexec_fn=...) raise on this (Linux) test host the way it would on
    real Windows — that would need an actual Windows run, out of scope for
    a unit test. What we CAN and must verify precisely is the one thing our
    fix actually controls: that runner.py computes preexec_fn=None when
    sys.platform says Windows, before it ever reaches Popen — captured by
    wrapping the real subprocess.Popen so the tool still genuinely runs."""
    print("\n[windows preexec_fn skipped]")
    import subprocess as _sp
    from forge import runner as _runner_mod

    captured: dict = {}
    real_popen = _sp.Popen

    def _capturing_popen(*args, **kwargs):
        captured["preexec_fn"] = kwargs.get("preexec_fn")
        return real_popen(*args, **kwargs)

    orig_platform = _runner_mod.sys.platform
    orig_popen = _runner_mod.subprocess.Popen
    _runner_mod.sys.platform = "win32"
    _runner_mod.subprocess.Popen = _capturing_popen
    try:
        with tempfile.TemporaryDirectory() as td:
            reg = Registry(Path(td))
            reg.create("echo", "echo tool", ECHO_SCHEMA, ECHO_IMPL)
            r = run_tool(reg, "echo", {"msg": "hi"}, permission_mode="yes")
        t("tool still runs correctly under simulated Windows", r.ok)
        t("preexec_fn is None when sys.platform == 'win32'",
          captured.get("preexec_fn") is None,
          detail=f"got {captured.get('preexec_fn')!r}")
    finally:
        _runner_mod.sys.platform = orig_platform
        _runner_mod.subprocess.Popen = orig_popen

    # Sanity: confirm the SAME tool call on the real (non-Windows) platform
    # still uses the rlimit preexec_fn — proves the fix is conditional, not
    # a blanket removal of the POSIX sandbox path.
    captured.clear()
    _runner_mod.subprocess.Popen = _capturing_popen
    try:
        with tempfile.TemporaryDirectory() as td:
            reg = Registry(Path(td))
            reg.create("echo", "echo tool", ECHO_SCHEMA, ECHO_IMPL)
            run_tool(reg, "echo", {"msg": "hi"}, permission_mode="yes")
        t("preexec_fn still set on the real (non-Windows) platform",
          captured.get("preexec_fn") is not None)
    finally:
        _runner_mod.subprocess.Popen = orig_popen


def test_promote_layout():
    print("\n[promote layout]")
    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("echo", "e", ECHO_SCHEMA, ECHO_IMPL)
        skill_dir = reg.promote("echo")
        t("SKILL.md written", (skill_dir / "SKILL.md").exists())
        t("impl copied alongside",
          any(p.suffix == ".py" for p in skill_dir.iterdir()))
        body = (skill_dir / "SKILL.md").read_text()
        t("SKILL.md has frontmatter",
          body.startswith("---\nname: echo"))


# ---------- driver ----------------------------------------------------------

def main() -> int:
    test_happy_path()
    test_schema_violations()
    test_tamper_detection()
    test_permission_gate()
    test_concurrency()
    test_output_cap()
    test_timeout()
    test_manifest_corruption()
    test_audit_integrity()
    test_invalid_runtime()
    test_windows_preexec_fn_skipped()
    test_promote_layout()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
