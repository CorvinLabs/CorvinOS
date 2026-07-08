#!/usr/bin/env python3
"""test_provenance.py — locks the shared Art. 50 §4 marking contract so the
three delivery paths (adapter._envelope / completion_notify / scheduler) cannot
drift apart. Run: python3 operator/bridges/shared/test_provenance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provenance import build_provenance  # noqa: E402


def main() -> int:
    p = build_provenance("discord", "1501540900529246251", "coder")
    assert p["ai_generated"] is True
    assert p["generator_id"] == "corvin_os"
    assert p["persona"] == "coder"
    # session_id embeds the routing id as a string, never a lossy number.
    assert p["session_id"] == "discord:1501540900529246251"
    assert "1501540900529246251" in p["session_id"]
    assert isinstance(p["timestamp_utc"], str) and "T" in p["timestamp_utc"]
    # Exactly these five keys — a new/removed key is a contract change.
    assert set(p) == {"ai_generated", "generator_id", "persona",
                      "session_id", "timestamp_utc"}, sorted(p)
    # None chat_id degrades safely.
    assert build_provenance("web", None)["session_id"] == "web:"
    print("PASS: build_provenance contract (shape + snowflake-safe session_id)")
    print("\nALL PASSED (1/1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
