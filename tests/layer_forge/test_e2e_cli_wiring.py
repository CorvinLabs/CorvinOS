"""E2E wiring proof for Layer Forge through its CLI (ADR-2222 D5/D6).

Drives scripts/layer_forge_cli.py as a REAL SUBPROCESS against a sandboxed
CORVIN_HOME, then checks the side effects independently of the CLI's stdout:
the registry file on disk and the tenant's hash-chained audit log (verified
with ``forge.security_events.verify_chain``).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI = REPO_ROOT / "scripts" / "layer_forge_cli.py"
TENANT = "_default"


@pytest.fixture
def home(tmp_path):
    return tmp_path / "corvin_home"


# `create` runs the REVIEW phase, which calls the Anthropic API — the one external boundary of
# Layer Forge. In a subprocess an in-process monkeypatch does not apply, and a test-only
# environment switch in the product code would be a back door. So the child starts through this
# wrapper: it replaces only that boundary, then runs the REAL CLI file via runpy. Without it these
# four tests passed on a machine with an API key and failed on one without
# ("Could not resolve authentication method"), which read as a product failure.
_CLI_WRAPPER = """
import runpy, sys
from core.orchestration.layer_forge import orchestrator
from core.orchestration.layer_forge.review import ReviewVerdict
orchestrator.review_layer_definition = lambda manifest, enforcement, **_k: ReviewVerdict("PASS", flags=[])
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def _run_cli(home: Path, *args):
    env = dict(os.environ, CORVIN_HOME=str(home), FORGE_ROOT=str(home / "forge_root"),
               PYTHONPATH=os.pathsep.join(filter(None, [str(CLI.parents[1]), os.environ.get("PYTHONPATH")])))
    env.pop("CORVIN_TENANT_ID", None)
    return subprocess.run([sys.executable, "-c", _CLI_WRAPPER, str(CLI), "--tenant", TENANT, *args],
                          capture_output=True, text=True, timeout=120, env=env)


def _manifest(tmp_path: Path, manifest: dict) -> str:
    p = tmp_path / f"{manifest.get('id', 'm')}-{manifest.get('version', 'v')}.json"
    p.write_text(json.dumps(manifest))
    return str(p)


def _chain(home: Path) -> Path:
    return home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"


def _lf_events(home: Path) -> list[dict]:
    chain = _chain(home)
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in recs if str(r.get("event_type", r.get("event", ""))).startswith("layer_forge.")]


def _etype(r: dict) -> str:
    return r.get("event_type") or r.get("event")


def _details(r: dict) -> dict:
    return r.get("details") or {}


def _verify_chain(home: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT / "corvin_operator" / "forge"))
    from forge.security_events import verify_chain

    ok, problems = verify_chain(_chain(home))
    assert ok, problems


def _registry_file(home: Path, key: str) -> Path:
    return home / "tenants" / TENANT / "global" / "layer_forge" / "registry" / f"{key}.json"


def test_create_lands_in_registry_and_hash_chain(tmp_path, home):
    m = {
        "id": "e2e.test-rule", "version": "0.1.0",
        "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
        "host_awareness": {"source_tree": {"paths": ["scripts/layer_forge_cli.py"]},
                           "cross_check": "none"},
    }
    result = _run_cli(home, "create", _manifest(tmp_path, m), "--skip-gates")

    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == "SUCCESS" and out["registry_key"] == "e2e.test-rule@0.1.0"
    assert json.loads(_registry_file(home, "e2e.test-rule@0.1.0").read_text())["status"] == "accepted"

    events = _lf_events(home)
    types = [_etype(e) for e in events]
    # The pipeline records far more than ADR-2222's first three events by now: the three boot-time
    # enforcement rules, the REVIEW verdict and the canary assignment all land before the definition
    # is proposed. Pin the ORDER of the decisions (audit-first: each precedes the state change it
    # justifies), not an exact list that every new phase silently invalidates.
    expected_order = ["layer_forge.enforcement_evaluated", "layer_forge.review_evaluated",
                      "layer_forge.canary_rollout_assigned", "layer_forge.definition_proposed",
                      "layer_forge.definition_transitioned"]
    positions = [types.index(t) for t in expected_order]
    assert positions == sorted(positions), types
    assert types.count("layer_forge.enforcement_evaluated") == 3        # schema + boundaries + host awareness
    assert types.count("layer_forge.definition_proposed") == 1 and types.count("layer_forge.definition_transitioned") == 1
    assert types[-1] == "layer_forge.definition_transitioned"
    proposed = _details(events[types.index("layer_forge.definition_proposed")])
    assert proposed["entry_id"] == "e2e.test-rule"
    assert proposed["gates_skipped"] is True
    assert proposed["actor"] == "cli"
    assert proposed["tenant_id"] == TENANT
    transitioned = _details(events[types.index("layer_forge.definition_transitioned")])
    assert transitioned["from_status"] == "proposed"
    assert transitioned["to_status"] == "accepted"
    assert all(e.get("hash") and "prev_hash" in e for e in events)
    _verify_chain(home)


