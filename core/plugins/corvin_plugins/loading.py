"""Who is loading right now — the one fact three guards kept re-deriving badly.

Three separate defects in this package had the same root: code that needed to
know *which plugin is currently acting* tried to infer it from something else,
and the inference was wrong in a different way each time.

* Releasing a provider slot on unload matched the slot's contents against the
  plugin OBJECT. A plugin that registered a helper object
  (``ctx.audit_registry.set_active(self._sink)`` — the API allows it) kept the
  slot after being disabled, and went on receiving a copy of every audit event
  of every tenant.
* The same release picked the provider module from ``plugin_type``. A plugin
  whose type has no provider module (``bridge_channel``…) could still take a
  provider slot, because ``build_context`` hands every registry to every
  plugin — and then nothing could ever release it.
* ``register_hook`` verified the caller's tenant by looking the plugin up in the
  registry. That works during ``on_load`` and nowhere else, so a hook registered
  from ``__init__`` (before the plugin is registered) or after ``unregister``
  claimed any tenant it liked.

All three are answered by tracking the fact directly instead of reconstructing
it: the registry marks a plugin as *loading* around its ``on_load``, and the
guards ask. A context variable rather than a global, so concurrent loads on
different threads do not see each other's plugin.

**This is not an identity guarantee, and must not be read as one.** The
contextmanager below is public, so any in-process code can claim to be any
plugin; ``threading.Thread`` does not inherit ContextVars, so a worker started
during a load answers ``None``; ``asyncio.create_task`` copies them, so a task
can still claim the plugin after it unloaded. That is not a defect to be fixed
here — in CPython there is no property of a caller that the caller cannot set,
and three earlier derivations (the object, then the ``plugin_id`` argument) were
broken the same way.

What this buys is **attribution**: honest plugins are correctly associated with
their slots and hooks, cleanup finds what to release, and every action carries a
name. Anything that must hold against a HOSTILE plugin belongs in a subprocess.
See ``docs/claude-ref/layer-plugins.md`` § "The perimeter is attribution".
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator, NamedTuple, Optional


class LoadingPlugin(NamedTuple):
    """The plugin whose ``on_load`` is currently executing on this thread."""

    plugin_id: str
    tenant_id: str


_current: contextvars.ContextVar[Optional[LoadingPlugin]] = contextvars.ContextVar(
    "corvin_loading_plugin", default=None
)


@contextmanager
def loading(plugin_id: str, tenant_id: str) -> Iterator[None]:
    """Mark ``plugin_id`` as the plugin currently being loaded.

    Set by :meth:`PluginRegistry.register` around ``on_load``. Restored on the
    way out even if ``on_load`` raises — a failed load must not leave the
    process thinking that plugin is still acting.
    """
    token = _current.set(LoadingPlugin(plugin_id=plugin_id, tenant_id=tenant_id))
    try:
        yield
    finally:
        _current.reset(token)


def current() -> Optional[LoadingPlugin]:
    """The plugin whose ``on_load`` is running here, or None outside a load."""
    return _current.get()


__all__ = ["LoadingPlugin", "current", "loading"]
