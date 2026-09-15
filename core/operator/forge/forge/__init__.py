"""claude-tool-forge: runtime tool generation for Claude Code (MVP)."""
# Windows: install POSIX-stdlib stand-ins (fcntl/resource) BEFORE any submodule
# runs its module-level ``import fcntl`` — otherwise ``from forge import ...``
# raises ImportError at import on Windows (taking the gateway/license/compliance/
# console packages down with it) or, where a caller wraps the import in
# try/except, SILENTLY disables the L16 hash-chained audit chain. No-op on POSIX.
from . import _wincompat as _wincompat  # noqa: E402
_wincompat.install()

from .registry import Registry, ToolSpec  # noqa: E402
from .runner import SchemaError, ToolError, run_tool  # noqa: E402

__all__ = ["Registry", "ToolSpec", "run_tool", "SchemaError", "ToolError"]
