"""ADR-0159 M2 — SandboxProvider: platform-independent Forge sandbox abstraction.

Tier selection order:
  1. CORVIN_SANDBOX env var  (bwrap | docker | none)
  2. bwrap on PATH           → bwrap
  3. docker info success     → docker
  4. fallback                → none

The ``none`` tier never disables the L10 path-gate; it only removes filesystem
namespacing.  self_test.py emits WARNING (or CRITICAL when
forge.require_sandbox=true) when ``none`` is active so the operator is always
aware of reduced isolation.

No ``import anthropic`` in this module (CI AST lint enforces).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # avoid anthropic at all times

_log = logging.getLogger("corvin.forge.sandbox")


class SandboxTier(str, Enum):
    BWRAP = "bwrap"
    DOCKER = "docker"
    NONE = "none"


@dataclass(frozen=True)
class SandboxCapabilities:
    tier: SandboxTier
    has_network_isolation: bool
    has_fs_namespacing: bool
    has_process_isolation: bool

    @classmethod
    def for_tier(cls, tier: SandboxTier) -> "SandboxCapabilities":
        if tier == SandboxTier.BWRAP:
            return cls(
                tier=tier,
                has_network_isolation=True,
                has_fs_namespacing=True,
                has_process_isolation=True,
            )
        if tier == SandboxTier.DOCKER:
            return cls(
                tier=tier,
                has_network_isolation=True,
                has_fs_namespacing=True,
                has_process_isolation=True,
            )
        # none
        return cls(
            tier=tier,
            has_network_isolation=False,
            has_fs_namespacing=False,
            has_process_isolation=False,
        )


def _have_bwrap() -> bool:
    return shutil.which("bwrap") is not None


def _have_docker() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


_DETECTED_TIER: SandboxTier | None = None


def detect_sandbox_tier(*, force: bool = False) -> SandboxTier:
    """Return the best available sandbox tier (cached after first call).

    Pass ``force=True`` to re-probe (e.g. after installing bwrap at runtime).
    """
    global _DETECTED_TIER
    if _DETECTED_TIER is not None and not force:
        return _DETECTED_TIER

    env_override = os.environ.get("CORVIN_SANDBOX", "").strip().lower()
    if env_override in ("bwrap", "docker", "none"):
        tier = SandboxTier(env_override)
        _DETECTED_TIER = tier
        _log.info("sandbox tier (env override): %s", tier.value)
        return tier

    if _have_bwrap():
        _DETECTED_TIER = SandboxTier.BWRAP
        _log.debug("sandbox tier: bwrap")
        return SandboxTier.BWRAP

    if _have_docker():
        _DETECTED_TIER = SandboxTier.DOCKER
        _log.info("sandbox tier: docker (bwrap unavailable)")
        return SandboxTier.DOCKER

    _DETECTED_TIER = SandboxTier.NONE
    _log.warning(
        "sandbox tier: none — bwrap and docker both unavailable. "
        "L10 path-gate remains active; filesystem namespacing is NOT in effect. "
        "Set CORVIN_SANDBOX=docker or install bwrap to restore isolation."
    )
    return SandboxTier.NONE


def is_sandbox_available() -> bool:
    """True when at least bwrap or docker is available."""
    return detect_sandbox_tier() != SandboxTier.NONE


def get_capabilities() -> SandboxCapabilities:
    return SandboxCapabilities.for_tier(detect_sandbox_tier())
