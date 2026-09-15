"""Shared device fingerprint computation — single source of truth.

All three license callers (sob.py, session_refresh.py, validator.py) import
compute_device_fp() from here.  Having one canonical implementation eliminates
the three-formula divergence that caused SOB unsealing to fail on installations
without the corvin_license package (FND-LIC-07).

Formula:
  Primary:  sha256(corvin_license.trial.machine_fingerprint().encode())[:32]
  Fallback: sha256(sha256("{machine_id}:{hostname}:{mac}".encode())[:32].encode())[:32]
            anchored on /etc/machine-id — requires root to change on systemd.
"""
import hashlib
import socket
import uuid
from pathlib import Path


def compute_device_fp() -> str:
    """Return a stable 32-char hex device fingerprint."""
    try:
        import sys as _sys
        _core_lic = Path(__file__).resolve().parents[2] / "core" / "license"
        if str(_core_lic) not in _sys.path:
            _sys.path.insert(0, str(_core_lic))
        from corvin_license.trial import machine_fingerprint as _mfp  # type: ignore
        machine_fp = _mfp()
    except Exception:
        machine_id = ""
        for _mp in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                machine_id = Path(_mp).read_text().strip()
                if machine_id:
                    break
            except Exception:
                pass
        hostname = "unknown"
        try:
            hostname = socket.gethostname()
        except Exception:
            pass
        mac = format(uuid.getnode(), "012x")
        machine_fp = hashlib.sha256(
            f"{machine_id}:{hostname}:{mac}".encode()
        ).hexdigest()[:32]
    return hashlib.sha256(machine_fp.encode()).hexdigest()[:32]
