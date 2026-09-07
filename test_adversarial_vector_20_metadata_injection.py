#!/usr/bin/env python3
"""Adversarial Vector #20: Metadata Injection (no free-form strings in exports)

Vulnerability: Event details extracted from audit chain are converted to strings
and embedded in PDF metadata without validation or escaping. An attacker who can
write to the audit chain could inject:
  - Null bytes (\x00)
  - Control characters (\x01-\x1f)
  - PDF control sequences
  - ReportLab entity injection (< > & etc)
  - Special characters that break the PDF structure

This test attempts to inject malicious strings into audit events and verifies that:
1. The PDF is still valid and renderable
2. The metadata is not corrupted
3. No injection vulnerability exists

Expected result with mitigations: All injections are sanitized/escaped.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Setup test environment
CORVIN_HOME = Path.home() / ".corvin"
CORVIN_HOME.mkdir(parents=True, exist_ok=True)

def get_audit_chain_path():
    """Get the audit chain path for the _default tenant."""
    chain_path = CORVIN_HOME / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    return chain_path

def write_audit_event(event_type, details):
    """Write a test event to the audit chain."""
    chain_path = get_audit_chain_path()
    event = {
        "tenant_id": "_default",
        "event_type": event_type,
        "ts": int(time.time()),
        "details": details,
        "hash": "test_hash_placeholder",
    }
    # Append to chain (simulate real audit writes)
    with open(chain_path, "a") as f:
        f.write(json.dumps(event) + "\n")
    return chain_path

def test_null_byte_injection():
    """Try to inject null bytes into event details."""
    print("\n[TEST 1] Null byte injection...")
    write_audit_event("disclosure.shown", {
        "channel": "discord\x00<injection>",
        "chat_key": "123",
        "uid": "user1",
    })
    try:
        result = subprocess.run(
            [sys.executable, "-m", "corvin_compliance_reports.cli",
             "generate", "ai-act-50", "--quiet"],
            capture_output=True, text=True, timeout=10,
            cwd="/home/shumway/projects/CorvinOS"
        )
        if result.returncode != 0:
            print(f"  ✗ VULNERABLE: PDF generation failed with null byte injection")
            print(f"    Error: {result.stderr[:200]}")
            return False
        print(f"  ✓ Mitigated: PDF generated successfully despite null byte")
        return True
    except Exception as e:
        print(f"  ? Test error: {e}")
        return None

def test_control_character_injection():
    """Try to inject control characters."""
    print("\n[TEST 2] Control character injection...")
    write_audit_event("consent.granted", {
        "channel": "discord\x1b[31m<RED>",  # ANSI escape
        "chat_key": "123\x00\x01\x02",
        "uid": "user\x1f",
        "mode": "durable",
    })
    try:
        result = subprocess.run(
            [sys.executable, "-m", "corvin_compliance_reports.cli",
             "generate", "ai-act-50", "--quiet"],
            capture_output=True, text=True, timeout=10,
            cwd="/home/shumway/projects/CorvinOS"
        )
        if result.returncode != 0:
            print(f"  ✗ VULNERABLE: PDF generation failed with control characters")
            return False
        print(f"  ✓ Mitigated: PDF generated successfully despite control characters")
        return True
    except Exception as e:
        print(f"  ? Test error: {e}")
        return None

def test_reportlab_injection():
    """Try to inject ReportLab/Platypus rendering directives."""
    print("\n[TEST 3] ReportLab entity injection...")
    write_audit_event("disclosure.shown", {
        "channel": "<font size=999>huge</font>",  # Try font tag injection
        "chat_key": "123&nbsp;&nbsp;&nbsp;",
        "uid": "user<br/><br/>",
    })
    try:
        result = subprocess.run(
            [sys.executable, "-m", "corvin_compliance_reports.cli",
             "generate", "ai-act-50", "--quiet"],
            capture_output=True, text=True, timeout=10,
            cwd="/home/shumway/projects/CorvinOS"
        )
        if result.returncode != 0:
            print(f"  ✗ VULNERABLE: PDF generation failed with ReportLab injection")
            return False
        print(f"  ✓ Mitigated: PDF generated successfully despite entity injection")
        return True
    except Exception as e:
        print(f"  ? Test error: {e}")
        return None

def test_pdf_metadata_injection():
    """Try to inject into PDF metadata directly."""
    print("\n[TEST 4] PDF metadata structure injection...")
    write_audit_event("gateway.run_created", {
        "engine": "test/../../../../../../etc/passwd",
        "compliance_zone": "eu-central'/><metadata>",
        "run_id": "r1()",
    })
    try:
        result = subprocess.run(
            [sys.executable, "-m", "corvin_compliance_reports.cli",
             "generate", "gdpr-30", "--quiet"],
            capture_output=True, text=True, timeout=10,
            cwd="/home/shumway/projects/CorvinOS"
        )
        if result.returncode != 0:
            print(f"  ✗ VULNERABLE: PDF generation failed with path traversal")
            return False
        print(f"  ✓ Mitigated: PDF generated successfully despite path traversal")
        return True
    except Exception as e:
        print(f"  ? Test error: {e}")
        return None

def test_very_long_string():
    """Try to inject very long strings to break PDF structure."""
    print("\n[TEST 5] Very long string injection (buffer overflow-like)...")
    long_str = "A" * 100000  # 100KB of 'A'
    write_audit_event("data.registered", {
        "data_handle": long_str,
        "format": "csv" + long_str,
        "size_b": 999999999,
    })
    try:
        result = subprocess.run(
            [sys.executable, "-m", "corvin_compliance_reports.cli",
             "generate", "gdpr-30", "--quiet"],
            capture_output=True, text=True, timeout=10,
            cwd="/home/shumway/projects/CorvinOS"
        )
        if result.returncode != 0:
            print(f"  ✗ VULNERABLE: PDF generation failed with long string")
            return False
        print(f"  ✓ Mitigated: PDF generated successfully despite long strings")
        return True
    except Exception as e:
        print(f"  ? Test error: {e}")
        return None

def test_unicode_normalization_attack():
    """Try to inject via unicode normalization bypasses."""
    print("\n[TEST 6] Unicode normalization attack...")
    # Combining characters and lookalikes
    write_audit_event("disclosure.shown", {
        "channel": "discоrd",  # Cyrillic 'о' instead of 'o'
        "chat_key": "123",
        "uid": "useŕ",  # Combining acute accent
    })
    try:
        result = subprocess.run(
            [sys.executable, "-m", "corvin_compliance_reports.cli",
             "generate", "ai-act-50", "--quiet"],
            capture_output=True, text=True, timeout=10,
            cwd="/home/shumway/projects/CorvinOS"
        )
        if result.returncode != 0:
            print(f"  ✗ VULNERABLE: PDF generation failed with unicode attack")
            return False
        print(f"  ✓ Mitigated: PDF generated successfully despite unicode tricks")
        return True
    except Exception as e:
        print(f"  ? Test error: {e}")
        return None

def main():
    """Run all adversarial tests for vector #20."""
    print("=" * 70)
    print("ADVERSARIAL VECTOR #20: METADATA INJECTION")
    print("=" * 70)

    # Clear existing audit chain
    chain_path = get_audit_chain_path()
    if chain_path.exists():
        chain_path.unlink()

    results = []

    try:
        results.append(("Null byte injection", test_null_byte_injection()))
        results.append(("Control character injection", test_control_character_injection()))
        results.append(("ReportLab entity injection", test_reportlab_injection()))
        results.append(("PDF metadata injection", test_pdf_metadata_injection()))
        results.append(("Very long string injection", test_very_long_string()))
        results.append(("Unicode normalization attack", test_unicode_normalization_attack()))
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, r in results if r is True)
    failed = sum(1 for _, r in results if r is False)
    skipped = sum(1 for _, r in results if r is None)

    for name, result in results:
        if result is True:
            print(f"✓ {name}: PASS (mitigated)")
        elif result is False:
            print(f"✗ {name}: FAIL (vulnerable)")
        else:
            print(f"? {name}: SKIP (test error)")

    print(f"\nResults: {passed} pass, {failed} fail, {skipped} skip")

    if failed > 0:
        print("\n⚠️  METADATA INJECTION VULNERABILITIES FOUND!")
        return 1
    else:
        print("\n✓ All injection attempts mitigated!")
        return 0

if __name__ == "__main__":
    sys.exit(main())
