"""Seed test data for geo-tracking (for development/testing)."""
import logging
from datetime import datetime, timedelta
import hashlib
import random

from . import geo_schema

logger = logging.getLogger(__name__)

# Sample cities with coordinates
SAMPLE_DATA = {
    'DE': {
        'name': 'Germany',
        'regions': {
            'BW': {'name': 'Baden-Württemberg', 'cities': [
                {'name': 'Stuttgart', 'lat': 48.78, 'lng': 9.18, 'instances': 45},
                {'name': 'Karlsruhe', 'lat': 49.01, 'lng': 8.40, 'instances': 28},
            ]},
            'BY': {'name': 'Bavaria', 'cities': [
                {'name': 'Munich', 'lat': 48.14, 'lng': 11.58, 'instances': 67},
                {'name': 'Nuremberg', 'lat': 49.45, 'lng': 11.08, 'instances': 34},
            ]},
            'BE': {'name': 'Berlin', 'cities': [
                {'name': 'Berlin', 'lat': 52.52, 'lng': 13.40, 'instances': 89},
            ]},
        }
    },
    'US': {
        'name': 'United States',
        'regions': {
            'CA': {'name': 'California', 'cities': [
                {'name': 'San Francisco', 'lat': 37.77, 'lng': -122.41, 'instances': 78},
                {'name': 'Los Angeles', 'lat': 34.05, 'lng': -118.24, 'instances': 92},
            ]},
            'NY': {'name': 'New York', 'cities': [
                {'name': 'New York City', 'lat': 40.71, 'lng': -74.01, 'instances': 156},
            ]},
            'WA': {'name': 'Washington', 'cities': [
                {'name': 'Seattle', 'lat': 47.61, 'lng': -122.33, 'instances': 45},
            ]},
        }
    },
    'GB': {
        'name': 'United Kingdom',
        'regions': {
            'EN': {'name': 'England', 'cities': [
                {'name': 'London', 'lat': 51.51, 'lng': -0.13, 'instances': 134},
                {'name': 'Manchester', 'lat': 53.48, 'lng': -2.24, 'instances': 67},
            ]},
            'SC': {'name': 'Scotland', 'cities': [
                {'name': 'Edinburgh', 'lat': 55.95, 'lng': -3.19, 'instances': 43},
            ]},
        }
    },
    'JP': {
        'name': 'Japan',
        'regions': {
            'TK': {'name': 'Tokyo', 'cities': [
                {'name': 'Tokyo', 'lat': 35.68, 'lng': 139.69, 'instances': 95},
            ]},
            'KN': {'name': 'Kanagawa', 'cities': [
                {'name': 'Yokohama', 'lat': 35.45, 'lng': 139.63, 'instances': 54},
            ]},
        }
    },
}


def seed_geo_data(db_dsn: str, count_per_city: int = 10) -> int:
    """Seed realistic test data into PostgreSQL.
    
    Args:
        db_dsn: PostgreSQL connection string
        count_per_city: Instances per city to generate
        
    Returns:
        Total number of rows inserted
    """
    total = 0
    
    for country_code, country_data in SAMPLE_DATA.items():
        for region_code, region_data in country_data['regions'].items():
            for city_data in region_data['cities']:
                city_name = city_data['name']
                base_instances = city_data.get('instances', count_per_city)
                
                # Create instances with slightly randomized timestamps
                for i in range(base_instances):
                    days_back = random.randint(0, 13)  # 0-13 days (fits Tier 3 TTL)
                    instance_id = f"{country_code}-{region_code}-{city_name}-{i}"
                    instance_hash = hashlib.sha256(instance_id.encode()).hexdigest()[:16]
                    
                    # Randomize coordinates slightly around city center
                    lat = city_data['lat'] + random.uniform(-0.05, 0.05)
                    lng = city_data['lng'] + random.uniform(-0.05, 0.05)
                    
                    created_date = (datetime.utcnow() - timedelta(days=days_back)).date()
                    
                    try:
                        geo_schema.insert_geo_ping(
                            dsn=db_dsn,
                            instance_id_hash=instance_hash,
                            country=country_code,
                            tier=3,
                            region=region_code,
                            city=city_name,
                            grid_lat=round(lat, 1),
                            grid_lng=round(lng, 1),
                        )
                        total += 1
                    except Exception as e:
                        logger.warning(f"Failed to insert: {e}")
    
    logger.info(f"✅ Seeded {total} test records")
    return total


if __name__ == "__main__":
    import os
    dsn = os.environ.get('DATABASE_URL', 'postgresql://localhost/corvinOS')
    seed_geo_data(dsn)
