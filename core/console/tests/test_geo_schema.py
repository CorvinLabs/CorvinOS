"""Tests for geo_schema.py (database migration & queries)."""
import os
from unittest.mock import patch, MagicMock

import pytest

from corvin_console.aco import geo_schema


class TestGeoSchema:
    """Test geo-tracking database layer."""

    def test_create_table_sql_exists(self):
        """Verify CREATE TABLE SQL is defined."""
        assert geo_schema.CREATE_TABLE_SQL
        assert "instance_geo_pings" in geo_schema.CREATE_TABLE_SQL
        assert "instance_id_hash" in geo_schema.CREATE_TABLE_SQL

    def test_create_indexes_sql_exists(self):
        """Verify index definitions exist."""
        assert len(geo_schema.CREATE_INDEXES_SQL) > 0
        assert any("country" in sql.lower() for sql in geo_schema.CREATE_INDEXES_SQL)
        assert any("created" in sql.lower() for sql in geo_schema.CREATE_INDEXES_SQL)

    def test_ttl_delete_sql_exists(self):
        """Verify TTL delete SQL is defined."""
        assert geo_schema.TTL_DELETE_SQL
        assert "30 days" in geo_schema.TTL_DELETE_SQL
        assert "14 days" in geo_schema.TTL_DELETE_SQL

    @patch('corvin_console.aco.geo_schema.psycopg2.connect')
    def test_migrate_schema_success(self, mock_connect):
        """Test successful schema migration."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        result = geo_schema.migrate_schema("postgresql://test")

        assert result is True
        mock_cur.execute.assert_called()
        mock_conn.commit.assert_called()
        mock_cur.close.assert_called()
        mock_conn.close.assert_called()

    @patch('corvin_console.aco.geo_schema.psycopg2.connect')
    def test_migrate_schema_failure(self, mock_connect):
        """Test schema migration with DB error."""
        mock_connect.side_effect = Exception("Connection failed")

        result = geo_schema.migrate_schema("postgresql://invalid")

        assert result is False

    @patch('corvin_console.aco.geo_schema.psycopg2.connect')
    def test_insert_geo_ping_success(self, mock_connect):
        """Test inserting a geo ping."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        result = geo_schema.insert_geo_ping(
            "postgresql://test",
            instance_id_hash="abc123def",
            country="DE",
            tier=1,
            region="BW",
            city="Stuttgart",
            grid_lat=51.3,
            grid_lng=12.1,
        )

        assert result is True
        mock_cur.execute.assert_called()

    @patch('corvin_console.aco.geo_schema.psycopg2.connect')
    def test_cleanup_ttl_success(self, mock_connect):
        """Test TTL cleanup job."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 42
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        deleted = geo_schema.cleanup_ttl("postgresql://test")

        assert deleted == 42
        mock_cur.execute.assert_called()
        mock_conn.commit.assert_called()

    @patch('corvin_console.aco.geo_schema.psycopg2.connect')
    def test_get_country_stats_success(self, mock_connect):
        """Test fetching country stats."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {'country': 'DE', 'instances': 456, 'active_24h': 234, 'retention': 0.95},
            {'country': 'US', 'instances': 389, 'active_24h': 178, 'retention': 0.92},
        ]
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        stats = geo_schema.get_country_stats("postgresql://test", tier=1)

        assert 'DE' in stats
        assert stats['DE']['instances'] == 456
        assert stats['US']['instances'] == 389

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
