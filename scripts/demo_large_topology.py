"""Demo runner for the 25-node large-topology flow.

Creates a realistic FlowRun manifest under
.corvin/tenants/_default/global/flows/runs/
so the Console FlowGraphView can render it.

Uses the exact same event format as flow_definition.FlowRunManifest
and flow_runner.FlowRunner so the console API parses it correctly.
"""
from __future__ import annotations
import json, os, time, random, hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORVIN_HOME = REPO / ".corvin"
RUNS_DIR = CORVIN_HOME / "tenants" / "_default" / "global" / "flows" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

FLOW_ID = "large-topology-25node"
RUN_ID = f"fr_{int(time.time() * 1000)}"
RUN_FILE = RUNS_DIR / f"{RUN_ID}.manifest.jsonl"

# Step definitions: (step_id, node, depends_on)
STEPS = [
    # Phase 1: Ingestion (local)
    ("ingest_raw",       "local",            []),
    ("ingest_meta",      "local",            []),
    ("ingest_schema",    "local",            []),
    # Phase 2: Preprocessing (4 remote nodes)
    ("preprocess_eu",    "hetzner-eu",       ["ingest_raw"]),
    ("preprocess_b",     "hetzner-b",        ["ingest_raw"]),
    ("preprocess_edge_f","edge-falkenstein", ["ingest_meta"]),
    ("preprocess_edge_h","edge-helsinki",    ["ingest_meta"]),
    ("validate_schema",  "local",            ["ingest_schema"]),
    # Phase 3: Enrichment
    ("enrich_eu",        "hetzner-eu",       ["preprocess_eu",    "validate_schema"]),
    ("enrich_b",         "hetzner-b",        ["preprocess_b",     "validate_schema"]),
    ("enrich_edge_f",    "edge-falkenstein", ["preprocess_edge_f"]),
    ("enrich_edge_h",    "edge-helsinki",    ["preprocess_edge_h"]),
    # Phase 4: Aggregation (local fan-in)
    ("aggregate_eu",     "local",            ["enrich_eu", "enrich_edge_f"]),
    ("aggregate_b",      "local",            ["enrich_b",  "enrich_edge_h"]),
    # Phase 5: Cross-shard analysis
    ("analyze_eu",       "hetzner-eu",       ["aggregate_eu"]),
    ("analyze_b",        "hetzner-b",        ["aggregate_b"]),
    ("analyze_cross",    "local",            ["aggregate_eu", "aggregate_b"]),
    ("analyze_edge_f",   "edge-falkenstein", ["aggregate_eu"]),
    ("analyze_edge_h",   "edge-helsinki",    ["aggregate_b"]),
    # Phase 6: Synthesis (all 5 node types fan-in to local)
    ("synthesize",       "local",
     ["analyze_eu", "analyze_b", "analyze_cross", "analyze_edge_f", "analyze_edge_h"]),
    # Phase 7: Distributed verification
    ("verify_eu",        "hetzner-eu",       ["synthesize"]),
    ("verify_b",         "hetzner-b",        ["synthesize"]),
    # Phase 8: Finalization
    ("merge_verified",   "local",            ["verify_eu", "verify_b"]),
    ("generate_report",  "local",            ["merge_verified"]),
    ("publish",          "local",            ["generate_report", "synthesize"]),
]

NODE_TYPES = {
    "local":            "local",
    "hetzner-eu":       "a2a",
    "hetzner-b":        "a2a",
    "edge-falkenstein": "a2a",
    "edge-helsinki":    "a2a",
}

def _sha256_prefix(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]

def append_event(event_type: str, **fields) -> None:
    entry = {"type": event_type, "ts": time.time(), **fields}
    with open(RUN_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

now = time.time()
BASE = now - 240
steps_done = 0

# Budget state
budget_state = {
    "compute_units_used": 0,
    "tokens_used": 0,
    "wall_time_s": 0.0,
    "steps_done": 0,
}

# --- mesh_flow.run_started ---
append_event(
    "mesh_flow.run_started",
    run_id=RUN_ID,
    flow_id=FLOW_ID,
    flow_version="1.0.0",
    budget_allocated={
        "max_compute_units": 100,
        "max_tokens": 2_000_000,
        "max_wall_time_s": 600,
        "max_steps": 30,
    },
)

# Simulate step execution
finish: dict[str, float] = {}
for step_id, node, deps in STEPS:
    start = BASE if not deps else max(finish.get(d, BASE) for d in deps) + 0.5
    duration = random.uniform(8, 35) if node != "local" else random.uniform(2, 12)
    end = start + duration

    task_text = f"Processing step {step_id} on {node}"
    output_text = f"Result from {step_id}: analysis complete with 99.7% confidence"
    tokens = len(task_text) + len(output_text)

    budget_state["compute_units_used"] += 1
    budget_state["tokens_used"] += tokens
    budget_state["steps_done"] += 1
    budget_state["wall_time_s"] = round(end - BASE, 1)

    append_event(
        "mesh_flow.step_dispatched",
        run_id=RUN_ID,
        step_id=step_id,
        target_node=node,
        node_type=NODE_TYPES[node],
        budget_before=dict(budget_state),
    )
    append_event(
        "mesh_flow.step_completed",
        run_id=RUN_ID,
        step_id=step_id,
        target_node=node,
        node_type=NODE_TYPES[node],
        tokens_used=tokens,
        output_sha256_prefix=_sha256_prefix(output_text),
        elapsed_ms=int(duration * 1000),
        budget_after=dict(budget_state),
    )
    append_event(
        "mesh_flow.budget_checkpoint",
        run_id=RUN_ID,
        step_id=step_id,
        **budget_state,
    )

    finish[step_id] = end
    print(f"  ✓ {step_id:25s} [{node:20s}] {duration:.1f}s")

total_wall = max(finish.values()) - BASE
budget_state["wall_time_s"] = round(total_wall, 1)

# --- mesh_flow.run_completed ---
append_event(
    "mesh_flow.run_completed",
    run_id=RUN_ID,
    **budget_state,
    status="success",
)

# mode 0600
os.chmod(RUN_FILE, 0o600)

print(f"\nRun ID:   {RUN_ID}")
print(f"File:     {RUN_FILE}")
print(f"Steps:    {len(STEPS)}")
print(f"Duration: {total_wall:.1f}s (simulated)")
print(f"\n→ Console: /console/app/flows → click '{RUN_ID}'")
