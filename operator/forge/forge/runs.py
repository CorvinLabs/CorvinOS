"""Run-workspace bookkeeping (AWP-inspired).

Each tool call gets its own folder under ``runs/<ts>_<id>/``:

    run_manifest.json   input + tool_sha + start time + budget (written before run)
    run_completion.json status + exit_code + duration + sandbox + artifacts list
    RUN_SUMMARY.md      ≤ 1 KB human-readable digest (Voice/Messenger-friendly)
    stdout.json         the tool's raw stdout JSON (full result)
    stderr.txt          captured stderr (only if non-empty)
    artifacts/          directory the tool may write into (rw-bound in bwrap)

This module is transport-agnostic: it knows nothing about MCP. The runner
calls ``begin_run`` before exec and ``end_run`` after.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunContext:
    id: str
    dir: Path
    artifacts_dir: Path
    started_at: float
    tool_name: str
    tool_sha: str
    input_sha: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def manifest_path(self) -> Path:    return self.dir / "run_manifest.json"
    @property
    def completion_path(self) -> Path:  return self.dir / "run_completion.json"
    @property
    def summary_path(self) -> Path:     return self.dir / "RUN_SUMMARY.md"
    @property
    def stdout_path(self) -> Path:      return self.dir / "stdout.json"


def _input_sha(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def begin_run(
    workspace_root: Path,
    *,
    tool_name: str,
    tool_sha: str,
    input_payload: dict[str, Any],
    budget: dict | None = None,
    augment=None,  # callable(payload, artifacts_dir) -> augmented_payload
    redact=None,   # callable(augmented_payload) -> safe_for_manifest_payload
) -> RunContext:
    """Allocate a run folder, optionally augment the payload, write the manifest.

    The ``augment`` hook lets the caller inject runtime context (like
    ``_artifacts_dir``) into the payload before the manifest is written.
    The ``redact`` hook returns a copy of the augmented payload with sensitive
    fields masked — that copy goes into the manifest, but the real payload is
    kept in ``ctx.payload`` for the subprocess (and the cache key, which is
    computed from the real payload so identical real inputs still hit cache).
    """
    # Sub-second precision in the timestamp so lexicographic sort == chronological
    now = time.time()
    ts = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now))
    millis = int((now - int(now)) * 1000)
    rid = f"{ts}.{millis:03d}_{uuid.uuid4().hex[:8]}"
    rdir = Path(workspace_root) / "runs" / rid
    adir = rdir / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    augmented = (
        augment(input_payload, adir) if augment else dict(input_payload)
    )
    isha = _input_sha(augmented)
    manifest_input = redact(augmented) if redact else augmented

    manifest = {
        "run_id": rid,
        "tool": tool_name,
        "tool_sha": tool_sha,
        "input": manifest_input,
        "input_sha": isha,
        "started_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)
        ),
        "budget": budget or {},
    }
    (rdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    return RunContext(
        id=rid,
        dir=rdir,
        artifacts_dir=adir,
        started_at=started,
        tool_name=tool_name,
        tool_sha=tool_sha,
        input_sha=isha,
        payload=augmented,
    )


def _scan_artifacts(adir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not adir.exists():
        return out
    for p in sorted(adir.rglob("*")):
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        rec: dict[str, Any] = {
            "path": str(p.absolute()),
            "rel": str(p.relative_to(adir)),
            "bytes": stat.st_size,
            "kind": p.suffix.lstrip(".") or "bin",
        }
        if 0 < stat.st_size <= 4096:
            try:
                rec["preview"] = p.read_text(errors="replace")[:512]
            except Exception:
                pass
        out.append(rec)
    return out


def end_run(
    ctx: RunContext,
    *,
    status: str,
    exit_code: int,
    duration_s: float,
    sandbox: str,
    stdout_data: Any,
    stderr_text: str = "",
    summary: str = "",
) -> dict[str, Any]:
    """Finalize a run: scan artifacts, write completion + stdout + summary."""
    artifacts = _scan_artifacts(ctx.artifacts_dir)

    completion = {
        "run_id": ctx.id,
        "status": status,
        "exit_code": exit_code,
        "duration_s": round(duration_s, 4),
        "sandbox": sandbox,
        "artifacts": artifacts,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    ctx.completion_path.write_text(json.dumps(completion, indent=2))

    if stdout_data is None:
        ctx.stdout_path.write_text("null\n")
    else:
        ctx.stdout_path.write_text(json.dumps(stdout_data, indent=2) + "\n")

    if stderr_text:
        (ctx.dir / "stderr.txt").write_text(stderr_text)

    short_summary = summary.strip()
    if len(short_summary) > 800:
        short_summary = short_summary[:800] + "…"
    md = (
        f"# Run {ctx.id}\n"
        f"**Tool:** `{ctx.tool_name}` (sha={ctx.tool_sha})\n"
        f"**Status:** {status} | **rc:** {exit_code} | "
        f"**Duration:** {duration_s:.3f}s | **Sandbox:** {sandbox}\n"
        f"**Artifacts:** {len(artifacts)}"
        f"{(' (' + ', '.join(a['rel'] for a in artifacts[:5]) + ')') if artifacts else ''}\n"
    )
    if short_summary:
        md += f"\n{short_summary}\n"
    ctx.summary_path.write_text(md)
    return completion


def list_runs(workspace_root: Path) -> list[dict[str, Any]]:
    """Return run records (id + manifest summary), newest first."""
    rdir = Path(workspace_root) / "runs"
    if not rdir.exists():
        return []
    out = []
    for d in sorted(rdir.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        mpath = d / "run_manifest.json"
        cpath = d / "run_completion.json"
        rec: dict[str, Any] = {"run_id": d.name, "dir": str(d)}
        try:
            if mpath.exists():
                m = json.loads(mpath.read_text())
                rec.update({"tool": m.get("tool"),
                            "tool_sha": m.get("tool_sha"),
                            "started_at": m.get("started_at")})
            if cpath.exists():
                c = json.loads(cpath.read_text())
                rec.update({"status": c.get("status"),
                            "duration_s": c.get("duration_s"),
                            "sandbox": c.get("sandbox"),
                            "n_artifacts": len(c.get("artifacts") or [])})
        except Exception:
            pass
        out.append(rec)
    return out


def show_run(workspace_root: Path, run_id: str | None = None) -> dict[str, Any]:
    """Return manifest, completion, summary text for a single run.
    ``run_id=None`` → latest run."""
    runs = list_runs(workspace_root)
    if not runs:
        raise FileNotFoundError("no runs in this workspace")
    if run_id is None:
        rec = runs[0]
    else:
        rec = next((r for r in runs if r["run_id"] == run_id), None)
        if rec is None:
            raise FileNotFoundError(f"run not found: {run_id}")
    rdir = Path(rec["dir"])
    out = dict(rec)
    if (rdir / "run_manifest.json").exists():
        out["manifest"] = json.loads((rdir / "run_manifest.json").read_text())
    if (rdir / "run_completion.json").exists():
        out["completion"] = json.loads((rdir / "run_completion.json").read_text())
    if (rdir / "RUN_SUMMARY.md").exists():
        out["summary_md"] = (rdir / "RUN_SUMMARY.md").read_text()
    return out


def cleanup_runs(workspace_root: Path, *, keep: int) -> dict[str, Any]:
    """Delete all but the most recent ``keep`` runs. Returns counts."""
    import shutil
    rdir = Path(workspace_root) / "runs"
    if not rdir.exists() or keep < 0:
        return {"deleted": 0, "kept": 0}
    runs = sorted(
        (d for d in rdir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    kept = runs[:keep]
    to_delete = runs[keep:]
    for d in to_delete:
        shutil.rmtree(d, ignore_errors=True)
    return {"deleted": len(to_delete), "kept": len(kept),
            "kept_ids": [d.name for d in kept]}

