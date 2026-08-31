"""Platform-specific utilities (Windows/Unix cross-platform support).

Provides abstractions for:
  - File permissions (chmod on Unix, ACL on Windows)
  - IPC transports (Unix sockets vs TCP loopback)
  - Environment variable access
"""

from .file_permissions import (
    setup_audit_file_permissions,
    setup_corvin_home_permissions,
    setup_file_permissions,
    setup_socket_directory_permissions,
)

__all__ = [
    "setup_audit_file_permissions",
    "setup_corvin_home_permissions",
    "setup_file_permissions",
    "setup_socket_directory_permissions",
]
