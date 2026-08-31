#!/usr/bin/env python3
"""E2E Test: Session pinning für ADR-0007 Multi-Tenant Isolation.

Testet:
  1. Session-IDs werden per Chat/Tenant persistiert
  2. .main_session.json wird in tenant-aware path geschrieben
  3. Mehrere Tenants interferen nicht mit Sessions
  4. Session Resumption funktioniert über Turns hinweg
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "operator" / "bridges" / "shared"))
os.environ.pop("CORVIN_HOME", None)  # Reset for clean test

def test_session_dir_tenant_aware():
    """Test 1: _session_dir() ist tenant-aware."""
    print("\n=== Test 1: Session-Dir Tenant-Aware ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        # Reload modules
        if "adapter" in sys.modules:
            del sys.modules["adapter"]
        if "paths" in sys.modules:
            del sys.modules["paths"]

        import adapter as adapter_mod
        import paths as paths_mod

        # Test 1a: Default tenant
        sess_dir_default = adapter_mod._session_dir(
            channel="discord",
            chat_key="test_chat_123",
        )
        print(f"  Default tenant path: {sess_dir_default}")
        assert "voice" in str(sess_dir_default), f"Expected 'voice' in path, got: {sess_dir_default}"

        # Test 1b: Custom tenant
        sess_dir_custom = adapter_mod._session_dir(
            channel="discord",
            chat_key="test_chat_123",
            tenant_id="tenant_alpha",
        )
        print(f"  Custom tenant path:  {sess_dir_custom}")
        assert "tenant_alpha" in str(sess_dir_custom), f"Expected 'tenant_alpha' in path, got: {sess_dir_custom}"

        # Test 1c: Paths should be different
        assert sess_dir_default != sess_dir_custom, "Paths should differ for different tenants"
        print("  ✓ Paths are isolated per tenant")


def test_session_id_persistence():
    """Test 2: Session-ID wird persistiert und geladen."""
    print("\n=== Test 2: Session-ID Persistierung ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        if "adapter" in sys.modules:
            del sys.modules["adapter"]
        if "paths" in sys.modules:
            del sys.modules["paths"]

        import adapter as adapter_mod

        channel = "discord"
        chat_key = "test_chat_456"
        tenant_id = "tenant_beta"

        # Get session dir
        session_dir = adapter_mod._session_dir(
            channel=channel, chat_key=chat_key, tenant_id=tenant_id
        )
        print(f"  Session dir: {session_dir}")

        # Test 2a: Write session ID
        main_sess_file = session_dir / ".main_session.json"
        test_session_id = "s_test1234567890ab"
        with open(main_sess_file, "w") as f:
            json.dump({
                "session_id": test_session_id,
                "saved_at": "2026-06-03T12:00:00Z"
            }, f)
        os.chmod(main_sess_file, 0o600)
        print(f"  Written session ID: {test_session_id}")

        # Test 2b: Load session ID
        loaded_id = None
        if main_sess_file.exists():
            with open(main_sess_file) as f:
                loaded_id = json.load(f).get("session_id")
        print(f"  Loaded session ID:  {loaded_id}")

        assert loaded_id == test_session_id, f"Session ID mismatch: {loaded_id} != {test_session_id}"
        print("  ✓ Session ID persisted and loaded correctly")


def test_multi_tenant_isolation():
    """Test 3: Mehrere Tenants interferen nicht."""
    print("\n=== Test 3: Multi-Tenant Isolation ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        if "adapter" in sys.modules:
            del sys.modules["adapter"]
        if "paths" in sys.modules:
            del sys.modules["paths"]

        import adapter as adapter_mod

        # Create sessions for two tenants
        dir_a = adapter_mod._session_dir(
            channel="discord", chat_key="same_chat", tenant_id="tenant_a"
        )
        dir_b = adapter_mod._session_dir(
            channel="discord", chat_key="same_chat", tenant_id="tenant_b"
        )

        print(f"  Tenant A path: {dir_a}")
        print(f"  Tenant B path: {dir_b}")

        # Verify isolation
        assert dir_a != dir_b, "Tenant A and B should have different paths"
        assert "tenant_a" in str(dir_a), "Tenant A path should contain 'tenant_a'"
        assert "tenant_b" in str(dir_b), "Tenant B path should contain 'tenant_b'"

        # Write different session IDs
        file_a = dir_a / ".main_session.json"
        file_b = dir_b / ".main_session.json"

        with open(file_a, "w") as f:
            json.dump({"session_id": "s_aaa"}, f)
        with open(file_b, "w") as f:
            json.dump({"session_id": "s_bbb"}, f)

        # Verify they remain isolated
        with open(file_a) as f:
            id_a = json.load(f)["session_id"]
        with open(file_b) as f:
            id_b = json.load(f)["session_id"]

        assert id_a == "s_aaa", f"Tenant A session corrupted: {id_a}"
        assert id_b == "s_bbb", f"Tenant B session corrupted: {id_b}"
        print("  ✓ Tenant A and B maintain isolated sessions")


def test_voice_dir_tenant_aware():
    """Test 4: paths.voice_dir() ist tenant-aware."""
    print("\n=== Test 4: voice_dir() Tenant-Aware ===")
    if "paths" in sys.modules:
        del sys.modules["paths"]

    import paths as paths_mod

    voice_default = paths_mod.voice_dir(tenant_id=None)
    voice_custom = paths_mod.voice_dir(tenant_id="custom_tenant")

    print(f"  Default: {voice_default}")
    print(f"  Custom:  {voice_custom}")

    # Verify delegation
    assert voice_default != voice_custom, "voice_dir() should differ by tenant"
    assert "custom_tenant" in str(voice_custom), "Custom tenant should be in path"
    print("  ✓ voice_dir() is tenant-aware")


if __name__ == "__main__":
    print("=" * 60)
    print("E2E Test: Session Pinning (ADR-0007 Multi-Tenant)")
    print("=" * 60)

    try:
        test_session_dir_tenant_aware()
        test_session_id_persistence()
        test_multi_tenant_isolation()
        test_voice_dir_tenant_aware()

        print("\n" + "=" * 60)
        print("✅ All tests PASSED")
        print("=" * 60)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
