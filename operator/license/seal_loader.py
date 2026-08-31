"""Load the ``corvin_seal`` extension or fall back to the Python stub (ADR-0111).

Import this module, never the stub or the extension directly:

    from operator.license.seal_loader import unseal, verify_manifest, seal_version

Resolution order:
  1. ``operator/license/_corvin_seal.<platform-ext>`` (compiled Rust binary)
  2. ``_corvin_seal_stub`` (Python reference implementation, always present)

The compiled extension is the production root of trust.  The stub is used
during development and in CI.  Callers cannot tell which is loaded.
"""
from __future__ import annotations

import importlib
import logging
import platform
from typing import Any

log = logging.getLogger("corvin.license.seal")

_PLATFORM_EXTS = {
    "Linux":   ".so",
    "Darwin":  ".dylib",
    "Windows": ".pyd",
}


def _try_load_compiled() -> Any | None:
    """Attempt to import the compiled Rust extension.  Returns the module or None."""
    try:
        import importlib.util as _ilu
        import os
        from pathlib import Path

        here = Path(__file__).resolve().parent
        ext = _PLATFORM_EXTS.get(platform.system(), ".so")
        candidates = [
            here / f"_corvin_seal{ext}",
            here / "_corvin_seal.so",
            here / "_corvin_seal.pyd",
        ]
        for path in candidates:
            if path.exists():
                spec = _ilu.spec_from_file_location("_corvin_seal", path)
                if spec and spec.loader:
                    mod = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    log.info("seal_loader: using compiled corvin_seal binary %s", path.name)
                    return mod
    except Exception as exc:  # noqa: BLE001
        log.debug("seal_loader: compiled binary not loadable (%s)", exc)
    return None


def _load_stub() -> Any:
    from . import _corvin_seal_stub
    log.warning(
        "seal_loader: using Python stub — compiled Rust binary absent. "
        "Dev/CI mode only; stub lacks compile-time key hardening (ADR-0144 F-04)."
    )
    return _corvin_seal_stub


# Module-level singleton — resolved once at first import.
# Track compiled status separately; __name__ carries the package prefix when the
# stub is imported via `from . import`, so name-comparison is unreliable.
_compiled_module: Any | None = _try_load_compiled()
_seal_module: Any = _compiled_module if _compiled_module is not None else _load_stub()
_using_compiled: bool = _compiled_module is not None


def unseal(
    instance_id: str,
    device_fp: str,
    sob_bytes: bytes,
    manifest_nonce_epoch: int,
) -> dict[str, Any] | None:
    """Decrypt and validate a Sealed Offline Bundle."""
    return _seal_module.unseal(instance_id, device_fp, sob_bytes, manifest_nonce_epoch)


def verify_manifest(manifest_json: bytes, sig_bytes: bytes) -> bool:
    """Verify a GitHub trust-manifest RS256 signature."""
    return _seal_module.verify_manifest(manifest_json, sig_bytes)


def seal_version() -> str:
    """Return the seal extension version string."""
    return _seal_module.seal_version()


def is_compiled() -> bool:
    """True when the compiled Rust binary is in use (production)."""
    return _using_compiled
