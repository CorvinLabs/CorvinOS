"""DataHub Skill — unified data ingestion layer."""

import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from .manifest import DataManifest, Document, compute_manifest_hash
from .security.scanner import SecurityScanner
from .quality.scorer import QualityScorer
from .ingestion.ingester import DataSourceIngestor


@dataclass
class DataHubRequest:
    """Request to DataHub."""
    sources: Dict[str, Any]  # {"memory:tier2": {...}, "rag": {...}, ...}
    quality_filters: Dict[str, float]  # {"relevance_min": 0.7, "freshness_hours": 48}
    format_hint: str  # "skill_generation" | "tool_generation" | "context"


class DataHubSkill:
    """Unified data ingestion + quality scoring + security scanning."""

    def __init__(self):
        self.scanner = SecurityScanner()
        self.scorer = QualityScorer()
        self.ingester = DataSourceIngestor()
        self.cache = {}  # Simple in-memory cache (source key → DataManifest)

    async def execute(self, request: DataHubRequest) -> DataManifest:
        """
        Main execution: ingest → scan → score → filter → output DataManifest.
        """
        # Check cache
        cache_key = self._compute_cache_key(request)
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Phase 1: Ingest
        documents_raw, ingest_errors = await self.ingester.ingest_all(
            request.sources
        )
        if ingest_errors:
            print(f"⚠️  Ingestion errors: {ingest_errors}")

        # Phase 2: Security scan + redact
        documents_scanned = []
        for doc in documents_raw:
            redacted_content, security_issues = self.scanner.scan_document(
                doc.content, doc.source
            )
            # Store redacted content
            doc.content = redacted_content
            documents_scanned.append((doc, security_issues))

        # Phase 3: Quality scoring
        documents_scored = []
        for doc, security_issues in documents_scanned:
            # Compute quality dimensions
            relevance = self.scorer.compute_relevance(0.3, request.format_hint)  # mock
            freshness = self.scorer.compute_freshness(12)  # mock: 12 hours old
            coverage = self.scorer.compute_coverage(len(documents_raw), 10)  # mock
            completeness = self.scorer.compute_completeness(True, True, 0)

            quality_score = self.scorer.compute_document_quality(
                relevance, freshness, coverage, completeness
            )

            # Create Document
            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            scored_doc = Document(
                id=doc.id,
                source=doc.source,
                content=doc.content,
                quality_score=quality_score,
                freshness_hours=12,
                security_issues=list(security_issues),
                extracted_entities={},  # TODO: NER/entity extraction
                timestamp_ingested=doc.extracted_at.isoformat(),
                content_hash=content_hash,
            )
            documents_scored.append(scored_doc)

        # Phase 4: Filter by quality thresholds
        relevance_min = request.quality_filters.get("relevance_min", 0.6)
        filtered_docs = [
            doc for doc in documents_scored if doc.quality_score >= relevance_min
        ]

        # Phase 5: Compute manifest metadata
        quality_scores = [doc.quality_score for doc in filtered_docs]
        manifest_quality = self.scorer.compute_manifest_quality(quality_scores)

        source_breakdown = self._compute_source_breakdown(filtered_docs)
        security_summary = self._compute_security_summary(filtered_docs)

        # Phase 6: Build manifest
        manifest_id = self._generate_manifest_id()
        metadata = {
            "total_tokens": sum(len(doc.content.split()) for doc in filtered_docs),
            "quality_score": manifest_quality,
            "source_breakdown": source_breakdown,
            "security_issues": security_summary,
            "document_count": len(filtered_docs),
            "timestamp_created": datetime.utcnow().isoformat(),
        }

        examples = [
            doc.id
            for doc in sorted(filtered_docs, key=lambda d: d.quality_score, reverse=True)[
                :5
            ]
        ]

        manifest_dict = {
            "manifest_id": manifest_id,
            "documents": [doc.to_dict() for doc in filtered_docs],
            "metadata": metadata,
            "examples": examples,
            "relationships": [],  # TODO: detect cross-doc relationships
            "timestamp_created": datetime.utcnow().isoformat(),
            "manifest_hash": "",  # placeholder
        }

        # Compute hash
        manifest_dict["manifest_hash"] = compute_manifest_hash(manifest_dict)

        # Create immutable manifest
        manifest = DataManifest(
            manifest_id=manifest_dict["manifest_id"],
            documents=filtered_docs,
            metadata=metadata,
            examples=examples,
            relationships=[],
            timestamp_created=datetime.utcnow().isoformat(),
            manifest_hash=manifest_dict["manifest_hash"],
        )

        # Cache it
        self.cache[cache_key] = manifest

        return manifest

    def _compute_cache_key(self, request: DataHubRequest) -> str:
        """Hash request to create cache key."""
        key_str = str(sorted(request.sources.items()))
        return hashlib.sha256(key_str.encode()).hexdigest()

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
