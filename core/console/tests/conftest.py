"""Test isolation for the console suite.

Root-cause fix for cross-test pollution (project review 2026-06-23, console
LDD pass): several tests deliberately purge the project's own modules from
``sys.modules`` (``del sys.modules[k]`` for ``corvin_console.*`` / ``forge.*`` /
``corvin_gateway.*``) and/or ``importlib.reload`` them so a fresh import re-reads
``CORVIN_HOME``. They never restore the table.

Other test modules bind a module object at *import* time
(``import corvin_console.routes.chat as chat_routes``) and later call methods on
that bound object, while patching via the dotted string path
(``patch("corvin_console.routes.chat.chat_runtime.get_session")``) which
re-resolves through ``sys.modules``. Once a prior test purged ``sys.modules``,
the bound object and the freshly-re-imported one diverge: the patch lands on the
fresh module, the route runs on the stale one, the mock never takes effect, and
the route 404s / reads the real ``~/.corvin``. Symptom: ~20 route tests that pass
in isolation fail in the full suite (execution-log, os-turns, workdir, ...).

Two autouse, function-scoped fixtures restore the baseline after every test so no
test can leak module-table or environment state into the next one. This isolates
the whole class without rewriting the individual tests.
"""
from __future__ import annotations

import os
import sys

import pytest

# Top-level packages whose module identity must not leak between tests. Tests
# routinely del/reload these; restoring them keeps every test's bound references
# consistent with what `patch("<dotted.path>")` re-resolves.
_TRACKED_PREFIXES = (
    "corvin_console",
    "corvin_gateway",
    "forge",
    "license",
)


def _is_tracked(modname: str) -> bool:
    head = modname.split(".", 1)[0]
    return head in _TRACKED_PREFIXES


@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    """Snapshot + restore the tracked slice of sys.modules around each test.

    After the test: drop any tracked module it added or reload-replaced, then
    re-insert the exact objects present before the test ran. This un-does
    `del sys.modules[...]`, `importlib.reload(...)`, and fresh re-imports so the
    next test sees the same module objects its module-level imports bound to.
    """
    snapshot = {k: v for k, v in sys.modules.items() if _is_tracked(k)}
    try:
        yield
    finally:
        # Re-insert the exact objects present before the test. This un-does a
        # test's `del sys.modules[...]`, fresh re-import, and `importlib.reload`
        # (reload swaps contents in place, so re-inserting the snapshot object is
        # a harmless no-op for same-identity keys and corrects re-imported ones).
        #
        # We deliberately do NOT delete modules a test newly imported: deleting a
        # legitimately-added key (e.g. a route module imported for the first time
        # mid-suite) breaks the next test that does `importlib.reload(<that>)` with
        # "module not in sys.modules". Restoration alone fixes the deletion-class
        # pollution; over-eager removal created a fresh failure class.
        for k, mod in snapshot.items():
            sys.modules[k] = mod


# Environment keys tests mutate directly (os.environ[...] = ...) without
# restoring — a second, independent pollution channel (reader != writer paths).
_TRACKED_ENV = (
    "CORVIN_HOME",
    "CORVIN_TENANT_ID",
    "VOICE_AUDIT_PATH",
    "XDG_CONFIG_HOME",
    "CORVIN_USE_ENGINE_LAYER",
    "CORVIN_OS_MODEL_ALLOW_HAIKU",
    "CORVIN_TTS_LOCAL_ONLY",
)


@pytest.fixture(autouse=True)
def _isolate_environ():
    """Snapshot + restore the tracked environment keys around each test.

    Also defaults CORVIN_TTS_LOCAL_ONLY=1 for every test: a route-level test
    that doesn't stub key resolution would otherwise make a LIVE billable
    OpenAI TTS call from pytest whenever a real key is configured on the host
    (observed 2026-07-19 — the "test mutates live service" class). Tests that
    genuinely exercise the cloud branch monkeypatch.delenv it explicitly and
    stub the openai module.
    """
    saved = {k: os.environ.get(k) for k in _TRACKED_ENV}
    os.environ["CORVIN_TTS_LOCAL_ONLY"] = "1"
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
