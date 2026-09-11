"""Unit tests for DataSourceIngestor."""

import asyncio
from datetime import datetime
from core.skills.os_skills.data_hub.ingestion.ingester import DataSourceIngestor, IngestedDocument


class TestDataSourceIngestor:
    """Test document ingestion and deduplication."""

    def test_ingest_memory_placeholder(self):
        """Memory ingestion returns empty list (mocked in real implementation)."""
        ingester = DataSourceIngestor()

        async def test():
            docs = await ingester.ingest_memory(tier="tier2")
            assert isinstance(docs, list)
            # Empty in placeholder implementation
            return docs

        docs = asyncio.run(test())
        assert isinstance(docs, list)

    def test_ingest_rag_placeholder(self):
        """RAG ingestion returns empty list (mocked in real implementation)."""
        ingester = DataSourceIngestor()

        async def test():
            docs = await ingester.ingest_rag(collection="default")
            assert isinstance(docs, list)
            return docs

        docs = asyncio.run(test())
        assert isinstance(docs, list)

    def test_ingest_mcp_placeholder(self):
        """MCP ingestion returns empty list (mocked in real implementation)."""
        ingester = DataSourceIngestor()

        async def test():
            docs = await ingester.ingest_mcp("test-server", "/resource")
            assert isinstance(docs, list)
            return docs

        docs = asyncio.run(test())
        assert isinstance(docs, list)

    def test_ingest_files_placeholder(self):
        """File ingestion returns ([], []) in placeholder."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_files(["/tmp/test.txt"])
            assert isinstance(docs, list)
            assert isinstance(errors, list)
            return docs, errors

        docs, errors = asyncio.run(test())
        assert isinstance(docs, list)
        assert isinstance(errors, list)

    def test_deduplicate_documents_empty(self):
        """Empty document list dedups to empty."""
        ingester = DataSourceIngestor()

        result = ingester.deduplicate_documents([])
        assert result == []

    def test_deduplicate_documents_single(self):
        """Single document is not deduplicated."""
        ingester = DataSourceIngestor()

        doc = IngestedDocument(
            id="d1",
            source="test",
            content="content",
            extracted_at=datetime.now(),
            metadata={},
        )

        result = ingester.deduplicate_documents([doc])
        assert len(result) == 1
        assert result[0].id == "d1"

    def test_deduplicate_documents_duplicates(self):
        """Exact duplicates are removed."""
        ingester = DataSourceIngestor()

        doc1 = IngestedDocument("d1", "source", "same content", datetime.now(), {})
        doc2 = IngestedDocument("d2", "source", "same content", datetime.now(), {})
        doc3 = IngestedDocument("d3", "source", "different", datetime.now(), {})

        result = ingester.deduplicate_documents([doc1, doc2, doc3])
        # Should keep first occurrence of each content hash
        assert len(result) == 2
        assert result[0].id == "d1"  # First with "same content"
        assert result[1].id == "d3"  # Only occurrence of "different"

    def test_ingest_all_empty_sources(self):
        """Empty sources dict returns ([], [])."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_all({})
            assert docs == []
            assert errors == []
            return docs, errors

        docs, errors = asyncio.run(test())
        assert docs == []
        assert errors == []

    def test_ingest_all_unknown_source(self):
        """Unknown source type returns error."""
        ingester = DataSourceIngestor()

        async def test():
            docs, errors = await ingester.ingest_all({"unknown:type": {}})
            assert docs == []
            assert len(errors) > 0
            return docs, errors

        docs, errors = asyncio.run(test())
        assert len(errors) > 0
        assert "Unknown source" in errors[0]

    def test_ingest_all_handles_errors(self):
        """Errors in one source don't stop others."""
        ingester = DataSourceIngestor()

        # Mock to throw an error
        async def failing_ingest_memory(*args, **kwargs):
            raise ValueError("Connection failed")

        ingester.ingest_memory = failing_ingest_memory

        async def test():
            docs, errors = await ingester.ingest_all({"memory:tier2": {}})
            # Should have error but continue gracefully
            assert len(errors) > 0
            return docs, errors

        docs, errors = asyncio.run(test())
        assert len(errors) > 0

    def test_ingested_document_fields(self):
        """IngestedDocument has all required fields."""
        doc = IngestedDocument(
            id="d1",
            source="memory:tier2",
            content="Test content here",
            extracted_at=datetime.now(),
            metadata={"key": "value"},
        )

        assert doc.id == "d1"
        assert doc.source == "memory:tier2"
        assert doc.content == "Test content here"
        assert doc.extracted_at is not None
        assert doc.metadata == {"key": "value"}

    def test_deduplicate_preserves_order(self):
        """Deduplication preserves document order."""
        ingester = DataSourceIngestor()

        doc1 = IngestedDocument("d1", "s", "a", datetime.now(), {})
        doc2 = IngestedDocument("d2", "s", "b", datetime.now(), {})
        doc3 = IngestedDocument("d3", "s", "c", datetime.now(), {})

        result = ingester.deduplicate_documents([doc1, doc2, doc3])
        assert result[0].id == "d1"
        assert result[1].id == "d2"
        assert result[2].id == "d3"
