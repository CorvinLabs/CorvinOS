"""conftest.py — pytest session fixtures for operator/bridges/shared/ tests.

Autouse fixture 1: reset CLAG shadow hashes before each test so that
cross-test shadow contamination does not produce false ChainIntegrityFailure
failures in tests that call consent.is_granted() or disclosure.mark_seen()
and share the "L16.consent_gate" / "L19.disclosure_gate" layer_id.

Autouse fixture 2 (ADR-0215 adversarial review, 2026-07-24, CRITICAL): many
tests in this directory fake the `claude` binary by prepending a fake-bin
directory onto PATH alone. `helper_model.resolve_claude_bin()` and
`agents.claude_code._configured_claude_bin()` BOTH check CORVIN_CLAUDE_BIN
(then CLAUDE_BIN) BEFORE ever consulting PATH — by design, so a real
deployment's pin survives a stripped systemd PATH. On any machine where
CORVIN_CLAUDE_BIN is set to the real CLI (the CANONICAL, documented
production pin — see house_rules.py's "[Engine-Autodetect Stripped-PATH]"
docstring — which is exactly the kind of machine these tests exist to
protect), a PATH-only fake silently gets bypassed and the REAL `claude`
CLI is spawned against synthetic test input instead: real API cost, and
unpredictable (place a real personalized reply where a deterministic fake
was expected) output asserted against as if it were the fake. Reproduced
directly: with CORVIN_CLAUDE_BIN set, test_adapter_engine_path.py's
simple-prompt test took 12s and failed on an idle-timeout instead of the
fake script's instant echo; unset, 0.3s and green. This fixture clears
both vars before every test and restores the ORIGINAL (pre-test) value
after — a test that legitimately needs a specific pin (e.g.
test_engine_path_no_engine_reachable_surfaces_clear_notice) still sets it
itself inside the test body, which composes fine with a clear-before/
restore-after wrapper.

Autouse fixture 3 (ADR-0215 follow-up, 2026-07-24, root cause of a
pre-existing, test-order-dependent flake in test_adapter_security_
hardening.py): the SAME "sets os.environ, never restores it" pattern as
fixture 2, but generalized and traced to its actual root cause.
test_adapter_btw.py's and test_adapter_audit.py's `_fresh_adapter(env_
overrides)` helpers do `os.environ[k] = v` for every override key —
including `ADAPTER_BRIDGES_DIR`, pointed at a `tempfile.mkdtemp()`
directory — with NO try/finally, NO teardown, NO monkeypatch. Once any
test in either file runs, `ADAPTER_BRIDGES_DIR` stays set in os.environ
for the REST OF THE PYTEST PROCESS, pointing at a now-deleted temp dir.
`test_adapter_security_hardening.py::test_whitelist_missing_logs_warning`
(and 10 sibling tests) write their fixture settings to the REAL
`operator/bridges/telegram/settings.json` (no ADAPTER_BRIDGES_DIR of
their own) — but `adapter._load_channel_settings()` honours the leaked,
stale ADAPTER_BRIDGES_DIR first, resolves to the deleted temp dir, gets
FileNotFoundError, and returns `{}` — silently the wrong config source.
Reproduced directly via bisection: this file's 11 tests fail ONLY when
run after test_adapter_btw.py/test_adapter_audit.py in the same pytest
process, never in isolation. Rather than patch every test file that sets
os.environ without restoring it (an unbounded, recurring class — fixture
2 above is the same problem, narrower), this fixture snapshots the FULL
environment before every test and restores it exactly afterward — the
general form that makes the whole bug class structurally impossible
going forward, superseding the need for per-variable fixtures like #2
(kept for its detailed diagnostic value, not because it's still load-
bearing on its own).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_shared_dir = Path(__file__).resolve().parent
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))

_forge_inner = Path(__file__).resolve().parents[2] / "forge" / "forge"
if str(_forge_inner) not in sys.path:
    sys.path.insert(0, str(_forge_inner))

try:
    from clag import clear_shadow_hashes as _clear_shadows  # type: ignore
    _HAS_CLAG = True
except ImportError:
    _HAS_CLAG = False


@pytest.fixture(autouse=True)
def _reset_clag_shadows():
    """Clear CLAG per-layer shadow hashes before and after every test."""
    if _HAS_CLAG:
        _clear_shadows()
    yield
    if _HAS_CLAG:
        _clear_shadows()


@pytest.fixture(autouse=True)
def _isolate_claude_bin_pin():
    """See module docstring, autouse fixture 2 — prevents PATH-only fake
    `claude` binaries from being silently bypassed by a real
    CORVIN_CLAUDE_BIN/CLAUDE_BIN pin in the ambient environment."""
    saved = {k: os.environ.get(k) for k in ("CORVIN_CLAUDE_BIN", "CLAUDE_BIN")}
    os.environ.pop("CORVIN_CLAUDE_BIN", None)
    os.environ.pop("CLAUDE_BIN", None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _snapshot_and_restore_environ():
    """See module docstring, autouse fixture 3 — the general fix for the
    "test sets os.environ, never restores it" bug class (root cause of the
    test_adapter_security_hardening.py ADAPTER_BRIDGES_DIR flake). Runs
    OUTER-MOST relative to fixture 2 isn't required — order between
    autouse fixtures at the same scope doesn't matter here since both only
    read/restore os.environ around the test body, and pytest tears down
    fixtures in reverse setup order regardless."""
    before = dict(os.environ)
    yield
    after_keys = set(os.environ.keys())
    before_keys = set(before.keys())
    for k in after_keys - before_keys:
        os.environ.pop(k, None)
    for k in before_keys:
        if os.environ.get(k) != before[k]:
            os.environ[k] = before[k]
