"""The Console as a bundled/builtin ``web_surface`` plugin (ADR-0356, P2.5).

Historically the Console SPA was hard-wired into ``standalone.py``: the app
imported ``mount_static`` and mounted ``web-next/dist`` at ``/console/`` with no
notion that this was one UI among possible many. P2.5 turns that implicit wiring
into an explicit plugin declaration — the first step toward the Console being a
*replaceable* surface (a FrontendForge-built panel set, P6; loaded by the plugin
loader, P7) rather than a fixed part of core.

What this plugin does and does NOT do
-------------------------------------
* It DECLARES the Console: ``plugin_type='web_surface'``, ``boot_layer='bundled'``
  (disableable — a UI is not a compliance mechanism), ``origin='builtin'``.
* It reports WHERE it mounts (``mount_path='/console/'``) and WHAT it serves
  (``spa_dist_dir()`` → the built SPA, or None when unbuilt).
* It does NOT start a server, and it does NOT mount anything itself. The OS owns
  the ASGI app; ``standalone.py`` still performs the actual mount today. Wiring
  the mount THROUGH this plugin is the plugin-loader's job (P7), deliberately not
  done here — P2.5 is the declaration, not the load. Until then this is a
  reachable, self-describing manifest that the registry and tests can read.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from ..protocol import HealthStatus, PluginContext

log = logging.getLogger(__name__)

#: Ship-dark flag gating the Console web_surface declaration (default OFF).
#: Mirrors bridges' ``bridge_supervisor_plugins``: declaring the Console as a
#: plugin must not change a default install, which still mounts the SPA the old
#: hard-wired way via standalone.py until the loader (P7) mounts it through here.
FLAG_ID = "console_web_surface_plugin"


def _load_is_enabled() -> Any | None:
    """Return ``corvin_core.feature_flags.is_enabled``, or None if absent.
    Identical convention to bridges/supervisor.py._load_is_enabled."""
    try:
        from corvin_core.feature_flags import is_enabled  # type: ignore[import-not-found]
        return is_enabled
    except ImportError:
        pass
    console = Path(__file__).resolve().parents[3] / "console"
    if console.is_dir() and str(console) not in sys.path:
        sys.path.append(str(console))
    try:
        from corvin_core.feature_flags import is_enabled  # type: ignore[import-not-found]
        return is_enabled
    except ImportError:
        return None


def console_flag_enabled(tenant_id: str) -> bool:
    """Read the ``console_web_surface_plugin`` flag, defaulting to OFF. Every
    failure mode (flags module absent, flag unregistered, overlay unreadable)
    resolves to False — the safe direction for a ship-dark flag."""
    is_enabled = _load_is_enabled()
    if is_enabled is None:
        return False
    try:
        return bool(is_enabled(FLAG_ID, tenant_id))
    except Exception:  # noqa: BLE001 — an unreadable flag is an off flag
        log.debug("feature flag %s could not be read — treating as off", FLAG_ID)
        return False


class ConsolePlugin:
    """Bundled/builtin ``web_surface`` plugin describing the CorvinOS Console SPA."""

    # ── CorvinPlugin lifecycle contract ──
    plugin_id = "console"
    plugin_type = "web_surface"
    version = "1.0.0"
    display_name = "CorvinOS Console"

    # ── plugin-registry axes (ADR-0243/0233) ──
    boot_layer = "bundled"   # disableable — a UI is not a compliance mechanism
    origin = "builtin"       # ships in the repo, first-party provenance

    # ── WebSurface contract ──
    mount_path = "/console/"

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    def on_load(self, ctx: PluginContext) -> None:
        """Called once after discovery. The Console does not self-register with a
        capability registry (there is no web_surface registry yet — that is P7);
        it only records its context so health_check can report against it. Kept
        deliberately side-effect-free so declaring the plugin never changes how
        the already-running Console is served."""
        self._ctx = ctx

    def on_unload(self) -> None:
        self._ctx = None

    def spa_dist_dir(self) -> Path | None:
        """Absolute path to the built Console SPA (``web-next/dist`` inside the
        ``corvin_console`` package), or None when it has not been built or the
        package is absent (a stripped/headless install). Resolves the path from
        the ``corvin_console`` package so it stays correct across source-tree and
        wheel layouts — the same bundle ``standalone.py`` mounts."""
        try:
            import corvin_console  # type: ignore[import-not-found]
        except Exception:
            return None
        pkg_dir = Path(corvin_console.__file__).resolve().parent
        dist = pkg_dir / "web-next" / "dist"
        return dist if (dist / "index.html").is_file() else None

    def health_check(self) -> HealthStatus:
        """Report whether the SPA bundle is present. Not-built is ``ok=True`` — a
        headless install (corvinos-run) deliberately ships no SPA, and painting
        that red would train the operator to ignore the health surface. ``ok=False``
        is reserved for a genuinely broken state; there is none for a passive
        declaration, so this never returns False today."""
        dist = self.spa_dist_dir()
        if dist is None:
            return HealthStatus(
                ok=True,
                message="no SPA bundle (headless or unbuilt) — expected",
                details={"plugin_id": self.plugin_id, "mount_path": self.mount_path},
            )
        return HealthStatus(
            ok=True,
            message="SPA bundle present",
            details={"plugin_id": self.plugin_id, "mount_path": self.mount_path,
                     "dist": str(dist)},
        )
