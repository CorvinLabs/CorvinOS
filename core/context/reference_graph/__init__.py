"""Context-as-Reference-Graph (ADR-0563, ADR-0564)."""

from .types import ContextReference, ContextDigest, ContextBuildError, ValidateResult
from .builder import CRGBuilder
from .validation import validate_reference, build_digest
from .audit import emit_digest_validated, emit_reference_resolved, emit_reference_hash_mismatch, emit_event
from .dag import ReferenceGraph, CycleError, DanglingDependencyError, DagValidation, validate_reference_dag
from .loader import (
    ReferenceLoader,
    ResolvedReference,
    LoaderStats,
    ReferenceNotInDigestError,
    ReferenceHashMismatchError,
    ReferenceUnavailableError,
)

__all__ = [
    'ContextReference',
    'ContextDigest',
    'ContextBuildError',
    'ValidateResult',
    'CRGBuilder',
    'validate_reference',
    'build_digest',
    'emit_digest_validated',
    'emit_reference_resolved',
    'emit_reference_hash_mismatch',
    'emit_event',
    'ReferenceGraph',
    'CycleError',
    'DanglingDependencyError',
    'DagValidation',
    'validate_reference_dag',
    'ReferenceLoader',
    'ResolvedReference',
    'LoaderStats',
    'ReferenceNotInDigestError',
    'ReferenceHashMismatchError',
    'ReferenceUnavailableError',
]
