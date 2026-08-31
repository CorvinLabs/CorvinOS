"""Persistent config at ~/.config/corvin-launcher/config.json."""
import json
import os
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path.home() / ".config" / "corvin-launcher" / "config.json"

_DEFAULTS: dict[str, Any] = {
    "ollama_url": "http://127.0.0.1:11434",
    "model": "qwen3:8b",
    "bridge": None,
    "image": "ghcr.io/anthropic/corvinos:latest",
    "container_name": "corvinos",
    "data_dir": str(Path.home() / ".corvin-data"),
    "auto_update": True,
}


def load() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            return {**_DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def save(cfg: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get(key: str) -> Any:
    return load().get(key, _DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)
