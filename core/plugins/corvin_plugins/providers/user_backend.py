"""UserBackend registry — an external directory, deny-on-anything-else (ADR-0233).

Three states, deliberately distinct:

* **no backend installed** — ``get_active()`` returns ``None``.  Core auth is
  responsible; nothing changed about how this install authenticates.
* **backend says no** — ``authenticate()`` returns ``None`` → deny.
* **backend broke** — raises or times out → deny.

There is **no default backend**.  A default that denied everything would lock out
every install the moment a call site appeared; a default that admitted anything
would be an auth bypass.  Absence is the honest third state, and
:func:`authenticate` below makes the deny path the only reachable one for the two
failure states.

Usage (plugin on_load):
    ctx.user_registry.set_active(self)

Usage (caller):
    from corvin_plugins.providers import user_backend
    result = await user_backend.authenticate(creds)      # None => deny
    if result is None and not user_backend.is_installed():
        ...fall through to core auth...
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from corvin_plugins import circuit_breaker as _breakers

if TYPE_CHECKING:
    from corvin_plugins.protocol import UserBackend as _UBProto

_log = logging.getLogger("corvin.auth.backend")


def _plugin_id(backend: object, owner: str | None = None) -> str:
    """Breaker key for a backend instance.

    ``owner`` — the plugin that installed the slot — wins when known. Keying on
    the OBJECT gave a plugin two identities: a plugin that installs a helper
    (``set_active(self._sink)``) produced the key ``anonymous:Sink``, which the
    registry has never heard of, so ``get_breaker()`` could not see its boot
    layer and the compliance exemption silently did not apply.

    Falls back to the object's own id, then to the class name: a breaker keyed
    on the class is still per-backend, and refusing to guard an unlabelled
    backend would be worse than an imperfect key.
    """
    return (
        owner
        or getattr(backend, "plugin_id", None)
        or f"anonymous:{type(backend).__name__}"
    )

#: Hard ceiling for a backend call.  A directory that hangs must not hold an auth
#: request open — a timeout is a deny, not a wait.
DEFAULT_TIMEOUT_S = 10.0


class UserBackendRegistry:
    """Holds the active UserBackend for this process.  Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner_plugin_id: str | None = None
        self._active: _UBProto | None = None

    def set_active(self, provider: _UBProto) -> None:
        """Install ``provider`` as the active one for this process.

        Records WHICH PLUGIN did it (``loading.current()``), so the slot can
        later be released by plugin identity rather than by matching the
        object or guessing from ``plugin_type``. A plugin that installs a
        helper object still owns the slot.
        """
        from .. import loading as _loading

        _who = _loading.current()
        with self._lock:
            # Only a plugin that is LOADING may claim ownership. A set_active()
            # from anywhere else (a request handler, a thread a plugin spawned,
            # a timer) used to write None here — which not only left the new
            # occupant unowned, it ERASED the previous legitimate owner, so the
            # slot could never be released by anyone again. Keeping the old
            # owner is the lesser wrong: the slot still belongs to whoever took
            # it during a load, and unloading them releases it.
            if _who is not None:
                self._owner_plugin_id = _who.plugin_id
            self._active = provider

    def clear(self) -> None:
        with self._lock:
            self._owner_plugin_id = None
            self._active = None

    def clear_if_active(self, provider: object) -> bool:
        """Detach only if ``provider`` is the one currently installed.

        Instance-checked: a plugin unloading must not evict a provider that a
        DIFFERENT plugin installed after it.
        """
        with self._lock:
            if self._active is not provider:
                return False
            self._owner_plugin_id = None
            self._active = None
            return True

    def get_active(self) -> _UBProto | None:
        with self._lock:
            return self._active

    def release_owned_by(self, plugin_id: str) -> bool:
        """Release the slot if ``plugin_id`` is the plugin that took it.

        Identity-based, which is the point: the object in the slot may be a
        helper the plugin created rather than the plugin itself, and the
        plugin's ``plugin_type`` may not even name this registry. Ownership is
        recorded at ``set_active`` time and is the only thing that answers
        "is this slot yours" correctly.
        """
        with self._lock:
            if self._owner_plugin_id is None or self._owner_plugin_id != plugin_id:
                return False
            self._owner_plugin_id = None
            self._active = None
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current provider, if it is known."""
        with self._lock:
            return self._owner_plugin_id

    def is_installed(self) -> bool:
        return self.get_active() is not None


_registry: UserBackendRegistry = UserBackendRegistry()


def get_active() -> _UBProto | None:
    return _registry.get_active()


def set_active(provider: _UBProto) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def is_installed() -> bool:
    """True when a plugin has claimed authentication for this process."""
    return _registry.is_installed()


async def authenticate(
    credentials: dict, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> dict | None:
    """Authenticate through the installed backend.  Returns None on ANY failure.

    Deny-on-error is structural here rather than left to each call site: an
    exception, a timeout, a non-dict return and a missing ``user_id`` all collapse
    to ``None``.  A caller can therefore never accidentally admit a session by
    forgetting one of those branches.

    ``None`` does NOT distinguish "no backend" from "denied" — use
    :func:`is_installed` for that, and only to decide whether to fall through to
    core auth, never to admit.
    """
    backend = _registry.get_active()
    if backend is None:
        return None

    # The breaker contains an unreachable directory. It is driven by INFRASTRUCTURE
    # outcomes only — see _plugin_id() note below.
    breaker = _breakers.get_breaker(_plugin_id(backend, _registry.owner_plugin_id()))
    try:
        breaker.guard()
    except _breakers.CircuitOpen:
        _log.error("user backend circuit open — denying without calling the directory")
        return None

    try:
        result = await asyncio.wait_for(
            backend.authenticate(credentials), timeout=timeout_s
        )
    except asyncio.TimeoutError as exc:
        breaker.record_failure(exc)
        _log.error("user backend authenticate timed out after %.1fs — denying", timeout_s)
        return None
    except Exception as exc:  # noqa: BLE001 — a broken directory must deny, not admit
        breaker.record_failure(exc)
        # Class name only: str(exc) on an LDAP/OIDC error routinely contains the
        # bind DN, the server URL or the submitted username.
        _log.error("user backend authenticate failed (%s) — denying", type(exc).__name__)
        return None

    # A REJECTED credential is a working backend, not a failure. Counting denials
    # as breaker failures would turn three wrong passwords into a 30-second
    # outage for every user — a self-inflicted DoS. Only exceptions and timeouts
    # above may open this breaker.
    breaker.record_success()

    if not isinstance(result, dict) or not result.get("user_id"):
        if result is not None:
            _log.error(
                "user backend returned a malformed principal (%s) — denying",
                type(result).__name__,
            )
        return None

    # A backend must not hand back credential material; drop it before the
    # principal travels further into the session layer or a log line.
    for secret_key in ("password", "password_hash", "token", "secret", "client_secret"):
        result.pop(secret_key, None)
    return result


async def get_user(user_id: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict | None:
    """Look a user up.  Returns None when absent, on error, or with no backend."""
    backend = _registry.get_active()
    if backend is None:
        return None
    try:
        result = await asyncio.wait_for(backend.get_user(user_id), timeout=timeout_s)
    except asyncio.TimeoutError:
        _log.error("user backend get_user timed out after %.1fs", timeout_s)
        return None
    except Exception as exc:  # noqa: BLE001
        _log.error("user backend get_user failed (%s)", type(exc).__name__)
        return None
    return result if isinstance(result, dict) else None


async def enforce_quota(
    user_id: str, resource: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> None:
    """Ask the backend to enforce a quota.

    Re-raises whatever the backend raises (that is how a quota denial travels),
    but converts an unreachable backend into a denial too: if the quota check
    cannot be performed, the resource is not granted.  Fail-closed — a directory
    outage must not become unlimited quota.
    """
    backend = _registry.get_active()
    if backend is None:
        return  # core quota logic applies
    try:
        await asyncio.wait_for(backend.enforce_quota(user_id, resource), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        _log.error("user backend enforce_quota timed out after %.1fs — denying", timeout_s)
        raise QuotaUndeterminedError(resource) from exc


class QuotaUndeterminedError(RuntimeError):
    """The quota could not be evaluated, so the resource is denied (fail-closed)."""

    def __init__(self, resource: str):
        super().__init__(f"quota for {resource!r} could not be determined")
        self.resource = resource


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)


def release_owned_by(plugin_id: str) -> bool:
    return _registry.release_owned_by(plugin_id)
