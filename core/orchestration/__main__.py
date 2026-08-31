"""CorvinOS Brain entry point.

Run with: python -m core.orchestration
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .config import BrainConfigLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point for CorvinOS Brain."""
    logger.info("=" * 60)
    logger.info("🧠 CorvinOS Brain v0.2 Starting")
    logger.info("=" * 60)

    try:
        # Load brain from config
        logger.info("Loading Brain configuration...")
        brain = BrainConfigLoader.load_brain()

        logger.info(f"✓ Brain loaded with {len(brain.hub.subsystems)} subsystems")
        for name in brain.hub.subsystems:
            logger.info(f"  ✓ {name}")

        # Setup signal handlers
        shutdown_event = asyncio.Event()

        def handle_signal(sig, frame):
            logger.info(f"Received signal {sig}, shutting down...")
            shutdown_event.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        # Run Brain
        logger.info("Starting orchestration loop...")
        logger.info("=" * 60)

        # Create task for brain and shutdown event
        brain_task = asyncio.create_task(brain.run_forever())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        # Wait for either brain to crash or shutdown signal
        done, pending = await asyncio.wait(
            [brain_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel pending tasks
        for task in pending:
            task.cancel()

        logger.info("=" * 60)
        logger.info("Shutting down Brain...")
        await brain.shutdown()
        logger.info("✓ Brain shutdown complete")
        logger.info("=" * 60)

    except FileNotFoundError as e:
        logger.error(f"❌ Config not found: {e}")
        logger.error("Create config with: corvin-brain config init")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Brain crashed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
