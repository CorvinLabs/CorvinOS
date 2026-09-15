"""
Quality Metrics API Routes (ADR-0731, ADR-0733)
Specification-as-Loss-Landscape endpoints for console dashboard
"""

from fastapi import APIRouter, HTTPException, Query
import json
from pathlib import Path
from typing import Optional
import csv
from io import StringIO
from starlette.responses import StreamingResponse

router = APIRouter(prefix="/v1/console/quality", tags=["quality"])


@router.get("/task/{task_id}")
async def get_quality_metrics(task_id: str):
    """
    GET /v1/console/quality/task/{task_id}

    Returns quality metrics for a task: quality score, DoD/Hallucin breakdown,
    convergence history, spec version, constraints, audit trail.
    """
    # Load from convergence_history stored by iteration_loop_integration.py
    # In production, this would query a database; for Phase 3 MVP, load from JSON files

    try:
        corvin_home = Path.home() / ".corvin"
        task_dir = corvin_home / "quality_metrics" / task_id

        if not task_dir.exists():
            raise HTTPException(status_code=404, detail=f"No metrics for task {task_id}")

        # Load convergence history
        history_file = task_dir / "convergence_history.json"
        convergence_history = []
        if history_file.exists():
            with open(history_file) as f:
                convergence_history = json.load(f)

        # Load spec
        spec_file = task_dir / "spec.json"
        spec_constraints = []
        spec_version = 1
        if spec_file.exists():
            with open(spec_file) as f:
                spec_data = json.load(f)
                spec_constraints = spec_data.get("constraints", [])
                spec_version = spec_data.get("version", 1)

        # Compute final scores
        if convergence_history:
            final = convergence_history[-1]
            quality_score = final.get("quality_score", 0.0)
            dod_score = final.get("dod_score", 0.0)
            hallucin_score = final.get("hallucin_score", 0.0)
            iteration = final.get("iteration", len(convergence_history))
        else:
            quality_score = dod_score = hallucin_score = 0.0
            iteration = 0

        return {
            "task_id": task_id,
            "task_type": "code_generation",  # TODO: load from metadata
            "task_size": "medium",  # TODO: load from metadata
            "status": "converged" if quality_score >= 0.85 else "in_progress",
            "quality_score": quality_score,
            "dod_score": dod_score,
            "hallucin_score": hallucin_score,
            "iteration": iteration,
            "spec_version": spec_version,
            "convergence_history": convergence_history,
            "spec_constraints": spec_constraints,
            "audit_events": [],  # TODO: load from audit trail
            "last_updated": convergence_history[-1].get("timestamp", "")
            if convergence_history
            else "",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metrics/export")
async def export_metrics(
    task_id: str = Query(...),
    format: str = Query("csv", regex="^(csv|json)$"),
    include_audit: bool = Query(True),
    include_spec_history: bool = Query(True),
):
    """
    POST /v1/console/quality/metrics/export

    Exports quality metrics as CSV or JSON with optional audit trail and spec history.
    """

    # Fetch metrics
    metrics_response = await get_quality_metrics(task_id)

    if format == "json":
        return {
            "status": "success",
            "data": metrics_response,
            "filename": f"quality_metrics_{task_id}.json",
        }

    # CSV export
    output = StringIO()
    writer = csv.writer(output)

    # Headers
    headers = [
        "iteration",
        "quality_score",
        "dod_score",
        "hallucin_score",
        "loss",
        "timestamp",
    ]
    if include_spec_history:
        headers.extend(["spec_version", "num_constraints"])

    writer.writerow(headers)

    # Data rows
    for point in metrics_response["convergence_history"]:
        row = [
            point.get("iteration", ""),
            f"{point.get('quality_score', 0):.4f}",
            f"{point.get('dod_score', 0):.4f}",
            f"{point.get('hallucin_score', 0):.4f}",
            f"{point.get('loss', 0):.4f}",
            point.get("timestamp", ""),
        ]
        if include_spec_history:
            row.extend([
                metrics_response.get("spec_version", 1),
                len(metrics_response.get("spec_constraints", [])),
            ])
        writer.writerow(row)

    csv_content = output.getvalue()

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=quality_metrics_{task_id}.csv"},
    )
