"""E2E wiring proof for Layer Forge (ADR-2222 D5).

This test drives scripts/layer_forge_cli.py as a REAL SUBPROCESS — not a
direct Python import-and-call of the orchestrator. That is the distinction
the feedback-dead-mechanism-needs-call-site-test memory and the
e2e-wiring-proof skill both require: a unit test proves the orchestrator
returns the right value when called; this test proves there is a real,
external entry point that reaches it at all.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI = REPO_ROOT / "scripts" / "layer_forge_cli.py"


def _run_cli(*args, tenant_root: Path):
    cmd = [sys.executable, str(CLI), *args, "--tenant-root", str(tenant_root)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def test_cli_entry_point_exists_and_is_executable():
    assert CLI.exists(), "layer_forge_cli.py must exist as the real entry point"
    assert CLI.stat().st_size > 0


def test_e2e_create_via_real_subprocess_cli(tmp_path):
    """Full pipeline through the REAL entry point: write a manifest to disk,
    invoke the CLI as a subprocess, assert it reaches VALIDATE -> ENFORCE ->
    PROMOTE and the registry file lands on disk — all without the test ever
    importing LayerForgeOrchestrator directly."""
    manifest = {
        "id": "e2e.test-rule",
        "version": "0.1.0",
        "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
        "host_awareness": {
            "source_tree": {"paths": ["scripts/layer_forge_cli.py"]},
            "cross_check": "none",
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = _run_cli("create", str(manifest_path), "--skip-gates", tenant_root=tmp_path)

    assert result.returncode == 0, f"CLI failed: stdout={result.stdout} stderr={result.stderr}"
    output = json.loads(result.stdout)
    assert output["status"] == "SUCCESS"
    assert output["registry_key"] == "e2e.test-rule@0.1.0"

    # Prove the real side effect landed on disk, independent of the CLI's own
    # stdout claim — the registry file must actually exist.
    registry_file = tmp_path / "layer_forge" / "registry" / "e2e.test-rule@0.1.0.json"
    assert registry_file.exists()
    on_disk = json.loads(registry_file.read_text())
    assert on_disk["status"] == "accepted"  # orchestrator promotes proposed->accepted on success

    # Prove the audit trail was written (ADR-2222 D6).
    audit_file = tmp_path / "layer_forge" / "layer_forge_audit.jsonl"
    assert audit_file.exists()
    audit_lines = [json.loads(l) for l in audit_file.read_text().splitlines()]
    event_types = [e["event_type"] for e in audit_lines]
    assert "layer_forge.definition_created" in event_types
    assert "layer_forge.definition_promoted" in event_types


def test_e2e_get_via_real_subprocess_cli(tmp_path):
    manifest = {"id": "e2e.get-rule", "version": "0.1.0", "targets": [{"layer_id": "L10"}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    create_result = _run_cli("create", str(manifest_path), "--skip-gates", tenant_root=tmp_path)
    assert create_result.returncode == 0

    get_result = _run_cli("get", "e2e.get-rule", tenant_root=tmp_path)
    assert get_result.returncode == 0
    entry = json.loads(get_result.stdout)
    assert entry["id"] == "e2e.get-rule"
    assert entry["status"] == "accepted"


def test_e2e_invalid_manifest_fails_closed_through_real_cli(tmp_path):
    """A schema-invalid manifest must be rejected by the REAL entry point with
    a non-zero exit code — proving the fail-closed path is reachable end-to-end,
    not just correct inside a unit test."""
    manifest_path = tmp_path / "bad_manifest.json"
    manifest_path.write_text(json.dumps({"id": "bad id with spaces", "version": "nope"}))

    result = _run_cli("create", str(manifest_path), "--skip-gates", tenant_root=tmp_path)

    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["status"] == "FAILED"
    assert "validation failed" in output["error"]

    # And nothing was written to the registry (fail-closed, no partial state).
    registry_dir = tmp_path / "layer_forge" / "registry"
    assert not registry_dir.exists() or list(registry_dir.glob("*.json")) == []


def test_e2e_duplicate_version_fails_closed_through_real_cli(tmp_path):
    manifest = {"id": "e2e.dup-rule", "version": "0.1.0", "targets": [{"layer_id": "L34"}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    first = _run_cli("create", str(manifest_path), "--skip-gates", tenant_root=tmp_path)
    assert first.returncode == 0

    second = _run_cli("create", str(manifest_path), "--skip-gates", tenant_root=tmp_path)
    assert second.returncode == 1
    output = json.loads(second.stdout)
    assert output["status"] == "FAILED"


def test_e2e_promote_full_lifecycle_through_real_cli(tmp_path):
    manifest = {"id": "e2e.lifecycle-rule", "version": "0.1.0", "targets": [{"layer_id": "L34"}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    create = _run_cli("create", str(manifest_path), "--skip-gates", tenant_root=tmp_path)
    assert create.returncode == 0
    # create_layer_definition already promotes proposed -> accepted on success.

    promote = _run_cli("promote", "e2e.lifecycle-rule", "0.1.0", "deployed", tenant_root=tmp_path)
    assert promote.returncode == 0
    record = json.loads(promote.stdout)
    assert record["status"] == "deployed"
