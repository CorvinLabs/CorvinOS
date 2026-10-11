"""Regressions for the 2026-09-27 adversarial review of core/learning.

Each test failed before its fix (see the module each one names).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _chain_records():
    chain = Path(os.environ["VOICE_AUDIT_PATH"])
    if not chain.exists():
        return []
    return [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]


def test_every_learning_event_type_is_allowlisted():
    """event_store.EventStore.write_event commits ``learning.<type>`` and proves
    the commit by reading back ``audit_ref`` — a type without an allowlist has
    that key scrubbed and EVERY write of it fails closed (geo_mismatch,
    scene_rendered, plugin_*, ... until 2026-09-27)."""
    from core.learning.event_persistence import _LEARNING_EVENT_ALLOWLISTS
    from core.learning.learning_events import EventType

    missing = [t.value for t in EventType if f"learning.{t.value}" not in _LEARNING_EVENT_ALLOWLISTS]
    assert missing == []


















def test_confidence_persistence_honours_the_one_resolver(monkeypatch, tmp_path):
    from core.learning import confidence_persistence

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "h"))
    assert confidence_persistence._corvin_home() == tmp_path / "h"




