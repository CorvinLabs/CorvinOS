"""
State Persistence with Write-Ahead Log (WAL) — Phase 2

Durability SLA: Every update → .wal file immediately,
atomic flush to .json every 60s or 100 updates.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import hashlib
import threading


@dataclass
class StateChecksum:
    """Checksum for state integrity."""
    data_hash: str
    timestamp_ms: float


class StateStore:
    """Persistent state storage with Write-Ahead Log."""

    def __init__(self, base_dir: str = "~/.corvin/plugins/state"):
        self.base_dir = Path(base_dir).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.states: Dict[str, Dict[str, Any]] = {}
        self.wal_buffer: Dict[str, list] = {}  # Pending writes per plugin
        self.last_flush_time: Dict[str, float] = {}
        self.metrics = {
            "writes": 0,
            "flushes": 0,
            "recoveries": 0,
            "corruptions": 0,
        }
        self._lock = threading.RLock()  # Reentrant lock to allow nested acquisitions

    def _get_state_path(self, plugin_id: str) -> Path:
        """Get path for plugin state file."""
        return self.base_dir / f"{plugin_id}.v1.json"

    def _get_wal_path(self, plugin_id: str) -> Path:
        """Get path for plugin WAL file."""
        return self.base_dir / f"{plugin_id}.wal"

    def load_or_create(self, plugin_id: str, default: Dict[str, Any] = None) -> Dict[str, Any]:
        """Load state from disk, or create new with default."""
        with self._lock:
            state_path = self._get_state_path(plugin_id)
            wal_path = self._get_wal_path(plugin_id)

            # Step 1: Check WAL (recovery)
            if wal_path.exists():
                state = self._recover_from_wal(plugin_id, wal_path)
                if state is not None:
                    self.metrics["recoveries"] += 1
                    self.states[plugin_id] = state
                    return state

            # Step 2: Load from disk
            if state_path.exists():
                try:
                    with open(state_path, "r") as f:
                        data = json.load(f)
                        self.states[plugin_id] = data
                        return data
                except (json.JSONDecodeError, IOError):
                    self.metrics["corruptions"] += 1
                    # Fall through to default

            # Step 3: Create new
            default_state = default or {}
            self.states[plugin_id] = default_state
            self.wal_buffer[plugin_id] = []
            self.last_flush_time[plugin_id] = time.time()
            return default_state

    def update(self, plugin_id: str, key: str, value: Any):
        """Update single key (WAL → immediate, flush → batched)."""
        with self._lock:
            if plugin_id not in self.states:
                self.states[plugin_id] = {}

            # Step 1: Update in-memory
            self.states[plugin_id][key] = value

            # Step 2: Write to WAL immediately (fire-and-forget durability)
            if plugin_id not in self.wal_buffer:
                self.wal_buffer[plugin_id] = []

            self.wal_buffer[plugin_id].append({
                "op": "set",
                "key": key,
                "value": value,
                "timestamp_ms": time.time() * 1000,
            })

            self.metrics["writes"] += 1

            # Step 3: Append to WAL file
            wal_path = self._get_wal_path(plugin_id)
            try:
                with open(wal_path, "a") as f:
                    f.write(json.dumps({
                        "op": "set",
                        "key": key,
                        "value": value,
                        "timestamp_ms": time.time() * 1000,
                    }) + "\n")
            except IOError as e:
                print(f"WAL write failed: {e}")

            # Step 4: Check if time to flush (every 100 updates or 60s)
            should_flush = (
                len(self.wal_buffer[plugin_id]) >= 100
                or (time.time() - self.last_flush_time.get(plugin_id, 0)) > 60
            )

            if should_flush:
                self.flush(plugin_id)

    def flush(self, plugin_id: str):
        """Atomically flush state to JSON."""
        with self._lock:
            if plugin_id not in self.states:
                return

            state_path = self._get_state_path(plugin_id)
            wal_path = self._get_wal_path(plugin_id)

            try:
                # Write to temp file first (atomic)
                temp_path = state_path.with_suffix(".tmp")
                with open(temp_path, "w") as f:
                    json.dump(self.states[plugin_id], f, default=str)

                # Atomic rename (cross-platform handling)
                try:
                    os.replace(str(temp_path), str(state_path))
                except (OSError, FileExistsError) as e:
                    raise RuntimeError(f"State flush failed: {e}")

                # Clear WAL
                if wal_path.exists():
                    wal_path.unlink()

                self.wal_buffer[plugin_id] = []
                self.last_flush_time[plugin_id] = time.time()
                self.metrics["flushes"] += 1

            except IOError as e:
                print(f"Flush failed: {e}")

    def _recover_from_wal(self, plugin_id: str, wal_path: Path) -> Optional[Dict[str, Any]]:
        """Recover state from WAL."""
        state = {}

        try:
            with open(wal_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry["op"] == "set":
                        state[entry["key"]] = entry["value"]

            # Delete WAL after successful recovery
            wal_path.unlink()
            return state

        except Exception as e:
            print(f"WAL recovery failed: {e}")
            return None

    def get(self, plugin_id: str, key: str, default: Any = None) -> Any:
        """Get state value."""
        with self._lock:
            if plugin_id not in self.states:
                return default
            return self.states[plugin_id].get(key, default)

    def get_all(self, plugin_id: str) -> Dict[str, Any]:
        """Get entire state for plugin."""
        with self._lock:
            return self.states.get(plugin_id, {})

    def get_checksum(self, plugin_id: str) -> str:
        """Get checksum of state (for validation)."""
        with self._lock:
            if plugin_id not in self.states:
                return ""

            state_str = json.dumps(self.states[plugin_id], sort_keys=True, default=str)
            return hashlib.sha256(state_str.encode()).hexdigest()

    def get_metrics(self) -> Dict[str, Any]:
        """Get store metrics."""
        with self._lock:
            return {
                **self.metrics,
                "total_plugins": len(self.states),
                "wal_entries": sum(len(v) for v in self.wal_buffer.values()),
            }

    def shutdown(self):
        """Flush all pending state on shutdown."""
        with self._lock:
            for plugin_id in list(self.states.keys()):
                self.flush(plugin_id)


# Global state store instance
_state_store: Optional[StateStore] = None


def get_state_store() -> StateStore:
    """Get or create global state store."""
    global _state_store
    if _state_store is None:
        _state_store = StateStore()
    return _state_store
