"""CorvinOS Browser Automation Layer (ADR-0182).

Agent-driven browser with Set-of-Marks perception, a compliant action surface,
and a live user view. Public API:

    from corvin_console.browser import BrowserSession, BrowserSessionManager
    from corvin_console.browser.tools import BROWSER_TOOLS

The console exposes these over REST (`routes/browser.py`) — which doubles as the
tool surface an engine calls and the live-view backend the user watches.
"""
from __future__ import annotations

from .compliance import EgressDecision, check_egress, is_sensitive
from .manager import BrowserSessionManager
from .marks import Mark, Observation
from .session import BrowserActionError, BrowserSession

__all__ = [
    "BrowserSession",
    "BrowserSessionManager",
    "BrowserActionError",
    "Observation",
    "Mark",
    "EgressDecision",
    "check_egress",
    "is_sensitive",
]
