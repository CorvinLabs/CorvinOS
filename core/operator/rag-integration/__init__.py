"""RAG Integration — Layer 43 Multi-Provider RAG Registry.

Implementation of ADR-0089: Manifest-based RAG provider registry
enabling customers to register external knowledge bases (Elasticsearch,
Vector DBs, Google Drive, Custom APIs) without code changes.

Phase 1 (Jun 2026): Manifest Spec & Validation
Phase 2 (Jul 2026): Registry + CLI
Phase 3 (Aug 2026): Query API & Orchestrator
Phase 4 (Sep 2026): Console UI
Phase 5 (Sep 2026): Compliance Gates
Phase 6 (Oct 2026): Example Providers
Phase 7 (Nov 2026): RAG Hub
"""

__version__ = "0.1.0"
__phase__ = "Phase 1 — Manifest Spec & Validation"

# The package-relative export only resolves when this directory is imported as a
# real package. The shipped directory name is hyphenated ("rag-integration"),
# which is not a valid Python package name, so an import as ``rag_integration``
# never happens at runtime — modules are reached via sys.path injection instead
# (see tests/conftest.py and the console _operator_bootstrap). Guard the export
# so test collection (which imports this __init__) does not abort on the
# unresolvable relative import.
try:
    from .validator.manifest_validator import (  # noqa: F401
        ManifestValidator,
        ValidationResult,
        ComplianceValidator,
    )

    __all__ = [
        "ManifestValidator",
        "ValidationResult",
        "ComplianceValidator",
    ]
except ImportError:
    __all__ = []
