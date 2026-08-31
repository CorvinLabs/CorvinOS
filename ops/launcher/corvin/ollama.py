"""Ollama detection and model listing."""
import sys
import urllib.request
import urllib.error
import json
from typing import Optional


CANDIDATE_URLS = [
    "http://127.0.0.1:11434",
    "http://localhost:11434",
]

_DEFAULT_MODELS = [
    "qwen3:8b",
    "qwen3:14b",
    "gemma4:27b",
    "llama3.1:8b",
    "mistral:7b",
]


def detect_url(hint: Optional[str] = None) -> Optional[str]:
    """Return the first reachable Ollama base URL, or None."""
    candidates = ([hint] if hint else []) + CANDIDATE_URLS
    for url in candidates:
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=3) as r:
                if r.status == 200:
                    return url.rstrip("/")
        except Exception:
            pass
    return None


def list_models(base_url: str) -> list[str]:
    """Return model names pulled on the Ollama instance."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def host_url_for_docker(ollama_url: str) -> str:
    """
    Translate a localhost Ollama URL to one reachable from inside a Docker
    container.

    - Linux + Docker Engine: --network host  →  same URL works
    - macOS / Windows Docker Desktop: host.docker.internal resolves automatically
    """
    if sys.platform == "linux":
        return ollama_url  # --network host will be added to docker run
    if "localhost" in ollama_url or "127.0.0.1" in ollama_url:
        port = ollama_url.split(":")[-1]
        return f"http://host.docker.internal:{port}"
    return ollama_url
