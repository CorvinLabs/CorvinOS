"""ADR-0885 step 3 — the live host serves the Models console and NOT the
deleted Engine Configuration page.

Runs against the live console at 127.0.0.1:8765 (skipped when it is not
listening). Positive control first: the Models header caption — a rendered
string literal that survives minification — must be found in the crawled
chunk graph; only then is the old page's h1 asserted absent.
"""
from __future__ import annotations

import pytest

from tests.e2e._console_chunks import assert_present_then_absent, console_up, crawl_served_chunks

#: pages/models/tabs.ts::MARKER_HEADER — keep byte-identical.
MARKER_HEADER = "Which model serves each turn, what it costs, and what the selector has learned."
#: The deleted pages/engine-config.tsx h1. Verified to have no remaining
#: frontend source: `git grep 'Engine Configuration' -- web-next/src` = 0.
OLD_H1 = "Engine Configuration"
#: The deleted panel's h1 (also its old sidebar label).
OLD_COST_H1 = "Model Cost Optimizer"

pytestmark = pytest.mark.skipif(not console_up(), reason="console not listening on 127.0.0.1:8765")


@pytest.fixture(scope="module")
def bodies() -> dict[str, str]:
    return crawl_served_chunks()


def test_models_console_is_served_and_engine_configuration_is_gone(bodies) -> None:
    assert_present_then_absent(bodies, present=MARKER_HEADER, absent=OLD_H1)


def test_models_console_is_served_and_cost_optimizer_page_is_gone(bodies) -> None:
    assert_present_then_absent(bodies, present=MARKER_HEADER, absent=OLD_COST_H1)


def test_old_deep_links_still_answer_the_spa_shell() -> None:
    """The redirects are client-side; the host must still serve the shell for
    the old paths so the router can redirect (a 404 here would strand every
    bookmark)."""
    import urllib.request
    from tests.e2e._console_chunks import BASE

    for path in ("/console/app/engine-config", "/console/app/model-cost-optimizer",
                 "/console/app/model-selection", "/console/app/models"):
        with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as resp:
            assert resp.status == 200, path
