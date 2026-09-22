#!/usr/bin/env python3
"""Demo script for media system activation

This script demonstrates:
1. Uploading media files to the media manager
2. Listing media with filters
3. Sending media to Discord
4. Streaming media via console
5. Managing media lifecycle
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add repo to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from core.media.manager import MediaManager
from core.media.models import MediaFile
from core.bridges.discord import DiscordBridge
from core.bridges.console import ConsoleBridge


def print_header(title):
    """Print a formatted header"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_media_info(media: MediaFile):
    """Pretty print media information"""
    print(f"  📄 ID:           {media.media_id}")
    print(f"  📝 Name:         {media.original_name}")
    print(f"  📊 Size:         {media.size_mb():.1f} MB")
    print(f"  🎬 MIME Type:    {media.mime_type}")
    print(f"  ⏱️  Created:      {media.created_at}")
    if media.duration_seconds:
        print(f"  ⏳ Duration:     {media.duration_seconds}s")
    if media.width and media.height:
        print(f"  📐 Resolution:   {media.width}x{media.height}")
    print(f"  🏷️  Tags:        {', '.join(media.tags) or '(none)'}")
    print(f"  📌 Description:  {media.description or '(none)'}")
    print(f"  🌐 Public:       {'Yes' if media.is_public else 'No'}")
    print(f"  🔗 Bridges:      {', '.join(media.bridge_ids.keys()) if media.bridge_ids else 'None'}")
    print()


def demo_upload():
    """Demo: Upload media files"""
    print_header("DEMO 1: Upload Media Files")

    manager = MediaManager()

    # Create demo video file
    demo_video = Path.home() / ".corvin" / "demo_video.mp4"
    if not demo_video.exists():
        print(f"📝 Creating demo video file: {demo_video}")
        demo_video.parent.mkdir(parents=True, exist_ok=True)
        # Create a minimal MP4-like file (fake, just for demo)
        with open(demo_video, "wb") as f:
            f.write(b"fake mp4 content" * 10000)  # ~160KB

    print(f"📤 Uploading: {demo_video.name}")
    media = manager.upload(
        file_path=str(demo_video),
        tags=["demo", "corvinOS"],
        description="CorvinOS Video System Demo",
        is_public=True
    )

    print("✅ Upload successful!")
    print_media_info(media)

    return media


def demo_list(media: MediaFile):
    """Demo: List media files"""
    print_header("DEMO 2: List & Filter Media")

    manager = MediaManager()

    # List all
    all_media = manager.list()
    print(f"📋 Total media files: {len(all_media)}")
    for m in all_media:
        print(f"  • {m.original_name} ({m.size_mb():.1f}MB) - {', '.join(m.tags)}")
    print()

    # Filter by tag
    demo_media = manager.list(tags=["demo"])
    print(f"🔍 Filtered by 'demo' tag: {len(demo_media)} file(s)")
    for m in demo_media:
        print(f"  • {m.original_name}")
    print()


def demo_console_bridge(media: MediaFile):
    """Demo: Register media for console display"""
    print_header("DEMO 3: Console Bridge (Web Display)")

    bridge = ConsoleBridge()

    print(f"🌐 Registering media for console display...")
    result = bridge.send(media, {})

    if result["status"] == "registered":
        print("✅ Registration successful!")
        print(f"  Display URL: {result['url']}")
        print(f"  MIME Type:   {result['type']}")
        print(f"  Size:        {result['size_mb']}MB")
    else:
        print(f"❌ Registration failed: {result.get('error')}")


def demo_discord_bridge(media: MediaFile):
    """Demo: Test Discord integration (without real token)"""
    print_header("DEMO 4: Discord Bridge (Real Bot Integration)")

    bridge = DiscordBridge()

    print(f"🤖 Testing Discord bridge configuration...")

    if not bridge.can_send():
        print("⚠️  Discord bridge not configured (missing DISCORD_BOT_TOKEN)")
        print("   To enable: export DISCORD_BOT_TOKEN='your_bot_token'")
        print()
        print("   To test without a real token, would send:")
    else:
        print("✅ Discord bot is configured!")

    config = {
        "channel_id": "123456789",
        "message": "Check out this CorvinOS demo! 🎬"
    }

    print(f"\n  Channel ID: {config['channel_id']}")
    print(f"  Message:   {config['message']}")
    print(f"  File:      {media.original_name}")
    print(f"  Size:      {media.size_mb():.1f}MB")
    print()

    # Test (will succeed or fail based on token)
    result = bridge.send(media, config)

    if result["status"] in ["sent", "pending"]:
        print("✅ Send successful!")
        print(f"  Status: {result.get('status')}")
        print(f"  URL:    {result.get('url')}")
    else:
        print(f"ℹ️  Send result: {result.get('status')}")
        if "error" in result:
            print(f"   Reason: {result['error']}")


