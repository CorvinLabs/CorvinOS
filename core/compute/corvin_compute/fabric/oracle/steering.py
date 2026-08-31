"""SteeringVector parsing and application (ADR-0026 §B).

The Oracle subprocess emits structured JSON; this module parses it into
a SteeringVector and provides _apply_steering to translate it into concrete
BackendParams via the backend's translate_steering() method.

Steering format:
  {"lr": "↓0.3", "max_depth": "↑1", "subsample": "↑0.05"}

"↓0.3" → multiply current value by (1 - 0.3) = 0.7
"↑1"   → integer: add 1; float: multiply by 1.1 (handled by _apply_directive)

MUST NOT import anthropic / openai / google.cloud.aiplatform.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..backends.protocol import BackendParams, BackendSession, SteeringVector

log = logging.getLogger(__name__)

# Valid direction characters
_DIRECTIONS = {"↓", "↑"}


def _parse_steering(raw: str) -> Optional[SteeringVector]:
    """Parse a JSON string from the oracle subprocess into a SteeringVector.

    Returns None on any parse failure (graceful degrade).
    """
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        log.warning("oracle output not valid JSON: %s", exc)
        return None

    if not isinstance(data, dict):
        log.warning("oracle JSON is not an object (got %s)", type(data).__name__)
        return None

    # Validate each entry — must be str(direction + magnitude)
    vector: dict[str, str] = {}
    for key, val in data.items():
        if not isinstance(key, str):
            log.warning("oracle steering key is not a string: %r", key)
            continue
        if not isinstance(val, str):
            log.warning("oracle steering value for %r is not a string: %r", key, val)
            continue
        if not val or val[0] not in _DIRECTIONS:
            log.warning("oracle steering value for %r has invalid direction: %r", key, val)
            continue
        # Validate magnitude is numeric
        magnitude_str = val[1:]
        try:
            float(magnitude_str)
        except ValueError:
            log.warning(
                "oracle steering value for %r has non-numeric magnitude: %r",
                key, val
            )
            continue
        vector[key] = val

    if not vector:
        log.warning("oracle produced empty steering vector after validation")
        return None

    return SteeringVector(vector=vector)


def _apply_steering(
    session: BackendSession,
    steering: SteeringVector,
    backend: Any,
) -> None:
    """Apply a SteeringVector to a session via backend.translate_steering().

    Mutates session.params in place.  Never raises — any failure is logged
    and silently discarded (graceful degrade contract).
    """
    try:
        backend_params: BackendParams = backend.translate_steering(steering)
        session.apply_params(backend_params)
        log.debug(
            "applied steering to session %s: keys=%s",
            session.run_id, sorted(steering.vector.keys())
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("apply_steering failed for %s: %s", session.run_id, exc)


__all__ = ["_parse_steering", "_apply_steering"]
