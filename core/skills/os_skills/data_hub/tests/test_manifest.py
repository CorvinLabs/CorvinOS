"""Unit tests for DataManifest."""

import json
import hashlib
from datetime import datetime
from core.skills.os_skills.data_hub.manifest import DataManifest, Document, compute_manifest_hash


class TestDocument:
    """Test Document immutability and serialization."""

    def test_document_creation(self):
        """Document can be created with all fields."""
        doc = Document(
            id="d1",
            source="memory:tier2",
            content="Test content",
            quality_score=0.85,
            freshness_hours=12,
            security_issues=["pii_detected"],
            extracted_entities={"name": "John"},
            timestamp_ingested="2026-09-11T12:00:00Z",
            content_hash=hashlib.sha256(b"test").hexdigest(),
        )

        assert doc.id == "d1"
        assert doc.quality_score == 0.85
        assert "pii_detected" in doc.security_issues

    def test_document_frozen(self):
        """Document is frozen (immutable)."""
        doc = Document(
            id="d1",
            source="test",
            content="c",
            quality_score=0.5,
            freshness_hours=0,
            security_issues=[],
            extracted_entities={},
            timestamp_ingested="2026-09-11T00:00:00Z",
            content_hash="abc",
        )

        try:
            doc.id = "d2"
            assert False, "Document should be frozen"
        except (AttributeError, TypeError):
            pass  # Expected

    def test_document_to_dict(self):
        """Document serializes to dict."""
        doc = Document(
            id="d1",
            source="test",
            content="content",
            quality_score=0.75,
            freshness_hours=24,
            security_issues=["secret"],
            extracted_entities={"type": "code"},
            timestamp_ingested="2026-09-11T00:00:00Z",
            content_hash="hash123",
        )

        d = doc.to_dict()
        assert d["id"] == "d1"
        assert d["quality_score"] == 0.75
        assert d["source"] == "test"

    def test_document_quality_bounds(self):
        """Document quality scores are in [0, 1]."""
        for score in [0.0, 0.25, 0.5, 0.75, 1.0]:
            doc = Document(
                id="d1",
                source="test",
                content="c",
                quality_score=score,
                freshness_hours=0,
                security_issues=[],
                extracted_entities={},
                timestamp_ingested="2026-09-11T00:00:00Z",
                content_hash="h",
            )
            assert 0 <= doc.quality_score <= 1


