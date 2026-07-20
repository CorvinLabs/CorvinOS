"""PostgreSQL Schema for Geo-Tracking (Phase 3.1).

Handles instance_geo_pings table creation, indexes, and TTL jobs.
"""
import logging
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

logger = logging.getLogger(__name__)


# ============ Schema DDL ============

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS instance_geo_pings (
  id BIGSERIAL PRIMARY KEY,
  instance_id_hash VARCHAR(64) NOT NULL,
  country VARCHAR(2),
  region VARCHAR(2),
  city VARCHAR(128),
  geo_grid_lat DECIMAL(4,1),
  geo_grid_lng DECIMAL(5,1),
  geo_consent_tier INT,
  created_at DATE,
  
  INDEX idx_country (country),
  INDEX idx_region (country, region),
  INDEX idx_city (country, city),
  INDEX idx_created (created_at)
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_geo_country ON instance_geo_pings(country);",
    "CREATE INDEX IF NOT EXISTS idx_geo_region ON instance_geo_pings(country, region);",
    "CREATE INDEX IF NOT EXISTS idx_geo_city ON instance_geo_pings(country, city);",
    "CREATE INDEX IF NOT EXISTS idx_geo_created ON instance_geo_pings(created_at);",
]

TTL_DELETE_SQL = """
DELETE FROM instance_geo_pings
WHERE (geo_consent_tier = 2 AND created_at < CURRENT_DATE - INTERVAL '30 days')
   OR (geo_consent_tier = 3 AND created_at < CURRENT_DATE - INTERVAL '14 days');
"""


def get_db_connection(dsn: str):
    """Create PostgreSQL connection."""
    if not HAS_PSYCOPG2:
        raise ImportError("psycopg2 required for geo_schema migrations")
    return psycopg2.connect(dsn)


def migrate_schema(dsn: str) -> bool:
    """Execute schema migration.
    
    Args:
        dsn: PostgreSQL connection string
        
    Returns:
        True if successful
    """
    try:
        conn = get_db_connection(dsn)
        cur = conn.cursor()
        
        logger.info("Creating instance_geo_pings table...")
        cur.execute(CREATE_TABLE_SQL)
        
        logger.info("Creating indexes...")
        for sql in CREATE_INDEXES_SQL:
            cur.execute(sql)
        
        conn.commit()
        logger.info("✅ Geo-tracking schema migrated successfully")
        cur.close()
        conn.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Schema migration failed: {e}")
        return False


def cleanup_ttl(dsn: str) -> int:
    """Run TTL cleanup job (delete expired rows).
    
    Returns:
        Number of rows deleted
    """
    try:
        conn = get_db_connection(dsn)
        cur = conn.cursor()
        
        logger.info("Running TTL cleanup...")
        cur.execute(TTL_DELETE_SQL)
        deleted = cur.rowcount
        
        conn.commit()
        logger.info(f"✅ TTL cleanup: deleted {deleted} rows")
        cur.close()
        conn.close()
        return deleted
        
    except Exception as e:
        logger.error(f"❌ TTL cleanup failed: {e}")
        return 0


def insert_geo_ping(
    dsn: str,
    instance_id_hash: str,
    country: str,
    tier: int,
    region: Optional[str] = None,
    city: Optional[str] = None,
    grid_lat: Optional[float] = None,
    grid_lng: Optional[float] = None,
) -> bool:
    """Insert a single geo ping."""
    try:
        conn = get_db_connection(dsn)
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO instance_geo_pings
            (instance_id_hash, country, region, city, geo_grid_lat, geo_grid_lng, geo_consent_tier, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_DATE)
        """, (instance_id_hash, country, region, city, grid_lat, grid_lng, tier))
        
        conn.commit()
        cur.close()
        conn.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Insert geo ping failed: {e}")
        return False


def get_country_stats(dsn: str, tier: int = 1) -> dict:
    """Fetch country-level stats from database."""
    try:
        conn = get_db_connection(dsn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if tier == 1:
            cur.execute("""
                SELECT 
                  country,
                  COUNT(*) as instances,
                  COUNT(CASE WHEN created_at >= CURRENT_DATE - INTERVAL '1 day' THEN 1 END) as active_24h,
                  COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0) as retention
                FROM instance_geo_pings
                WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
                  AND geo_consent_tier >= 1
                GROUP BY country
                ORDER BY instances DESC;
            """)
        
        results = {row['country']: {
            'instances': row['instances'],
            'active_24h': row['active_24h'],
            'retention': float(row['retention'] or 0.92),
        } for row in cur.fetchall()}
        
        cur.close()
        conn.close()
        return results
        
    except Exception as e:
        logger.error(f"❌ Query failed: {e}")
        return {}


if __name__ == "__main__":
    # Test migration
    import os
    dsn = os.environ.get('DATABASE_URL', 'postgresql://localhost/corvinOS')
    
    if migrate_schema(dsn):
        logger.info("✅ Schema migration successful")
    else:
        logger.error("❌ Schema migration failed")
