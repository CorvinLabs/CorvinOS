"""Scroll capture for the screenshot capturer: one numbered frame per scroll position.

Opt-in. Without a ScrollConfig (or with steps=0 and to_end=False) the capturer keeps
its single-frame behaviour, so existing storyboards produce the same output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional


class ScrollCaptureError(RuntimeError):
    """Scroll capture could not run (e.g. Playwright missing)."""


@dataclass(frozen=True)
class ScrollConfig:
    """How far to scroll after the first frame."""

    steps: int = 0                    # extra scroll positions after the first frame
    amount_px: Optional[int] = None   # px per step; None = one viewport height
    to_end: bool = False              # keep scrolling until the page bottom
    settle_ms: int = 500              # wait after each scroll before capturing
    max_frames: int = 20              # hard cap against endless pages

    @property
    def enabled(self) -> bool:
        return self.steps > 0 or self.to_end

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "ScrollConfig":
        """Read scroll_* keys (e.g. from storyboard metadata). Missing keys keep the defaults."""
        if not raw:
            return cls()
        defaults = cls()
        return cls(
            steps=int(raw.get("scroll_steps", defaults.steps)),
            amount_px=(int(raw["scroll_amount_px"]) if raw.get("scroll_amount_px") else None),
            to_end=bool(raw.get("scroll_to_end", defaults.to_end)),
            settle_ms=int(raw.get("scroll_settle_ms", defaults.settle_ms)),
            max_frames=int(raw.get("scroll_max_frames", defaults.max_frames)),
        )


def plan_scroll_offsets(
    scroll_height: int,
    viewport_height: int,
    config: ScrollConfig,
) -> list[int]:
    """Target scroll offsets in px; the first frame is always at 0.

    Offsets are clamped to the page end (scroll_height - viewport_height). Once an offset
    reaches that end, planning stops, so the bottom is never captured twice.
    """
    if not config.enabled:
        return [0]
    max_offset = max(0, scroll_height - viewport_height)
    step = config.amount_px or viewport_height
    if step <= 0:
        raise ValueError("scroll step must be positive")
    if config.to_end:
        count = math.ceil(max_offset / step) + 1
    else:
        count = config.steps + 1
    count = min(count, config.max_frames)

    offsets: list[int] = []
    for index in range(count):
        target = min(index * step, max_offset)
        if offsets and target == offsets[-1]:
            break  # already at the page end: a further frame would be a duplicate
        offsets.append(target)
    return offsets


async def capture_scroll_frames(page: Any, config: ScrollConfig) -> list[tuple[int, bytes]]:
    """Scroll a Playwright page top-down and return (scroll_y, png) per distinct position.

    Duplicates are dropped in two ways: a planned offset is skipped once the page end was
    reached, and a frame is dropped when the real scroll position did not move (the page
    clamped the scroll, or lazy loading shifted the bottom). The scroll height is re-read
    after every scroll, so a page that grows while scrolling still ends cleanly.
    """
    metrics = await page.evaluate(
        "() => ({height: document.documentElement.scrollHeight, viewport: window.innerHeight})"
    )
    offsets = plan_scroll_offsets(metrics["height"], metrics["viewport"], config)

    frames: list[tuple[int, bytes]] = []
    last_y: Optional[int] = None
    for target in offsets:
        await page.evaluate("y => window.scrollTo(0, y)", target)
        await page.wait_for_timeout(config.settle_ms)
        position = await page.evaluate(
            "() => ({y: Math.round(window.scrollY), height: document.documentElement.scrollHeight,"
            " viewport: window.innerHeight})"
        )
        if position["y"] == last_y:
            break  # the page did not move: this frame would repeat the previous one
        frames.append((position["y"], await page.screenshot(type="png")))
        last_y = position["y"]
        if position["y"] + position["viewport"] >= position["height"]:
            break  # bottom reached by the real scroll height
        if len(frames) >= config.max_frames:
            break
    return frames
