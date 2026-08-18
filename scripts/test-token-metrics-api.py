#!/usr/bin/env python3
"""Test Token Metrics API to verify data retrieval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.learning.event_emitter import EventEmitter
from core.learning.token_metrics_db import TokenMetricsDB
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.token_metrics_aggregator import TokenMetricsAggregator
from core.learning.token_baseline import ComparisonEngine


def test_api():
    """Test the complete token metrics API stack."""
    print("🧪 Testing Token Metrics API Stack\n")

    # Initialize components
    db_path = Path.home() / ".corvin" / "token_metrics.db"
    db = TokenMetricsDB(db_path)
    emitter = EventEmitter()
    store = TokenMetricsStore(emitter, db=db)
    comparison_engine = ComparisonEngine()
    aggregator = TokenMetricsAggregator(store, comparison_engine)

    print(f"✓ Initialized all components")
    print(f"✓ Database: {db_path}")
    print("")

    # Test with the "current" session (what the frontend expects)
    session_id = "current"

    print(f"📊 Querying session: {session_id}")
    print("")

    # Get dashboard data
    try:
        dashboard_data = aggregator.get_session_dashboard_data(session_id)
        print(f"✓ Dashboard data retrieved")
        print(f"  Summary:")
        print(f"    Turn count: {dashboard_data['summary']['turn_count']}")
        print(f"    Total tokens: {dashboard_data['summary']['total_tokens']:,}")
        print(f"    Baseline tokens: {dashboard_data['summary']['baseline_tokens']:,}")
        print(f"    Savings: {dashboard_data['summary']['savings_percent']:.1f}%")
        print("")
    except Exception as e:
        print(f"❌ Failed to get dashboard data: {e}")
        return False

    # Get detailed metrics
    try:
        metrics_list = aggregator.get_session_metrics(session_id)
        print(f"✓ Metrics list retrieved ({len(metrics_list)} turns)")
        if metrics_list:
            print(f"  First turn:")
            m = metrics_list[0]
            print(f"    Turn ID: {m.get('turn_id')}")
            print(f"    Tokens: {m.get('total_tokens')}")
            print(f"    Savings: {m.get('savings_percent'):.1f}%")
        print("")
    except Exception as e:
        print(f"❌ Failed to get metrics list: {e}")
        return False

    print("✅ All tests passed! API is working correctly.")
    return True


if __name__ == "__main__":
    success = test_api()
    sys.exit(0 if success else 1)
