"""Persistent config at ~/.config/corvin-launcher/config.json."""
import json
import os
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path.home() / ".config" / "corvin-launcher" / "config.json"

_DEFAULTS: dict[str, Any] = {
    "bridge": None,
    "image": "ghcr.io/anthropic/corvinos:latest",
    "container_name": "corvinos",
    "data_dir": str(Path.home() / ".corvin-data"),
    "auto_update": True,
}

# Keys written by launchers before ADR-2087 (local Ollama removed). A config
# file that still carries them loads normally; the keys are dropped on read
# and disappear on the next save.
_LEGACY_KEYS = frozenset({"ollama_url", "model"})


def load() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            if isinstance(data, dict):
                data = {k: v for k, v in data.items() if k not in _LEGACY_KEYS}
                return {**_DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def is_configured() -> bool:
    """True once ``corvin setup`` (or ``corvin config set``) has written a config file."""
    return _CONFIG_PATH.exists()


def save(cfg: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get(key: str) -> Any:
    return load().get(key, _DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)
