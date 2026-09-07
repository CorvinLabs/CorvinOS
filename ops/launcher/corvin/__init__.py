"""Corvin launcher — install and run Corvin with Ollama."""
try:
    from importlib.metadata import version as _dist_version

    __version__ = _dist_version("corvinos")
except Exception:  # noqa: BLE001 — not installed as a distribution (source checkout)
    __version__ = "0.0.0+unknown"
