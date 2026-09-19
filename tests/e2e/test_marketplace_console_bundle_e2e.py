"""ADR-0892 — the live host serves the ONE marketplace and NOT the deleted
hub or package page.

Runs against the live console at 127.0.0.1:8765 (skipped when it is not
listening). Positive control first: the Marketplace header caption — a rendered
string literal that survives minification — must be found in the crawled
chunk graph; only then are the old surfaces' strings asserted absent.
"""
from __future__ import annotations

import pytest

from tests.e2e._console_chunks import assert_present_then_absent, console_up, crawl_served_chunks

#: pages/marketplace/tabs.ts::MARKER_HEADER — keep byte-identical.
MARKER_HEADER = (
    "Install, enable and remove plugins, skill packages and MCP tools — "
    "every action changes this install and is audited."
)
#: The deleted pages/marketplace-hub.tsx h1 / subtitle.
OLD_HUB_SUBTITLE = "Discover skills, plugins, tools, connectors, and layers"
#: The deleted components/PackageMarketplace.tsx h1.
OLD_PACKAGES_H1 = "Package Marketplace"
#: The deleted hub's only CTA — it navigated to a 404.
OLD_HUB_CTA = "Open in "

pytestmark = pytest.mark.skipif(not console_up(), reason="console not listening on 127.0.0.1:8765")


@pytest.fixture(scope="module")
def bodies() -> dict[str, str]:
    return crawl_served_chunks()


def test_marketplace_is_served_and_the_hub_is_gone(bodies) -> None:
    assert_present_then_absent(bodies, present=MARKER_HEADER, absent=OLD_HUB_SUBTITLE)


def test_marketplace_is_served_and_the_package_page_is_gone(bodies) -> None:
    assert_present_then_absent(bodies, present=MARKER_HEADER, absent=OLD_PACKAGES_H1)


def test_old_deep_links_still_answer_the_spa_shell() -> None:
    """The redirects are client-side; the host must still serve the shell for
    the old paths so the router can redirect (a 404 here would strand every
    bookmark)."""
    import urllib.request
    from tests.e2e._console_chunks import BASE

    for path in ("/console/app/marketplace-hub", "/console/app/plugin-center",
                 "/console/app/packages", "/console/app/marketplace"):
        with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as resp:
            assert resp.status == 200, path
