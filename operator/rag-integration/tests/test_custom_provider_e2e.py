"""E2E Test: User creates custom provider via web form end-to-end.

Simulates:
1. User fills form (4 steps)
2. Backend validates and creates provider
3. Manifest is saved locally
4. CLI can verify provider works

This test runs OFFLINE (no real API) using mocked responses.
"""
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# Fix Python path to load operator.bridges modules
# Path from test: operator/rag-integration/tests/test_*.py → CorvinOS root
repo_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(repo_root))
# Also add operator directly
sys.path.insert(0, str(repo_root / "operator"))


# ── Mock Form Inputs ────────────────────────────────────────

FORM_INPUT = {
    "provider_id": "test-search-engine",
    "name": "Test Search Engine",
    "description": "A test RAG provider for E2E testing",
    "author": "test-team",
    "version": "1.0.0",
    "endpoint": "https://api.example.com/search",
    "method": "POST",
    "timeout_ms": 5000,
    "auth_type": "bearer-token",
    "auth_token_env_var": "TEST_API_TOKEN",
    "query_format_sample": '{"search": "{query}", "max_results": {limit}}',
    "content_path": "results[].body",
    "score_path": "results[].relevance",
    "metadata_path": "results[]",
    "source_url_path": "results[].url",
    "capabilities": ["keyword-search", "filtering-by-metadata"],
    "data_classification": "INTERNAL",
    "compliance_zone": "EU",
}


# ── Test: Manifest Generation ──────────────────────────────

def test_manifest_generation():
    """Test that manifest generator creates valid YAML."""
    from shared.rag_manifest_generator import (
        RAGManifestGenerator,
        BasicInfoInput,
        APIConfigInput,
        ResponseMappingInput,
        ComplianceInput,
    )

    basic = BasicInfoInput(
        provider_id=FORM_INPUT["provider_id"],
        name=FORM_INPUT["name"],
        description=FORM_INPUT["description"],
        author=FORM_INPUT["author"],
        version=FORM_INPUT["version"],
    )

    api_config = APIConfigInput(
        endpoint=FORM_INPUT["endpoint"],
        method=FORM_INPUT["method"],
        timeout_ms=FORM_INPUT["timeout_ms"],
        auth_type=FORM_INPUT["auth_type"],
        auth_token_env_var=FORM_INPUT["auth_token_env_var"],
        query_format_sample=FORM_INPUT["query_format_sample"],
    )

    response_mapping = ResponseMappingInput(
        content_path=FORM_INPUT["content_path"],
        score_path=FORM_INPUT["score_path"],
        metadata_path=FORM_INPUT["metadata_path"],
        source_url_path=FORM_INPUT["source_url_path"],
    )

    compliance = ComplianceInput(
        capabilities=FORM_INPUT["capabilities"],
        data_classification=FORM_INPUT["data_classification"],
        compliance_zone=FORM_INPUT["compliance_zone"],
    )

    # Generate
    success, manifest_yaml, error = RAGManifestGenerator.generate(
        basic, api_config, response_mapping, compliance
    )

    # Verify
    assert success, f"Generation failed: {error}"
    assert manifest_yaml, "Manifest should not be empty"
    assert "apiVersion: rag.corvin.io/v1alpha1" in manifest_yaml
    assert "kind: RAGProvider" in manifest_yaml
    assert FORM_INPUT["provider_id"] in manifest_yaml

    # Parse YAML to verify structure
    manifest = yaml.safe_load(manifest_yaml)
    assert manifest["metadata"]["name"] == FORM_INPUT["provider_id"]
    assert manifest["spec"]["retrieval"]["endpoint"] == FORM_INPUT["endpoint"]
    assert manifest["spec"]["response_format"]["content_path"] == FORM_INPUT["content_path"]

    print(f"✅ Manifest generation test passed")
    return manifest_yaml


# ── Test: Import/Export ────────────────────────────────────

def test_import_manifest():
    """Test that generated manifest can be imported locally."""
    from shared.rag_import_export import RAGProviderImportExport

    # Generate manifest first
    manifest_yaml = test_manifest_generation()

    # Import to temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_dir = Path(tmpdir)

        success, provider_id, error = RAGProviderImportExport.import_manifest(
            manifest_yaml,
            registry_dir,
            FORM_INPUT["provider_id"],
        )

        assert success, f"Import failed: {error}"
        assert provider_id == FORM_INPUT["provider_id"]

        # Verify file was created
        manifest_file = registry_dir / f"{provider_id}.yaml"
        assert manifest_file.exists(), f"Manifest file not created: {manifest_file}"

        # Verify content
        with open(manifest_file) as f:
            saved_content = f.read()
            assert FORM_INPUT["provider_id"] in saved_content

    print(f"✅ Import test passed")


# ── Test: Manifest Validation ──────────────────────────────

def test_manifest_validation():
    """Test that generated manifest passes validation."""
    from shared.rag_import_export import RAGProviderImportExport

    manifest_yaml = test_manifest_generation()

    valid, error = RAGProviderImportExport.validate_manifest(manifest_yaml)

    assert valid, f"Manifest validation failed: {error}"

    print(f"✅ Validation test passed")


# ── Test: Form Validation ──────────────────────────────────

