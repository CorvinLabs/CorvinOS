"""DataHub (Phase 2 Tier 2) — Skill + Plugin Data Aggregation"""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional
from datetime import datetime

@dataclass
class DataEntry:
    key: str
    value: Any
    source: str
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"

class DataHub:
    """Central data store for skills + plugins."""
    
    def __init__(self):
        self.data: Dict[str, DataEntry] = {}
    
    async def store(self, key: str, value: Any, source: str) -> None:
        """Store data entry."""
        self.data[key] = DataEntry(key, value, source)
    
    async def retrieve(self, key: str) -> Optional[Any]:
        """Get data entry."""
        entry = self.data.get(key)
        return entry.value if entry else None
    
    async def list_by_source(self, source: str) -> List[DataEntry]:
        """Get all entries from a source."""
        return [e for e in self.data.values() if e.source == source]

_datahub = DataHub()

async def datahub_get(key: str) -> Optional[Any]:
    return await _datahub.retrieve(key)

async def datahub_set(key: str, value: Any, source: str) -> None:
    await _datahub.store(key, value, source)
