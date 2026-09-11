"""Data source ingestion — Memory, RAG, MCP, Files."""

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
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

    def __init__(self, tenant_id: str = "_default"):
        """Initialize DataSourceIngestor.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation (default: "_default")
        """
        self.tenant_id = tenant_id
        self.sources = {}
        self.cache_expiry_minutes = 60
        self._corvin_home = self._get_corvin_home()

    def _get_corvin_home(self) -> Path:
        """Get CORVIN_HOME path, respecting environment variable."""
        root = os.environ.get("CORVIN_HOME", "").strip()
        if root:
            return Path(os.path.expandvars(root)).expanduser()
        repo_local = Path(__file__).resolve().parents[4] / ".corvin"
        if repo_local.is_dir():
            return repo_local
        return Path.home() / ".corvin"

    def _get_tenant_memory_path(self) -> Path:
        """Construct path to tenant memory storage."""
        return self._corvin_home / "tenants" / self.tenant_id / "memory"

    def _get_memory_file(self, tier: str) -> Path:
        """Get path to memory file for a specific tier."""
        memory_path = self._get_tenant_memory_path()
        return memory_path / f"{tier}_memory.jsonl"

    async def ingest_memory(
        self, tier: str = "tier2", tags: Optional[List[str]] = None
    ) -> List[IngestedDocument]:
        """
        Ingest from Memory (tier1/tier2/tier3).

        Tiers:
        - tier1: Most recent, high-priority items
        - tier2: Standard context, default
        - tier3: Historical, lower priority

        Args:
            tier: Memory tier to ingest (tier1/tier2/tier3)
            tags: Optional list of tags to filter by

        Returns:
            List of IngestedDocument from the memory tier
        """
        documents = []

        # Validate tier
        if tier not in ["tier1", "tier2", "tier3"]:
            return []

        memory_file = self._get_memory_file(tier)

        # If memory file doesn't exist, return empty (graceful degradation)
        if not memory_file.exists():
            return []

        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)

                        # Filter by tags if provided
                        if tags:
                            entry_tags = entry.get("tags", [])
                            if not any(tag in entry_tags for tag in tags):
                                continue

                        doc = IngestedDocument(
                            id=entry.get("id", str(uuid.uuid4())),
                            source=f"memory:{tier}",
                            content=entry.get("content", ""),
                            extracted_at=datetime.fromisoformat(
                                entry.get("timestamp", datetime.utcnow().isoformat())
                            ),
                            metadata={
                                "tags": entry.get("tags", []),
                                "relevance": entry.get("relevance", 0.5),
                                "tier": tier,
                            },
                        )
                        documents.append(doc)
                    except (json.JSONDecodeError, ValueError):
                        # Skip malformed lines gracefully
                        continue
        except (OSError, IOError) as e:
            # Graceful error handling - return what we have
            pass

        return documents

    async def ingest_rag(
        self,
        collection: str = "default",
        relevance_threshold: float = 0.5,
        max_results: int = 100,
    ) -> List[IngestedDocument]:
        """
        Ingest from RAG embeddings.

        Args:
            collection: Which embedding collection to query
            relevance_threshold: Minimum similarity score (0-1)
            max_results: Maximum documents to retrieve

        Returns:
            List of IngestedDocument from RAG embeddings
        """
        documents = []

        # Phase 1: Graceful stub (RAG backend may not be available yet)
        # In production, this would query:
        # - Milvus / Pinecone / local embedding service
        # - Return top-k results by similarity score

        try:
            # Try to locate RAG collection directory
            rag_path = self._corvin_home / "tenants" / self.tenant_id / "rag" / collection

            if not rag_path.exists():
                return []

            # If a collection file exists, read it
            collection_file = rag_path / "embeddings.jsonl"
            if collection_file.exists():
                with open(collection_file, "r", encoding="utf-8") as f:
                    count = 0
                    for line in f:
                        if not line.strip() or count >= max_results:
                            continue
                        try:
                            entry = json.loads(line)

                            # Filter by relevance threshold
                            if entry.get("score", 0.0) < relevance_threshold:
                                continue

                            doc = IngestedDocument(
                                id=entry.get("id", str(uuid.uuid4())),
                                source=f"rag:{collection}",
                                content=entry.get("text", ""),
                                extracted_at=datetime.fromisoformat(
                                    entry.get("timestamp", datetime.utcnow().isoformat())
                                ),
                                metadata={
                                    "collection": collection,
                                    "relevance_score": entry.get("score", 0.0),
                                    "embedding_dims": entry.get("embedding_dims", 0),
                                },
                            )
                            documents.append(doc)
                            count += 1
                        except (json.JSONDecodeError, ValueError):
                            continue
        except (OSError, IOError):
            pass

        return documents

    async def ingest_mcp(
        self, server_name: str, resource_path: str
    ) -> List[IngestedDocument]:
        """
        Ingest from MCP data source.

        Args:
            server_name: MCP server identifier
            resource_path: Resource path within the server

        Returns:
            List of IngestedDocument from MCP server
        """
        documents = []

        # Phase 1: Graceful stub (MCP integration may require async runtime)
        # In production, this would:
        # - Query MCP server at server_name
        # - Fetch resource at resource_path
        # - Parse and return documents

        try:
            # Try to locate MCP cache directory
            mcp_path = self._corvin_home / "tenants" / self.tenant_id / "mcp_cache" / server_name

            if not mcp_path.exists():
                return []

            # If a resource cache exists, read it
            resource_file = mcp_path / f"{resource_path.replace('/', '_')}.json"
            if resource_file.exists():
                with open(resource_file, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)

                        # Handle both single document and list of documents
                        items = data if isinstance(data, list) else [data]

                        for item in items:
                            doc = IngestedDocument(
                                id=item.get("id", str(uuid.uuid4())),
                                source=f"mcp:{server_name}",
                                content=item.get("content", ""),
                                extracted_at=datetime.fromisoformat(
                                    item.get("timestamp", datetime.utcnow().isoformat())
                                ),
                                metadata={
                                    "server": server_name,
                                    "resource_path": resource_path,
                                    "resource_type": item.get("type", "unknown"),
                                },
                            )
                            documents.append(doc)
                    except (json.JSONDecodeError, ValueError):
                        pass
        except (OSError, IOError):
            pass

        return documents

    async def ingest_files(
        self, file_paths: List[str]
    ) -> Tuple[List[IngestedDocument], List[str]]:
        """
        Ingest from local files (txt, md, py, json, pdf via text extraction).

        Args:
            file_paths: List of file paths to ingest

        Returns:
            (documents, errors): Tuple of successfully ingested documents and error messages
        """
        documents = []
        errors = []

        for file_path in file_paths:
            try:
                path = Path(file_path).resolve()

                # Security: prevent directory traversal
                if not path.exists():
                    errors.append(f"File not found: {file_path}")
                    continue

                if not path.is_file():
                    errors.append(f"Not a file: {file_path}")
                    continue

                # Get file extension
                ext = path.suffix.lower()

                # Read file based on type
                content = None
                if ext in [".txt", ".md", ".py", ".json", ".csv", ".yaml", ".yml"]:
                    try:
                        content = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        # Try with latin-1 as fallback
                        try:
                            content = path.read_text(encoding="latin-1")
                        except Exception as e:
                            errors.append(f"Failed to read {file_path}: {str(e)}")
                            continue
                elif ext == ".pdf":
                    # For Phase 1, attempt simple text extraction (requires pypdf2 or similar)
                    try:
                        import pypdf
                        with open(path, "rb") as f:
                            reader = pypdf.PdfReader(f)
                            content = "\n".join(
                                page.extract_text() for page in reader.pages
                            )
                    except ImportError:
                        errors.append(f"PDF support not available (pypdf not installed): {file_path}")
                        continue
                    except Exception as e:
                        errors.append(f"Failed to extract text from PDF {file_path}: {str(e)}")
                        continue
                else:
                    errors.append(f"Unsupported file type: {ext}")
                    continue

                if not content or not content.strip():
                    errors.append(f"File is empty: {file_path}")
                    continue

                # Create document
                stat = path.stat()
                doc = IngestedDocument(
                    id=str(uuid.uuid4()),
                    source=f"files:{path.name}",
                    content=content,
                    extracted_at=datetime.fromtimestamp(stat.st_mtime),
                    metadata={
                        "file_path": str(path),
                        "file_size": stat.st_size,
                        "file_type": ext,
                        "file_name": path.name,
                    },
                )
                documents.append(doc)

            except Exception as e:
                errors.append(f"Unexpected error processing {file_path}: {str(e)}")

        return documents, errors

    def deduplicate_documents(
        self, documents: List[IngestedDocument]
    ) -> List[IngestedDocument]:
        """
        Remove exact duplicates and near-duplicates.

        Uses SHA256 for exact matching and content length + sample matching for near-duplicates.
        """
        seen_hashes = set()
        seen_signatures = set()
        deduplicated = []

        for doc in documents:
            # Exact duplicate check using SHA256
            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

            # Near-duplicate detection: length + first/last 50 chars
            content_length = len(doc.content)
            first_chunk = doc.content[:50]
            last_chunk = doc.content[-50:] if len(doc.content) > 100 else ""
            signature = (content_length, first_chunk, last_chunk)

            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)

            deduplicated.append(doc)

        return deduplicated

    async def ingest_all(
        self,
        sources: Dict[str, Any],  # {"memory:tier2": {...}, "rag": {...}, ...}
    ) -> Tuple[List[IngestedDocument], List[str]]:
        """
        Ingest from all specified sources.

        Args:
            sources: Dictionary of sources to ingest from

        Returns:
            (all_documents, errors): Tuple of all documents and error messages
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