def test_form_validation():
    """Test that form validation works correctly."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rag_manifest_generator",
        Path(__file__).parent.parent.parent.parent / "operator" / "bridges" / "shared" / "rag_manifest_generator.py"
    )
    rag_gen_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rag_gen_module)
    RAGManifestGenerator = rag_gen_module.RAGManifestGenerator

    # Test valid query format
    valid, error = RAGManifestGenerator.validate_query_format_sample('{"q": "{query}", "n": {limit}}')
    assert valid, f"Valid format rejected: {error}"

    # Test invalid query format (missing {query})
    valid, error = RAGManifestGenerator.validate_query_format_sample('{"n": {limit}}')
    assert not valid, "Invalid format accepted"

    # Test JSONPath validation
    for path in ["results[].content", "data.items[]", "body"]:
        valid, error = RAGManifestGenerator.validate_jsonpath(path)
        assert valid, f"Valid JSONPath rejected: {path}"

    print(f"✅ Form validation test passed")


# ── Test: Hash Computation ─────────────────────────────────

def test_manifest_hash():
    """Test that manifest hashing works."""
    from shared.rag_import_export import RAGProviderImportExport

    manifest_yaml = test_manifest_generation()

    hash1 = RAGProviderImportExport.compute_manifest_hash(manifest_yaml)
    hash2 = RAGProviderImportExport.compute_manifest_hash(manifest_yaml)

    # Same manifest = same hash
    assert hash1 == hash2, "Hash not deterministic"

    # Different manifest = different hash
    modified = manifest_yaml.replace("1.0.0", "2.0.0")
    hash3 = RAGProviderImportExport.compute_manifest_hash(modified)
    assert hash1 != hash3, "Hash not sensitive to changes"

    # Verify hash format (SHA256 = 64 hex chars)
    assert len(hash1) == 64, f"Hash should be 64 chars, got {len(hash1)}"
    assert all(c in "0123456789abcdef" for c in hash1), "Hash should be hex"

    print(f"✅ Hash test passed")


# ── Test: Complete E2E Flow ────────────────────────────────

def test_complete_e2e_flow():
    """Integration test: form → generation → validation → import → CLI verify"""
    from shared.rag_manifest_generator import (
        RAGManifestGenerator,
        BasicInfoInput,
        APIConfigInput,
        ResponseMappingInput,
        ComplianceInput,
    )
    from shared.rag_import_export import RAGProviderImportExport

    print("\n🔄 E2E FLOW: User creates custom provider\n")

    # Step 1: Build form inputs
    print("Step 1: User fills form (4 steps)...")
    basic = BasicInfoInput(
        provider_id=FORM_INPUT["provider_id"],
        name=FORM_INPUT["name"],
        description=FORM_INPUT["description"],
        author=FORM_INPUT["author"],
        version=FORM_INPUT["version"],
    )
    api_config = APIConfigInput(
        endpoint=FORM_INPUT["endpoint"],
        method=FORM_INPUT["method"],
        timeout_ms=FORM_INPUT["timeout_ms"],
        auth_type=FORM_INPUT["auth_type"],
        auth_token_env_var=FORM_INPUT["auth_token_env_var"],
        query_format_sample=FORM_INPUT["query_format_sample"],
    )
    response_mapping = ResponseMappingInput(
        content_path=FORM_INPUT["content_path"],
        score_path=FORM_INPUT["score_path"],
        metadata_path=FORM_INPUT["metadata_path"],
        source_url_path=FORM_INPUT["source_url_path"],
    )
    compliance = ComplianceInput(
        capabilities=FORM_INPUT["capabilities"],
        data_classification=FORM_INPUT["data_classification"],
        compliance_zone=FORM_INPUT["compliance_zone"],
    )
    print("  ✅ Form input validated\n")

    # Step 2: Generate manifest
    print("Step 2: Backend generates manifest...")
    success, manifest_yaml, error = RAGManifestGenerator.generate(
        basic, api_config, response_mapping, compliance
    )
    assert success, f"Failed to generate: {error}"
    print("  ✅ Manifest generated (YAML)\n")

    # Step 3: Validate manifest
    print("Step 3: Validate manifest structure...")
    valid, error = RAGProviderImportExport.validate_manifest(manifest_yaml)
    assert valid, f"Manifest invalid: {error}"
    print("  ✅ Manifest is valid\n")

    # Step 4: Register locally
    print("Step 4: Register provider locally...")
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_dir = Path(tmpdir)
        import_success, provider_id, import_error = RAGProviderImportExport.import_manifest(
            manifest_yaml,
            registry_dir,
            FORM_INPUT["provider_id"],
        )
        assert import_success, f"Import failed: {import_error}"
        print(f"  ✅ Provider registered: {provider_id}\n")

        # Step 5: Verify with "CLI"
        print("Step 5: Verify (like CLI would)...")
        manifest_file = registry_dir / f"{provider_id}.yaml"
        assert manifest_file.exists()
        with open(manifest_file) as f:
            loaded = yaml.safe_load(f)
        assert loaded["metadata"]["name"] == provider_id
        print(f"  ✅ Verified: {manifest_file}\n")

    print("🎉 E2E FLOW COMPLETE\n")


# ── Run All Tests ──────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("CUSTOM PROVIDER E2E TEST SUITE")
    print("=" * 60 + "\n")

    try:
        test_form_validation()
        test_manifest_generation()
        test_manifest_validation()
        test_manifest_hash()
        test_import_manifest()
        test_complete_e2e_flow()

        print("\n" + "=" * 60)
        print("✅ ALL E2E TESTS PASSED")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        raise
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise
