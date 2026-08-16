"""
Navigator: Wing/Room hierarchical navigation (MemPalace-inspired)

Wings: Tenants/Projects
Rooms: Thematic areas within a Wing (topics, domains)
Drawers: Individual artifacts in a Room
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from .drawer import DrawerManager


class Room:
    """A thematic area containing artifacts."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.drawer_manager = DrawerManager(path)

    def summary(self) -> Dict[str, int]:
        """Count artifacts by type."""
        all_drawers = self.drawer_manager.list_all()
        return {
            'ideas': len(all_drawers['ideas']),
            'concepts': len(all_drawers['concepts']),
            'adrs': len(all_drawers['adrs']),
            'plans': len(all_drawers['plans']),
            'total': sum(len(d) for d in all_drawers.values()),
        }


class Wing:
    """A project/tenant containing rooms."""

    def __init__(self, name: str, path: Path, tenant_id: str = '_default'):
        """
        name: Wing name (e.g., 'idea-pipeline')
        path: Path to Wing root
        tenant_id: Tenant identifier
        """
        self.name = name
        self.path = path
        self.tenant_id = tenant_id
        self.path.mkdir(parents=True, exist_ok=True)

        # Discover rooms (subdirectories)
        self.rooms: Dict[str, Room] = {}
        self._discover_rooms()

    def _discover_rooms(self) -> None:
        """Auto-discover rooms from directory structure."""
        # Rooms are subdirectories containing drawer dirs (ideas/, concepts/, etc.)
        for room_dir in self.path.iterdir():
            if not room_dir.is_dir():
                continue
            if room_dir.name.startswith('.'):
                continue

            # Check if this looks like a room (has drawer subdirs)
            has_drawers = any(
                (room_dir / drawer_name).exists()
                for drawer_name in ['ideas', 'concepts', 'adrs', 'implementation-plans']
            )

            if has_drawers or not list(room_dir.iterdir()):
                # It's a room (or empty, so treat as room)
                room = Room(room_dir.name, room_dir)
                self.rooms[room_dir.name] = room

    def create_room(self, name: str) -> Room:
        """Create a new room."""
        if name in self.rooms:
            return self.rooms[name]

        room_path = self.path / name
        room_path.mkdir(parents=True, exist_ok=True)

        room = Room(name, room_path)
        self.rooms[name] = room

        return room

    def get_room(self, name: str) -> Optional[Room]:
        """Get room by name."""
        return self.rooms.get(name)

    def list_rooms(self) -> List[Tuple[str, Dict[str, int]]]:
        """List all rooms with summaries."""
        return [(room_name, room.summary()) for room_name, room in sorted(self.rooms.items())]

    def summary(self) -> Dict[str, int]:
        """Aggregate statistics across all rooms."""
        total = {
            'ideas': 0,
            'concepts': 0,
            'adrs': 0,
            'plans': 0,
        }

        for room in self.rooms.values():
            room_summary = room.summary()
            for key in total.keys():
                total[key] += room_summary.get(key, 0)

        total['total'] = sum(total.values())
        total['rooms'] = len(self.rooms)

        return total


class IdeaPalace:
    """Root navigator: manages all Wings."""

    def __init__(self, palace_root: Path = None):
        """
        palace_root: Root path for all wings (default: ~/.corvin/tenants/_default/idea-pipeline/)
        """
        if palace_root is None:
            palace_root = Path.home() / '.corvin' / 'tenants' / '_default' / 'idea-pipeline'

        self.palace_root = palace_root
        self.palace_root.mkdir(parents=True, exist_ok=True)

        # Discover wings (subdirectories)
        self.wings: Dict[str, Wing] = {}
        self._discover_wings()

    def _discover_wings(self) -> None:
        """Auto-discover wings."""
        for wing_dir in self.palace_root.iterdir():
            if not wing_dir.is_dir():
                continue
            if wing_dir.name.startswith('.'):
                continue

            wing = Wing(wing_dir.name, wing_dir)
            self.wings[wing_dir.name] = wing

    def create_wing(self, name: str, tenant_id: str = '_default') -> Wing:
        """Create or get a wing."""
        if name in self.wings:
            return self.wings[name]

        wing_path = self.palace_root / name
        wing_path.mkdir(parents=True, exist_ok=True)

        wing = Wing(name, wing_path, tenant_id)
        self.wings[name] = wing

        return wing

    def get_wing(self, name: str) -> Optional[Wing]:
        """Get wing by name."""
        return self.wings.get(name)

    def list_wings(self) -> List[Tuple[str, Dict[str, int]]]:
        """List all wings with summaries."""
        return [(wing_name, wing.summary()) for wing_name, wing in sorted(self.wings.items())]

    def search_all(self, query: str) -> Dict[str, List[Dict]]:
        """Search across all wings."""
        results = {}

        for wing_name, wing in self.wings.items():
            wing_results = {}
            for room_name, room in wing.rooms.items():
                matches = room.drawer_manager.search_by_index(query)
                if matches:
                    wing_results[room_name] = matches

            if wing_results:
                results[wing_name] = wing_results

        return results

    def summary(self) -> Dict[str, int]:
        """Global statistics."""
        total = {
            'ideas': 0,
            'concepts': 0,
            'adrs': 0,
            'plans': 0,
            'wings': len(self.wings),
            'rooms': 0,
        }

        for wing in self.wings.values():
            wing_summary = wing.summary()
            for key in ['ideas', 'concepts', 'adrs', 'plans']:
                total[key] += wing_summary.get(key, 0)
            total['rooms'] += wing_summary.get('rooms', 0)

        total['total'] = sum(v for k, v in total.items() if k not in ['wings', 'rooms', 'total'])

        return total
