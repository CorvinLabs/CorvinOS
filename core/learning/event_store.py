"""Learning Event Store — Audit-First Persistence with Query Cache"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict
import hashlib


@dataclass(frozen=True)
class LearningEvent:
    """Immutable learning event — audit-first, hash-chained"""
    id: str
    tenant_id: str
    timestamp: str
    event_type: str
    skill_id: str
    input_hash: str
    output_hash: str
    signal: float | None
    prev_hash: str
    hash: str
    lom: str

    @staticmethod
    def compute_hash(event_dict: dict) -> str:
        content = json.dumps(event_dict, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def from_dict(cls, d: dict):
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


class EventStore:
    """Audit-first event persistence with cache"""

    def __init__(self, corvin_home: Path = None):
        if corvin_home is None:
            corvin_home = Path.home() / ".corvin"
        self.corvin_home = corvin_home
        self.cache_path = corvin_home / "tenants" / "_default" / "learning" / "events.jsonl"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache_path.exists():
            self.cache_path.touch()

    async def write_event(self, event: LearningEvent, audit_backend=None) -> bool:
        try:
            if audit_backend:
                await audit_backend.write_event(
                    event_type="learning_event",
                    event_data=asdict(event),
                    tenant_id=event.tenant_id,
                )
        except Exception as e:
            print(f"CRITICAL: Audit write failed: {e}")
            return False

        try:
            with open(self.cache_path, "a") as f:
                f.write(json.dumps(asdict(event)) + "\n")
        except Exception as e:
            print(f"WARNING: Cache write failed: {e}")

        return True

    async def query_events(
        self, tenant_id: str, skill_id: str | None = None, limit: int = 100
    ) -> list[LearningEvent]:
        events = []
        try:
            with open(self.cache_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("tenant_id") != tenant_id:
                        continue
                    if skill_id and data.get("skill_id") != skill_id:
                        continue
                    events.append(LearningEvent.from_dict(data))
                    if len(events) >= limit:
                        break
        except FileNotFoundError:
            pass
        return events

    async def verify_chain(self, tenant_id: str) -> bool:
        events = await self.query_events(tenant_id, limit=1000)
        for i, event in enumerate(events):
            if i > 0 and event.prev_hash != events[i - 1].hash:
                return False
        return True
