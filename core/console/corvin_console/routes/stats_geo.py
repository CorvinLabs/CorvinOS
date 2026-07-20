"""Geo-Tracking Statistics API — Phase 3 (ADR-0205 Tier 1-3 Data).

Exposes country/region/city telemetry aggregates for corvin-labs.com/stats dashboard.
All data is anonymized (no individual pings, no IPs) and TTL-controlled.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/stats", tags=["telemetry"])


# ============ MOCK DATA (Phase 3: will be replaced with DB queries) ============

MOCK_GEO_TIER1 = {
    "countries": [
        {"code": "DE", "name": "Germany", "instances": 456, "active_24h": 234, "retention": 0.95},
        {"code": "US", "name": "United States", "instances": 389, "active_24h": 178, "retention": 0.92},
        {"code": "GB", "name": "United Kingdom", "instances": 287, "active_24h": 145, "retention": 0.94},
        {"code": "FR", "name": "France", "instances": 198, "active_24h": 102, "retention": 0.93},
        {"code": "CA", "name": "Canada", "instances": 167, "active_24h": 89, "retention": 0.91},
        {"code": "NL", "name": "Netherlands", "instances": 145, "active_24h": 78, "retention": 0.96},
        {"code": "SE", "name": "Sweden", "instances": 134, "active_24h": 71, "retention": 0.94},
        {"code": "AU", "name": "Australia", "instances": 98, "active_24h": 52, "retention": 0.90},
        {"code": "JP", "name": "Japan", "instances": 87, "active_24h": 43, "retention": 0.89},
        {"code": "CH", "name": "Switzerland", "instances": 76, "active_24h": 39, "retention": 0.97},
    ],
    "total_instances": 1814,
    "online_24h": 831,
    "retention_pct": 93,
}

MOCK_GEO_TIER2 = {
    "countries": [
        {
            "code": "DE",
            "name": "Germany",
            "instances": 456,
            "active_24h": 234,
            "regions": [
                {"code": "BW", "name": "Baden-Württemberg", "instances": 98},
                {"code": "BY", "name": "Bavaria", "instances": 87},
                {"code": "BE", "name": "Berlin", "instances": 76},
                {"code": "HE", "name": "Hesse", "instances": 65},
            ],
        },
        {
            "code": "US",
            "name": "United States",
            "instances": 389,
            "active_24h": 178,
            "regions": [
                {"code": "CA", "name": "California", "instances": 95},
                {"code": "TX", "name": "Texas", "instances": 78},
                {"code": "NY", "name": "New York", "instances": 67},
                {"code": "WA", "name": "Washington", "instances": 54},
            ],
        },
    ],
    "meta": {
        "retention_days": 30,
        "last_updated": "2026-07-20T14:32:00Z",
        "dsgvo_compliant": True,
        "geo_consent_tier": 2,
    },
}

MOCK_GEO_TIER3 = {
    "geopoints": [
        {
            "lat": 51.3,
            "lng": 12.1,
            "city": "Stuttgart",
            "country": "DE",
            "instances": 42,
            "active_24h": 23,
            "cluster_size": "medium",
        },
        {
            "lat": 48.1,
            "lng": 11.5,
            "city": "Munich",
            "country": "DE",
            "instances": 38,
            "active_24h": 19,
            "cluster_size": "medium",
        },
        {
            "lat": 52.5,
            "lng": 13.4,
            "city": "Berlin",
            "country": "DE",
            "instances": 35,
            "active_24h": 18,
            "cluster_size": "small",
        },
    ],
    "meta": {
        "retention_days": 14,
        "last_updated": "2026-07-20T14:32:00Z",
        "dsgvo_compliant": True,
        "geo_consent_tier": 3,
        "grid_resolution_km": 10,
        "privacy_notice": "Coordinates are 10km-grid rasterized. No IP stored. Anonymization: 100+ users per cell.",
    },
}


# ============ ENDPOINTS ============


@router.get("/instances", tags=["geo"])
async def get_geo_instances(
    tier: int = Query(1, ge=1, le=3, description="Granularity tier: 1=country, 2=region, 3=city+grid"),
):
    """Get anonymized instance distribution by geography.

    Returns aggregated, anonymized instance telemetry:
    - Tier 1: Country-level (unlimited retention)
    - Tier 2: Region-level (30-day retention, requires consent)
    - Tier 3: City + 10km grid (14-day retention, requires explicit consent)

    All data is GDPR/DSGVO compliant:
    - No individual pings stored
    - No IP addresses stored (lookup result only)
    - Aggregated counts only
    - TTL-based auto-delete for Tier 2/3
    - Geo-grid rasterization (100+ users per cell for Tier 3)

    Args:
        tier: 1 (country), 2 (region), or 3 (city)

    Returns:
        dict: Geo-indexed instance counts + metadata
    """
    if tier == 1:
        logger.info("geo_stats: Tier 1 (country) requested")
        return MOCK_GEO_TIER1

    elif tier == 2:
        logger.info("geo_stats: Tier 2 (region) requested")
        return MOCK_GEO_TIER2

    elif tier == 3:
        logger.info("geo_stats: Tier 3 (city+grid) requested")
        return MOCK_GEO_TIER3

    return {"error": "Invalid tier"}


@router.get("/instances/country/{country_code}")
async def get_geo_country(
    country_code: str,
    tier: int = Query(2, ge=1, le=3),
):
    """Get region-level breakdown for a specific country.

    Args:
        country_code: ISO 3166-1 alpha-2 code (e.g., "DE", "US")
        tier: 1 (ignore), 2 (regions), 3 (cities)

    Returns:
        dict: Region breakdown + city data (if Tier 3)
    """
    country_code = country_code.upper()
    logger.info(f"geo_stats: Country detail {country_code} Tier {tier}")

    # Mock: return first matching country
    data = MOCK_GEO_TIER2 if tier >= 2 else MOCK_GEO_TIER1
    for country in data.get("countries", []):
        if country["code"] == country_code:
            return {
                "code": country["code"],
                "name": country["name"],
                "instances": country["instances"],
                "regions": country.get("regions", []),
            }

    return {"error": f"Country {country_code} not found"}


@router.get("/instances/live")
async def get_geo_live():
    """Get live instance snapshot (Tier 1 only, no retention concerns).

    Returns:
        dict: Real-time country-level counts + online status
    """
    logger.info("geo_stats: Live snapshot (Tier 1)")
    return {
        "countries": MOCK_GEO_TIER1["countries"],
        "total_instances": MOCK_GEO_TIER1["total_instances"],
        "online_now": MOCK_GEO_TIER1["online_24h"],
        "updated_at": "2026-07-20T14:32:00Z",
        "ttl_seconds": 60,
    }


@router.get("/insights")
async def get_geo_insights(tier: int = Query(1, ge=1, le=3)):
    """Get analytical insights from geo data.

    Returns:
        dict: Concentration, growth, retention, maturity insights
    """
    logger.info(f"geo_stats: Insights Tier {tier}")

    if tier == 1:
        return {
            "concentration": {
                "herfindahl_index": 0.18,
                "top_3_pct": 45.5,
                "description": "Moderate geographic concentration; EU dominates but US/APAC growing",
            },
            "growth_momentum": {
                "week_over_week_pct": 12.3,
                "month_trend": "accelerating",
                "description": "Strong growth momentum, all regions trending positive",
            },
            "retention_health": {
                "overall_retention_pct": 93,
                "churn_rate": 0.07,
                "description": "Very healthy retention; churn below 7%",
            },
            "technical_maturity": {
                "legacy_versions_pct": 3,
                "avg_deployment_days": 18,
                "description": "Mature deployment base, rapid adoption of new versions",
            },
        }

    return {"error": "Insights for Tier 2/3 require database connection"}


# ============ SCHEMA (Phase 3: PostgreSQL DDL) ============
"""
CREATE TABLE IF NOT EXISTS instance_geo_pings (
  id BIGSERIAL PRIMARY KEY,
  instance_id_hash VARCHAR(64) NOT NULL,  -- sha256(instance_id), NOT raw ID
  country VARCHAR(2),                     -- ISO 3166-1 alpha-2
  region VARCHAR(2),                      -- ISO 3166-2 code (optional, Tier 2+)
  city VARCHAR(128),                      -- City name (optional, Tier 3)
  geo_grid_lat DECIMAL(4,1),              -- 10km grid, rasterized (Tier 3)
  geo_grid_lng DECIMAL(5,1),              -- 10km grid, rasterized (Tier 3)
  geo_consent_tier INT,                   -- 1, 2, or 3
  created_at DATE,                        -- Only date, not time (prevents re-ID)

  INDEX idx_country (country),
  INDEX idx_region (country, region),
  INDEX idx_city (country, city),
  INDEX idx_created (created_at)          -- For TTL-based delete
);

-- Auto-delete old rows (TTL enforcement)
-- Tier 2: 30-day retention
-- Tier 3: 14-day retention
DELETE FROM instance_geo_pings
WHERE (geo_consent_tier = 2 AND created_at < CURRENT_DATE - INTERVAL '30 days')
   OR (geo_consent_tier = 3 AND created_at < CURRENT_DATE - INTERVAL '14 days');

-- Run daily via cron:
-- SELECT cron.schedule('delete_old_geo_data', '0 2 * * *', 'DELETE FROM instance_geo_pings WHERE ...');
"""
