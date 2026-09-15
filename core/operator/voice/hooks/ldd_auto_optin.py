#!/usr/bin/env python3
"""SessionStart hook: write ldd.json when LDD_AUTO_OPTIN=1.

When LDD_AUTO_OPTIN=1 is set in the operator's environment (~/.bashrc),
this hook ensures the on-disk ldd.json reflects that intent so that
CLI tools (/ldd-status, /ldd-set) agree with what skill_inject.py enforces
at runtime via the same env-var branch in ldd.is_layer_active().

Design: idempotent and fail-open. If the file already has enabled=True,
the hook exits immediately (no write). All I/O errors are swallowed with
a warning; the hook never blocks the session.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env)
    return Path.home() / ".corvin"


def main() -> None:
    if os.environ.get("LDD_AUTO_OPTIN") != "1":
        sys.exit(0)

    try:
        # Resolve forge.paths for the canonical corvin_home() if available.
        _repo = Path(__file__).resolve().parents[4]
        _forge = _repo / "operator" / "forge"
        if str(_forge) not in sys.path:
            sys.path.insert(0, str(_forge))
        from forge.paths import corvin_home  # type: ignore[import]
        base = Path(corvin_home())
    except Exception:
        base = _corvin_home()

    ldd_path = base / "global" / "ldd.json"

    # Skip if already enabled (idempotent).
    try:
        if ldd_path.exists():
            existing = json.loads(ldd_path.read_text(encoding="utf-8"))
            if existing.get("enabled") is True:
                sys.exit(0)
    except Exception:
        pass  # corrupt file — overwrite below

    # Import the canonical LAYERS list from ldd.py.
    try:
        _shared = Path(__file__).resolve().parents[3] / "bridges" / "shared"
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from ldd import LAYERS  # type: ignore[import]
    except Exception as exc:
        print(f"[ldd_auto_optin] warning: cannot import ldd.LAYERS: {exc}", file=sys.stderr)
        sys.exit(0)

    target = {
        "enabled": True,
        "layers": {layer: True for layer in LAYERS},
    }

    try:
        ldd_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = ldd_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(target, indent=2), encoding="utf-8")
        tmp.replace(ldd_path)
    except Exception as exc:
        print(f"[ldd_auto_optin] warning: could not write {ldd_path}: {exc}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