def demo_metadata():
    """Demo: Extract and display media metadata"""
    print_header("DEMO 5: Media Metadata")

    manager = MediaManager()
    all_media = manager.list()

    if not all_media:
        print("No media files found to inspect")
        return

    media = all_media[0]
    print(f"📊 Metadata for: {media.original_name}\n")
    print_media_info(media)


def demo_cleanup():
    """Demo: Cleanup expired media"""
    print_header("DEMO 6: Cleanup & Expiration")

    manager = MediaManager()

    print("🧹 Scanning for expired media...")
    deleted = manager.cleanup_expired()

    print(f"✅ Cleanup complete")
    print(f"  Deleted: {deleted} file(s)")
    print()

    remaining = manager.list()
    print(f"  Remaining: {len(remaining)} file(s)")


def demo_rest_api():
    """Demo: Show REST API endpoints"""
    print_header("DEMO 7: REST API Endpoints")

    print("""
📡 Media System REST API Endpoints
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POST   /v1/console/media/upload
       Upload a media file

       Request:
         - file: multipart file (required)
         - tags: ["tag1", "tag2"] (optional)
         - description: "text" (optional)
         - is_public: true/false (optional)

       Response: 201 Created
         {
           "status": "uploaded",
           "media": {
             "media_id": "media_abc123...",
             "filename": "video.mp4",
             "size_mb": 12.5,
             "url": "/v1/console/media/media_abc123..."
           }
         }

GET    /v1/console/media/list
       List all media files

       Query Parameters:
         - tags: ["tag1", "tag2"] (optional)

       Response: 200 OK
         {
           "total": 5,
           "media": [ ... ]
         }

GET    /v1/console/media/{media_id}
       Stream media file for playback

       Response: 200 OK (video/audio/image stream)

GET    /v1/console/media/{media_id}/metadata
       Get media metadata

       Response: 200 OK
         {
           "media_id": "...",
           "filename": "...",
           "size_mb": 12.5,
           "duration_seconds": 120,
           "width": 1920,
           "height": 1080,
           ...
         }

POST   /v1/console/media/send-to-bridge
       Send media to Discord/Telegram/etc

       Request:
         {
           "media_id": "media_abc123...",
           "bridge": "discord",
           "config": {
             "channel_id": "123456789",
             "message": "Check this out!"
           }
         }

       Response: 202 Accepted
         {
           "status": "sent",
           "bridge": "discord",
           "result": { ... }
         }

DELETE /v1/console/media/{media_id}
       Delete a media file

       Response: 200 OK
         {
           "status": "deleted",
           "media_id": "..."
         }

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔗 Supported Bridges:
   • discord:  Send to Discord channels (requires DISCORD_BOT_TOKEN)
   • console:  Register for web display (always available)
   • telegram: Send to Telegram chats (optional, requires token)

""")


def main():
    """Run all demos"""
    print("\n" + "="*60)
    print("  🎬 CorvinOS Media System — Activation Demo")
    print("="*60)
    print(f"\n  Time: {datetime.now().isoformat()}")
    print(f"  Media Home: {Path.home() / '.corvin' / 'media'}")

    try:
        # Run demos in sequence
        media = demo_upload()
        demo_list(media)
        demo_console_bridge(media)
        demo_discord_bridge(media)
        demo_metadata()
        demo_cleanup()
        demo_rest_api()

        # Summary
        print_header("✅ Demo Complete")
        print("""
The Media System is now active and ready to use!

Next steps:
1. Run the test suite: pytest tests/e2e/test_media_system_e2e.py -v
2. Start the console: corvin-serve
3. Navigate to http://localhost:8765/console/media
4. Upload a video and send it to Discord

For more info, see:
  • docs/MEDIA_SYSTEM_ACTIVATION.md
  • docs/MEDIA_BRIDGES.md
  • core/media/manager.py (API reference)
""")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
