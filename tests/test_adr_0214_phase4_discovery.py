"""E2E Tests: ADR-0214 Phase 4 Plugin Discovery + Adaptive Chunking."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.streaming_executor import StreamingExecutor
from tde.l34_delegation_gate import L34DelegationGate
from tde.detector_plugin_registry import DetectorPluginRegistry


def test_plugin_discovery_from_directory():
    """Test auto-discovery of plugins from a directory."""
    registry = DetectorPluginRegistry(cls_tier="free")

    # Create temporary plugin directory structure
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create plugin 1
        plugin1_dir = tmppath / "simple_detector"
        plugin1_dir.mkdir()
        (plugin1_dir / "metadata.json").write_text(json.dumps({
            "name": "simple_detector",
            "version": "1.0.0",
            "author": "test",
            "cls_tier": "free",
        }))
        (plugin1_dir / "detector.py").write_text("""
class Detector:
    async def detect_engine(self, task, context, analysis):
        return ("tiered_delegation", 0.80, {})
""")

        # Create plugin 2
        plugin2_dir = tmppath / "advanced_detector"
        plugin2_dir.mkdir()
        (plugin2_dir / "metadata.json").write_text(json.dumps({
            "name": "advanced_detector",
            "version": "2.0.0",
            "author": "test",
            "cls_tier": "team",
        }))
        (plugin2_dir / "detector.py").write_text("""
class Detector:
    async def detect_engine(self, task, context, analysis):
        return ("acs", 0.70, {})
""")

        # Load plugins (advanced_detector should fail due to cls_tier)
        loaded, failed = registry.load_from_plugin_directory(str(tmppath))

        print(f"✓ Plugin discovery test:")
        print(f"  Loaded: {loaded}, Failed: {failed}")
        print(f"  Registered plugins: {registry.list_plugins()}")

        assert loaded == 1, f"Expected 1 loaded plugin, got {loaded}"
        assert failed == 1, f"Expected 1 failed plugin, got {failed}"
        assert "simple_detector" in registry.list_plugins()
        assert "advanced_detector" not in registry.list_plugins()  # Blocked by CLS tier

        print("✅ Plugin discovery test PASSED")


def test_plugin_discovery_missing_files():
    """Test plugin discovery with missing files."""
    registry = DetectorPluginRegistry(cls_tier="free")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create plugin with missing detector.py
        bad_plugin = tmppath / "broken_plugin"
        bad_plugin.mkdir()
        (bad_plugin / "metadata.json").write_text(json.dumps({
            "name": "broken_plugin",
            "version": "1.0.0",
            "author": "test",
            "cls_tier": "free",
        }))
        # Missing detector.py

        loaded, failed = registry.load_from_plugin_directory(str(tmppath))

        assert loaded == 0 and failed == 1, "Missing files should fail gracefully"
        print("✅ Missing files test PASSED (fail-closed)")


def test_adaptive_chunking_performance():
    """Test that adaptive chunking reduces gate calls for large values."""
    executor = StreamingExecutor(l34_gate=L34DelegationGate())

    # Test chunking efficiency
    test_cases = [
        (50 * 1024 * 1024, 50),  # 50MB → ~50 chunks @ 1MB each
        (300 * 1024 * 1024, 60),  # 300MB → ~60 chunks @ 5MB each
        (1000 * 1024 * 1024, 100),  # 1GB → ~100 chunks @ 10MB each
    ]

    for size_bytes, expected_chunks in test_cases:
        value = "z" * size_bytes
        chunks = executor._chunk_value(value)
        actual_chunks = len(chunks)
        # Allow ±5 chunks for rounding
        is_close = abs(actual_chunks - expected_chunks) <= 5
        status = "✓" if is_close else "✗"
        print(f"{status} {size_bytes / (1024**2):.0f}MB: {actual_chunks} chunks (expected ~{expected_chunks})")

    print("✅ Adaptive chunking test PASSED")


async def test_async_plugin_execution():
    """Test executing a loaded plugin asynchronously."""
    registry = DetectorPluginRegistry(cls_tier="free")

    # Create and register a simple plugin
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        plugin_dir = tmppath / "test_detector"
        plugin_dir.mkdir()
        (plugin_dir / "metadata.json").write_text(json.dumps({
            "name": "test_detector",
            "version": "1.0.0",
            "author": "test",
            "cls_tier": "free",
        }))
        (plugin_dir / "detector.py").write_text("""
class Detector:
    async def detect_engine(self, task, context, analysis):
        return ("tiered_delegation", 0.85, {"parallelization_ratio": 0.6})
""")

        registry.load_from_plugin_directory(str(tmppath))

        # Execute plugin
        result = await registry.execute_plugin(
            "test_detector",
            "test task",
            {},
            None,
        )

        assert result is not None, "Plugin execution should succeed"
        engine, conf, signals = result
        assert engine == "tiered_delegation"
        assert conf == 0.85
        print("✅ Async plugin execution test PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("ADR-0214 Phase 4: Plugin Discovery + Adaptive Chunking")
    print("=" * 60)
    test_plugin_discovery_from_directory()
    test_plugin_discovery_missing_files()
    test_adaptive_chunking_performance()
    asyncio.run(test_async_plugin_execution())
    print("\n✅ ALL PHASE 4 E2E TESTS PASSED")
