"""Crawl: every GET in ``app.routes``, tenant-B session, path params from tenant-A fixtures,
no tenant-A marker in any body. Reports a status histogram; requires a floor of 200s and
caps timeouts so a crawl that reaches nothing cannot pass.

Red on main (3029088f8) on three routes: ``/memory`` and ``/memory/{name}`` (S-1a,
``routes/memory.py:33-61``) and ``/plugins/corvin-knowledge/config`` (S-1b: one shared
``~/.claude/plugins/corvin-knowledge.json`` for every tenant,
``routes/plugins_corvin_knowledge_api.py:43,55-61``). Green after S-1a and S-1b land.
The S-5f stores (roles, quota, consent, disclosure) are planted for tenant A; no GET route
returned their marker to tenant B in this crawl.
"""
from __future__ import annotations

from collections import Counter

import pytest

from ._preconditions import require

require()

import httpx  # noqa: E402

from . import harness  # noqa: E402
from .conftest import MARKER_A, MEMORY_FILE_A  # noqa: E402

SKIP_FRAGMENTS = ("stream", "/ws", "openapi", "/docs", "/redoc", "/events", "/sse")
MIN_200 = 300      # measured 327 x 200 on 3029088f8 (WP-02.md); fewer means the crawl went blind
MAX_TIMEOUTS = 10


def _fill(path: str, params: list[str]) -> str:
    for p in params:
        value = MEMORY_FILE_A if ("name" in p or "file" in p) else harness.TENANT_A
        path = path.replace("{" + p + "}", value)
    return path


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "S-1a + S-1b open: tenant-A marker on /memory, /memory/{name} (routes/memory.py:33-61) and "
    "/plugins/corvin-knowledge/config (routes/plugins_corvin_knowledge_api.py:43, one shared "
    "~/.claude/plugins/corvin-knowledge.json); turns green with S-1a (no ~/.claude in memory routes) "
    "and S-1b (KG config per tenant); remove this mark in the commit that lands the second of the two"))
def test_crawl_tenant_b_sees_no_tenant_a_marker(server, planted, routes, sess_b):
    hist: Counter = Counter()
    leaks, timeouts = [], []
    with server.client() as c:
        for r in harness.get_routes(routes):
            if any(f in r["path"] for f in SKIP_FRAGMENTS):
                hist["skipped"] += 1
                continue
            url = _fill(r["path"], r["params"])
            try:
                resp = c.get(url, cookies=sess_b.cookies(), timeout=10.0)
            except httpx.TimeoutException:
                timeouts.append(url)
                continue
            hist[resp.status_code] += 1
            if MARKER_A in resp.text:
                leaks.append(f"{resp.status_code} {url}")
    summary = f"histogram={dict(sorted(hist.items(), key=str))} timeouts={len(timeouts)} " \
              f"stores_planted={planted['stores']}"
    print(summary)
    assert len(timeouts) <= MAX_TIMEOUTS, f"too many timeouts ({timeouts[:10]}); {summary}"
    assert hist[200] >= MIN_200, f"crawl too blind: {hist[200]} x 200 < {MIN_200}; {summary}"
    assert not leaks, f"tenant-A marker visible to tenant B on {len(leaks)} route(s): {leaks[:20]}; {summary}"
