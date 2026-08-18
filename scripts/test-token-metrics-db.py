#!/usr/bin/env python3
"""Direct test of Token Metrics Database."""

import sqlite3
from pathlib import Path
from datetime import datetime


def test_db():
    """Test database directly."""
    print("🧪 Testing Token Metrics Database\n")

    db_path = Path.home() / ".corvin" / "token_metrics.db"

    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        return False

    print(f"✓ Database exists at {db_path}\n")

    # Connect and query
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get count
        cursor.execute("SELECT COUNT(*) FROM token_metrics")
        count = cursor.fetchone()[0]
        print(f"📊 Database Statistics:")
        print(f"   Total records: {count}")

        if count == 0:
            print(f"   ⚠️ No data found!")
            conn.close()
            return False

        # Get summary for "current" session
        cursor.execute("""
            SELECT
                session_id,
                COUNT(*) as turn_count,
                SUM(total_tokens) as total_tokens,
                SUM(baseline_tokens) as baseline_tokens,
                AVG(savings_percent) as avg_savings
            FROM token_metrics
            WHERE session_id = 'current'
            GROUP BY session_id
        """)
        row = cursor.fetchone()

        if row:
            session_id, turns, total_tokens, baseline_tokens, avg_savings = row
            print(f"\n✓ Session 'current' metrics:")
            print(f"   Turns: {turns}")
            print(f"   Total tokens: {total_tokens:,}")
            print(f"   Baseline tokens: {baseline_tokens:,}")
            print(f"   Avg savings: {avg_savings:.1f}%")

            # Show sample turn
            cursor.execute("""
                SELECT turn_id, total_tokens, savings_percent, task_type
                FROM token_metrics
                WHERE session_id = 'current'
                LIMIT 1
            """)
            sample = cursor.fetchone()
            if sample:
                turn_id, tokens, savings, task_type = sample
                print(f"\n✓ Sample turn:")
                print(f"   Turn ID: {turn_id}")
                print(f"   Tokens: {tokens}")
                print(f"   Savings: {savings:.1f}%")
                print(f"   Type: {task_type}")
        else:
            print(f"   ⚠️ No data for session 'current'")
            return False

        conn.close()
        print(f"\n✅ Database test passed!")
        return True

    except Exception as e:
        print(f"❌ Database test failed: {e}")
        return False


if __name__ == "__main__":
    import sys
    success = test_db()
    sys.exit(0 if success else 1)
