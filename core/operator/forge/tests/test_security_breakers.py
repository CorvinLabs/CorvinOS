"""ST9.4 + ST9.5 E2E: rate limiter + circuit breaker.

Fictional scenario: an operator's policy says "max 3 calls per minute,
trip the circuit after 2 consecutive failures, reset after 0.5s." We
forge a couple of tools and exercise the limits over MCP.

We also unit-test the breaker / limiter directly because the time-based
state-machine transitions are easier to assert without going through the
JSON-RPC dance.
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

from forge.breakers import BreakerRegistry, CircuitBreaker, RateLimiter
from forge.policy import Policy
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


# ---------- direct unit tests -------------------------------------------

def test_token_bucket_rate_limiter():
    print("\n[RateLimiter: token bucket math]")
    rl = RateLimiter(capacity=60)  # 60 per 60s = 1 token/s
    # capacity tokens are immediately consumable
    for i in range(60):
        consumed = rl.try_consume()
        if not consumed:
            t(f"failed at call {i+1}", False)
            return
    t("60 calls consumed up to capacity", True)
    t("61st call rejected (no tokens left)", rl.try_consume() is False)
    # wait long enough for ≥1 token to refill at 1/s
    time.sleep(1.2)
    t("after ~1.2s refill, 1 more call ok", rl.try_consume() is True)


def test_circuit_breaker_state_machine():
    print("\n[CircuitBreaker: CLOSED → OPEN → HALF_OPEN → CLOSED]")
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=0.5,
                        half_open_max=2)
    # CLOSED — successes don't move the needle
    cb.record_success()
    t("after success, state=CLOSED",     cb.state == "CLOSED")

    # 3 consecutive failures → OPEN
    cb.record_failure()
    cb.record_failure()
    transition = cb.record_failure()
    t("3rd failure → state=OPEN",        cb.state == "OPEN")
    t("transition reported as 'opened'", transition == "opened")
    ok, _ = cb.can_execute()
    t("OPEN refuses execution",          ok is False)

    # Wait reset_timeout, then can_execute → flips to HALF_OPEN
    time.sleep(0.6)
    ok, reason = cb.can_execute()
    t("after reset_timeout, HALF_OPEN allows probe",
      ok is True and reason == "half_open_probe")
    t("state is HALF_OPEN", cb.state == "HALF_OPEN")
    # Half-open allows up to half_open_max probes
    ok2, reason2 = cb.can_execute()
    t("second half-open probe also allowed",
      ok2 is True and reason2 == "half_open_probe")
    ok3, reason3 = cb.can_execute()
    t("third probe rejected (max=2)",
      ok3 is False and reason3 == "half_open_exhausted")

    # Probe succeeds → back to CLOSED
    transition = cb.record_success()
    t("HALF_OPEN + success → CLOSED",
      cb.state == "CLOSED" and transition == "closed_from_half_open")


def test_circuit_breaker_half_open_failure_reopens():
    print("\n[CircuitBreaker: HALF_OPEN failure → reopens immediately]")
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=0.3,
                        half_open_max=2)
    cb.record_failure(); cb.record_failure()
    t("OPEN after 2 failures", cb.state == "OPEN")
    time.sleep(0.4)
    cb.can_execute()  # transitions to HALF_OPEN, attempt 1
    t("HALF_OPEN after timeout", cb.state == "HALF_OPEN")
    transition = cb.record_failure()
    t("failure during HALF_OPEN re-opens",
      cb.state == "OPEN" and transition == "reopened_from_half_open")


def test_breaker_registry_uses_per_tool_overrides():
    print("\n[BreakerRegistry honors policy.rate_limit_per_tool]")
    pol = Policy(rate_limit_default_per_minute=30,
                 rate_limit_per_tool={"csv.heavy": 6})
    reg = BreakerRegistry(pol)
    rl_heavy = reg.get_limiter("csv.heavy")
    rl_other = reg.get_limiter("text.fmt")
    t("csv.heavy capacity=6", rl_heavy.capacity == 6)
    t("text.fmt capacity=30 (default)", rl_other.capacity == 30)
    # Same name returns same instance (state is shared)
    rl_heavy2 = reg.get_limiter("csv.heavy")
    t("registry returns same limiter instance",
      rl_heavy is rl_heavy2)


# ---------- MCP-level E2E -------------------------------------------------

QUICK_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"data": {"a": p.get("a", 0)}}))
'''
QUICK_SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}

CRASH_IMPL = '''#!/usr/bin/env python3
import json, sys
json.loads(sys.stdin.read())
sys.exit(7)
'''


def _forge(client, *, name, impl=QUICK_IMPL, schema=None, meta=None):
    args = {"name": name, "description": name,
            "input_schema": schema or QUICK_SCHEMA, "impl": impl}
    if meta is not None:
        args["meta"] = meta
    return client.request("tools/call",
                          {"name": "forge_tool", "arguments": args})


def test_rate_limit_rejects_after_capacity():
    print("\n[MCP: rate_limit=3/min, 4 quick calls → 4th rejected]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "rate_limit": {"default_calls_per_minute": 3},
            "circuit_breaker": {"enabled": False},  # isolate the rate-limit
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="quick")
            ok = []
            for i in range(4):
                resp = client.request("tools/call",
                                       {"name": "quick", "arguments": {"a": i}})
                ok.append(resp["result"].get("isError") is False)
        finally:
            client.close()
        t("first 3 calls succeed", ok[:3] == [True, True, True])
        t("4th call rejected",     ok[3] is False)
        # audit.jsonl should record rate_limit.exceeded
        audit = (td / "audit.jsonl").read_text().splitlines()
        events = [json.loads(l) for l in audit
                  if "rate_limit.exceeded" in l]
        t("rate_limit.exceeded event in audit",
          len(events) >= 1)
        t("event has tool=quick",
          all(e.get("tool") == "quick" for e in events))


def test_per_tool_rate_limit_override():
    print("\n[MCP: per-tool override beats default]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "rate_limit": {"default_calls_per_minute": 10,
                            "per_tool": {"slow_tool": 1}},
            "circuit_breaker": {"enabled": False},
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="slow_tool")
            r1 = client.request("tools/call",
                                 {"name": "slow_tool", "arguments": {}})
            r2 = client.request("tools/call",
                                 {"name": "slow_tool", "arguments": {}})
        finally:
            client.close()
        t("first call ok",      r1["result"].get("isError") is False)
        t("second call rejected (capacity=1)",
          r2["result"].get("isError") is True)
        t("error mentions rate_limit.exceeded",
          "rate_limit.exceeded" in r2["result"]["content"][0]["text"])


def test_circuit_breaker_opens_after_threshold():
    print("\n[MCP: 2 crashes trip the breaker, 3rd call refused without exec]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "rate_limit": {"default_calls_per_minute": 100},
            "circuit_breaker": {"enabled": True,
                                 "failure_threshold": 2,
                                 "reset_timeout": 60,
                                 "half_open_max": 1},
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="boom", impl=CRASH_IMPL,
                   schema={"type": "object", "properties": {}})
            r1 = client.request("tools/call",
                                 {"name": "boom", "arguments": {}})
            r2 = client.request("tools/call",
                                 {"name": "boom", "arguments": {}})
            t1 = time.monotonic()
            r3 = client.request("tools/call",
                                 {"name": "boom", "arguments": {}})
            elapsed = time.monotonic() - t1
        finally:
            client.close()
        t("call 1 fails (tool crashed)",  r1["result"].get("isError") is True)
        t("call 2 fails (tool crashed)",  r2["result"].get("isError") is True)
        t("call 3 fails (breaker open)",  r3["result"].get("isError") is True)
        text = r3["result"]["content"][0]["text"]
        t("call 3 error names breaker, NOT exec",
          "circuit_breaker" in text and "exit" not in text)
        t("call 3 returned fast (<200ms — no subprocess)",
          elapsed < 0.2, detail=f"elapsed={elapsed*1000:.0f}ms")
        # audit
        audit = (td / "audit.jsonl").read_text().splitlines()
        opened_events = [json.loads(l) for l in audit
                          if "circuit_breaker.opened" in l]
        t("circuit_breaker.opened event recorded",
          len(opened_events) >= 1)


def test_circuit_breaker_recovers_after_reset_timeout():
    print("\n[MCP: breaker trips, reset_timeout, success → CLOSED]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "rate_limit": {"default_calls_per_minute": 100},
            "circuit_breaker": {"enabled": True, "failure_threshold": 1,
                                 "reset_timeout": 0.5, "half_open_max": 1},
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            # 1) Fail-prone tool first
            _forge(client, name="flaky", impl=CRASH_IMPL,
                   schema={"type": "object", "properties": {}})
            client.request("tools/call",
                            {"name": "flaky", "arguments": {}})  # opens

            # 2) Replace impl with a clean one (overwrite=true)
            client.request("tools/call", {
                "name": "forge_tool",
                "arguments": {"name": "flaky", "description": "fixed",
                              "input_schema":
                                  {"type": "object", "properties": {}},
                              "impl": QUICK_IMPL,
                              "overwrite": True},
            })
            # state is still OPEN — calling immediately should be refused
            r_open = client.request("tools/call",
                                     {"name": "flaky", "arguments": {}})
            t("immediately after fix: still refused (still OPEN)",
              r_open["result"].get("isError") is True and
              "circuit_breaker" in r_open["result"]["content"][0]["text"])

            # 3) Wait reset_timeout then call → HALF_OPEN probe → CLOSED
            time.sleep(0.6)
            r_probe = client.request("tools/call",
                                      {"name": "flaky", "arguments": {}})
            t("after reset_timeout: probe succeeds",
              r_probe["result"].get("isError") is False)
            r_after = client.request("tools/call",
                                      {"name": "flaky", "arguments": {}})
            t("subsequent call also ok (breaker now CLOSED)",
              r_after["result"].get("isError") is False)
        finally:
            client.close()


def test_schema_error_does_not_count_against_breaker():
    print("\n[MCP: SchemaError does NOT trip the breaker]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "rate_limit": {"default_calls_per_minute": 100},
            "circuit_breaker": {"enabled": True, "failure_threshold": 2,
                                 "reset_timeout": 60, "half_open_max": 1},
        }))
        schema = {"type": "object", "required": ["a"],
                   "properties": {"a": {"type": "integer"}}}
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="strict", impl=QUICK_IMPL, schema=schema)
            # Send 3 schema-violating calls
            for _ in range(3):
                client.request("tools/call",
                                {"name": "strict", "arguments": {}})  # missing 'a'
            # Now a valid call — breaker should still be CLOSED
            r_ok = client.request("tools/call",
                                   {"name": "strict", "arguments": {"a": 1}})
        finally:
            client.close()
        t("after 3 schema errors, valid call still works",
          r_ok["result"].get("isError") is False,
          detail=f"text={r_ok['result']['content'][0]['text'][:80]}")


def main() -> int:
    test_token_bucket_rate_limiter()
    test_circuit_breaker_state_machine()
    test_circuit_breaker_half_open_failure_reopens()
    test_breaker_registry_uses_per_tool_overrides()
    test_rate_limit_rejects_after_capacity()
    test_per_tool_rate_limit_override()
    test_circuit_breaker_opens_after_threshold()
    test_circuit_breaker_recovers_after_reset_timeout()
    test_schema_error_does_not_count_against_breaker()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
