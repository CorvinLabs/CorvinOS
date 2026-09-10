"""DataManifest schema — immutable, normalized data representation."""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import hashlib


@dataclass(frozen=True)
class Document:
    """Immutable document from a data source."""
    id: str
    source: str  # "memory:tier2", "rag:embeddings", "files:/path", "mcp:name"
    content: str
    quality_score: float  # 0–1, where 1 = perfect
    freshness_hours: int
    security_issues: List[str]  # ["secret_detected", "pii_detected", "injection_detected"]
    extracted_entities: Dict[str, Any]
    timestamp_ingested: str  # ISO8601
    content_hash: str  # SHA256 of content (for deduplication)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataManifest:
    """Immutable, normalized data manifest output from DataHub."""
    manifest_id: str
    documents: List[Document]
    metadata: Dict[str, Any]  # quality_score, source_breakdown, security_issues, etc.
    examples: List[str]  # IDs of most-relevant documents
    relationships: List[Dict[str, str]]  # cross-document links
    timestamp_created: str  # ISO8601
    manifest_hash: str  # SHA256 (immutability proof)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "documents": [doc.to_dict() for doc in self.documents],
            "metadata": self.metadata,
            "examples": self.examples,
            "relationships": self.relationships,
            "timestamp_created": self.timestamp_created,
            "manifest_hash": self.manifest_hash,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataManifest":
        """Deserialize from dict (for testing, caching)."""
        docs = [Document(**doc) for doc in data.get("documents", [])]
        return cls(
            manifest_id=data["manifest_id"],
            documents=docs,
            metadata=data.get("metadata", {}),
            examples=data.get("examples", []),
            relationships=data.get("relationships", []),
            timestamp_created=data.get("timestamp_created", datetime.utcnow().isoformat()),
            manifest_hash=data.get("manifest_hash", ""),
        )


def compute_manifest_hash(manifest_dict: Dict[str, Any]) -> str:
    """Compute SHA256 of manifest (excludes hash itself for immutability proof)."""
    # Remove manifest_hash to prevent circular dependency
    data_to_hash = {k: v for k, v in manifest_dict.items() if k != "manifest_hash"}
    json_str = json.dumps(data_to_hash, sort_keys=True)
    return hashlib.sha256(json_str.encode()).hexdigest()
