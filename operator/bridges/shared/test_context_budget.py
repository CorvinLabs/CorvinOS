#!/usr/bin/env python3
"""test_context_budget.py — E2E for Layer 20 context budget (Phase-1 MVP).

Drives the bookkeeping API against a real filesystem (CORVIN_HOME
redirected to a tempdir) and verifies:

  - register / account / check / working_set / evict / set_quota /
    set_oom_policy / unregister round-trip
  - check_budget action ladder: ok at <90%, warn at >=90%, oom at >100%
  - check_budget at the EXACT 100% quota boundary under reject policy
    (used == quota is a documented current-behavior boundary: `warn`,
    not `reject`, since the over-quota check is strict `>`)
  - eviction drops oldest until target reached, returns turn_ids in
    order, updates used, increments evictions counter
  - reject policy: action=reject, allowed=False
  - compress / evict policies: action set, allowed=True
  - set_quota / set_oom_policy + persistence (mtime-cache invalidation)
  - 4-thread concurrent account_turn: total tokens match expectation
  - validation: bad session_id, bad quota, bad oom_policy, missing
    session, negative tokens, target_pct out of range
  - format_budget_table renders chat-friendly output

Per-subtask E2E rule from CLAUDE.md: real filesystem, real fcntl
locks, no mocks.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_module(home: Path):
    os.environ["CORVIN_HOME"] = str(home)
    for mod in ("context_budget", "paths"):
        sys.modules.pop(mod, None)
    import context_budget  # type: ignore
    return context_budget


# --------------------------------------------------------------------- cases

def case_register_creates_record(home: Path) -> None:
    _section("register_session_budget creates a budget record")
    cb = _fresh_module(home)
    rec = cb.register_session_budget("s_a", quota=50_000)
    assert rec["session_id"] == "s_a"
    assert rec["quota"] == 50_000
    assert rec["used"] == 0
    assert rec["turns"] == []
    assert rec["oom_policy"] == "compress"
    print(f"  PASS quota={rec['quota']} used={rec['used']} policy={rec['oom_policy']}")
    # Persisted to disk
    assert cb.budgets_file().exists()
    print(f"  PASS persisted to {cb.budgets_file().name}")


def case_account_turn_adds_to_used(home: Path) -> None:
    _section("account_turn appends to working set, updates used")
    cb = _fresh_module(home)
    cb.register_session_budget("s_b", quota=10_000)
    cb.account_turn("s_b", "t1", 1500, content_hash="abc")
    cb.account_turn("s_b", "t2", 2300)
    rec = cb.get_budget("s_b")
    assert rec["used"] == 3800
    assert len(rec["turns"]) == 2
    assert rec["turns"][0]["turn_id"] == "t1"
    assert rec["turns"][0]["content_hash"] == "abc"
    print(f"  PASS used={rec['used']} after 2 turns")


def case_check_budget_action_ladder(home: Path) -> None:
    _section("check_budget action ladder: ok / warn / oom-policy")
    cb = _fresh_module(home)
    cb.register_session_budget("ladder", quota=1000, oom_policy="evict")

    # 0% used -> ok
    d = cb.check_budget("ladder")
    assert d["allowed"] and d["action"] == "ok", d
    print(f"  PASS at 0%: allowed={d['allowed']} action={d['action']}")

    # 50% used -> still ok
    cb.account_turn("ladder", "t1", 500)
    d = cb.check_budget("ladder")
    assert d["action"] == "ok", d
    print(f"  PASS at 50%: action={d['action']}")

    # 90% used -> warn
    cb.account_turn("ladder", "t2", 400)
    d = cb.check_budget("ladder")
    assert d["action"] == "warn", d
    assert d["allowed"] is True
    print(f"  PASS at 90%: action={d['action']} allowed={d['allowed']}")

    # 110% used -> evict (per oom_policy)
    cb.account_turn("ladder", "t3", 200)
    d = cb.check_budget("ladder")
    assert d["action"] == "evict"
    assert d["allowed"] is True  # evict is non-blocking
    print(f"  PASS at 110%: action={d['action']} allowed={d['allowed']}")


def case_check_budget_reject_policy_blocks(home: Path) -> None:
    _section("reject policy: action=reject, allowed=False above quota")
    cb = _fresh_module(home)
    cb.register_session_budget("strict", quota=1000, oom_policy="reject")
    cb.account_turn("strict", "t1", 1500)
    d = cb.check_budget("strict")
    assert d["action"] == "reject"
    assert d["allowed"] is False
    print(f"  PASS over-quota with reject: allowed={d['allowed']}")


def case_check_budget_reject_policy_exact_quota_boundary(home: Path) -> None:
    _section("reject policy at EXACT 100% quota boundary (used == quota)")
    cb = _fresh_module(home)
    cb.register_session_budget("boundary", quota=200, oom_policy="reject")
    cb.account_turn("boundary", "t1", 200)  # used == quota exactly, 100%
    d = cb.check_budget("boundary")
    assert d["used"] == 200 and d["quota"] == 200, d
    # NOTE: check_budget's over-quota branch is `if used > quota` (strict),
    # so at used == quota exactly it falls through to the warn branch
    # instead of triggering the reject policy. This locks in that
    # (surprising, possibly unintended) current behavior: a reject-policy
    # session sitting at exactly 100% usage is still allowed to proceed.
    assert d["action"] == "warn", d
    assert d["allowed"] is True, d
    print(
        "  PASS at exactly 100% under reject policy: "
        f"action={d['action']} allowed={d['allowed']} "
        "(boundary is > not >=, one token over is required to actually reject)"
    )

    # One token over the quota DOES trigger the reject policy.
    cb.account_turn("boundary", "t2", 1)  # used = 201, 100.5%
    d2 = cb.check_budget("boundary")
    assert d2["used"] == 201, d2
    assert d2["action"] == "reject", d2
    assert d2["allowed"] is False, d2
    print(f"  PASS at 201/200 (just over): action={d2['action']} allowed={d2['allowed']}")


def case_check_budget_pending_tokens(home: Path) -> None:
    _section("check_budget with pending_tokens previews future state")
    cb = _fresh_module(home)
    cb.register_session_budget("preview", quota=1000)
    cb.account_turn("preview", "t1", 500)  # now 50%

    d = cb.check_budget("preview", pending_tokens=600)
    # 500 + 600 = 1100, over quota
    assert d["used"] == 1100
    assert d["action"] != "ok", d
    print(f"  PASS pending lookahead: action={d['action']}")


def case_evict_drops_oldest_to_target(home: Path) -> None:
    _section("evict drops oldest turns until below target")
    cb = _fresh_module(home)
    cb.register_session_budget("evicting", quota=10_000)
    for i in range(5):
        cb.account_turn("evicting", f"t{i}", 2000)
    # used = 10_000 (100%)

    evicted = cb.evict("evicting", target_pct=0.5)  # target = 5_000
    # Need to drop 3 oldest: 2k+2k+2k = 6k removed, leaves 4k (under 5k)
    assert evicted == ["t0", "t1", "t2"], evicted
    rec = cb.get_budget("evicting")
    assert rec["used"] == 4000
    assert [t["turn_id"] for t in rec["turns"]] == ["t3", "t4"]
    assert rec["evictions"] == 3
    print(f"  PASS evicted {evicted}, remaining used={rec['used']}")


def case_evict_target_used_absolute(home: Path) -> None:
    _section("evict accepts absolute target_used")
    cb = _fresh_module(home)
    cb.register_session_budget("ab", quota=20_000)
    for i in range(4):
        cb.account_turn("ab", f"t{i}", 5_000)
    # used = 20_000

    evicted = cb.evict("ab", target_used=12_000)
    # Drop t0, t1: 10k removed, leaves 10k (under 12k)
    assert evicted == ["t0", "t1"], evicted
    rec = cb.get_budget("ab")
    assert rec["used"] == 10_000
    print(f"  PASS evicted to absolute target_used=12000, actual={rec['used']}")


def case_evict_validates_target_pct(home: Path) -> None:
    _section("evict rejects target_pct outside (0, 1)")
    cb = _fresh_module(home)
    cb.register_session_budget("v", quota=1000)
    for bad in (0.0, 1.0, -0.5, 1.5):
        try:
            cb.evict("v", target_pct=bad)
        except ValueError:
            print(f"  PASS rejected target_pct={bad}")
        else:
            raise AssertionError(f"expected ValueError for {bad}")


def case_set_quota_and_policy(home: Path) -> None:
    _section("set_quota and set_oom_policy update + persist")
    cb = _fresh_module(home)
    cb.register_session_budget("dyn", quota=10_000, oom_policy="evict")
    cb.set_quota("dyn", 50_000)
    cb.set_oom_policy("dyn", "reject")
    rec = cb.get_budget("dyn")
    assert rec["quota"] == 50_000
    assert rec["oom_policy"] == "reject"
    print(f"  PASS dyn -> quota={rec['quota']} policy={rec['oom_policy']}")

    # Force a fresh load (simulate another process reading)
    cb._read_cache.update(mtime=-1.0, data={})
    rec2 = cb.get_budget("dyn")
    assert rec2["quota"] == 50_000 and rec2["oom_policy"] == "reject"
    print("  PASS persisted across cache invalidation")


def case_concurrent_account_no_loss(home: Path) -> None:
    _section("4 threads concurrent account_turn: no loss, used = sum")
    cb = _fresh_module(home)
    cb.register_session_budget("hot", quota=1_000_000)

    N = 25
    THREADS = 4
    PER_TURN_TOKENS = 100

    def worker(tid: int) -> None:
        for i in range(N):
            cb.account_turn("hot", f"t{tid}_{i:03d}", PER_TURN_TOKENS)

    threads = [
        threading.Thread(target=worker, args=(t,)) for t in range(THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rec = cb.get_budget("hot")
    expected = THREADS * N * PER_TURN_TOKENS
    assert rec["used"] == expected, (
        f"expected {expected} tokens, got {rec['used']} — lock failed"
    )
    assert len(rec["turns"]) == THREADS * N
    print(f"  PASS used={rec['used']} turns={len(rec['turns'])} (no race losses)")


def case_register_validates_inputs(home: Path) -> None:
    _section("register_session_budget validates inputs")
    cb = _fresh_module(home)
    for bad_id in ("",):
        try:
            cb.register_session_budget(bad_id, quota=1000)
        except ValueError:
            print(f"  PASS rejected session_id={bad_id!r}")
        else:
            raise AssertionError("expected ValueError")
    for bad_q in (0, -1):
        try:
            cb.register_session_budget("s", quota=bad_q)
        except ValueError:
            print(f"  PASS rejected quota={bad_q}")
        else:
            raise AssertionError("expected ValueError")
    try:
        cb.register_session_budget("s2", quota=1000, oom_policy="explode")
    except ValueError:
        print("  PASS rejected oom_policy='explode'")
    else:
        raise AssertionError("expected ValueError")


def case_account_negative_tokens_rejected(home: Path) -> None:
    _section("account_turn rejects negative tokens")
    cb = _fresh_module(home)
    cb.register_session_budget("neg", quota=1000)
    try:
        cb.account_turn("neg", "t1", -5)
    except ValueError:
        print("  PASS rejected negative tokens")
        return
    raise AssertionError("expected ValueError")


def case_unregistered_session_raises(home: Path) -> None:
    _section("operations on unregistered session raise KeyError")
    cb = _fresh_module(home)
    for op in (
        lambda: cb.account_turn("ghost", "t1", 100),
        lambda: cb.check_budget("ghost"),
        lambda: cb.evict("ghost"),
        lambda: cb.working_set("ghost"),
        lambda: cb.set_quota("ghost", 100),
        lambda: cb.set_oom_policy("ghost", "evict"),
    ):
        try:
            op()
        except KeyError:
            continue
        raise AssertionError(f"expected KeyError from {op}")
    print("  PASS all 6 operations raise KeyError for missing session")


def case_get_budget_missing_returns_none(home: Path) -> None:
    _section("get_budget on missing session returns None")
    cb = _fresh_module(home)
    assert cb.get_budget("never") is None
    print("  PASS returns None")


def case_unregister_returns_true_when_existed(home: Path) -> None:
    _section("unregister_session_budget returns True/False correctly")
    cb = _fresh_module(home)
    cb.register_session_budget("rm", quota=100)
    assert cb.unregister_session_budget("rm") is True
    assert cb.unregister_session_budget("rm") is False  # second time
    assert cb.get_budget("rm") is None
    print("  PASS unregister roundtrip clean")


def case_list_budgets_sorted_by_usage(home: Path) -> None:
    _section("list_budgets sorted by usage descending")
    cb = _fresh_module(home)
    cb.register_session_budget("a", quota=10_000)
    cb.register_session_budget("b", quota=10_000)
    cb.register_session_budget("c", quota=10_000)
    cb.account_turn("a", "t", 100)
    cb.account_turn("b", "t", 5_000)
    cb.account_turn("c", "t", 2_000)
    out = [r["session_id"] for r in cb.list_budgets()]
    assert out == ["b", "c", "a"], out
    print(f"  PASS list order: {out}")


def case_format_budget_table_renders(home: Path) -> None:
    _section("format_budget_table renders a chat-friendly table")
    cb = _fresh_module(home)
    cb.register_session_budget("s_t1", quota=100_000, oom_policy="evict")
    cb.account_turn("s_t1", "t", 87_000)
    out = cb.format_budget_table(cb.list_budgets())
    print(out)
    for needle in ("SESSION", "QUOTA", "POLICY", "s_t1", "evict", "87"):
        assert needle in out, (needle, out)
    print("  PASS table contains expected columns + content")


def case_format_empty_returns_string(home: Path) -> None:
    _section("format_budget_table on empty returns explanatory string")
    cb = _fresh_module(home)
    s = cb.format_budget_table([])
    assert s == "(no session budgets registered)", s
    print(f"  PASS empty -> '{s}'")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved = os.environ.get("CORVIN_HOME")
    cases = [
        case_register_creates_record,
        case_account_turn_adds_to_used,
        case_check_budget_action_ladder,
        case_check_budget_reject_policy_blocks,
        case_check_budget_reject_policy_exact_quota_boundary,
        case_check_budget_pending_tokens,
        case_evict_drops_oldest_to_target,
        case_evict_target_used_absolute,
        case_evict_validates_target_pct,
        case_set_quota_and_policy,
        case_concurrent_account_no_loss,
        case_register_validates_inputs,
        case_account_negative_tokens_rejected,
        case_unregistered_session_raises,
        case_get_budget_missing_returns_none,
        case_unregister_returns_true_when_existed,
        case_list_budgets_sorted_by_usage,
        case_format_budget_table_renders,
        case_format_empty_returns_string,
    ]
    failures = 0
    for case in cases:
        home = Path(tempfile.mkdtemp(prefix="cb-test-"))
        try:
            case(home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    if saved is None:
        os.environ.pop("CORVIN_HOME", None)
    else:
        os.environ["CORVIN_HOME"] = saved

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
