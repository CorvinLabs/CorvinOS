"""Auto-select Docker or native backend."""
from . import native_backend, docker_backend


def get():
    """Return the active backend module (native preferred, Docker fallback)."""
    if native_backend.is_available():
        return native_backend
    return docker_backend
