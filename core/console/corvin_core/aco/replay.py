"""ACO Layer 2 — Chat Replay Engine.

Records a sequence of turn expectations as a replay manifest, then replays
them against a live chat session via the WebSocket API, asserting that the
observed chat_debug.jsonl event sequence matches expectations.

Replay manifests are JSON files with this schema:

    {
      "version": 1,
      "scenario": "string",
      "description": "string",
      "turns": [
        {
          "input": "user message text",
          "expect_events": ["turn.start", "turn.done"],
          "expect_fields": {"event": "delegation.decision", "will_delegate": true},
          "max_elapsed_ms": 60000,
          "tags": ["delegation", "acs"]
        }
      ]
    }

``expect_events`` checks that all listed event types appear after the turn starts.
``expect_fields`` checks for a specific event with all listed field values present.
``max_elapsed_ms`` fails the turn if turn.done arrives later than the threshold.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Manifest types ────────────────────────────────────────────────────────────

@dataclass
class TurnExpectation:
    input: str
    expect_events: list[str] = field(default_factory=list)
    expect_fields: dict[str, Any] = field(default_factory=dict)
    max_elapsed_ms: int = 60_000
    tags: list[str] = field(default_factory=list)


@dataclass
class ReplayManifest:
    version: int
    scenario: str
    description: str
    turns: list[TurnExpectation]

    @classmethod
    def from_file(cls, path: Path | str) -> "ReplayManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "ReplayManifest":
        turns = [
            TurnExpectation(
                input=t["input"],
                expect_events=t.get("expect_events", []),
                expect_fields=t.get("expect_fields", {}),
                max_elapsed_ms=t.get("max_elapsed_ms", 60_000),
                tags=t.get("tags", []),
            )
            for t in data.get("turns", [])
        ]
        return cls(
            version=data.get("version", 1),
            scenario=data.get("scenario", "unnamed"),
            description=data.get("description", ""),
            turns=turns,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "scenario": self.scenario,
            "description": self.description,
            "turns": [
                {
                    "input": t.input,
                    "expect_events": t.expect_events,
                    "expect_fields": t.expect_fields,
                    "max_elapsed_ms": t.max_elapsed_ms,
                    "tags": t.tags,
                }
                for t in self.turns
            ],
        }



def _check_expect_events(
    new_events: list[dict], expected: list[str]
) -> list[str]:
    """Return expected event types NOT found in new_events."""
    found = {e.get("event", "") for e in new_events}
    return [ev for ev in expected if ev not in found]


def _check_expect_fields(
    new_events: list[dict], expect_fields: dict[str, Any]
) -> list[str]:
    """Return field assertions that failed.

    expect_fields = {"event": "delegation.decision", "will_delegate": true}
    """
    if not expect_fields:
        return []
    target_event = expect_fields.get("event", "")
    candidates = [
        e for e in new_events
        if not target_event or e.get("event") == target_event
    ]
    for candidate in candidates:
        # Check all non-"event" fields
        other_fields = {k: v for k, v in expect_fields.items() if k != "event"}
        if all(candidate.get(k) == v for k, v in other_fields.items()):
            return []
    # No candidate matched all fields
    return [f"{k}={v!r}" for k, v in expect_fields.items()]


