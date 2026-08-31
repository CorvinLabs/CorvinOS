"""Real WorkerEngine backed by the `claude` CLI in headless mode (`-p`).

Kept in a separate module from engines.py so importing corvin_workflows
never requires the `claude` binary to be on PATH — only `--engine claude`
(CLI) or explicit construction (tests) touches this module.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .engines import EngineCall

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_DEFAULT_MODEL = "haiku"  # cheapest tier — this engine is for structural E2E proof, not quality
_DEFAULT_TIMEOUT_S = 60
_DENIED_TOOLS = "Bash Edit Write Read Glob Grep WebSearch WebFetch Task NotebookEdit"


class ClaudeEngineError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: first '{' to matching last '}' — the model sometimes adds a
    # sentence before/after the JSON despite instructions.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ClaudeEngineError(f"could not parse a JSON object from claude output: {text[:500]!r}")


def _build_prompt(call: EngineCall) -> str:
    parts = [call.instructions.strip()]
    if call.inputs:
        parts.append(f"\nWorkflow inputs:\n{json.dumps(call.inputs, indent=2, default=str)}")
    if call.state:
        # Keep it bounded — full state can be large after several nodes.
        trimmed = {k: v for k, v in call.state.items() if not str(k).startswith("_")}
        if trimmed:
            parts.append(f"\nUpstream context (state):\n{json.dumps(trimmed, indent=2, default=str)}")
    parts.append(
        "\nRespond with ONLY a single JSON object and nothing else — no markdown fences, "
        "no commentary, no explanation before or after it."
    )
    return "\n".join(parts)


@dataclass
class ClaudeCliEngine:
    """Production WorkerEngine: one `claude -p` subprocess per spawn()."""

    name: str = "claude-cli"
    model: str = _DEFAULT_MODEL
    timeout_s: int = _DEFAULT_TIMEOUT_S
    history: list[EngineCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if shutil.which("claude") is None:
            raise ClaudeEngineError(
                "ClaudeCliEngine requires the `claude` CLI on PATH — install Claude Code or use "
                "--engine stub"
            )

    def spawn(self, call: EngineCall) -> dict[str, Any]:
        self.history.append(call)
        prompt = _build_prompt(call)
        cmd = [
            "claude", "-p", prompt,
            "--model", self.model,
            "--disallowedTools", _DENIED_TOOLS,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise ClaudeEngineError(
                f"claude call for agent={call.agent!r} timed out after {self.timeout_s}s"
            ) from e
        if proc.returncode != 0:
            raise ClaudeEngineError(
                f"claude call for agent={call.agent!r} exited {proc.returncode}: "
                f"{proc.stderr.strip()[-2000:]}"
            )
        return _extract_json(proc.stdout)
