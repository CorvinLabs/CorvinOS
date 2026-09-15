"""ST9.1 E2E: Policy-Loader.

Fictional scenario: an operator hands the forge a workflow with a
``policy.json`` that says

  - cap CPU at 5s (manager wants 100s — must be clamped)
  - allow only ``csv`` and ``stats`` namespaces
  - forbid anything matching ``shell.*`` or ``debug.*``
  - rate-limit ``csv.heavy`` to 6 calls/min

We validate that the loader reads it correctly, that ``clamp_budget``
narrows manager-requested budgets, that ``name_allowed`` enforces the
allowlist + forbidden globs, and that the absent-file case yields strict
defaults.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.policy import Budget, Policy


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


SAMPLE_POLICY = {
    "version": 1,
    "default_budget": {"cpu_seconds": 3, "wall_seconds": 10,
                        "output_bytes": 1048576, "artifact_bytes": 8388608},
    "max_budget":      {"cpu_seconds": 5, "wall_seconds": 30,
                        "output_bytes": 4194304, "artifact_bytes": 67108864},
    "forbidden_imports": ["socket", "subprocess", "ctypes"],
    "forbidden_tool_names": ["shell.*", "debug.*"],
    "allowed_namespaces": ["csv", "stats"],
    "rate_limit": {"default_calls_per_minute": 30,
                    "per_tool": {"csv.heavy": 6}},
    "circuit_breaker": {"enabled": True, "failure_threshold": 4,
                         "reset_timeout": 90, "half_open_max": 3},
    "network": {"default": False},
    "audit": {"hash_chain": True},
}


def test_missing_file_yields_strict_defaults():
    print("\n[missing policy.json → strict defaults]")
    with tempfile.TemporaryDirectory() as td:
        pol = Policy.load(Path(td))
        t("default_budget.cpu_seconds = 10", pol.default_budget.cpu_seconds == 10)
        t("max_budget.cpu_seconds = 60",     pol.max_budget.cpu_seconds == 60)
        t("forbidden_imports include socket",
          "socket" in pol.forbidden_imports)
        t("forbidden_tool_names default includes shell.*",
          "shell.*" in pol.forbidden_tool_names)
        t("allowed_namespaces is None (= no allowlist)",
          pol.allowed_namespaces is None)
        t("rate_limit_default = 60",
          pol.rate_limit_default_per_minute == 60)
        t("circuit_breaker_enabled = True",
          pol.circuit_breaker_enabled is True)
        t("network deny-by-default", pol.network_default is False)
        t("audit hash-chain on by default", pol.audit_hash_chain is True)


def test_loads_custom_policy():
    print("\n[explicit policy.json overrides every field]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps(SAMPLE_POLICY))
        pol = Policy.load(td)
        t("default_budget.cpu_seconds = 3",
          pol.default_budget.cpu_seconds == 3)
        t("max_budget.cpu_seconds = 5",
          pol.max_budget.cpu_seconds == 5)
        t("forbidden_imports = [socket, subprocess, ctypes]",
          pol.forbidden_imports == ["socket", "subprocess", "ctypes"])
        t("forbidden_tool_names = [shell.*, debug.*]",
          pol.forbidden_tool_names == ["shell.*", "debug.*"])
        t("allowed_namespaces = [csv, stats]",
          pol.allowed_namespaces == ["csv", "stats"])
        t("rate_limit_default = 30",
          pol.rate_limit_default_per_minute == 30)
        t("rate_limit_per_tool[csv.heavy] = 6",
          pol.rate_limit_per_tool.get("csv.heavy") == 6)
        t("cb.failure_threshold = 4",
          pol.circuit_breaker_failure_threshold == 4)
        t("cb.reset_timeout = 90",
          pol.circuit_breaker_reset_timeout == 90.0)
        t("cb.half_open_max = 3",
          pol.circuit_breaker_half_open_max == 3)


def test_malformed_json_raises():
    print("\n[malformed policy.json raises clear ValueError]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text("this is not json")
        try:
            Policy.load(td)
            t("malformed → raises", False)
        except ValueError as e:
            t("malformed → raises ValueError", True)
            t("error message says 'malformed'",
              "malformed" in str(e))


def test_clamp_budget():
    print("\n[clamp_budget enforces the envelope]")
    pol = Policy(
        default_budget=Budget(5, 10, 1024, 4096),
        max_budget=Budget(10, 20, 2048, 8192),
    )

    # Manager requests within max → not clamped
    applied, info = pol.clamp_budget(Budget(8, 15, 1500, 6000))
    t("within-envelope budget passes through",
      (applied.cpu_seconds, applied.wall_seconds,
       applied.output_bytes, applied.artifact_bytes) == (8, 15, 1500, 6000))
    t("no clamp_info on within-envelope", info == {})

    # Manager requests beyond max → clamped down
    applied, info = pol.clamp_budget({"cpu_seconds": 100, "wall_seconds": 999,
                                       "output_bytes": 99999, "artifact_bytes": 99999})
    t("CPU clamped to 10",      applied.cpu_seconds == 10)
    t("wall clamped to 20",     applied.wall_seconds == 20)
    t("stdout clamped to 2048", applied.output_bytes == 2048)
    t("artifacts clamped to 8192", applied.artifact_bytes == 8192)
    t("clamp_info reports CPU was 100→10",
      info.get("cpu_seconds") == (100, 10))
    t("clamp_info has 4 fields", len(info) == 4)

    # No request → default_budget, no clamping
    applied, info = pol.clamp_budget(None)
    t("no request → default_budget",
      applied.cpu_seconds == 5 and applied.wall_seconds == 10)
    t("no request → no clamp_info", info == {})


def test_name_allowed_forbidden_globs():
    print("\n[name_allowed: forbidden_tool_names is absolute deny]")
    pol = Policy(forbidden_tool_names=["shell.*", "danger.*", "exact"])
    ok, reason = pol.name_allowed("shell.execute")
    t("shell.execute denied", not ok)
    t("reason names rule",   reason == "forbidden:shell.*")
    ok, _ = pol.name_allowed("danger.detonate")
    t("danger.detonate denied", not ok)
    ok, reason = pol.name_allowed("exact")
    t("exact-match name denied", not ok and reason == "forbidden:exact")
    ok, _ = pol.name_allowed("csv.stats")
    t("unrelated name allowed", ok)


def test_name_allowed_with_namespace_allowlist():
    print("\n[name_allowed: namespace allowlist + forbidden = both rules]")
    pol = Policy(
        forbidden_tool_names=["shell.*"],
        allowed_namespaces=["csv", "stats", "text"],
    )
    ok, _ = pol.name_allowed("csv.group_stats")
    t("csv.* allowed", ok)
    ok, reason = pol.name_allowed("network.fetch")
    t("network.* denied (not in allowlist)", not ok)
    t("reason cites namespace_not_allowed",
      reason.startswith("namespace_not_allowed:"))
    # Forbidden beats allowlist if both rules would match
    ok, reason = pol.name_allowed("shell.execute")
    t("forbidden glob fires before namespace check",
      not ok and reason == "forbidden:shell.*")


def test_rate_limit_for_tool():
    print("\n[rate_limit_for: per-tool override + default fallback]")
    pol = Policy(
        rate_limit_default_per_minute=30,
        rate_limit_per_tool={"csv.heavy": 6, "stats.fast": 120},
    )
    t("override applies for csv.heavy",
      pol.rate_limit_for("csv.heavy") == 6)
    t("override applies for stats.fast",
      pol.rate_limit_for("stats.fast") == 120)
    t("default applies for unknown tools",
      pol.rate_limit_for("text.format") == 30)


def main() -> int:
    test_missing_file_yields_strict_defaults()
    test_loads_custom_policy()
    test_malformed_json_raises()
    test_clamp_budget()
    test_name_allowed_forbidden_globs()
    test_name_allowed_with_namespace_allowlist()
    test_rate_limit_for_tool()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
