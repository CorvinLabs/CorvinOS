#!/usr/bin/env python3
"""
Generate Marketplace Index for ADR-0511
Produces JSON index of all buildin + community plugins
"""

import json
import os
from pathlib import Path
from datetime import datetime

def generate_marketplace_index():
    """Generate marketplace/index.json with all plugins"""
    
    plugins = [
        # Buildin Plugins (1)
        {
            "id": "observability",
            "name": "Observability",
            "category": "Observability",
            "version": "1.0.0",
            "author": "CorvinOS Core",
            "description": "Metrics, logs, tracing, dashboards",
            "tier": "buildin",
            "boot_layer": "bundled",
            "rating": 4.6,
            "installs": 2789,
        },
        # Community Plugins are now managed via Corvin-Marketplace only
    ]
    
    index = {
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "total_plugins": len(plugins),
        "categories": {
            "Observability": 1,
        },
        "stats": {
            "buildin_plugins": 1,
            "community_plugins": 0,
            "total_installs": sum(p.get("installs", 0) for p in plugins),
            "avg_rating": round(sum(p.get("rating", 0) for p in plugins) / len(plugins), 2),
        },
        "plugins": plugins,
    }
    
    return index

def main():
    index = generate_marketplace_index()
    
    # Create output directory
    output_dir = Path("/home/shumway/projects/CorvinOS/outputs/marketplace")
    output_dir.mkdir(exist_ok=True)
    
    # Write index.json
    index_file = output_dir / "index.json"
    with open(index_file, "w") as f:
        json.dump(index, f, indent=2)
    
    print(f"✅ Generated: {index_file}")
    print(f"   Total plugins: {index['stats']['total_plugins']}")
    print(f"   Total installs: {index['stats']['total_installs']}")
    print(f"   Avg rating: {index['stats']['avg_rating']}★")
    
    return index

if __name__ == "__main__":
    main()