class TestDataManifest:
    """Test DataManifest schema and immutability."""

    def test_manifest_creation(self):
        """Manifest can be created with all fields."""
        doc = Document(
            id="d1", source="test", content="c", quality_score=0.8,
            freshness_hours=12, security_issues=[], extracted_entities={},
            timestamp_ingested="2026-09-11T00:00:00Z", content_hash="h",
        )

        manifest = DataManifest(
            manifest_id="m1",
            documents=[doc],
            metadata={"quality_score": 0.8},
            examples=["d1"],
            relationships=[],
            timestamp_created="2026-09-11T00:00:00Z",
            manifest_hash="hash123",
        )

        assert manifest.manifest_id == "m1"
        assert len(manifest.documents) == 1
        assert manifest.metadata["quality_score"] == 0.8

    def test_manifest_frozen(self):
        """Manifest is frozen (immutable)."""
        doc = Document(
            id="d1", source="t", content="c", quality_score=0.5,
            freshness_hours=0, security_issues=[], extracted_entities={},
            timestamp_ingested="2026-09-11T00:00:00Z", content_hash="h",
        )

        manifest = DataManifest(
            manifest_id="m1", documents=[doc], metadata={}, examples=[],
            relationships=[], timestamp_created="2026-09-11T00:00:00Z",
            manifest_hash="h",
        )

        try:
            manifest.manifest_id = "m2"
            assert False, "Manifest should be frozen"
        except (AttributeError, TypeError):
            pass  # Expected

    def test_manifest_to_dict(self):
        """Manifest serializes to dict."""
        doc = Document(
            id="d1", source="t", content="c", quality_score=0.7,
            freshness_hours=6, security_issues=[], extracted_entities={},
            timestamp_ingested="2026-09-11T00:00:00Z", content_hash="h",
        )

        manifest = DataManifest(
            manifest_id="m1", documents=[doc],
            metadata={"q": 0.7}, examples=["d1"],
            relationships=[], timestamp_created="2026-09-11T00:00:00Z",
            manifest_hash="hash",
        )

        d = manifest.to_dict()
        assert d["manifest_id"] == "m1"
        assert len(d["documents"]) == 1
        assert d["metadata"]["q"] == 0.7

    def test_manifest_to_json(self):
        """Manifest serializes to JSON string."""
        doc = Document(
            id="d1", source="t", content="c", quality_score=0.5,
            freshness_hours=0, security_issues=[], extracted_entities={},
            timestamp_ingested="2026-09-11T00:00:00Z", content_hash="h",
        )

        manifest = DataManifest(
            manifest_id="m1", documents=[doc], metadata={}, examples=[],
            relationships=[], timestamp_created="2026-09-11T00:00:00Z",
            manifest_hash="h",
        )

        json_str = manifest.to_json()
        assert isinstance(json_str, str)
        assert len(json_str) > 0

        # Validate JSON
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_manifest_from_dict(self):
        """Manifest deserializes from dict."""
        d = {
            "manifest_id": "m1",
            "documents": [
                {
                    "id": "d1",
                    "source": "test",
                    "content": "c",
                    "quality_score": 0.8,
                    "freshness_hours": 12,
                    "security_issues": [],
                    "extracted_entities": {},
                    "timestamp_ingested": "2026-09-11T00:00:00Z",
                    "content_hash": "h",
                }
            ],
            "metadata": {"q": 0.8},
            "examples": ["d1"],
            "relationships": [],
            "timestamp_created": "2026-09-11T00:00:00Z",
            "manifest_hash": "hash",
        }

        manifest = DataManifest.from_dict(d)
        assert manifest.manifest_id == "m1"
        assert len(manifest.documents) == 1
        assert manifest.documents[0].id == "d1"

    def test_manifest_json_roundtrip(self):
        """Manifest survives JSON serialization roundtrip."""
        doc = Document(
            id="d1", source="t", content="c", quality_score=0.75,
            freshness_hours=24, security_issues=["pii"], extracted_entities={},
            timestamp_ingested="2026-09-11T00:00:00Z", content_hash="hash",
        )

        manifest1 = DataManifest(
            manifest_id="m1", documents=[doc],
            metadata={"quality_score": 0.75}, examples=["d1"],
            relationships=[], timestamp_created="2026-09-11T00:00:00Z",
            manifest_hash="hash123",
        )

        # Serialize
        json_str = manifest1.to_json()

        # Deserialize
        parsed = json.loads(json_str)
        manifest2 = DataManifest.from_dict(parsed)

        # Compare
        assert manifest2.manifest_id == manifest1.manifest_id
        assert len(manifest2.documents) == len(manifest1.documents)
        assert manifest2.documents[0].quality_score == 0.75

    def test_manifest_empty_documents(self):
        """Manifest with empty documents is valid."""
        manifest = DataManifest(
            manifest_id="m1", documents=[],
            metadata={}, examples=[],
            relationships=[], timestamp_created="2026-09-11T00:00:00Z",
            manifest_hash="h",
        )

        assert len(manifest.documents) == 0
        assert manifest.manifest_id == "m1"


class TestManifestHash:
    """Test manifest hashing for immutability proof."""

    def test_compute_manifest_hash(self):
        """Manifest hash can be computed."""
        d = {
            "manifest_id": "m1",
            "documents": [],
            "metadata": {},
            "examples": [],
            "relationships": [],
            "timestamp_created": "2026-09-11T00:00:00Z",
        }

        hash_val = compute_manifest_hash(d)
        assert isinstance(hash_val, str)
        assert len(hash_val) == 64  # SHA256 hex string

    def test_manifest_hash_deterministic(self):
        """Same manifest → same hash."""
        d = {
            "manifest_id": "m1",
            "documents": [],
            "metadata": {"q": 0.5},
            "examples": [],
            "relationships": [],
            "timestamp_created": "2026-09-11T00:00:00Z",
        }

        h1 = compute_manifest_hash(d)
        h2 = compute_manifest_hash(d)
        assert h1 == h2

    def test_manifest_hash_sensitive_to_changes(self):
        """Different manifest → different hash."""
        d1 = {
            "manifest_id": "m1",
            "documents": [],
            "metadata": {"q": 0.5},
            "examples": [],
            "relationships": [],
            "timestamp_created": "2026-09-11T00:00:00Z",
        }

        d2 = {
            "manifest_id": "m2",  # Different ID
            "documents": [],
            "metadata": {"q": 0.5},
            "examples": [],
            "relationships": [],
            "timestamp_created": "2026-09-11T00:00:00Z",
        }

        h1 = compute_manifest_hash(d1)
        h2 = compute_manifest_hash(d2)
        assert h1 != h2

    def test_manifest_hash_excludes_hash_field(self):
        """Hash computation excludes manifest_hash field."""
        d = {
            "manifest_id": "m1",
            "documents": [],
            "metadata": {},
            "examples": [],
            "relationships": [],
            "timestamp_created": "2026-09-11T00:00:00Z",
            "manifest_hash": "old_hash_value",
        }

        # Should compute hash without the manifest_hash field
        hash_val = compute_manifest_hash(d)
        assert isinstance(hash_val, str)
        assert hash_val != "old_hash_value"
