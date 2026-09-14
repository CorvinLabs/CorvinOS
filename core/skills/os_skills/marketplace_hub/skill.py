"""Marketplace Hub Skill — Minimal."""

from dataclasses import dataclass
from typing import List, Dict
from enum import Enum


class ItemType(str, Enum):
    """Marketplace item types."""
    PLUGIN = "plugin"
    SKILL = "skill"
    TOOL = "tool"
    DATASET = "dataset"


@dataclass
class MarketplaceItem:
    """Item in marketplace."""
    name: str
    item_type: ItemType
    version: str = "1.0.0"
    category: str = "general"


class MarketplaceHubSkill:
    """Marketplace discovery hub (minimal)."""

    def __init__(self):
        self.items: List[MarketplaceItem] = []
        self.index: Dict[str, MarketplaceItem] = {}

    def register_item(self, item: MarketplaceItem) -> bool:
        """Register item in marketplace."""
        if item.name in self.index:
            return False

        self.items.append(item)
        self.index[item.name] = item
        return True

    def search(self, query: str) -> List[MarketplaceItem]:
        """Search marketplace."""
        return [i for i in self.items if query.lower() in i.name.lower() or query.lower() in i.category.lower()]

    def list_by_type(self, item_type: ItemType) -> List[MarketplaceItem]:
        """List items by type."""
        return [i for i in self.items if i.item_type == item_type]

    def get_index(self) -> dict:
        """Get full marketplace index."""
        return {
            "total_items": len(self.items),
            "by_type": {
                t.value: len([i for i in self.items if i.item_type == t])
                for t in ItemType
            }
        }
