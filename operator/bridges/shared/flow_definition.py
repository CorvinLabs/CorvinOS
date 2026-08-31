"""flow_definition.py — CorvinFlow M1: FlowDefinition, FlowBudget, FlowRunManifest.

ADR-0121 structural types.  Three guarantees:
  1. FlowDefinition is parsed structurally from YAML — never eval()'d.
  2. Template variables are allow-listed to {flow.input.*} and {steps.*}.
  3. FlowRunManifest is append-only JSONL at mode 0600 (GDPR Art. 5).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml


ALLOWED_TEMPLATE_VARS: frozenset[str] = frozenset({"flow.input", "steps"})


class FlowDefinitionError(Exception):
    """Raised when a FlowDefinition fails structural validation."""


class FlowBudgetExceeded(Exception):
    """Raised when any budget dimension is exhausted before a step spawns."""


class FlowDefinition:
    """Parsed, validated representation of a flow.yaml. Never eval()'d.

    Template variables in step.task are restricted to the allow-list
    ALLOWED_TEMPLATE_VARS — structural defence against prompt injection via
    FlowBundle distribution.
    """

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        flow = raw.get("flow", {})
        self.id: str = flow.get("id", "")
        self.version: str = flow.get("version", "0.0.0")
        self.budget: dict[str, Any] = flow.get("budget", {})
        self.compliance: dict[str, Any] = flow.get("compliance", {})
        self.steps: dict[str, Any] = flow.get("steps", {})
        self._validate()

    def _validate(self) -> None:
        if not self.id:
            raise FlowDefinitionError("flow.id is required")
        if not self.steps:
            raise FlowDefinitionError("flow.steps must not be empty")
        for step_id, step in self.steps.items():
            self._check_template(step_id, step.get("task", ""))

    def _check_template(self, step_id: str, task: str) -> None:
        for var in re.findall(r"\{([^}]+)\}", task):
            allowed = any(
                var == ap or var.startswith(ap + ".")
                for ap in ALLOWED_TEMPLATE_VARS
            )
            if not allowed:
                raise FlowDefinitionError(
                    f"step '{step_id}' uses disallowed template variable "
                    f"'{{{var}}}' — only {{flow.input.*}} and {{steps.*}} allowed"
                )

    @classmethod
    def from_yaml(cls, text: str) -> "FlowDefinition":
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            raise FlowDefinitionError(
                f"flow YAML must be a mapping, got "
                f"{type(raw).__name__ if raw is not None else 'null'}"
            )
        return cls(raw)

    @classmethod
    def from_file(cls, path: Path) -> "FlowDefinition":
        return cls.from_yaml(path.read_text())


class FlowBudget:
    """Runtime budget tracker for a FlowRun — five independent dimensions.

    All dimensions are checked pre-spawn (before any A2A envelope is built).
    Record accounting post-step with record_step().
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self.max_compute_units: int = spec.get("max_compute_units", 9_999)
        self.max_tokens: int = spec.get("max_tokens", 10_000_000)
        self.max_wall_time_s: float = float(spec.get("max_wall_time_s", 3_600.0))
        self.max_cost_usd: float = float(spec.get("max_cost_usd", 999.0))
        self.max_steps: int = spec.get("max_steps", 100)
        self._compute_used: int = 0
        self._tokens_used: int = 0
        self._cost_used: float = 0.0
        self._steps_done: int = 0
        self._start_ts: float = time.time()

    def check(self) -> None:
        """Raise FlowBudgetExceeded if any dimension is exhausted."""
        elapsed = time.time() - self._start_ts
        if self._compute_used >= self.max_compute_units:
            raise FlowBudgetExceeded(
                f"compute_units exhausted: {self._compute_used}/{self.max_compute_units}"
            )
        if self._tokens_used >= self.max_tokens:
            raise FlowBudgetExceeded(
                f"tokens exhausted: {self._tokens_used}/{self.max_tokens}"
            )
        if elapsed >= self.max_wall_time_s:
            raise FlowBudgetExceeded(
                f"wall_time exceeded: {elapsed:.1f}s/{self.max_wall_time_s}s"
            )
        if self._cost_used >= self.max_cost_usd:
            raise FlowBudgetExceeded(
                f"cost exceeded: ${self._cost_used:.3f}/${self.max_cost_usd:.2f}"
            )
        if self._steps_done >= self.max_steps:
            raise FlowBudgetExceeded(
                f"step_count exceeded: {self._steps_done}/{self.max_steps}"
            )

    def record_step(
        self, *, compute: int = 1, tokens: int = 0, cost: float = 0.0
    ) -> None:
        self._compute_used += compute
        self._tokens_used += tokens
        self._cost_used += cost
        self._steps_done += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "compute_used": self._compute_used,
            "compute_remaining": self.max_compute_units - self._compute_used,
            "tokens_used": self._tokens_used,
            "tokens_remaining": self.max_tokens - self._tokens_used,
            "steps_done": self._steps_done,
            "wall_time_elapsed_s": round(time.time() - self._start_ts, 2),
        }


class FlowRunManifest:
    """Append-only JSONL manifest for a FlowRun.

    Mode 0600 — GDPR Art. 5 metadata-only principle: output content never
    stored, only output_sha256_prefix (first 16 hex chars of SHA-256).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        os.chmod(path, 0o600)

    def append(self, event_type: str, **fields: Any) -> None:
        entry = {"type": event_type, "ts": time.time(), **fields}
        with open(self._path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def events(self) -> list[dict[str, Any]]:
        lines = self._path.read_text().strip().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def event_types(self) -> list[str]:
        return [e["type"] for e in self.events()]


def sha256_prefix(text: str) -> str:
    """First 16 hex chars of SHA-256(text) — safe for audit fields."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
