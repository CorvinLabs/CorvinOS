"""Data source ingestion — Memory, RAG, MCP, Files."""

import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class IngestedDocument:
    """Raw ingested document before quality scoring."""
    id: str
    source: str
    content: str
    extracted_at: datetime
    metadata: Dict[str, Any]


class DataSourceIngestor:
    """Ingest from all supported sources."""

    def __init__(self):
        self.sources = {}

    async def ingest_memory(
        self, tier: str = "tier2", tags: Optional[List[str]] = None
    ) -> List[IngestedDocument]:
        """Ingest from Memory (tier1/tier2/tier3)."""
        # Placeholder: real implementation queries memory backend
        # For now, return empty list (will be mocked in tests)
        return []

    async def ingest_rag(
        self,
        collection: str = "default",
        relevance_threshold: float = 0.5,
        max_results: int = 100,
    ) -> List[IngestedDocument]:
        """Ingest from RAG embeddings."""
        # Placeholder: real implementation queries RAG backend
        return []

    async def ingest_mcp(
        self, server_name: str, resource_path: str
    ) -> List[IngestedDocument]:
        """Ingest from MCP data source."""
        # Placeholder: real implementation queries MCP server
        return []

    async def ingest_files(
        self, file_paths: List[str]
    ) -> Tuple[List[IngestedDocument], List[str]]:
        """
        Ingest from local files (PDF, DOCX, CSV, plaintext).
        Returns: (documents, errors)
        """
        # Placeholder: real implementation reads files
        return [], []

    def deduplicate_documents(
        self, documents: List[IngestedDocument]
    ) -> List[IngestedDocument]:
        """Remove exact duplicates and near-duplicates."""
        seen_hashes = set()
        deduplicated = []

        for doc in documents:
            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            if content_hash not in seen_hashes:
                seen_hashes.add(content_hash)
                deduplicated.append(doc)

        return deduplicated

    async def ingest_all(
        self,
        sources: Dict[str, Any],  # {"memory:tier2": {...}, "rag": {...}, ...}
    ) -> Tuple[List[IngestedDocument], List[str]]:
        """
        Ingest from all specified sources.
        Returns: (all_documents, errors)
        """
        all_docs = []
        errors = []

        for source_key, source_config in sources.items():
            try:
                if source_key.startswith("memory:"):
                    tier = source_key.split(":")[-1]
                    docs = await self.ingest_memory(tier=tier)
                elif source_key.startswith("rag:"):
                    collection = source_config.get("collection", "default")
                    docs = await self.ingest_rag(collection=collection)
                elif source_key.startswith("mcp:"):
                    server = source_config.get("server")
                    path = source_config.get("path", "/")
                    docs = await self.ingest_mcp(server, path)
                elif source_key.startswith("files:"):
                    paths = source_config.get("paths", [])
                    docs, file_errors = await self.ingest_files(paths)
                    errors.extend(file_errors)
                else:
                    errors.append(f"Unknown source: {source_key}")
                    continue

                all_docs.extend(docs)
            except Exception as e:
                errors.append(f"{source_key}: {str(e)}")

        # Deduplicate across all sources
        deduplicated = self.deduplicate_documents(all_docs)

        return deduplicated, errors
