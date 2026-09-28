"""Fixtures for CEL tests."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

# Use relative import to avoid 'operator' stdlib conflict
from ..memory_lookup import MemoryLookup


@pytest.fixture(autouse=True)
def _member_tier_for_forging(monkeypatch):
    """Pin the licence tier to ``member`` for CEL tests (ADR-0701).

    The forge stages and both registries refuse ``forge.create`` on the free
    tier, fail-closed — which is correct, and is the tier a test environment
    without a licence resolves to. Tests that exercise forging therefore run
    as a member; the free-tier refusal is proven by tests that re-pin
    ``free`` explicitly (``test_forge_stages_adr0283.py::FreeTierSkips``)."""
    try:
        import corvin_operator.license.capability_api as _ca
    except ImportError:
        yield
        return
    monkeypatch.setattr(_ca, "active_tier", lambda **_k: "member")
    yield


@pytest.fixture(autouse=True)
def _fresh_corvin_home(tmp_path, monkeypatch):
    """One CORVIN_HOME per test.

    The CE licence pools (``ce_llm_quota.json``, ``context_engineering_quota.json``)
    are DAILY counters under CORVIN_HOME. Shared across a test file they are
    exhausted by the sixth synthesising test on the free tier (5/day), and
    every later test silently loses its synthesis — an order-dependent failure
    of ``test_full_pipeline_e2e.py``, not a product defect. A test that sets
    its own CORVIN_HOME still does; this only removes the shared default."""
    home = tmp_path / "corvin_home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    yield


@pytest.fixture
def temp_memory_dir():
    """Create temporary memory directory with sample files."""
    with TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Sample memory file 1: voice-related incident
        (tmppath / "voice-summary-context-loss.md").write_text(
            """---
name: voice_summary_context_loss
description: Voice summary drops critical context
metadata:
  type: incident
---

# Voice Summary Context Loss

Voice module drops critical warnings during text compression.
Fixed by: stripping verbatim, adding truncation warnings.

Related: [[voice-mode-2-adr0194]]
""",
            encoding="utf-8",
        )

        # Sample memory file 2: refactoring decision
        (tmppath / "feedback-recurring-race-fix-primitive.md").write_text(
            """---
name: recurring_race_primitive_fix
description: Fix primitive, not call-site, for recurring bugs
metadata:
  type: feedback
---

# Recurring Race: Fix Primitive Not Call-Site

When same bug class recurs 2+ times:
- Invariant in function (not call site patch)
- Mechanical completeness test
- Prevents future instances

Vs. patching each call-site individually.
""",
            encoding="utf-8",
        )

        # Sample memory file 3: test strategy
        (tmppath / "tests-audit-chain-isolation.md").write_text(
            """---
name: audit_chain_isolation
description: conftest binds VOICE_AUDIT_PATH to tmp
metadata:
  type: test-strategy
---

# Tests: Audit Chain Isolation

conftest must bind VOICE_AUDIT_PATH to tmp directory.
Otherwise: permanent events pollute GDPR compliance chain.

Prevents: audit chain corruption in test runs.
""",
            encoding="utf-8",
        )

        yield tmppath


@pytest.fixture
def memory_lookup(temp_memory_dir):
    """Create MemoryLookup instance with temp directory."""
    return MemoryLookup(memory_dir=temp_memory_dir, cache_ttl_minutes=1)


@pytest.fixture
def sample_enriched_task():
    """Create sample EnrichedTask-like object."""

    class FakeNormalized:
        summary = "Fix bug in voice module with recursive calls"

    class FakeEnrichedTask:
        def __init__(self):
            self.normalized = FakeNormalized()
            self.key_terms = ["voice", "recursion", "bug"]
            self.complexity = 0.5

    return FakeEnrichedTask()
