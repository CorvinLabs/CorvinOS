"""Comprehensive tests for DataSourceIngestor (Phase 1)."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from core.skills.os_skills.data_hub.ingestion.ingester import (
    DataSourceIngestor,
    IngestedDocument,
)


class TestIngestedDocument:
    """Test IngestedDocument dataclass."""

    def test_create_document(self):
        """Create an IngestedDocument with all fields."""
        now = datetime.utcnow()
        doc = IngestedDocument(
            id="test-1",
            source="memory:tier2",
            content="Test content",
            extracted_at=now,
            metadata={"key": "value"},
        )

        assert doc.id == "test-1"
        assert doc.source == "memory:tier2"
        assert doc.content == "Test content"
        assert doc.extracted_at == now
        assert doc.metadata == {"key": "value"}

    def test_document_with_empty_metadata(self):
        """Create document with empty metadata."""
        doc = IngestedDocument(
            id="test-2",
            source="file:test.txt",
            content="content",
            extracted_at=datetime.utcnow(),
            metadata={},
        )

        assert doc.metadata == {}


class TestIngestMemory:
    """Test ingest_memory() method."""

    def test_ingest_memory_returns_list(self):
        """ingest_memory returns a list."""
        ingester = DataSourceIngestor()

        async def test():
            result = await ingester.ingest_memory(tier="tier2")
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_memory_with_valid_tier(self):
        """ingest_memory accepts valid tiers (tier1/tier2/tier3)."""
        ingester = DataSourceIngestor()

        async def test():
            for tier in ["tier1", "tier2", "tier3"]:
                result = await ingester.ingest_memory(tier=tier)
                assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_memory_with_invalid_tier(self):
        """ingest_memory with invalid tier returns empty list."""
        ingester = DataSourceIngestor()

        async def test():
            result = await ingester.ingest_memory(tier="tier99")
            assert result == []

        asyncio.run(test())

    def test_ingest_memory_nonexistent_file(self):
        """ingest_memory gracefully handles missing file."""
        ingester = DataSourceIngestor()

        async def test():
            # Non-existent file should return empty list
            result = await ingester.ingest_memory(tier="tier2")
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_memory_with_tags_filter(self):
        """ingest_memory supports tag filtering."""
        ingester = DataSourceIngestor()

        async def test():
            # Even if file doesn't exist, method should work
            result = await ingester.ingest_memory(
                tier="tier2", tags=["important", "recent"]
            )
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_memory_with_valid_file(self, tmp_path):
        """ingest_memory reads from valid memory file."""
        ingester = DataSourceIngestor(tenant_id="_default")

        # Create a temporary memory file
        memory_dir = tmp_path / "tenants" / "_default" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / "tier2_memory.jsonl"

        # Write test data
        test_data = [
            {
                "id": "mem-1",
                "content": "Memory content 1",
                "timestamp": datetime.utcnow().isoformat(),
                "tags": ["important"],
                "relevance": 0.9,
            },
            {
                "id": "mem-2",
                "content": "Memory content 2",
                "timestamp": datetime.utcnow().isoformat(),
                "tags": ["recent"],
                "relevance": 0.7,
            },
        ]

        with open(memory_file, "w") as f:
            for item in test_data:
                f.write(json.dumps(item) + "\n")

        # Mock the corvin_home to use tmp_path
        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                result = await ingester.ingest_memory(tier="tier2")
                assert len(result) == 2
                assert result[0].id == "mem-1"
                assert result[0].source == "memory:tier2"
                assert result[1].id == "mem-2"
                assert result[1].metadata["relevance"] == 0.7

            asyncio.run(test())

    def test_ingest_memory_with_tag_filter(self, tmp_path):
        """ingest_memory filters by tags."""
        ingester = DataSourceIngestor(tenant_id="_default")

        # Create a temporary memory file
        memory_dir = tmp_path / "tenants" / "_default" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / "tier2_memory.jsonl"

        # Write test data
        test_data = [
            {
                "id": "mem-1",
                "content": "Content 1",
                "timestamp": datetime.utcnow().isoformat(),
                "tags": ["important"],
            },
            {
                "id": "mem-2",
                "content": "Content 2",
                "timestamp": datetime.utcnow().isoformat(),
                "tags": ["recent"],
            },
        ]

        with open(memory_file, "w") as f:
            for item in test_data:
                f.write(json.dumps(item) + "\n")

        # Mock the corvin_home
        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                result = await ingester.ingest_memory(tier="tier2", tags=["important"])
                assert len(result) == 1
                assert result[0].metadata["tags"] == ["important"]

            asyncio.run(test())

    def test_ingest_memory_handles_malformed_json(self, tmp_path):
        """ingest_memory gracefully skips malformed JSON lines."""
        ingester = DataSourceIngestor(tenant_id="_default")

        memory_dir = tmp_path / "tenants" / "_default" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / "tier2_memory.jsonl"

        # Write mixed valid and invalid JSON
        with open(memory_file, "w") as f:
            f.write(json.dumps({"id": "valid-1", "content": "Good"}) + "\n")
            f.write("This is not valid JSON\n")
            f.write(json.dumps({"id": "valid-2", "content": "Also good"}) + "\n")

        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                result = await ingester.ingest_memory(tier="tier2")
                # Should have 2 valid entries
                assert len(result) == 2

            asyncio.run(test())


class TestIngestRag:
    """Test ingest_rag() method."""

    def test_ingest_rag_returns_list(self):
        """ingest_rag returns a list."""
        ingester = DataSourceIngestor()

        async def test():
            result = await ingester.ingest_rag()
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_rag_with_custom_collection(self):
        """ingest_rag accepts custom collection name."""
        ingester = DataSourceIngestor()

        async def test():
            result = await ingester.ingest_rag(collection="custom")
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_rag_respects_relevance_threshold(self, tmp_path):
        """ingest_rag filters by relevance threshold."""
        ingester = DataSourceIngestor(tenant_id="_default")

        # Create temporary RAG collection
        rag_dir = tmp_path / "tenants" / "_default" / "rag" / "default"
        rag_dir.mkdir(parents=True, exist_ok=True)
        embeddings_file = rag_dir / "embeddings.jsonl"

        test_data = [
            {
                "id": "rag-1",
                "text": "High relevance",
                "score": 0.95,
                "timestamp": datetime.utcnow().isoformat(),
            },
            {
                "id": "rag-2",
                "text": "Low relevance",
                "score": 0.3,
                "timestamp": datetime.utcnow().isoformat(),
            },
            {
                "id": "rag-3",
                "text": "Medium relevance",
                "score": 0.75,
                "timestamp": datetime.utcnow().isoformat(),
            },
        ]

        with open(embeddings_file, "w") as f:
            for item in test_data:
                f.write(json.dumps(item) + "\n")

        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                result = await ingester.ingest_rag(relevance_threshold=0.5)
                # Should get 2 items (0.95 and 0.75, not 0.3)
                assert len(result) == 2
                assert all(doc.metadata["relevance_score"] >= 0.5 for doc in result)

            asyncio.run(test())

    def test_ingest_rag_respects_max_results(self, tmp_path):
        """ingest_rag limits results by max_results."""
        ingester = DataSourceIngestor(tenant_id="_default")

        rag_dir = tmp_path / "tenants" / "_default" / "rag" / "default"
        rag_dir.mkdir(parents=True, exist_ok=True)
        embeddings_file = rag_dir / "embeddings.jsonl"

        # Write 10 items
        with open(embeddings_file, "w") as f:
            for i in range(10):
                f.write(
                    json.dumps({
                        "id": f"rag-{i}",
                        "text": f"Item {i}",
                        "score": 0.8,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                    + "\n"
                )

        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                result = await ingester.ingest_rag(max_results=5)
                assert len(result) == 5

            asyncio.run(test())


class TestIngestMcp:
    """Test ingest_mcp() method."""

    def test_ingest_mcp_returns_list(self):
        """ingest_mcp returns a list."""
        ingester = DataSourceIngestor()

        async def test():
            result = await ingester.ingest_mcp("test-server", "/resource")
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_mcp_with_server_and_path(self):
        """ingest_mcp accepts server name and resource path."""
        ingester = DataSourceIngestor()

        async def test():
            result = await ingester.ingest_mcp("my-server", "/data/resource")
            assert isinstance(result, list)

        asyncio.run(test())

    def test_ingest_mcp_with_cached_data(self, tmp_path):
        """ingest_mcp reads from cached MCP data."""
        ingester = DataSourceIngestor(tenant_id="_default")

        # Create temporary MCP cache
        mcp_dir = tmp_path / "tenants" / "_default" / "mcp_cache" / "test-server"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        resource_file = mcp_dir / "_resource.json"

        test_data = [
            {
                "id": "mcp-1",
                "content": "MCP content 1",
                "type": "document",
                "timestamp": datetime.utcnow().isoformat(),
            },
            {
                "id": "mcp-2",
                "content": "MCP content 2",
                "type": "document",
                "timestamp": datetime.utcnow().isoformat(),
            },
        ]

        with open(resource_file, "w") as f:
            json.dump(test_data, f)

        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                result = await ingester.ingest_mcp("test-server", "/resource")
                assert len(result) == 2
                assert result[0].source == "mcp:test-server"
                assert result[0].metadata["resource_path"] == "/resource"

            asyncio.run(test())


class TestIngestFiles:
    """Test ingest_files() method."""

    def test_ingest_files_returns_tuple(self):
        """ingest_files returns (documents, errors) tuple."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_files([])
            assert isinstance(docs, list)
            assert isinstance(errors, list)

        asyncio.run(test())

    def test_ingest_files_txt_file(self):
        """ingest_files reads .txt files."""
        ingester = DataSourceIngestor()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("This is test content\nMultiple lines\n")
            temp_path = f.name

        try:
            async def test():
                docs, errors = await ingester.ingest_files([temp_path])
                assert len(docs) == 1
                assert docs[0].content == "This is test content\nMultiple lines\n"
                assert docs[0].metadata["file_type"] == ".txt"
                assert len(errors) == 0

            asyncio.run(test())
        finally:
            Path(temp_path).unlink()

    def test_ingest_files_json_file(self):
        """ingest_files reads .json files."""
        ingester = DataSourceIngestor()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"key": "value", "data": [1, 2, 3]}, f)
            temp_path = f.name

        try:
            async def test():
                docs, errors = await ingester.ingest_files([temp_path])
                assert len(docs) == 1
                assert '"key": "value"' in docs[0].content
                assert docs[0].metadata["file_type"] == ".json"
                assert len(errors) == 0

            asyncio.run(test())
        finally:
            Path(temp_path).unlink()

    def test_ingest_files_md_file(self):
        """ingest_files reads .md files."""
        ingester = DataSourceIngestor()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Header\nSome markdown content")
            temp_path = f.name

        try:
            async def test():
                docs, errors = await ingester.ingest_files([temp_path])
                assert len(docs) == 1
                assert "# Header" in docs[0].content
                assert docs[0].metadata["file_type"] == ".md"

            asyncio.run(test())
        finally:
            Path(temp_path).unlink()

    def test_ingest_files_py_file(self):
        """ingest_files reads .py files."""
        ingester = DataSourceIngestor()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def hello():\n    return 'world'")
            temp_path = f.name

        try:
            async def test():
                docs, errors = await ingester.ingest_files([temp_path])
                assert len(docs) == 1
                assert "def hello():" in docs[0].content

            asyncio.run(test())
        finally:
            Path(temp_path).unlink()

    def test_ingest_files_nonexistent_file(self):
        """ingest_files reports error for nonexistent file."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_files(["/nonexistent/path/file.txt"])
            assert len(docs) == 0
            assert len(errors) == 1
            assert "File not found" in errors[0]

        asyncio.run(test())

    def test_ingest_files_empty_file(self):
        """ingest_files reports error for empty file."""
        ingester = DataSourceIngestor()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            temp_path = f.name

        try:
            async def test():
                docs, errors = await ingester.ingest_files([temp_path])
                assert len(docs) == 0
                assert len(errors) == 1
                assert "empty" in errors[0].lower()

            asyncio.run(test())
        finally:
            Path(temp_path).unlink()

    def test_ingest_files_unsupported_type(self):
        """ingest_files reports error for unsupported file type."""
        ingester = DataSourceIngestor()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xyz", delete=False
        ) as f:
            f.write("content")
            temp_path = f.name

        try:
            async def test():
                docs, errors = await ingester.ingest_files([temp_path])
                assert len(docs) == 0
                assert len(errors) == 1
                assert "Unsupported file type" in errors[0]

            asyncio.run(test())
        finally:
            Path(temp_path).unlink()

    def test_ingest_files_directory(self):
        """ingest_files reports error for directories."""
        ingester = DataSourceIngestor()

        with tempfile.TemporaryDirectory() as temp_dir:
            async def test():
                docs, errors = await ingester.ingest_files([temp_dir])
                assert len(docs) == 0
                assert len(errors) == 1
                assert "Not a file" in errors[0]

            asyncio.run(test())

    def test_ingest_files_multiple_files(self):
        """ingest_files handles multiple files correctly."""
        ingester = DataSourceIngestor()

        files = []
        for i, ext in enumerate([".txt", ".md", ".json"]):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=ext, delete=False
            ) as f:
                f.write(f"Content {i}")
                files.append(f.name)

        try:
            async def test():
                docs, errors = await ingester.ingest_files(files)
                assert len(docs) == 3
                assert len(errors) == 0
                assert all(doc.content.startswith("Content") for doc in docs)

            asyncio.run(test())
        finally:
            for f in files:
                Path(f).unlink()


class TestDeduplication:
    """Test deduplicate_documents() method."""

    def test_deduplicate_empty_list(self):
        """Deduplication of empty list returns empty list."""
        ingester = DataSourceIngestor()
        result = ingester.deduplicate_documents([])
        assert result == []

    def test_deduplicate_single_document(self):
        """Single document is not removed."""
        ingester = DataSourceIngestor()
        doc = IngestedDocument(
            id="d1",
            source="test",
            content="unique",
            extracted_at=datetime.utcnow(),
            metadata={},
        )
        result = ingester.deduplicate_documents([doc])
        assert len(result) == 1

    def test_deduplicate_exact_duplicates(self):
        """Exact duplicate content is removed."""
        ingester = DataSourceIngestor()
        doc1 = IngestedDocument(
            "d1", "source", "same content", datetime.utcnow(), {}
        )
        doc2 = IngestedDocument(
            "d2", "source", "same content", datetime.utcnow(), {}
        )
        result = ingester.deduplicate_documents([doc1, doc2])
        assert len(result) == 1
        assert result[0].id == "d1"

    def test_deduplicate_different_content(self):
        """Different content is preserved."""
        ingester = DataSourceIngestor()
        doc1 = IngestedDocument("d1", "s", "content1", datetime.utcnow(), {})
        doc2 = IngestedDocument("d2", "s", "content2", datetime.utcnow(), {})
        result = ingester.deduplicate_documents([doc1, doc2])
        assert len(result) == 2

    def test_deduplicate_near_duplicates(self):
        """Near-duplicates (same length, similar start/end) are removed."""
        ingester = DataSourceIngestor()
        # Two documents with same length and similar start/end
        content1 = "This is the start" + "X" * 100 + "end of content"
        content2 = "This is the start" + "Y" * 100 + "end of content"

        doc1 = IngestedDocument("d1", "s", content1, datetime.utcnow(), {})
        doc2 = IngestedDocument("d2", "s", content2, datetime.utcnow(), {})
        result = ingester.deduplicate_documents([doc1, doc2])

        # Should remove near-duplicate
        assert len(result) == 1

    def test_deduplicate_preserves_order(self):
        """Deduplication preserves document order."""
        ingester = DataSourceIngestor()
        doc1 = IngestedDocument("d1", "s", "a", datetime.utcnow(), {})
        doc2 = IngestedDocument("d2", "s", "b", datetime.utcnow(), {})
        doc3 = IngestedDocument("d3", "s", "c", datetime.utcnow(), {})

        result = ingester.deduplicate_documents([doc1, doc2, doc3])
        assert result[0].id == "d1"
        assert result[1].id == "d2"
        assert result[2].id == "d3"

    def test_deduplicate_multiple_exact_duplicates(self):
        """Multiple exact duplicates keep only the first."""
        ingester = DataSourceIngestor()
        content = "same"
        doc1 = IngestedDocument("d1", "s", content, datetime.utcnow(), {})
        doc2 = IngestedDocument("d2", "s", content, datetime.utcnow(), {})
        doc3 = IngestedDocument("d3", "s", content, datetime.utcnow(), {})

        result = ingester.deduplicate_documents([doc1, doc2, doc3])
        assert len(result) == 1
        assert result[0].id == "d1"


class TestIngestAll:
    """Test ingest_all() method."""

    def test_ingest_all_empty_sources(self):
        """ingest_all with no sources returns empty."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_all({})
            assert docs == []
            assert errors == []

        asyncio.run(test())

    def test_ingest_all_unknown_source(self):
        """ingest_all with unknown source reports error."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_all({"unknown:source": {}})
            assert len(docs) == 0
            assert len(errors) == 1
            assert "Unknown source" in errors[0]

        asyncio.run(test())

    def test_ingest_all_handles_errors_gracefully(self):
        """ingest_all continues on error."""
        ingester = DataSourceIngestor()

        # Mock to throw error
        async def failing_method(*args, **kwargs):
            raise ValueError("Test error")

        ingester.ingest_memory = failing_method

        async def test():
            docs, errors = await ingester.ingest_all({"memory:tier2": {}})
            # Should have error but not crash
            assert len(errors) > 0

        asyncio.run(test())

    def test_ingest_all_multiple_sources(self):
        """ingest_all combines results from multiple sources."""
        ingester = DataSourceIngestor()

        # Mock the ingestion methods
        async def mock_memory(*args, **kwargs):
            return [
                IngestedDocument(
                    "m1", "memory:tier2", "mem content", datetime.utcnow(), {}
                )
            ]

        async def mock_files(*args, **kwargs):
            return (
                [IngestedDocument(
                    "f1", "files:test.txt", "file content", datetime.utcnow(), {}
                )],
                [],
            )

        ingester.ingest_memory = mock_memory
        ingester.ingest_files = mock_files

        async def test():
            docs, errors = await ingester.ingest_all({
                "memory:tier2": {},
                "files:": {"paths": ["/tmp/test.txt"]},
            })
            assert len(docs) == 2
            assert len(errors) == 0

        asyncio.run(test())

    def test_ingest_all_deduplicates(self):
        """ingest_all deduplicates across sources."""
        ingester = DataSourceIngestor()

        # Mock to return same content
        async def mock_memory(*args, **kwargs):
            return [
                IngestedDocument("m1", "memory:tier2", "duplicate", datetime.utcnow(), {})
            ]

        async def mock_rag(*args, **kwargs):
            return [
                IngestedDocument("r1", "rag:default", "duplicate", datetime.utcnow(), {})
            ]

        ingester.ingest_memory = mock_memory
        ingester.ingest_rag = mock_rag

        async def test():
            docs, errors = await ingester.ingest_all({
                "memory:tier2": {},
                "rag:default": {},
            })
            # Should deduplicate to 1
            assert len(docs) == 1

        asyncio.run(test())

    def test_ingest_all_memory_and_rag(self):
        """ingest_all orchestrates memory and RAG ingestion."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_all({
                "memory:tier2": {},
                "rag:default": {},
            })
            assert isinstance(docs, list)
            assert isinstance(errors, list)

        asyncio.run(test())

    def test_ingest_all_memory_and_files(self):
        """ingest_all orchestrates memory and file ingestion."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_all({
                "memory:tier2": {},
                "files:": {"paths": []},
            })
            assert isinstance(docs, list)

        asyncio.run(test())


class TestIntegration:
    """Integration tests for DataSourceIngestor."""

    def test_full_ingestion_pipeline(self, tmp_path):
        """Full ingestion from memory to deduplication."""
        ingester = DataSourceIngestor(tenant_id="_default")

        # Create memory file
        memory_dir = tmp_path / "tenants" / "_default" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / "tier2_memory.jsonl"

        with open(memory_file, "w") as f:
            f.write(json.dumps({
                "id": "m1",
                "content": "memory content",
                "timestamp": datetime.utcnow().isoformat(),
            }) + "\n")

        with mock.patch.object(ingester, "_corvin_home", tmp_path):
            async def test():
                docs, errors = await ingester.ingest_all({"memory:tier2": {}})
                assert len(docs) == 1
                assert docs[0].content == "memory content"
                assert len(errors) == 0

            asyncio.run(test())

    def test_tenant_isolation(self):
        """Different tenant IDs use different paths."""
        ingester1 = DataSourceIngestor(tenant_id="tenant1")
        ingester2 = DataSourceIngestor(tenant_id="tenant2")

        assert ingester1._get_tenant_memory_path() != ingester2._get_tenant_memory_path()

    def test_corvin_home_resolution(self):
        """CORVIN_HOME environment variable is respected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                ingester = DataSourceIngestor()
                assert str(ingester._corvin_home) == tmpdir


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
