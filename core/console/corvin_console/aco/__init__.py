"""Backward-compat shim — aco moved to corvin_core.aco (ADR-0352 P2.1). The 26 test
files + intra-console relative imports keep importing corvin_console.aco.* while
they migrate. Each corvin_console/aco/<mod>.py aliases sys.modules to the real
corvin_core.aco.<mod> (no submodule double-load); this __init__ re-exports any
package-level names."""
from corvin_core.aco import *  # noqa: F401,F403
