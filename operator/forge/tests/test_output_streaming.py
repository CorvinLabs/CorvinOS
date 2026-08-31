"""S8 — output-bytes streaming: truncated stdout is preserved as artifact.

Fictional task: a forged tool spits out 8 MiB of data (as if it were
streaming a large CSV or log). The runtime cap is set to 1 MiB so the
truncation path fires. Without S8, the missing 7 MiB are silently lost
and a downstream consumer that trusts the result would see partial
data without realizing it. With S8, the full 8 MiB land in
``run/artifacts/full_stdout.bin`` and the envelope's ``meta`` block
carries enough info to fetch the rest:

  meta.stdout_truncated         = true
  meta.stdout_truncated_at_bytes = 1048576
  meta.stdout_total_bytes       = 8388608
  meta.stdout_full_artifact     = /path/to/run/<id>/artifacts/full_stdout.bin

The caller can then read the artifact directly (Read tool, filesystem)
or — once we add it — via a future ``forge_chunk(run_id, offset, len)``
MCP call.

Negative case: a tool whose stdout fits under the cap behaves exactly
as before — no truncation, no artifact, no meta entries.

Run as: python3 operator/forge/tests/test_output_streaming.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "forge"))

from forge.registry import Registry  # noqa: E402
from forge.runner import run_tool  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# 8 MiB tool — deterministic content so we can verify the artifact bytes.
EIGHT_MIB_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
size = int(p.get("size", 8 * 1024 * 1024))
ch = p.get("char", "x")
sys.stdout.write(ch * size)
'''
SIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "size": {"type": "integer", "minimum": 0},
        "char": {"type": "string"},
    },
}

# Small-output tool — stays well under any cap, used as the negative case.
SMALL_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"ok": True, "msg": p.get("msg", "hi")}))
'''
SMALL_SCHEMA = {
    "type": "object",
    "properties": {"msg": {"type": "string"}},
}


def main() -> int:
    print("[output streaming — fictional 8 MiB tool against 1 MiB cap]")

    with tempfile.TemporaryDirectory() as td:
        reg = Registry(Path(td))
        reg.create("flood", "floods stdout with 8 MiB",
                   SIZE_SCHEMA, EIGHT_MIB_IMPL)

        cap = 1 * 1024 * 1024  # 1 MiB cap
        size = 8 * 1024 * 1024  # 8 MiB output
        r = run_tool(reg, "flood", {"size": size, "char": "x"},
                     permission_mode="yes", output_cap=cap)

        t("call survives oversized stdout (envelope ok=True)",
          r.ok)
        t("stdout_truncated flag set on RunResult",
          r.stdout_truncated is True)

        env = r.envelope or {}
        meta = env.get("meta") or {}
        t("meta.stdout_truncated == True",
          meta.get("stdout_truncated") is True,
          detail=f"meta={meta!r}")
        t("meta.stdout_truncated_at_bytes == cap",
          meta.get("stdout_truncated_at_bytes") == cap,
          detail=f"got {meta.get('stdout_truncated_at_bytes')!r}")
        t("meta.stdout_total_bytes == 8 MiB",
          meta.get("stdout_total_bytes") == size,
          detail=f"got {meta.get('stdout_total_bytes')!r}")

        artifact_path_str = meta.get("stdout_full_artifact")
        t("meta.stdout_full_artifact set",
          isinstance(artifact_path_str, str) and artifact_path_str,
          detail=f"got {artifact_path_str!r}")

        artifact = Path(artifact_path_str) if artifact_path_str else None
        t("artifact file exists on disk",
          artifact is not None and artifact.is_file(),
          detail=f"path={artifact}")

        if artifact and artifact.is_file():
            full_bytes = artifact.read_bytes()
            t("artifact has full 8 MiB",
              len(full_bytes) == size,
              detail=f"got {len(full_bytes)} bytes")
            t("artifact content matches deterministic pattern",
              full_bytes == b"x" * size)

            # Fictional chunk-fetch: simulate a downstream caller that asks
            # for bytes [cap, 2*cap) — these are the FIRST bytes that were
            # truncated from the live output. They MUST be present in the
            # artifact, otherwise S8 doesn't actually solve the problem.
            chunk = full_bytes[cap:2 * cap]
            t("chunk [cap, 2*cap) matches expected pattern",
              chunk == b"x" * cap)

        # ---- negative: small output is unaffected --------------------------
        reg.create("tiny", "tiny ok", SMALL_SCHEMA, SMALL_IMPL)
        r_small = run_tool(reg, "tiny", {"msg": "hi"},
                           permission_mode="yes", output_cap=cap)
        t("small-output tool: not truncated",
          r_small.stdout_truncated is False)
        meta_s = (r_small.envelope or {}).get("meta") or {}
        t("small-output tool: no stdout_truncated meta",
          "stdout_truncated" not in meta_s,
          detail=f"meta={meta_s!r}")
        t("small-output tool: no stdout_full_artifact",
          "stdout_full_artifact" not in meta_s)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
