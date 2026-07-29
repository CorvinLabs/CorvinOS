"""Quality Layers configuration and resolver.

Quality Layers are toggleable disciplinary guidelines (ADR Gate, docs-as-definition-of-done, etc.)
that are injected into Claude Code sessions. This module manages the global quality-layers.json
config and provides queries about layer status.
"""

import copy
import json
import os
from pathlib import Path
from typing import Dict


def _quality_layers_path() -> Path:
    """Resolve quality-layers.json under the canonical corvin_home (CORVIN_HOME
    env → repo marker → ~/.corvin), evaluated per-call. A bare import-time
    Path.home()/.corvin constant ignored CORVIN_HOME and froze at import
    (path-audit 2026-06-25 #MEDIUM5)."""
    try:
        from paths import corvin_home as _ch  # operator/bridges/shared/paths.py
        home = _ch()
    except Exception:  # noqa: BLE001
        env = os.environ.get("CORVIN_HOME")
        home = Path(os.path.expanduser(env)) if env else (Path.home() / ".corvin")
    return home / "global" / "quality-layers.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "layers": {
        "adr_gate": True,
        "e2e_wiring_proof": True,
        "docs_as_definition_of_done": True,
        "e2e_driven_iteration": True,
        "usability_first": False,
    },
}


def load_config() -> Dict:
    """Load quality-layers.json, return defaults if missing.

    Returns a DEEP copy of DEFAULT_CONFIG — a shallow ``.copy()`` here left
    ``config["layers"]`` pointing at the SAME nested dict object as the
    module-level ``DEFAULT_CONFIG["layers"]``. Any caller that mutated the
    returned config in place before saving it (``enable_layer``/
    ``disable_layer`` both do exactly this) permanently corrupted the
    process-wide default for the rest of the run: the first
    ``disable_layer("adr_gate")`` call made with no config file on disk yet
    silently flipped ``DEFAULT_CONFIG["layers"]["adr_gate"]`` to ``False``
    forever, so every SUBSEQUENT "file missing → return defaults" call in
    the same process saw the poisoned value instead of the true default.
    Found via test_core_quality_skills.py exercising disable_layer/
    disable_all/reset in sequence in one process."""
    if _quality_layers_path().exists():
        try:
            with open(_quality_layers_path(), "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return copy.deepcopy(DEFAULT_CONFIG)
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config: Dict) -> None:
    """Save config to quality-layers.json."""
    _quality_layers_path().parent.mkdir(parents=True, exist_ok=True)
    with open(_quality_layers_path(), "w") as f:
        json.dump(config, f, indent=2)


def is_layer_enabled(layer_name: str) -> bool:
    """Check if a specific layer is enabled.

    Args:
        layer_name: e.g., 'adr_gate', 'docs_as_definition_of_done'

    Returns:
        True if globally enabled AND layer specifically enabled, False otherwise.
    """
    config = load_config()

    # Fail-open: if quality layers globally disabled, all return False
    if not config.get("enabled", True):
        return False

    # Fail-open: if layer not defined, treat as enabled (new layers auto-on)
    layers = config.get("layers", {})
    return layers.get(layer_name, True)


def enable_layer(layer_name: str) -> None:
    """Enable a specific layer."""
    config = load_config()
    if "layers" not in config:
        config["layers"] = {}
    config["layers"][layer_name] = True
    save_config(config)


def disable_layer(layer_name: str) -> None:
    """Disable a specific layer."""
    config = load_config()
    if "layers" not in config:
        config["layers"] = {}
    config["layers"][layer_name] = False
    save_config(config)


def enable_all() -> None:
    """Enable all quality layers globally."""
    config = load_config()
    config["enabled"] = True
    save_config(config)


def disable_all() -> None:
    """Disable all quality layers globally."""
    config = load_config()
    config["enabled"] = False
    save_config(config)


def get_status() -> Dict:
    """Return full status dict for display/debugging."""
    config = load_config()
    return {
        "globally_enabled": config.get("enabled", True),
        "layers": config.get("layers", {}),
    }


def list_layers() -> Dict[str, bool]:
    """Return dict of all layers and their status."""
    config = load_config()
    return config.get("layers", {})
