"""ST2 E2E: per-call run workspace (AWP-inspired).

Validates that every tool call leaves a complete, reproducible trace under
``.claude/forge/runs/<ts>_<id>/``: manifest, completion, RUN_SUMMARY.md,
stdout.json, and an artifacts/ directory the tool can actually write into.

Fictional task: forge a tool that synthesizes a sales CSV (deterministic
from a seed) directly into _artifacts_dir, then verify *everything* the
runner wrote to disk.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp import MCPClient


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


SYNTH_IMPL = r'''#!/usr/bin/env python3
"""Synthesize a sales CSV directly into _artifacts_dir."""
import csv, json, os, random, sys
p = json.loads(sys.stdin.read())
adir = p["_artifacts_dir"]
os.makedirs(adir, exist_ok=True)
out = os.path.join(adir, "sales.csv")
rng = random.Random(p["seed"])
groups = ["alpha", "beta", "gamma"]
total = 0.0
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["id", "group", "amount"])
    for i in range(int(p["n_rows"])):
        amt = round(rng.uniform(0, 1000), 2)
        total += amt
        w.writerow([i, rng.choice(groups), amt])
print(json.dumps({
    "summary": f"synthesized {p['n_rows']} rows, total={total:.2f}",
    "rows": int(p["n_rows"]),
    "total": round(total, 2),
}))
'''

SYNTH_SCHEMA = {
    "type": "object",
    "required": ["n_rows", "seed"],
    "properties": {
        "n_rows": {"type": "integer"},
        "seed":   {"type": "integer"},
    },
}


def test_run_workspace_layout():
    print("\n[run workspace layout]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        forge_root = td / "forge"
        client = MCPClient(forge_root)
        try:
            client.initialize()
            client.request("tools/call", {
                "name": "forge_tool",
                "arguments": {
                    "name": "synth_sales",
                    "description": "synthesize a sales CSV deterministically",
                    "input_schema": SYNTH_SCHEMA,
                    "impl": SYNTH_IMPL,
                },
            })
            resp = client.request("tools/call", {
                "name": "synth_sales",
                "arguments": {"n_rows": 250, "seed": 42},
            })
        finally:
            client.close()

        result = resp["result"]
        t("call ok", result.get("isError") is False)
        struct = result.get("structuredContent", {})
        run_id = struct.get("run_id")
        t("response has run_id", isinstance(run_id, str) and len(run_id) > 0,
          detail=f"run_id={run_id}")
        artifacts = struct.get("artifacts", [])
        t("response has 1 artifact (sales.csv)",
          len(artifacts) == 1 and artifacts[0]["rel"] == "sales.csv",
          detail=f"got {[a.get('rel') for a in artifacts]}")

        run_dir = forge_root / "runs" / run_id
        t("run dir exists", run_dir.is_dir())

        # 1. manifest written before exec
        mpath = run_dir / "run_manifest.json"
        t("run_manifest.json present", mpath.exists())
        manifest = json.loads(mpath.read_text())
        t("manifest.run_id matches", manifest.get("run_id") == run_id)
        t("manifest.tool = synth_sales", manifest.get("tool") == "synth_sales")
        t("manifest.tool_sha set", bool(manifest.get("tool_sha")))
        t("manifest.input_sha set",
          isinstance(manifest.get("input_sha"), str) and
          len(manifest["input_sha"]) == 16)
        t("manifest.input contains user payload",
          manifest["input"].get("n_rows") == 250 and
          manifest["input"].get("seed") == 42)
        t("manifest.input includes injected _artifacts_dir",
          "_artifacts_dir" in manifest["input"])

        # 2. completion written after exec
        cpath = run_dir / "run_completion.json"
        t("run_completion.json present", cpath.exists())
        completion = json.loads(cpath.read_text())
        t("completion.status = ok", completion.get("status") == "ok")
        t("completion.exit_code = 0", completion.get("exit_code") == 0)
        t("completion.duration_s > 0",
          isinstance(completion.get("duration_s"), (int, float)) and
          completion["duration_s"] > 0)
        t("completion.sandbox in {bwrap, rlimits}",
          completion.get("sandbox") in ("bwrap", "rlimits"))
        cart = completion.get("artifacts", [])
        t("completion has 1 artifact", len(cart) == 1)
        t("artifact.bytes > 0", cart[0].get("bytes", 0) > 0)
        t("artifact.kind = csv", cart[0].get("kind") == "csv")

        # 3. stdout.json — now wrapped in the AWP envelope by the runner
        spath = run_dir / "stdout.json"
        t("stdout.json present", spath.exists())
        envelope = json.loads(spath.read_text())
        t("stdout envelope has ok=true", envelope.get("ok") is True)
        t("stdout envelope has status=200", envelope.get("status") == 200)
        t("stdout envelope error is null", envelope.get("error") is None)
        data = envelope.get("data") or {}
        t("envelope.data.summary mentions 'rows'",
          "rows" in str(data.get("summary", "")).lower())
        t("envelope.data.rows == 250", data.get("rows") == 250)

        # 4. RUN_SUMMARY.md (≤ 1 KB-ish, human-readable)
        rspath = run_dir / "RUN_SUMMARY.md"
        t("RUN_SUMMARY.md present", rspath.exists())
        body = rspath.read_text()
        t("RUN_SUMMARY mentions tool name", "synth_sales" in body)
        t("RUN_SUMMARY mentions sandbox",
          "bwrap" in body or "rlimits" in body)
        t("RUN_SUMMARY size ≤ 2KB",
          len(body.encode()) <= 2048,
          detail=f"size={len(body.encode())}B")

        # 5. artifact really exists on disk and is a real CSV
        adir = run_dir / "artifacts"
        sales_csv = adir / "sales.csv"
        t("sales.csv exists in artifacts/", sales_csv.exists())
        with sales_csv.open() as fh:
            rows = list(csv.DictReader(fh))
        t("CSV has 250 rows", len(rows) == 250)
        t("CSV columns are id,group,amount",
          set(rows[0].keys()) == {"id", "group", "amount"})


def test_two_calls_get_distinct_run_ids():
    print("\n[two calls in a row → distinct run_ids]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        forge_root = td / "forge"
        client = MCPClient(forge_root)
        try:
            client.initialize()
            client.request("tools/call", {
                "name": "forge_tool",
                "arguments": {
                    "name": "synth_sales",
                    "description": "synth",
                    "input_schema": SYNTH_SCHEMA,
                    "impl": SYNTH_IMPL,
                },
            })
            r1 = client.request("tools/call", {
                "name": "synth_sales",
                "arguments": {"n_rows": 5, "seed": 1},
            })
            r2 = client.request("tools/call", {
                "name": "synth_sales",
                "arguments": {"n_rows": 5, "seed": 2},
            })
        finally:
            client.close()

        id1 = r1["result"]["structuredContent"]["run_id"]
        id2 = r2["result"]["structuredContent"]["run_id"]
        t("two distinct run_ids", id1 != id2,
          detail=f"id1={id1} id2={id2}")
        runs_dir = forge_root / "runs"
        t("two run folders on disk",
          sum(1 for p in runs_dir.iterdir() if p.is_dir()) >= 2)


def test_failed_run_still_persists_manifest():
    print("\n[failed run persists manifest + completion]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        forge_root = td / "forge"
        client = MCPClient(forge_root)
        try:
            client.initialize()
            client.request("tools/call", {
                "name": "forge_tool",
                "arguments": {
                    "name": "boom",
                    "description": "always fails",
                    "input_schema": {"type": "object", "properties": {}},
                    "impl": "#!/usr/bin/env python3\nimport sys\nsys.exit(7)\n",
                },
            })
            resp = client.request("tools/call",
                                  {"name": "boom", "arguments": {}})
        finally:
            client.close()

        t("boom isError=True", resp["result"].get("isError") is True)
        runs = list((forge_root / "runs").iterdir())
        t("a run folder was still created despite failure",
          len(runs) == 1)
        run_dir = runs[0]
        t("manifest still present", (run_dir / "run_manifest.json").exists())
        t("completion present with status=error",
          (run_dir / "run_completion.json").exists() and
          json.loads((run_dir / "run_completion.json").read_text()).get(
              "status") == "error")


def main() -> int:
    test_run_workspace_layout()
    test_two_calls_get_distinct_run_ids()
    test_failed_run_still_persists_manifest()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
