"""Workflow YAML loader.

Stdlib-only YAML subset parser would be too brittle; this module uses PyYAML
when present and falls back to JSON when a `.json` file is passed. Corvin
bridges already depend on PyYAML for the gateway, so import-failure here is
fail-loud.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorkflowDoc:
    """In-memory representation of a parsed workflow.awp.yaml."""

    awp_version: str
    name: str
    description: str
    inputs: dict[str, Any] = field(default_factory=dict)
    orchestration: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None  # set by load_workflow(); needed to reload on resume (ADR-0188 M5)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source_path: str | None = None) -> "WorkflowDoc":
        """Build a WorkflowDoc from an already-parsed mapping (YAML/JSON).

        Single source of truth for dict→WorkflowDoc, shared by load_workflow()
        and any caller that already holds the parsed data (e.g. the awpkg
        installer validating a workflow straight out of the zip, avoiding a
        temp-file round-trip)."""
        if not isinstance(data, dict):
            raise ValueError("workflow root must be a mapping")
        wf = data.get("workflow") or {}
        return cls(
            awp_version=str(data.get("awp", "1.0.0")),
            name=str(wf.get("name", "")),
            description=str(wf.get("description", "")),
            inputs=dict(data.get("inputs", {})),
            orchestration=dict(data.get("orchestration", {})),
            raw=data,
            source_path=source_path,
        )

    @property
    def graph(self) -> list[dict[str, Any]]:
        orch = self.orchestration or {}
        return list(orch.get("graph", []))

    @property
    def engine(self) -> str:
        return (self.orchestration or {}).get("engine", "dag")


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError("PyYAML is required to load .yaml workflows") from e
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("workflow root must be a mapping")
    return data


def load_workflow(path: str | Path) -> WorkflowDoc:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        data = _parse_yaml(text)
    elif p.suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"unknown workflow extension: {p.suffix}")

    return WorkflowDoc.from_dict(data, source_path=str(p))


def dump_workflow(doc: WorkflowDoc) -> str:
    """Round-trip helper for tests (JSON output keeps deps light)."""
    return json.dumps(doc.raw, sort_keys=True, indent=2)
