"""Video Producer + Marketplace Hub Live Tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills" / "os_skills"))

from video_producer.skill import VideoProducerSkill, Scene
from marketplace_hub.skill import MarketplaceHubSkill, MarketplaceItem, ItemType


def test_video_producer():
    """Video Producer basic test."""
    vp = VideoProducerSkill()
    assert len(vp.scenes) == 0

    vp.add_scene("Welcome to video.", 5)
    vp.add_scene("Content here.", 10)
    assert len(vp.scenes) == 2

    result = vp.generate("test_video", "/tmp/test.mp4")
    assert result

    meta = vp.get_metadata()
    assert meta["scenes"] == 2
    assert meta["total_duration_s"] == 15

    print("✅ Video Producer: basic generation works")


def test_marketplace_hub():
    """Marketplace Hub basic test."""
    hub = MarketplaceHubSkill()
    assert len(hub.items) == 0

    item1 = MarketplaceItem("test_plugin", ItemType.PLUGIN, "1.0.0", "testing")
    item2 = MarketplaceItem("test_skill", ItemType.SKILL, "1.0.0", "learning")

    assert hub.register_item(item1)
    assert hub.register_item(item2)
    assert not hub.register_item(item1)  # Duplicate
    assert len(hub.items) == 2

    # Search
    results = hub.search("test")
    assert len(results) == 2

    results = hub.search("plugin")
    assert len(results) == 1

    # List by type
    plugins = hub.list_by_type(ItemType.PLUGIN)
    assert len(plugins) == 1

    # Index
    index = hub.get_index()
    assert index["total_items"] == 2
    assert index["by_type"]["plugin"] == 1
    assert index["by_type"]["skill"] == 1

    print("✅ Marketplace Hub: basic discovery works")


def test_both_live():
    """E2E: Both skills."""
    print("\n" + "="*70)
    print("LIVE E2E TEST: Video Producer + Marketplace Hub")
    print("="*70 + "\n")

    # Video Producer
    vp = VideoProducerSkill()
    vp.add_scene("Intro", 3)
    vp.add_scene("Body", 10)
    vp.add_scene("Outro", 2)
    assert vp.generate("production", "/tmp/prod.mp4")
    print("✅ Video Producer: 3-scene video generated")

    # Marketplace
    hub = MarketplaceHubSkill()
    hub.register_item(MarketplaceItem("video_producer", ItemType.SKILL, "2.0.0", "video"))
    hub.register_item(MarketplaceItem("marketplace_hub", ItemType.SKILL, "1.0.0", "discovery"))
    print("✅ Marketplace Hub: 2 items registered")

    # Search
    found = hub.search("producer")
    assert len(found) == 1
    print("✅ Marketplace: search works")

    # Index
    idx = hub.get_index()
    print(f"✅ Marketplace: {idx['total_items']} items indexed")

    print("\n" + "="*70)
    print("✅ ALL LIVE TESTS PASSED")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_video_producer()
    test_marketplace_hub()
    test_both_live()
