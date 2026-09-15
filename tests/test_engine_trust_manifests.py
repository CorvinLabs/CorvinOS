"""Every shipped engine-trust manifest must load through the real loader.

Why this exists
---------------
``operator/bridges/shared/test_engine_trust.py`` already checks that bundled
manifests parse, but it is a self-running script (``main()`` under
``__main__``) with no module-level ``test_*`` functions, so ``pytest``
collects ZERO tests from it — it only runs via
``operator/bridges/run-all-tests.sh``.  It also hard-codes three engine ids
(``claude_code``, ``codex_cli``, ``opencode``), so ``hermes`` and ``copilot``
were never covered at all.

Both gaps were load-bearing on 2026-09-15: commit fa16e74b added five fields
to ``agents/trust/claude_code.yaml`` (``name``, ``schema_version``,
``binary_url``, ``context_preservation``, ``attestation_method``) citing
ADR-0610 — which is the *plugin capabilities* manifest schema, a different
subsystem; engine trust is ADR-0020.  The loader's allowlist is fail-CLOSED,
so the manifest went ``manifest-malformed`` → ``effective_tier=low`` and
EVERY claude_code spawn was rejected.  Nothing was red: pytest collected no
trust tests and run-all-tests.sh was not run.

This module iterates the bundle directory itself rather than a fixed list,
so a NEW manifest is covered the moment it is added.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

import engine_trust  # noqa: E402


def _bundled_manifest_ids() -> list[str]:
    return sorted(p.stem for p in engine_trust._BUNDLE_TRUST_DIR.glob("*.yaml"))


def test_bundle_dir_is_populated():
    """Guard the guard: an empty glob would make every test below vacuous."""
    ids = _bundled_manifest_ids()
    assert ids, f"no trust manifests found in {engine_trust._BUNDLE_TRUST_DIR}"


@pytest.mark.parametrize("engine_id", _bundled_manifest_ids())
def test_bundled_manifest_loads(engine_id: str):
    """A shipped manifest must satisfy the loader's own schema.

    The loader is fail-closed: anything it rejects downgrades the engine to
    ``effective_tier=low`` and blocks the spawn, so a manifest that does not
    load is not a cosmetic problem — it takes the engine offline.
    """
    m = engine_trust.load_manifest(engine_id)
    assert m.metadata.engine_id == engine_id, (
        f"{engine_id}.yaml declares engine_id={m.metadata.engine_id!r}; "
        f"the loader resolves manifests by FILENAME, so a mismatch means the "
        f"file can never be found under its own declared id"
    )


@pytest.mark.parametrize("engine_id", _bundled_manifest_ids())
def test_bundled_manifest_yields_a_reason_free_verdict(engine_id: str):
    """The verdict must not be one of the two manifest-integrity failures.

    ``manifest-missing`` / ``manifest-malformed`` mean the file on disk could
    not be used at all.  Every other outcome (including a deliberate
    ``trust-tier-too-low`` for a low-tier engine such as hermes, or an
    ``manifest-expired`` once valid_until passes) is a real policy decision
    and NOT this test's business — asserting ``passed`` would either pin the
    tenant's min_tier or fail the day a manifest legitimately expires.
    """
    verdict = engine_trust.evaluate_trust(engine_id, min_tier="low")
    assert verdict.reason not in ("manifest-missing", "manifest-malformed"), (
        f"{engine_id}.yaml did not load: {verdict.reason} "
        f"{verdict.detail or ''}"
    )
