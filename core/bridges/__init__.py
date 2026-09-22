"""Media distribution bridges for CorvinOS"""

from .base import BridgeBase
from .discord import DiscordBridge
from .console import ConsoleBridge

try:
    from .telegram import TelegramBridge
except ImportError:
    TelegramBridge = None

__all__ = [
    "BridgeBase",
    "DiscordBridge",
    "ConsoleBridge",
    "TelegramBridge",
]
