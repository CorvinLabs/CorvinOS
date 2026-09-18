"""Shared live-host helpers for console bundle proofs (ADR-0885 step 3).

Extracted from ``test_engine_config_real_data_e2e.py`` so more than one test
can prove "the string X is / is not in the JS the browser actually loads"
the honest way: a TRANSITIVE crawl of the served chunk graph over HTTP, plus
a positive control asserted BEFORE any absence — a one-level scan of the SPA
shell is vacuous, because every panel is a lazily imported chunk whose name
appears only inside an eager bundle, never in index.html.
"""
from __future__ import annotations

import re
import socket
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PORT = 8765
BASE = f"http://{HOST}:{PORT}"

_ASSET_RE = re.compile(r"assets/[A-Za-z0-9._-]+\.js")


def console_up() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except OSError:
        return False


def crawl_served_chunks() -> dict[str, str]:
    """Every JS chunk the browser can reach, fetched over HTTP, name → body."""
    plain = urllib.request.build_opener()
    with plain.open(f"{BASE}/console/", timeout=30) as resp:
        shell = resp.read().decode("utf-8", "replace")

    pending = set(_ASSET_RE.findall(shell))
    assert pending, f"no JS assets referenced by the SPA shell: {shell[:200]!r}"

    bodies: dict[str, str] = {}
    while pending:
        asset = pending.pop()
        if asset in bodies:
            continue
        try:
            with plain.open(f"{BASE}/console/{asset}", timeout=60) as resp:
                bodies[asset] = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            # A chunk name assembled at runtime from string fragments can crawl
            # into a 404; a genuinely missing chunk shows up as a positive
            # marker going missing, which callers assert separately.
            continue
        pending |= {ref for ref in _ASSET_RE.findall(bodies[asset]) if ref not in bodies}
    return bodies


def assert_present_then_absent(bodies: dict[str, str], present: str, absent: str) -> None:
    """Positive control FIRST — without it "absent" is indistinguishable from
    "the chunk was never fetched"."""
    hits = [name for name, body in bodies.items() if present in body]
    assert hits, (
        f"crawled {len(bodies)} served chunk(s) and none contains {present!r} — "
        f"the crawl did not reach the chunk, so an absence check would pass vacuously"
    )
    offenders = [name for name, body in bodies.items() if absent in body]
    assert not offenders, f"{absent!r} is still served in {offenders}"
