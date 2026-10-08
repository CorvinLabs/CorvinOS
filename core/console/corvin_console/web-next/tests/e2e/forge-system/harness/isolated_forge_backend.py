"""Isolated console/gateway backend for the Forge-system Playwright suite.

Started by ``playwright.forge-system.config.ts`` as its ``webServer``. It runs
the REAL ``corvin_gateway.app`` (same app the live ``corvin-webui.service``
serves) in-process, against a throwaway ``CORVIN_HOME`` — the live install on
:8765 and its audit chain are never touched (CLAUDE.md "Tests never touch the
live install").

Exactly two boundaries are replaced, both external to the forges under test:

* ``capability_api.active_tier`` → ``"member"``: ``Registry.create`` runs the
  real licence gate, which denies the free tier. Same patch as
  ``tests/forge_bundle/conftest.py::_forge_licence_tier``.
* Layer Forge's REVIEW phase calls the Anthropic API. It is replaced by a stub
  whose verdict and latency the specs steer through
  ``<home>/e2e-control/layer_review.json`` — ``{"verdict": "PASS"|"FLAGGED"|"ERROR",
  "delay_s": float}`` — so a slow or failing reviewer can be exercised
  deliberately. Every other gate (schema, pytest quality gates, enforcement,
  audit) runs for real.

Seeding writes each forge's store the way the forge itself does (the same
helpers the Python E2E fixtures use). The scenario is a fictional freight
forwarder, "Nordwind Logistik", whose automation spans all four forges.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[8]
HOME = Path(os.environ.get("FORGE_E2E_HOME", "/tmp/corvin-forge-e2e")).resolve()
PORT = int(os.environ.get("FORGE_E2E_PORT", "8799"))
TENANT = "_default"
MARKER = ".forge-e2e-home"  # only a directory carrying this marker is ever wiped


def _reset_home() -> None:
    if HOME.exists():
        if not (HOME / MARKER).exists():
            sys.exit(f"refusing to wipe {HOME}: it is not a forge-e2e home (no {MARKER})")
        shutil.rmtree(HOME)
    th = HOME / "tenants" / TENANT
    for sub in ("global/layer_forge/registry", "global/layer_forge/locks", "global/forge"):
        (th / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("skills_gen", "skills_packages", "skills_installed", "xdg", "e2e-control", "plugin-packages"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    (HOME / MARKER).write_text("throwaway CORVIN_HOME for the forge-system Playwright suite\n")
    # Throwaway installs must not count as instances: opt out of the ping /
    # heartbeat and healing traces (both default-ON, opt-out). Mode 0600 — the
    # gateway reads this file fail-closed on mode (ADR-0759).
    yml = th / "global" / "tenant.corvin.yaml"
    yml.write_text("spec:\n  telemetry:\n    ping_enabled: false\n    healing_traces: false\n")
    os.chmod(yml, 0o600)
    (HOME / "e2e-control" / "layer_review.json").write_text(json.dumps({"verdict": "PASS", "delay_s": 0}))


def _sandbox_env() -> None:
    # No FORGE_ROOT / VOICE_AUDIT_PATH: with CORVIN_HOME sandboxed the resolver's
    # own chain (<home>/tenants/_default/global/forge/audit.jsonl) is already
    # isolated, and the boot tripwire refuses any redirect to a second chain.
    # CORVIN_TASK_ID / CORVIN_CHANNEL_ID leak in from a bridge session that
    # starts the suite and would point the task/session tool scopes elsewhere.
    for k in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TASK_ID", "CORVIN_CHANNEL_ID"):
        os.environ.pop(k, None)
    os.environ.update({
        "CORVIN_HOME": str(HOME),
        "CORVIN_TENANT_ID": TENANT,
        "XDG_CONFIG_HOME": str(HOME / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(HOME / "audit-anchor.key"),  # absolute, or the writer refuses
        "CORVIN_FORCE_SCOPE": "user",
        "CORVIN_TELEMETRY_OPTIN": "false",
        # The console's KB projector defaults to the sibling Corvin-Knowledge
        # checkout and heals + COMMITS there every 2 s. A throwaway install
        # must never write the real knowledge base: point it at nothing.
        "CORVIN_KB_REPO": str(HOME / "no-knowledge-base"),
    })
    # Tool Forge's PROJECT scope finds its root by walking up from the process
    # cwd to a git checkout — CORVIN_HOME does not isolate it. Started from the
    # repo, this backend listed (and an accepted import could have written)
    # the live checkout's <repo>/.corvin/forge/tools. The throwaway home is no
    # git checkout, so the project scope stays inside it.
    os.chdir(HOME)
    for p in ("core/plugins", "corvin_operator/bridges/shared", "corvin_operator/skill-forge",
              "corvin_operator/forge", "core/compliance", "core/license", "core/gateway", "core/console", ""):
        sys.path.insert(0, str(REPO / p) if p else str(REPO))


def _patch_external_boundaries() -> None:
    from corvin_operator.license import capability_api

    capability_api.active_tier = lambda **_k: "member"

    from core.orchestration.layer_forge import orchestrator
    from core.orchestration.layer_forge.review import ReviewFlag, ReviewVerdict

    control = HOME / "e2e-control" / "layer_review.json"

    # Mirrors the REAL reviewer's contract: it never raises — an unreachable
    # model is a ReviewVerdict("ERROR"), and flags are ReviewFlag members.
    def _review(manifest, enforcement, **_k):
        try:
            cfg = json.loads(control.read_text())
        except (OSError, ValueError):
            cfg = {}
        time.sleep(float(cfg.get("delay_s", 0)))
        verdict = cfg.get("verdict", "PASS")
        if verdict == "ERROR":
            return ReviewVerdict("ERROR", reason="e2e: reviewer unavailable")
        if verdict == "FLAGGED":
            return ReviewVerdict("FLAGGED", flags=[ReviewFlag.SECURITY_GAP], reason="e2e: flagged")
        return ReviewVerdict("PASS", flags=[])

    orchestrator.review_layer_definition = _review


# ── Seed: Nordwind Logistik ─────────────────────────────────────────────────

SKILLS = {
    # skill_id: (version, body) — a pre-release and a build-metadata version on purpose
    "invoice-extract": ("1.4.2", "def run(text):\n    return {'lines': [l for l in text.splitlines() if l.strip()]}\n"),
    "customs-hs-classifier": ("2.0.0-rc.1", "def run(text):\n    return {'hs_code': '8471.30', 'confidence': 0.82}\n"),
    "eta-narrator": ("0.9.0+build.17", "def run(text):\n    return f'ETA: {text[:40]}'\n"),
}

TOOLS = {
    # name: (runtime, impl, meta)
    "nordwind.route_cost": ("python", (
        "import json, sys\n"
        "def run(req):\n"
        "    legs = req.get('legs', [])\n"
        "    return {'eur': round(sum(l['km'] * l.get('eur_per_km', 1.35) for l in legs), 2)}\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps(run(json.load(sys.stdin))))\n"
    ), {"deterministic": True, "budget": {"cpu_seconds": 5, "wall_seconds": 10}}),
    "nordwind.csv_merge": ("python", (
        "import csv, io\n"
        "def run(req):\n"
        "    rows = []\n"
        "    for blob in req.get('csv', []):\n"
        "        rows.extend(csv.DictReader(io.StringIO(blob)))\n"
        "    return {'rows': len(rows)}\n"
    ), {"requirements": ["python-dateutil==2.9.0"], "deterministic": True}),
    "nordwind.manifest_lint": ("bash", (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "jq -e '.containers | length > 0' >/dev/null && echo ok\n"
    ), {"secrets": ["NORDWIND_TMS_TOKEN"]}),
}

LAYERS = [
    # Two versions of the same layer: the export must carry exactly the ticked one.
    {"id": "nordwind.dataflow-guard", "version": "1.0.0",
     "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
     "quality_gates": [], "enforcement_rules": []},
    {"id": "nordwind.dataflow-guard", "version": "1.1.0",
     "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"},
                 {"layer_id": "L35", "layer_name": "Network Egress Lockdown"}],
     "quality_gates": [], "enforcement_rules": []},
    {"id": "nordwind.audit-sink", "version": "3.2.1",
     "targets": [{"layer_id": "L16", "layer_name": "Security hardening"}],
     "quality_gates": [], "enforcement_rules": []},
]

PLUGINS = [("nordwind-tms-bridge", "0.7.3"), ("nordwind-eta-panel", "1.0.0")]


def _seed_skills() -> None:
    for skill_id, (version, body) in SKILLS.items():
        folder = HOME / "skills_gen" / skill_id
        for sub in ("src", "hooks", "tests", "scripts", "docs", "references", ".forge"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / "src" / "skill.py").write_text(body)
        (folder / "README.md").write_text(f"# {skill_id}\n\nNordwind Logistik automation skill.\n")
        (folder / "skill.json").write_text(json.dumps({
            "skill_id": skill_id, "version": version, "boot_layer": "installed",
            "entry_point": "src.skill:run",
        }))
        (folder / ".forge" / "generation_context.json").write_text(json.dumps({
            "generated_at": "2026-10-08T00:00:00Z", "generated_by": "forge-e2e-harness",
            "skill_id": skill_id, "version": version,
        }))


def _seed_tools() -> None:
    from forge.multi_registry import MultiRegistry

    reg = MultiRegistry(tenant_id=TENANT)
    for name, (runtime, impl, meta) in TOOLS.items():
        reg.create(scope="user", name=name, description=f"Nordwind {name.split('.')[-1]}",
                   input_schema={"type": "object"}, impl=impl, runtime=runtime, meta=meta)


def _seed_layers() -> None:
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    from core.orchestration.layer_forge.registry import LayerRegistry

    registry = LayerRegistry(layer_forge_home(TENANT) / "registry")
    for manifest in LAYERS:
        registry.create(dict(manifest))


def _seed_plugin_packages() -> None:
    """ADR-0511 plugin packages on disk — Plugin Forge's export input (CLI only)."""
    for plugin_id, version in PLUGINS:
        pkg = HOME / "plugin-packages" / f"{plugin_id}-{version}.zip"
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "name": plugin_id, "version": version, "author": "Nordwind Logistik IT",
                "description": f"{plugin_id} for the e2e suite"}))
            zf.writestr("src/plugin.py", "def setup(ctx):\n    return {'ready': True}\n")
            zf.writestr("README.md", f"# {plugin_id}\n")


def main() -> None:
    _reset_home()
    _sandbox_env()
    _patch_external_boundaries()
    # FORGE_E2E_SEED=0 starts an EMPTY install — the receiving side of a share.
    if os.environ.get("FORGE_E2E_SEED", "1") != "0":
        _seed_skills()
        _seed_tools()
        _seed_layers()
        _seed_plugin_packages()
    (HOME / "e2e-control" / "seed.json").write_text(json.dumps({
        "skills": {k: v[0] for k, v in SKILLS.items()},
        "tools": sorted(TOOLS),
        "layers": [f"{m['id']}@{m['version']}" for m in LAYERS],
        "plugins": [f"{p}@{v}" for p, v in PLUGINS],
        "repo": str(REPO),
        "seeded": os.environ.get("FORGE_E2E_SEED", "1") != "0",
    }, indent=2))

    import uvicorn
    from corvin_gateway.app import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
