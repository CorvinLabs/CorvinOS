"""Sandboxed execution for the `code` node type (ADR-0188 M1).

Reuses Forge's bwrap sandbox *primitives* (`operator/forge/forge/sandbox.py`)
directly — not the full `run_tool()` orchestration, which is coupled to
Forge's tool registry / artifact store / audit envelope and does not apply to
a workflow-local, unregistered code snippet. A `code` node is architecturally
an anonymous, workflow-scoped Forge-tool invocation: same isolation layers
(bwrap namespace jail when available, POSIX rlimits + stripped env always),
no MCP registration, no LLM in front of it — satisfying AWP-1.0 spec rule R33
("[RUNTIME] node is type:deterministic — runtime must ensure no LLM calls")
for the first time with a real executor instead of an unenforced annotation.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_FORGE_ROOT = Path(__file__).resolve().parents[3] / "operator" / "forge"

_RUNNER_PREAMBLE = "import json\nimport sys\n\n"
_RUNNER_EPILOGUE = """

if __name__ == "__main__":
    _args = json.loads(sys.argv[1])
    _result = main(**_args)
    if not isinstance(_result, dict):
        raise TypeError(f"code node main() must return a dict, got {type(_result).__name__}")
    sys.stdout.write(json.dumps(_result))
"""

_DEFAULT_TIMEOUT_S = 10


class CodeExecutionError(RuntimeError):
    """Raised when the sandboxed script fails, times out, or misbehaves."""


def _resolve_selector(selector: str, *, state: dict[str, Any], inputs: dict[str, Any]) -> Any:
    """Same selector grammar as `fan_out.items_from`, extended to arbitrary
    depth: `node.field.subfield...` walks `state[node][field][subfield]...`
    one dict level per dot (needed for e.g. a `merge` node's single-key
    output wrapper: `combined.context.alcohol_flag`). A bare name (no dot)
    checks workflow `inputs` first, then top-level `state`."""
    if "." in selector:
        parts = selector.split(".")
        value: Any = state.get(parts[0])
        for part in parts[1:]:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value
    if selector in inputs:
        return inputs[selector]
    return state.get(selector)


def run_sandboxed_python(
    source: str,
    args: dict[str, Any],
    *,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run `source` (must define `def main(**kwargs) -> dict`) in a sandbox.

    Uses bwrap when available (namespace jail: no network, read-only system,
    dedicated /tmp — see forge/sandbox.py::build_bwrap_cmd); always applies
    POSIX rlimits + a stripped env as the belt-and-suspenders second layer,
    matching Forge's own documented defense-in-depth strategy.
    """
    if str(_FORGE_ROOT) not in sys.path:
        sys.path.insert(0, str(_FORGE_ROOT))
    from forge.sandbox import Limits, apply_rlimits, build_bwrap_cmd, have_bwrap, stripped_env

    with tempfile.TemporaryDirectory(prefix="awp_code_") as tmpdir:
        impl_path = Path(tmpdir) / "impl.py"
        impl_path.write_text(_RUNNER_PREAMBLE + source + _RUNNER_EPILOGUE, encoding="utf-8")
        args_json = json.dumps(args)

        limits = Limits(cpu_seconds=timeout_s)
        env = stripped_env()

        if have_bwrap():
            cmd = build_bwrap_cmd(
                [sys.executable, str(impl_path), args_json],
                impl_path,
                allow_network=False,
            )
        else:
            cmd = [sys.executable, str(impl_path), args_json]

        run_kwargs: dict[str, Any] = dict(
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        if sys.platform != "win32":
            # preexec_fn is POSIX-only; subprocess raises on Windows if set.
            run_kwargs["preexec_fn"] = lambda: apply_rlimits(limits)

        try:
            proc = subprocess.run(cmd, **run_kwargs)
        except subprocess.TimeoutExpired as e:
            raise CodeExecutionError(f"code node timed out after {timeout_s}s") from e

        if proc.returncode != 0:
            raise CodeExecutionError(
                f"code node exited {proc.returncode}: {proc.stderr.strip()[-2000:]}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise CodeExecutionError(
                f"code node stdout was not valid JSON: {proc.stdout[:500]!r}"
            ) from e
