"""Monotonic Time Guard — ADR-0144 Finding MED-1

Prevents clock-rollback attacks where expired tokens become valid again.

Persists the maximum-observed system time. If the clock resets below that,
reject the token as a clock-rollback event.

**Guard Invariant:**
  1. Persist max-seen timestamp in <corvin_home>/operator/license/monotonic.timestamp
  2. On each license validation, check: if `now < prior_max`, fail-closed
  3. Update max-seen to max(prior_max, now) after successful validation
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("corvin.license.monotonic")


def _monotonic_timestamp_path() -> Path:
    """Path to persisted maximum-seen timestamp."""
    # Same logic as validator.py uses to find corvin_home
    if env := os.environ.get("CORVIN_HOME"):
        corvin_home = Path(env).expanduser()
    else:
        corvin_home = Path("~/.corvin").expanduser()

    return corvin_home / "operator" / "license" / "monotonic.timestamp"


def _load_prior_max() -> Optional[int]:
    """Load the previously-persisted max timestamp, or None if file doesn't exist."""
    ts_file = _monotonic_timestamp_path()
    if not ts_file.exists():
        return None

    try:
        data = json.loads(ts_file.read_text())
        return int(data.get("max_timestamp"))
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        log.warning(
            "monotonic_time: failed to parse %s: %s; treating as no prior state",
            ts_file, e
        )
        return None


def _persist_max(timestamp: int) -> None:
    """Persist the new maximum timestamp."""
    ts_file = _monotonic_timestamp_path()
    ts_file.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "max_timestamp": timestamp,
        "updated_at": time.time(),  # When this guard last updated
    }

    try:
        ts_file.write_text(json.dumps(data, indent=2))
        ts_file.chmod(0o600)  # Read-only by owner
    except OSError as e:
        log.warning("monotonic_time: failed to persist timestamp: %s", e)


def check_clock_rollback(current_time: Optional[int] = None) -> bool:
    """
    Check if system clock has rolled back.

    **Returns:**
      - True if clock is valid (or first check)
      - False if clock has rolled back (reject token)

    **Side effect:**
      - Persists `current_time` as the new max if valid
    """
    current_time = current_time or int(time.time())
    prior_max = _load_prior_max()

    if prior_max is None:
        # First check — no prior state. Persist and accept.
        _persist_max(current_time)
        return True

    if current_time < prior_max:
        # Clock rolled back! This is a security event.
        log.error(
            "monotonic_time: CLOCK ROLLBACK detected! "
            "current=%d, prior_max=%d, delta=%ds — rejecting token",
            current_time, prior_max, (prior_max - current_time)
        )
        return False

    # Clock is monotonic. Update max and accept.
    if current_time > prior_max:
        _persist_max(current_time)

    return True
