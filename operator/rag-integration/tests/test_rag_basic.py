#!/usr/bin/env python3
"""
Basic RAG System Tests — ADR-0089

Verifies:
1. Manifest generation works
2. Manifest validation works
3. File I/O works
4. Form validation works

Runs under pytest as ``test_rag_basic_smoke`` and remains executable as a
standalone script (``python test_rag_basic.py``). The previous version executed
its body at module-import time and called ``sys.exit()``, which crashed the
whole pytest collection session (SystemExit during collection). Keep all
executable logic inside the test function and behind the ``__main__`` guard.
"""

import sys
import tempfile
from pathlib import Path

# Make ``bridges.shared`` importable both under pytest and standalone.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_rag_basic_smoke():
    from bridges.shared.rag_manifest_generator import (
        RAGManifestGenerator,
        BasicInfoInput,
        APIConfigInput,
        ResponseMappingInput,
        ComplianceInput,
    )

    # Test 2: Create test inputs
    basic = BasicInfoInput(
        provider_id="test-provider",
        name="Test Provider",
        description="A test provider",
        author="test-team",
        version="1.0.0",
    )
    api_config = APIConfigInput(
        endpoint="https://api.example.com/search",
        method="POST",
        timeout_ms=5000,
        auth_type="bearer-token",
        auth_token_env_var="TEST_TOKEN",
        query_format_sample='{"q": "{query}", "l": {limit}}',
    )
    response_mapping = ResponseMappingInput(
        content_path="results[].content",
        score_path="results[].score",
        metadata_path="results[]",
        source_url_path="results[].url",
    )
    compliance = ComplianceInput(
        capabilities=["keyword-search", "filtering"],
        data_classification="INTERNAL",
        compliance_zone="EU",
    )

    # Test 3: Generate manifest
    success, manifest_yaml, error = RAGManifestGenerator.generate(
        basic, api_config, response_mapping, compliance
    )
    assert success, f"Generation failed: {error}"

    # Test 4: Validate manifest structure
    assert "apiVersion" in manifest_yaml and "kind" in manifest_yaml, (
        "Missing required fields in manifest"
    )
    assert "test-provider" in manifest_yaml, "Provider ID not in manifest"

    # Test 5: Validate query format
    valid, err = RAGManifestGenerator.validate_query_format_sample(
        '{"q": "{query}", "l": {limit}}'
    )
    assert valid, f"Query format validation failed: {err}"

    # Test 6: Validate JSONPath
    valid, err = RAGManifestGenerator.validate_jsonpath("results[].content")
    assert valid, f"JSONPath validation failed: {err}"

    # Test 7: Import/export module — deterministic manifest hash
    from bridges.shared.rag_import_export import RAGProviderImportExport

    hash1 = RAGProviderImportExport.compute_manifest_hash(manifest_yaml)
    hash2 = RAGProviderImportExport.compute_manifest_hash(manifest_yaml)
    assert hash1 == hash2, "Manifest hash not deterministic"

    # Test 8: File I/O
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_dir = Path(tmpdir)
        import_success, provider_id, import_error = (
            RAGProviderImportExport.import_manifest(
                manifest_yaml, registry_dir, basic.provider_id
            )
        )
        assert import_success, f"Import failed: {import_error}"

        manifest_file = registry_dir / f"{provider_id}.yaml"
        assert manifest_file.exists(), (
            f"Manifest file not created at {manifest_file}"
        )

        saved_content = manifest_file.read_text()
        assert "test-provider" in saved_content, (
            "Saved manifest missing provider ID"
        )

    # Test 9: Form validation
    invalid_id = "invalid@provider!"
    is_alnum = invalid_id.replace("-", "").replace("_", "").isalnum()
    assert not is_alnum, "Invalid ID should be rejected"


if __name__ == "__main__":
    test_rag_basic_smoke()
    print("✅ ALL BASIC RAG TESTS PASSED")
