"""Pipeline manifest dataclasses and PipelineStore (ADR-0027)."""
from __future__ import annotations

import dataclasses
import json
import os
import re
import secrets
import string
import time
from pathlib import Path
from typing import Any

_PIPE_ALPHABET = string.ascii_letters + string.digits + "_-"

# stage_id / manager_id become a path component under the pipeline (or HAC) root
# and arrive from an LLM-authored submission, also at a steering gate
# (``add_stage``). Anything but this shape is refused at parse time; the store
# re-checks containment before it touches the filesystem.
_COMPONENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidComponentId(ValueError):
    """A stage_id / manager_id that may not be used as a path component."""


def validate_component_id(value: Any, *, kind: str = "stage_id") -> str:
    """Return *value* when it is a safe single path component, else raise."""
    if not isinstance(value, str) or not _COMPONENT_ID_RE.match(value):
        raise InvalidComponentId(
            f"invalid {kind} {str(value)[:80]!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return value


def require_unique_ids(ids: "list[str]", *, kind: str = "stage_id") -> None:
    """Refuse a list that names one id twice, compared case-INSENSITIVELY.

    A stage's result, its artifact directory and every ``$<stage_id>.`` reference
    are keyed by the id: two stages sharing one overwrite each other's result and
    artifacts, and a reference resolves to whichever ran last (review R3-BR-5).
    ``m`` and ``M`` are one directory on a case-insensitive filesystem (macOS,
    Windows), so they count as the same id.
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for raw in ids:
        key = str(raw).casefold()
        if key in seen and str(raw) not in dupes:
            dupes.append(str(raw))
        seen.add(key)
    if dupes:
        raise ValueError(f"duplicate {kind} (case-insensitive): {sorted(dupes)}")


def require_unique_stage_ids(stages: "list[Any]") -> None:
    """``require_unique_ids`` over ``StageSpec`` objects or raw stage dicts."""
    require_unique_ids(
        [s.get("stage_id", "") if isinstance(s, dict) else getattr(s, "stage_id", "")
         for s in stages],
        kind="stage_id")


def contained_path(root: Path, *parts: str) -> Path:
    """``root/parts...`` — refused unless it resolves inside *root*."""
    candidate = Path(root).joinpath(*parts)
    resolved_root = Path(root).resolve()
    if not candidate.resolve().is_relative_to(resolved_root):
        raise InvalidComponentId(
            f"path {'/'.join(parts)[:120]!r} escapes its compute root")
    return candidate


def new_pipeline_id() -> str:
    """Return a fresh ``pipeline_<22-url-safe-chars>`` identifier."""
    return "pipeline_" + "".join(secrets.choice(_PIPE_ALPHABET) for _ in range(22))


@dataclasses.dataclass
class StageSpec:
    """One compute stage inside a pipeline."""

    stage_id: str
    tool_name: str
    strategy: str = "grid"
    param_grid: dict = dataclasses.field(default_factory=dict)
    budget: dict = dataclasses.field(default_factory=dict)
    inputs: dict = dataclasses.field(default_factory=dict)
    # {key: "$stage_id.artifacts/file" | literal}
    outputs: list = dataclasses.field(default_factory=list)
    # expected artifact filenames
    sensitive_fields: list = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> StageSpec:
        return cls(
            stage_id=validate_component_id(d["stage_id"], kind="stage_id"),
            tool_name=str(d["tool_name"]),
            strategy=str(d.get("strategy", "grid")),
            param_grid=dict(d.get("param_grid") or {}),
            budget=dict(d.get("budget") or {}),
            inputs=dict(d.get("inputs") or {}),
            outputs=list(d.get("outputs") or []),
            sensitive_fields=list(d.get("sensitive_fields") or []),
        )


@dataclasses.dataclass
class PipelineManifest:
    """Describes a full pipeline job submitted to the PipelineEngine."""

    pipeline_id: str
    tenant_id: str
    stages: list  # list[StageSpec]
    steering_gate: bool = True
    steering_gate_timeout_s: float = 3600.0
    budget: dict = dataclasses.field(default_factory=dict)
    # audit trail of LLM-forged tools injected at gates
    forged_tools: list = dataclasses.field(default_factory=list)
    # [{gate_index, tool_name}]

    def to_dict(self) -> dict:
        return {
            "pipeline_id": self.pipeline_id,
            "tenant_id": self.tenant_id,
            "stages": [dataclasses.asdict(s) for s in self.stages],
            "steering_gate": self.steering_gate,
            "steering_gate_timeout_s": self.steering_gate_timeout_s,
            "budget": self.budget,
            "forged_tools": self.forged_tools,
        }


def _atomic_write_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    """Write *payload* to *path* atomically via temp-file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(
        path.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}"
    )
    tmp.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class PipelineStore:
    """On-disk artefacts for one pipeline run.

    Layout::

        <corvin_home>/tenants/<tid>/compute/pipelines/<pipeline_id>/
            manifest.json           — written once on start
            pipeline_summary.json   — rolling state
            stages/<stage_id>/
                artifacts/          — stage output files
    """

    def __init__(self, corvin_home: Path, tenant_id: str, pipeline_id: str) -> None:
        self.corvin_home = Path(corvin_home)
        self.tenant_id = tenant_id
        self.pipeline_id = pipeline_id
        self.root_dir: Path = (
            self.corvin_home
            / "tenants"
            / tenant_id
            / "compute"
            / "pipelines"
            / pipeline_id
        )

    # -- manifest -----------------------------------------------------------------

    def write_manifest(self, manifest: PipelineManifest) -> None:
        # started_at: the console lists it (routes/compute.py list_pipelines); it was never written.
        _atomic_write_json(self.root_dir / "manifest.json", {**manifest.to_dict(), "started_at": time.time()})

    def read_manifest(self) -> dict:
        return _read_json(self.root_dir / "manifest.json")

    # -- rolling summary ----------------------------------------------------------

    def write_summary(
        self,
        state: str,
        current_stage_id: str | None,
        completed_stages: list,
        best_losses: dict,
        error: str | None = None,
    ) -> None:
        payload = {
            "error": error,
            "pipeline_id": self.pipeline_id,
            "tenant_id": self.tenant_id,
            "state": state,
            "current_stage_id": current_stage_id,
            "completed_stages": completed_stages,
            "best_losses": best_losses,
        }
        _atomic_write_json(self.root_dir / "pipeline_summary.json", payload)

    def read_summary(self) -> dict:
        path = self.root_dir / "pipeline_summary.json"
        if not path.exists():
            return {}
        return _read_json(path)

    # -- stage helpers ------------------------------------------------------------

    def stage_artifacts_dir(self, stage_id: str) -> Path:
        """Return the artifacts directory for *stage_id* (no FS side-effects)."""
        validate_component_id(stage_id, kind="stage_id")
        return contained_path(self.root_dir, "stages", stage_id, "artifacts")

    def ensure_stage_dir(self, stage_id: str) -> Path:
        """Create and return the artifacts directory for *stage_id*."""
        d = self.stage_artifacts_dir(stage_id)
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        return d
