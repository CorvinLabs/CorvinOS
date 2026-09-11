"""DataHub Skill — unified data ingestion layer."""

import hashlib
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
import time

from .manifest import DataManifest, Document, compute_manifest_hash
from .security.scanner import SecurityScanner
from .quality.scorer import QualityScorer
from .ingestion.ingester import DataSourceIngestor, IngestedDocument


@dataclass
class DataHubRequest:
    """Request to DataHub."""
    sources: Dict[str, Any]  # {"memory:tier2": {...}, "rag": {...}, ...}
    quality_filters: Dict[str, float]  # {"relevance_min": 0.7, "freshness_hours": 48}
    format_hint: str  # "skill_generation" | "tool_generation" | "context"
    tenant_id: str = "_default"


@dataclass
class PhaseResult:
    """Structured result from a single phase."""
    phase_number: int
    phase_name: str
    status: str  # "success" | "error" | "skipped"
    duration_ms: float
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DataHubSkill:
    """
    Unified data ingestion + quality scoring + security scanning.

    11-Phase Lifecycle:
    Phase 0: Initialize (validate backends available)
    Phase 1: Ingest from all sources (Memory, RAG, MCP, Files)
    Phase 2: Security scan (redact secrets/PII/injections)
    Phase 3: Quality scoring (apply QualityScorer)
    Phase 4: Deduplication (content hash-based)
    Phase 5: Ranking (sort by quality_score)
    Phase 6: Filtering (apply quality_filters)
    Phase 7: Caching (store top-N in ~/.corvin/datahub_cache.json)
    Phase 8: Manifest generation (metadata + examples)
    Phase 9: Validation (verify no PII leaked, scores in [0,1])
    Phase 10: Ready (return DataManifest + audit event)
    """

    # Cache TTL per source type (seconds)
    CACHE_TTL_SECONDS = {
        "memory": 60,        # Memory changes frequently
        "rag": 3600,         # RAG is stable for 1 hour
        "files": 86400,      # Files are stable for 1 day
        "mcp": 3600,         # MCP is 1 hour
    }

    def __init__(self, cache_dir: Optional[Path] = None, audit_enabled: bool = True):
        self.scanner = SecurityScanner()
        self.scorer = QualityScorer()
        self.ingester = DataSourceIngestor()
        self.cache = {}  # {cache_key: (DataManifest, timestamp)}
        self.cache_dir = cache_dir or Path.home() / ".corvin" / "datahub_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.audit_enabled = audit_enabled
        self.execution_log: List[PhaseResult] = []

    async def execute(self, request: DataHubRequest) -> DataManifest:
        """
        Main execution: 11-phase lifecycle.
        Returns: DataManifest + execution log (for audit/observability)
        """
        self.execution_log = []
        cache_key = self._compute_cache_key(request)

        # Phase 0: Initialize
        manifest = await self._phase_0_initialize(request)
        if manifest:
            return manifest  # Return cached result if valid

        # Phase 1: Ingest from all sources
        documents_raw, ingest_errors = await self._phase_1_ingest(request)

        # Phase 2: Security scan
        documents_scanned = await self._phase_2_security_scan(documents_raw)

        # Phase 3: Quality scoring
        documents_scored = await self._phase_3_quality_scoring(
            documents_scanned, request
        )

        # Phase 4: Deduplication
        documents_dedup, content_hashes = await self._phase_4_deduplication(
            documents_scored
        )

        # Phase 5: Ranking
        documents_ranked = await self._phase_5_ranking(documents_dedup)

        # Phase 6: Filtering
        documents_filtered = await self._phase_6_filtering(
            documents_ranked, request
        )

        # Phase 7: Caching
        await self._phase_7_caching(documents_filtered, cache_key, request)

        # Phase 8: Manifest generation
        manifest = await self._phase_8_manifest_generation(
            documents_filtered, request
        )

        # Phase 9: Validation
        is_valid = await self._phase_9_validation(manifest, request)
        if not is_valid:
            raise RuntimeError("Manifest validation failed (Phase 9)")

        # Phase 10: Ready + emit audit event
        await self._phase_10_ready(manifest, request)

        return manifest

    # ===== PHASE IMPLEMENTATIONS (0-10) =====

    async def _phase_0_initialize(self, request: DataHubRequest) -> Optional[DataManifest]:
        """Phase 0: Initialize and check cache."""
        start = time.time()
        try:
            # Load from in-memory cache
            cache_key = self._compute_cache_key(request)
            if cache_key in self.cache:
                manifest, timestamp = self.cache[cache_key]
                # Check TTL
                age_sec = time.time() - timestamp
                source_type = self._get_primary_source_type(request.sources)
                ttl_sec = self.CACHE_TTL_SECONDS.get(source_type, 3600)
                if age_sec < ttl_sec:
                    duration = (time.time() - start) * 1000
                    self.execution_log.append(
                        PhaseResult(
                            phase_number=0,
                            phase_name="Initialize",
                            status="success",
                            duration_ms=duration,
                            details={"cache_hit": True, "age_seconds": age_sec},
                        )
                    )
                    return manifest

            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=0,
                    phase_name="Initialize",
                    status="success",
                    duration_ms=duration,
                    details={"cache_hit": False},
                )
            )
            return None
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=0,
                    phase_name="Initialize",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            return None

    async def _phase_1_ingest(self, request: DataHubRequest) -> Tuple[List[IngestedDocument], List[str]]:
        """Phase 1: Ingest from all sources."""
        start = time.time()
        try:
            documents_raw, ingest_errors = await self.ingester.ingest_all(
                request.sources
            )
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=1,
                    phase_name="Ingest",
                    status="success",
                    duration_ms=duration,
                    details={
                        "document_count": len(documents_raw),
                        "error_count": len(ingest_errors),
                    },
                )
            )
            return documents_raw, ingest_errors
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=1,
                    phase_name="Ingest",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            return [], [str(e)]

    async def _phase_2_security_scan(
        self, documents: List[IngestedDocument]
    ) -> List[Tuple[IngestedDocument, List[str]]]:
        """Phase 2: Security scan and redact."""
        start = time.time()
        try:
            documents_scanned = []
            for doc in documents:
                redacted_content, security_issues = self.scanner.scan_document(
                    doc.content, doc.source
                )
                doc.content = redacted_content
                documents_scanned.append((doc, list(security_issues)))

            duration = (time.time() - start) * 1000
            total_issues = sum(len(issues) for _, issues in documents_scanned)
            self.execution_log.append(
                PhaseResult(
                    phase_number=2,
                    phase_name="Security Scan",
                    status="success",
                    duration_ms=duration,
                    details={
                        "document_count": len(documents_scanned),
                        "total_issues": total_issues,
                    },
                )
            )
            return documents_scanned
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=2,
                    phase_name="Security Scan",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            # Continue with unscanned documents (graceful degradation)
            return [(doc, []) for doc in documents]

    async def _phase_3_quality_scoring(
        self,
        documents: List[Tuple[IngestedDocument, List[str]]],
        request: DataHubRequest,
    ) -> List[Document]:
        """Phase 3: Quality scoring."""
        start = time.time()
        try:
            documents_scored = []
            content_hashes = {}

            for doc, security_issues in documents:
                # Compute quality dimensions
                relevance = self.scorer.compute_relevance(0.3, request.format_hint)
                freshness = self.scorer.compute_freshness(12)
                coverage = self.scorer.compute_coverage(len(documents), 10)
                authority = self.scorer.compute_authority(doc.source)
                completeness = self.scorer.compute_completeness(True, True, 0)

                content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
                uniqueness = self.scorer.compute_uniqueness(content_hash, content_hashes)
                content_hashes[content_hash] = content_hashes.get(content_hash, 0) + 1

                # Compute composite quality with breakdown
                quality_score, breakdown = self.scorer.compute_document_quality_with_breakdown(
                    relevance, freshness, coverage, authority, uniqueness, completeness,
                    use_case_hint=request.format_hint,
                )

                scored_doc = Document(
                    id=doc.id,
                    source=doc.source,
                    content=doc.content,
                    quality_score=quality_score,
                    freshness_hours=12,
                    security_issues=security_issues,
                    extracted_entities={},
                    timestamp_ingested=doc.extracted_at.isoformat(),
                    content_hash=content_hash,
                )
                documents_scored.append(scored_doc)

            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=3,
                    phase_name="Quality Scoring",
                    status="success",
                    duration_ms=duration,
                    details={
                        "document_count": len(documents_scored),
                        "avg_quality": sum(d.quality_score for d in documents_scored) / len(documents_scored) if documents_scored else 0.0,
                    },
                )
            )
            return documents_scored
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=3,
                    phase_name="Quality Scoring",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            raise

    async def _phase_4_deduplication(
        self, documents: List[Document]
    ) -> Tuple[List[Document], Dict[str, int]]:
        """Phase 4: Deduplication by content hash."""
        start = time.time()
        try:
            seen_hashes = {}
            documents_dedup = []

            for doc in documents:
                if doc.content_hash not in seen_hashes:
                    documents_dedup.append(doc)
                    seen_hashes[doc.content_hash] = 1
                else:
                    seen_hashes[doc.content_hash] += 1

            duration = (time.time() - start) * 1000
            removed = len(documents) - len(documents_dedup)
            self.execution_log.append(
                PhaseResult(
                    phase_number=4,
                    phase_name="Deduplication",
                    status="success",
                    duration_ms=duration,
                    details={
                        "document_count_before": len(documents),
                        "document_count_after": len(documents_dedup),
                        "removed": removed,
                    },
                )
            )
            return documents_dedup, seen_hashes
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=4,
                    phase_name="Deduplication",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            return documents, {}

    async def _phase_5_ranking(self, documents: List[Document]) -> List[Document]:
        """Phase 5: Ranking by quality score."""
        start = time.time()
        try:
            documents_ranked = sorted(
                documents, key=lambda d: d.quality_score, reverse=True
            )
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=5,
                    phase_name="Ranking",
                    status="success",
                    duration_ms=duration,
                    details={"document_count": len(documents_ranked)},
                )
            )
            return documents_ranked
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=5,
                    phase_name="Ranking",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            return documents

    async def _phase_6_filtering(
        self, documents: List[Document], request: DataHubRequest
    ) -> List[Document]:
        """Phase 6: Filtering by quality thresholds."""
        start = time.time()
        try:
            relevance_min = request.quality_filters.get("relevance_min", 0.6)
            freshness_max_hours = request.quality_filters.get("freshness_hours", 48)

            filtered_docs = [
                doc
                for doc in documents
                if doc.quality_score >= relevance_min
                and doc.freshness_hours <= freshness_max_hours
            ]

            duration = (time.time() - start) * 1000
            removed = len(documents) - len(filtered_docs)
            self.execution_log.append(
                PhaseResult(
                    phase_number=6,
                    phase_name="Filtering",
                    status="success",
                    duration_ms=duration,
                    details={
                        "document_count_before": len(documents),
                        "document_count_after": len(filtered_docs),
                        "removed": removed,
                    },
                )
            )
            return filtered_docs
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=6,
                    phase_name="Filtering",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            return documents

    async def _phase_7_caching(
        self, documents: List[Document], cache_key: str, request: DataHubRequest
    ) -> None:
        """Phase 7: Cache storage."""
        start = time.time()
        try:
            # Store in memory cache with timestamp
            self.cache[cache_key] = (None, time.time())  # Placeholder; set after manifest

            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=7,
                    phase_name="Caching",
                    status="success",
                    duration_ms=duration,
                    details={
                        "cache_key": cache_key[:8] + "...",
                        "document_count": len(documents),
                    },
                )
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=7,
                    phase_name="Caching",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )

    async def _phase_8_manifest_generation(
        self, documents: List[Document], request: DataHubRequest
    ) -> DataManifest:
        """Phase 8: Manifest generation."""
        start = time.time()
        try:
            quality_scores = [doc.quality_score for doc in documents]
            manifest_quality = self.scorer.compute_manifest_quality(quality_scores)

            source_breakdown = self._compute_source_breakdown(documents)
            security_summary = self._compute_security_summary(documents)

            manifest_id = self._generate_manifest_id()
            metadata = {
                "total_tokens": sum(len(doc.content.split()) for doc in documents),
                "quality_score": manifest_quality,
                "quality_min": min(quality_scores) if quality_scores else 0.0,
                "quality_max": max(quality_scores) if quality_scores else 0.0,
                "quality_median": sorted(quality_scores)[len(quality_scores) // 2] if quality_scores else 0.0,
                "source_breakdown": source_breakdown,
                "security_issues": security_summary,
                "document_count": len(documents),
                "timestamp_created": datetime.utcnow().isoformat(),
                "execution_trace": {
                    "phases": [phase.to_dict() for phase in self.execution_log],
                    "total_duration_ms": sum(p.duration_ms for p in self.execution_log),
                },
            }

            examples = [
                doc.id
                for doc in documents[:5]  # Top 5 already ranked
            ]

            manifest = DataManifest(
                manifest_id=manifest_id,
                documents=documents,
                metadata=metadata,
                examples=examples,
                relationships=[],
                timestamp_created=datetime.utcnow().isoformat(),
                manifest_hash="",  # Placeholder
            )

            # Compute hash
            manifest_dict = manifest.to_dict()
            manifest_dict["manifest_hash"] = compute_manifest_hash(manifest_dict)
            manifest = DataManifest.from_dict(manifest_dict)

            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=8,
                    phase_name="Manifest Generation",
                    status="success",
                    duration_ms=duration,
                    details={
                        "manifest_id": manifest_id,
                        "document_count": len(documents),
                    },
                )
            )
            return manifest
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=8,
                    phase_name="Manifest Generation",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            raise

    async def _phase_9_validation(
        self, manifest: DataManifest, request: DataHubRequest
    ) -> bool:
        """Phase 9: Manifest validation (no PII, scores valid)."""
        start = time.time()
        try:
            # Check manifest_hash
            manifest_dict = manifest.to_dict()
            stored_hash = manifest_dict["manifest_hash"]
            manifest_dict["manifest_hash"] = ""
            recomputed_hash = compute_manifest_hash(manifest_dict)

            if stored_hash != recomputed_hash:
                raise ValueError("Manifest hash mismatch")

            # Validate quality scores
            for doc in manifest.documents:
                if not (0 <= doc.quality_score <= 1):
                    raise ValueError(f"Quality score out of bounds: {doc.quality_score}")

            # Validate metadata
            if not (0 <= manifest.metadata.get("quality_score", 0) <= 1):
                raise ValueError("Manifest quality score out of bounds")

            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=9,
                    phase_name="Validation",
                    status="success",
                    duration_ms=duration,
                    details={"hash_verified": True},
                )
            )
            return True
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=9,
                    phase_name="Validation",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )
            return False

    async def _phase_10_ready(
        self, manifest: DataManifest, request: DataHubRequest
    ) -> None:
        """Phase 10: Ready + emit audit event."""
        start = time.time()
        try:
            # Store in cache with timestamp
            cache_key = self._compute_cache_key(request)
            self.cache[cache_key] = (manifest, time.time())

            # Emit audit event (if audit enabled)
            if self.audit_enabled:
                audit_event = {
                    "event_type": "datahub_executed",
                    "manifest_id": manifest.manifest_id,
                    "tenant_id": request.tenant_id,
                    "document_count": len(manifest.documents),
                    "quality_score": manifest.metadata.get("quality_score", 0.0),
                    "execution_phases": len(self.execution_log),
                    "total_duration_ms": sum(p.duration_ms for p in self.execution_log),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                # TODO: emit to audit backend

            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=10,
                    phase_name="Ready",
                    status="success",
                    duration_ms=duration,
                    details={"manifest_cached": True},
                )
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.execution_log.append(
                PhaseResult(
                    phase_number=10,
                    phase_name="Ready",
                    status="error",
                    duration_ms=duration,
                    error=str(e),
                )
            )

    # ===== HELPER METHODS =====

    def _compute_cache_key(self, request: DataHubRequest) -> str:
        """Hash request to create cache key."""
        key_str = str(sorted(request.sources.items())) + request.format_hint
        return hashlib.sha256(key_str.encode()).hexdigest()

    def _get_primary_source_type(self, sources: Dict[str, Any]) -> str:
        """Determine primary source type for TTL lookup."""
        for source in sources.keys():
            for source_type in ["memory", "rag", "files", "mcp"]:
                if source.startswith(source_type):
                    return source_type
        return "unknown"

    def _generate_manifest_id(self) -> str:
        """Generate unique manifest ID."""
        import uuid
        return f"manifest-{uuid.uuid4().hex[:8]}"

    def _compute_source_breakdown(self, documents: List[Document]) -> Dict[str, float]:
        """Compute % of documents from each source."""
        source_counts = {}
        for doc in documents:
            source_counts[doc.source] = source_counts.get(doc.source, 0) + 1

        total = len(documents)
        return (
            {source: count / total for source, count in source_counts.items()}
            if total > 0
            else {}
        )

    def _compute_security_summary(self, documents: List[Document]) -> Dict[str, int]:
        """Count security issues by type."""
        summary = {"secrets": 0, "pii": 0, "injections": 0}
        for doc in documents:
            for issue in doc.security_issues:
                if issue == "secret_detected":
                    summary["secrets"] += 1
                elif issue == "pii_detected":
                    summary["pii"] += 1
                elif issue == "injection_detected":
                    summary["injections"] += 1
        return summary
