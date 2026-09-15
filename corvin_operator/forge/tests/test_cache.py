"""ST4 E2E: determinism cache.

Validates that tools forged with ``meta.deterministic=true`` get
their results cached: a second identical call replays the envelope from
disk, with sandbox="cache" and meta.replayed_from set.

Fictional task: forge a CPU-burning prime-counting tool that takes
noticeable time on the first call. Second identical call should return
in <50ms. Different input → cache miss → real run again.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp import MCPClient


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


PRIME_IMPL = '''#!/usr/bin/env python3
"""Count primes up to n using a slow trial-division loop (deterministic)."""
import json, sys
p = json.loads(sys.stdin.read())
n = int(p["n"])
def is_prime(k):
    if k < 2: return False
    i = 2
    while i*i <= k:
        if k % i == 0: return False
        i += 1
    return True
count = sum(1 for k in range(2, n+1) if is_prime(k))
print(json.dumps({
    "ok": True, "status": 200,
    "data": {"n": n, "primes": count, "summary": f"pi({n}) = {count}"},
    "error": None,
    "meta": {"deterministic": True, "side_effects": False}
}))
'''

NONDET_IMPL = '''#!/usr/bin/env python3
"""Returns a random number — explicitly non-deterministic."""
import json, sys, random
json.loads(sys.stdin.read())
print(json.dumps({"r": random.random()}))
'''

PRIME_SCHEMA = {"type": "object", "required": ["n"],
                "properties": {"n": {"type": "integer"}}}


def _forge(client, name, schema, impl, *, meta=None):
    args = {
        "name": name,
        "description": name,
        "input_schema": schema,
        "impl": impl,
    }
    if meta is not None:
        args["meta"] = meta
    return client.request("tools/call",
                          {"name": "forge_tool", "arguments": args})


def test_deterministic_cache_hit_on_second_identical_call():
    print("\n[deterministic tool: 2nd identical call hits the cache]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "count_primes", PRIME_SCHEMA, PRIME_IMPL,
                   meta={"deterministic": True, "side_effects": False})

            # First call: real run
            t1 = time.monotonic()
            r1 = client.request("tools/call", {
                "name": "count_primes",
                "arguments": {"n": 30000},
            }, timeout=20.0)
            d1 = time.monotonic() - t1
            s1 = r1["result"]["structuredContent"]
            t("call 1 ok", r1["result"].get("isError") is False)
            t("call 1 sandbox is bwrap or rlimits (not cache)",
              s1.get("sandbox") in ("bwrap", "rlimits"))
            t("call 1 has primes count", s1["data"].get("primes") > 0,
              detail=f"primes={s1['data']['primes']}")
            primes_seen = s1["data"]["primes"]

            # Second identical call: should hit cache
            t2 = time.monotonic()
            r2 = client.request("tools/call", {
                "name": "count_primes",
                "arguments": {"n": 30000},
            }, timeout=5.0)
            d2 = time.monotonic() - t2
            s2 = r2["result"]["structuredContent"]
            t("call 2 sandbox = cache",
              s2.get("sandbox") == "cache",
              detail=f"sandbox={s2.get('sandbox')}")
            t("call 2 same primes count",
              s2["data"].get("primes") == primes_seen)
            replayed = (s2.get("envelope", {}).get("meta") or {}).get("replayed_from")
            t("call 2 envelope.meta.replayed_from set",
              isinstance(replayed, str) and len(replayed) > 0,
              detail=f"replayed_from={replayed}")
            t("cache hit faster than first call",
              d2 < d1 * 0.9 if d1 > 0.1 else True,
              detail=f"first={d1*1000:.1f}ms cache={d2*1000:.1f}ms")

            # Third call with different n → cache miss, real run again
            r3 = client.request("tools/call", {
                "name": "count_primes",
                "arguments": {"n": 100},
            })
            s3 = r3["result"]["structuredContent"]
            t("different input → real sandbox again",
              s3.get("sandbox") in ("bwrap", "rlimits"))
            t("different input → different result",
              s3["data"]["primes"] != primes_seen)
        finally:
            client.close()


def test_nondeterministic_tool_does_not_cache():
    print("\n[non-deterministic tool: each call freshly runs]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "noise", {"type": "object", "properties": {}},
                   NONDET_IMPL)  # no meta → not cached
            r1 = client.request("tools/call",
                                {"name": "noise", "arguments": {}})
            r2 = client.request("tools/call",
                                {"name": "noise", "arguments": {}})
        finally:
            client.close()

        s1 = r1["result"]["structuredContent"]
        s2 = r2["result"]["structuredContent"]
        t("call 1 not cached", s1.get("sandbox") != "cache")
        t("call 2 not cached", s2.get("sandbox") != "cache")
        t("two different random results",
          s1["data"]["r"] != s2["data"]["r"])


def test_cache_file_lives_under_workspace_cache():
    print("\n[cache file is on disk under .forge/cache/]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "count_primes", PRIME_SCHEMA, PRIME_IMPL,
                   meta={"deterministic": True})
            client.request("tools/call",
                           {"name": "count_primes",
                            "arguments": {"n": 200}})
        finally:
            client.close()

        cache_dir = td / "cache"
        t("cache dir exists", cache_dir.is_dir())
        files = list(cache_dir.glob("*.json"))
        t("exactly one cache file written", len(files) == 1,
          detail=f"got {[f.name for f in files]}")
        record = json.loads(files[0].read_text())
        t("record has python_tag",
          isinstance(record.get("python_tag"), str))
        t("record has run_id pointing to a real run",
          isinstance(record.get("run_id"), str))
        t("record envelope.meta.deterministic = True",
          record["envelope"]["meta"].get("deterministic") is True)


def test_cache_invalidates_on_tool_recreation_with_different_impl():
    print("\n[recreating tool with different impl invalidates cache]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "count_primes", PRIME_SCHEMA, PRIME_IMPL,
                   meta={"deterministic": True})
            r1 = client.request("tools/call",
                                {"name": "count_primes",
                                 "arguments": {"n": 200}})
            sandbox1 = r1["result"]["structuredContent"]["sandbox"]
            t("first run not cached (fresh)",
              sandbox1 in ("bwrap", "rlimits"))

            # Now overwrite with a slightly modified impl (different sha)
            modified = PRIME_IMPL.replace("primes", "primes_v2")
            client.request("tools/call", {
                "name": "forge_tool",
                "arguments": {
                    "name": "count_primes",
                    "description": "v2",
                    "input_schema": PRIME_SCHEMA,
                    "impl": modified,
                    "overwrite": True,
                    "meta": {"deterministic": True},
                },
            })
            # Same input — but tool_sha changed, so it must miss the cache
            r2 = client.request("tools/call",
                                {"name": "count_primes",
                                 "arguments": {"n": 200}})
            sandbox2 = r2["result"]["structuredContent"]["sandbox"]
            t("after overwrite → cache miss (sandbox != cache)",
              sandbox2 in ("bwrap", "rlimits"),
              detail=f"sandbox={sandbox2}")
        finally:
            client.close()


def main() -> int:
    test_deterministic_cache_hit_on_second_identical_call()
    test_nondeterministic_tool_does_not_cache()
    test_cache_file_lives_under_workspace_cache()
    test_cache_invalidates_on_tool_recreation_with_different_impl()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