def test_real_quality_gate_runs_and_is_audited(tmp_path, home):
    m = {"id": "e2e.gated", "version": "0.1.0", "targets": [{"layer_id": "L34"}],
         "quality_gates": [{"gate_id": "schema-suite", "test_path": "tests/layer_forge/test_schema.py"}]}
    result = _run_cli(home, "create", _manifest(tmp_path, m))

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["gate_verdicts"][0]["status"] == "PASS"
    gate = [e for e in _lf_events(home) if _etype(e) == "layer_forge.quality_gate_evaluated"]
    assert [(_details(e)["gate_id"], _details(e)["status"]) for e in gate] == [("schema-suite", "PASS")]
    proposed = [e for e in _lf_events(home) if _etype(e) == "layer_forge.definition_proposed"]
    assert _details(proposed[0])["gates_skipped"] is False


def test_failing_gate_is_rejected_without_registry_entry(tmp_path, home):
    m = {"id": "e2e.ghost-gate", "version": "0.1.0", "targets": [{"layer_id": "L34"}],
         "quality_gates": [{"gate_id": "ghost", "test_path": "tests/layer_forge/does_not_exist.py"}]}
    result = _run_cli(home, "create", _manifest(tmp_path, m))

    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["status"] == "FAILED" and out["phase"] == "test"
    assert not _registry_file(home, "e2e.ghost-gate@0.1.0").exists()
    rejected = [e for e in _lf_events(home) if _etype(e) == "layer_forge.definition_rejected"]
    assert len(rejected) == 1
    assert _details(rejected[0])["phase"] == "test"
    assert _details(rejected[0])["failing_gates"] == ["ghost"]
    assert not [e for e in _lf_events(home) if _etype(e) == "layer_forge.definition_proposed"]
    _verify_chain(home)


def test_path_traversal_gate_is_refused_at_validation(tmp_path, home):
    m = {"id": "e2e.escape", "version": "0.1.0", "targets": [{"layer_id": "L34"}],
         "quality_gates": [{"gate_id": "evil", "test_path": "tests/../../../etc/passwd"}]}
    result = _run_cli(home, "create", _manifest(tmp_path, m))

    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["phase"] == "validate"
    assert not [e for e in _lf_events(home) if _etype(e) == "layer_forge.quality_gate_evaluated"]


def test_invalid_manifest_rejected_and_invalid_id_never_reaches_the_chain(tmp_path, home):
    bad = {"id": "bad id with spaces", "version": "nope", "targets": [{"layer_id": "L34"}]}
    result = _run_cli(home, "create", _manifest(tmp_path, bad))

    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["status"] == "FAILED" and "validation failed" in out["error"]
    registry_dir = home / "tenants" / TENANT / "global" / "layer_forge" / "registry"
    assert not registry_dir.exists() or not list(registry_dir.glob("*.json"))
    rejected = [e for e in _lf_events(home) if _etype(e) == "layer_forge.definition_rejected"]
    assert len(rejected) == 1
    d = _details(rejected[0])
    assert d["error_class"] == "LayerSchemaValidationError"
    assert "entry_id" not in d
    assert "bad id with spaces" not in _chain(home).read_text()


def test_duplicate_version_refused(tmp_path, home):
    path = _manifest(tmp_path, {"id": "e2e.dup", "version": "0.1.0", "targets": [{"layer_id": "L34"}]})
    assert _run_cli(home, "create", path, "--skip-gates").returncode == 0
    second = _run_cli(home, "create", path, "--skip-gates")
    assert second.returncode == 1
    assert "already exists" in json.loads(second.stdout)["error"]


def test_promote_get_list_through_cli_are_audited(tmp_path, home):
    path = _manifest(tmp_path, {"id": "e2e.life", "version": "0.1.0", "targets": [{"layer_id": "L34"}]})
    assert _run_cli(home, "create", path, "--skip-gates").returncode == 0

    promote = _run_cli(home, "promote", "e2e.life", "0.1.0", "deployed")
    assert promote.returncode == 0, promote.stdout + promote.stderr
    assert json.loads(promote.stdout)["status"] == "deployed"

    illegal = _run_cli(home, "promote", "e2e.life", "0.1.0", "proposed")
    assert illegal.returncode == 1

    got = _run_cli(home, "get", "e2e.life")
    assert json.loads(got.stdout)["status"] == "deployed"
    listed = _run_cli(home, "list")
    assert [e["id"] for e in json.loads(listed.stdout)] == ["e2e.life"]

    transitions = [(_details(e)["from_status"], _details(e)["to_status"])
                   for e in _lf_events(home) if _etype(e) == "layer_forge.definition_transitioned"]
    assert transitions == [("proposed", "accepted"), ("accepted", "deployed")]
    _verify_chain(home)


def test_get_with_traversal_id_is_not_found(home):
    result = _run_cli(home, "get", "../../etc/passwd")
    assert result.returncode == 1
    assert "not found" in json.loads(result.stdout)["error"]
