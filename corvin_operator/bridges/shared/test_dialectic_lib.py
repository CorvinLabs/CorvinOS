#!/usr/bin/env python3
"""E2E tests for the native dialectic decision library (Layer 11).

Covers:
  - Heat-Score gate filters trivial decisions out (returns thesis-only)
  - Mode resolution: profile → cfg → bundle default
  - Mode lookup chain: explicit > profile-override > profile-disable > cfg-disable > cfg > bundle
  - All four modes (off / fast / skill / cli)
  - skill-mode produces a wrapper block with thesis+antithesis+synthesis
  - cli-mode subprocess command structure (mocked via env-overridable shim)
  - Recursion guard: depth ≥ 1 degrades to off
  - The pre-calibration table (13 fictive tasks → trigger expectations)
  - CI-lint: ``import anthropic`` is forbidden in dialectic.py

Run as: python3 operator/bridges/shared/test_dialectic_lib.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Sandbox CORVIN_HOME BEFORE importing dialectic so config writes go to
# /tmp, not the real .corvinOS workspace.
_TD = Path(tempfile.mkdtemp(prefix="dialectic-lib-test-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"

import dialectic  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def reset_config():
    """Wipe the on-disk config + cache so each block starts fresh."""
    p = dialectic._config_path()
    if p.exists():
        p.unlink()
    # Bust the in-process cache.
    dialectic._CONFIG_CACHE = {}
    dialectic._CONFIG_MTIME = 0.0


# ── 1. Heat-Score formula calibration ──────────────────────────────────────

def case_heat_score_formula():
    print("\n[1] Heat-Score formula returns calibrated values")
    # Spot-check: skill-promotion task A (csv_diff → user-scope, 5 grades)
    # consequence=1.0, uncertainty=0.2, scope=5 → 0.4 + 0.06 + 0.3 = 0.76
    h = dialectic.heat_score(1.0, 0.2, 5)
    t("task A: csv_diff promotion → 0.76",
      abs(h - 0.76) < 1e-9, detail=f"got {h}")
    # Trivial routing (open URL, conf 0.85): 0.2 / 0.15 / 1
    h2 = dialectic.heat_score(0.2, 0.15, 1)
    # 0.4*0.2 + 0.3*0.15 + 0.3*0.2 = 0.08 + 0.045 + 0.06 = 0.185
    t("task D: trivial routing → 0.185",
      abs(h2 - 0.185) < 1e-9, detail=f"got {h2}")
    # Path-gate ambiguous Bash (eval $X with hint): 0.9 / 0.7 / 3
    h3 = dialectic.heat_score(0.9, 0.7, 3)
    # 0.4*0.9 + 0.3*0.7 + 0.3*0.6 = 0.36 + 0.21 + 0.18 = 0.75
    t("task H: ambiguous Bash → 0.75",
      abs(h3 - 0.75) < 1e-9, detail=f"got {h3}")
    # Edge: clamp inputs to [0,1] for c/u, scope to [0,5] band.
    t("clamp: consequence > 1 clamped",
      dialectic.heat_score(2.0, 0, 0) == dialectic.heat_score(1.0, 0, 0))
    t("clamp: scope > 5 clamped",
      dialectic.heat_score(0, 0, 99) == dialectic.heat_score(0, 0, 5))


# ── 2. Heat-Score gate filters below-threshold ─────────────────────────────

def case_heat_gate_short_circuits():
    print("\n[2] Heat-Score gate: below threshold → thesis-only / mode=off")
    reset_config()
    d = dialectic.decide(
        site="auto_routing",
        thesis={"persona": "browser", "confidence": 0.85},
        antithesis={"persona": "research", "confidence": 0.4},
        consequence=0.2, uncertainty=0.15, scope=1,  # heat ≈ 0.185
    )
    t("trivial routing: choice == thesis (no synthesis)",
      d.choice == {"persona": "browser", "confidence": 0.85})
    t("trivial routing: mode=off",
      d.mode == "off")
    t("trivial routing: antithesis == None (not built)",
      d.antithesis is None)
    t("trivial routing: heat captured",
      abs(d.heat - 0.185) < 1e-9)


# ── 3. Mode lookup chain ───────────────────────────────────────────────────

def case_mode_lookup_chain():
    print("\n[3] Mode resolution chain")
    reset_config()

    # Default — bundle says skill_promotion=skill
    t("default: skill_promotion → skill",
      dialectic.resolve_mode(site="skill_promotion") == "skill")

    # cfg disabled → off for all
    cfg = dialectic.load_config()
    cfg["enabled"] = False
    dialectic.save_config(cfg)
    t("cfg.enabled=false → all sites off",
      dialectic.resolve_mode(site="skill_promotion") == "off"
      and dialectic.resolve_mode(site="auto_routing") == "off")

    # cfg re-enabled, per-site mode set
    cfg["enabled"] = True
    cfg.setdefault("modes", {})["skill_promotion"] = "cli"
    dialectic.save_config(cfg)
    t("cfg.modes[site] = cli → cli",
      dialectic.resolve_mode(site="skill_promotion") == "cli")

    # profile disable beats cfg
    t("profile.dialectic_enabled=false beats cfg",
      dialectic.resolve_mode(site="skill_promotion",
                             profile={"dialectic_enabled": False}) == "off")

    # profile per-site override beats cfg
    t("profile.dialectic_mode_<site> beats cfg",
      dialectic.resolve_mode(site="skill_promotion",
                             profile={"dialectic_mode_skill_promotion": "off"})
      == "off")

    # explicit kwarg beats everything
    t("explicit kwarg beats profile + cfg",
      dialectic.resolve_mode(site="skill_promotion",
                             profile={"dialectic_mode_skill_promotion": "off"},
                             explicit="fast") == "fast")


# ── 4. Mode = off ──────────────────────────────────────────────────────────

def case_mode_off():
    print("\n[4] mode=off → thesis returned, no synthesis")
    reset_config()
    cfg = dialectic.load_config()
    cfg["modes"]["skill_promotion"] = "off"
    dialectic.save_config(cfg)
    d = dialectic.decide(
        site="skill_promotion",
        thesis="promote-OK",
        antithesis="promote-NO",
        heat=1.0,  # above threshold so we exercise the off branch, not the gate
    )
    t("off: choice == thesis",
      d.choice == "promote-OK")
    t("off: mode=off",
      d.mode == "off")
    t("off: synthesis is thesis stringified",
      d.synthesis == "promote-OK")


# ── 5. Mode = fast (auto_routing rule) ─────────────────────────────────────

def case_mode_fast_auto_routing():
    print("\n[5] mode=fast: auto_routing higher-confidence wins")
    reset_config()
    # Force fast mode for auto_routing with explicit kwarg.
    d = dialectic.decide(
        site="auto_routing",
        thesis={"persona": "research", "confidence": 0.5},
        antithesis={"persona": "browser", "confidence": 0.7},
        heat=0.9,  # above gate
        mode="fast",
    )
    t("fast/auto_routing: higher confidence wins",
      d.choice == {"persona": "browser", "confidence": 0.7})
    t("fast/auto_routing: mode=fast",
      d.mode == "fast")
    t("fast/auto_routing: thesis+antithesis preserved",
      d.thesis is not None and d.antithesis is not None)
    t("fast/auto_routing: why mentions confidence",
      "confidence" in d.why)


# ── 6. Mode = fast (path_gate fail-closed) ─────────────────────────────────

def case_mode_fast_path_gate():
    print("\n[6] mode=fast: path_gate ALWAYS denies (thesis wins)")
    reset_config()
    d = dialectic.decide(
        site="path_gate",
        thesis="deny",
        antithesis="false-positive-suspected",
        heat=0.9, mode="fast",
    )
    t("fast/path_gate: choice == 'deny' (thesis wins)",
      d.choice == "deny")
    t("fast/path_gate: why mentions fail-closed",
      "fail-closed" in d.why)


# ── 7. Mode = skill (block builder) ────────────────────────────────────────

def case_mode_skill_block():
    print("\n[7] mode=skill: returns wrapper block with all three tags")
    reset_config()
    d = dialectic.decide(
        site="skill_promotion",
        thesis="promote",
        antithesis={"reason": "low real-grade count"},
        heat=0.9, mode="skill",
    )
    t("skill: synthesis contains <dialectic site=\"skill_promotion\">",
      '<dialectic site="skill_promotion">' in d.synthesis)
    t("skill: synthesis contains <thesis>", "<thesis>" in d.synthesis)
    t("skill: synthesis contains <antithesis>", "<antithesis>" in d.synthesis)
    t("skill: synthesis contains <synthesis> placeholder",
      "<synthesis>" in d.synthesis and "fill in" in d.synthesis)
    t("skill: choice is the placeholder marker",
      d.choice == "<DIALECTIC_PLACEHOLDER>")
    t("skill: mode=skill", d.mode == "skill")


# ── 8. Mode = cli (subprocess result parsing) ──────────────────────────────

def case_mode_cli_parsing(monkeypatch_subprocess: bool = True):
    print("\n[8] mode=cli: subprocess output parsed into Decision")
    reset_config()
    # Monkey-patch the cli judge with a stub so we don't actually spawn
    # claude here. The point of this test is the parser, not real LLM.
    saved = dialectic._run_cli_judge

    def fake_judge(*, site, thesis, antithesis):  # noqa: ARG001
        return "B | (test stub) the antithesis is preferable"

    dialectic._run_cli_judge = fake_judge
    try:
        d = dialectic.decide(
            site="session_reset",
            thesis="reset-OK",
            antithesis="warn-first",
            heat=0.9, mode="cli",
        )
        t("cli: B | parse → choice == antithesis",
          d.choice == "warn-first",
          detail=f"got choice={d.choice!r}")
        t("cli: synthesis carries the cli line verbatim",
          d.synthesis.startswith("B |"))
        t("cli: why captured",
          "antithesis is preferable" in d.why)
    finally:
        dialectic._run_cli_judge = saved


# ── 9. Recursion guard ─────────────────────────────────────────────────────

def case_recursion_guard():
    print("\n[9] Recursion guard: depth ≥ 1 degrades nested call to off")
    reset_config()
    # Drive a fast synthesizer that itself calls decide() — that nested
    # call must come back as mode=off.
    inner_calls = {"count": 0, "modes": []}

    @dialectic.register_fast_synth("skill_promotion")
    def _outer(thesis, antithesis, ctx):
        inner = dialectic.decide(
            site="forge_creation",
            thesis="A", antithesis="B", heat=1.0, mode="fast",
        )
        inner_calls["count"] += 1
        inner_calls["modes"].append(inner.mode)
        return thesis, "outer-synthesis", "outer-why"

    try:
        dialectic.decide(
            site="skill_promotion",
            thesis="thesis-outer", antithesis="anti-outer",
            heat=1.0, mode="fast",
        )
        t("recursion: nested call observed",
          inner_calls["count"] == 1)
        t("recursion: nested mode degraded to off",
          inner_calls["modes"] == ["off"],
          detail=f"modes={inner_calls['modes']}")
    finally:
        # Restore the real fast synthesizer for skill_promotion (none).
        dialectic._FAST_SYNTHS.pop("skill_promotion", None)


# ── 10. Pre-calibration table sanity ───────────────────────────────────────

CALIBRATION = [
    # (label, site, c, u, scope, expected_trigger?)
    ("A skill_prom user 5g 0.8",   "skill_promotion", 1.0, 0.2, 5, True),
    ("B skill_prom proj 3g 0.5",   "skill_promotion", 0.3, 0.5, 2, False),
    ("C skill_prom user 4g 0.7",   "skill_promotion", 1.0, 0.3, 5, True),
    ("D routing trivial",          "auto_routing",    0.2, 0.15, 1, False),
    ("E routing low-conf",         "auto_routing",    0.2, 0.6, 1, False),
    ("F routing high-stake",       "auto_routing",    0.6, 0.55, 2, True),
    ("G path_gate clean",          "path_gate",       0.1, 0.05, 1, False),
    ("H path_gate eval+hint",      "path_gate",       0.9, 0.7, 3, True),
    ("I path_gate /tmp write",     "path_gate",       0.1, 0.1, 1, False),
    ("J forge new name",           "forge_creation",  0.2, 0.1, 1, False),
    ("K forge collision",          "forge_creation",  0.6, 0.5, 3, True),
    ("M reset 0 prom 2 tools",     "session_reset",   0.1, 0.1, 1, False),
    ("N reset 5 prom 12 tools",    "session_reset",   0.8, 0.3, 3, True),
]


def case_calibration_table():
    print("\n[10] Pre-calibration: 13 fictive tasks → trigger expectation")
    reset_config()
    mismatches = []
    for label, site, c, u, scope, expected in CALIBRATION:
        d = dialectic.decide(
            site=site,
            thesis="A-thesis", antithesis="B-anti",
            consequence=c, uncertainty=u, scope=scope,
            mode="fast",  # pin mode so we can reason about the gate alone
        )
        # below threshold → mode forced to "off"; above → "fast"
        triggered = (d.mode == "fast")
        ok = (triggered == expected)
        sym = "PASS" if ok else "FAIL"
        print(f"     {sym}  {label:32s} heat={d.heat:.2f} trig={triggered} expect={expected}")
        if not ok:
            mismatches.append(label)
    t("calibration: every task matches expected trigger",
      not mismatches,
      detail=f"mismatches={mismatches}" if mismatches else "")


# ── 11. CI-lint: import anthropic forbidden ────────────────────────────────

def case_no_anthropic_sdk_import():
    print("\n[11] dialectic.py must NOT import the Anthropic SDK")
    import ast
    src_path = (REPO / "operator" / "bridges" / "shared"
                / "dialectic.py")
    tree = ast.parse(src_path.read_text())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "anthropic":
                    bad.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "anthropic":
                bad.append(f"from {node.module} import ...")
    t("no real `import anthropic` AST node",
      not bad, detail=f"found={bad}")


# ── 12. Reply-footer formatting ────────────────────────────────────────────

def case_reply_footer():
    print("\n[12] Reply-footer: opt-in via cfg.show_in_reply")
    reset_config()
    cfg = dialectic.load_config()
    cfg["show_in_reply"] = False
    dialectic.save_config(cfg)
    d = dialectic.Decision(
        site="auto_routing", choice="browser",
        synthesis="browser", thesis="browser", antithesis="research",
        why="confidence-higher", mode="fast", heat=0.6,
    )
    t("show_in_reply=false → footer empty",
      dialectic.format_reply_footer([d]) == "")
    cfg["show_in_reply"] = True
    dialectic.save_config(cfg)
    out = dialectic.format_reply_footer([d])
    t("show_in_reply=true → non-empty footer",
      out and "[decision/auto_routing" in out,
      detail=f"out={out!r}")


def case_rate_limit_per_site():
    """Roadmap J — sliding-window rate limit on skill / cli modes."""
    print("\n[case] rate-limit per site (Roadmap J)")
    dialectic._rate_limit_reset()

    # Configure a small cap for forge_creation so the test stays fast.
    cfg = dialectic.load_config()
    cfg["rate_limits"] = {"forge_creation": 3}
    cfg["modes"]["forge_creation"] = "skill"  # ensure mode = skill
    dialectic.save_config(cfg)

    # Spend the budget — first 3 calls run as skill.
    skill_count = 0
    rate_limited = 0
    for i in range(6):
        d = dialectic.decide(
            site="forge_creation",
            thesis={"name": f"x{i}", "exists": False},
            antithesis={"name": f"x{i}", "exists": True},
            heat=0.9,
        )
        if d.mode == "skill":
            skill_count += 1
        elif d.mode == "off" and "rate-limit" in d.why:
            rate_limited += 1

    t("first 3 calls land in skill mode",
      skill_count == 3, detail=f"got {skill_count}")
    t("calls 4-6 are rate-limited (mode=off, why mentions rate-limit)",
      rate_limited == 3, detail=f"got {rate_limited}")

    # The audit chain must contain dialectic.rate_limited events for the
    # 3 throttled calls.
    audit_path = Path(_TD) / "global" / "forge" / "audit.jsonl"
    if audit_path.exists():
        events = []
        for line in audit_path.read_text().splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        rl_events = [e for e in events
                     if e.get("event_type") == "dialectic.rate_limited"]
        t("audit chain has at least 3 dialectic.rate_limited events",
          len(rl_events) >= 3, detail=f"got {len(rl_events)}")
        if rl_events:
            t("rate_limited events carry site=forge_creation",
              all(e.get("details", {}).get("site") == "forge_creation"
                  for e in rl_events),
              detail=f"sites={[e.get('details', {}).get('site') for e in rl_events]}")
            t("rate_limited events carry the cap value",
              all(e.get("details", {}).get("cap") == 3 for e in rl_events))
    else:
        t("audit file written for rate-limit events", False,
          detail=f"missing {audit_path}")

    # After reset, budget is restored — next call is skill again.
    dialectic._rate_limit_reset()
    d2 = dialectic.decide(
        site="forge_creation",
        thesis={"name": "post-reset", "exists": False},
        antithesis={"name": "post-reset", "exists": True},
        heat=0.9,
    )
    t("after _rate_limit_reset(), next call lands in skill mode again",
      d2.mode == "skill", detail=f"got mode={d2.mode!r} why={d2.why!r}")


def case_rate_limit_does_not_throttle_fast_off():
    """Fast / off modes are sub-millisecond — no rate-limit cost makes sense
    and we don't want to trigger throttling on cheap paths."""
    print("\n[case] rate-limit ignores fast/off modes")
    dialectic._rate_limit_reset()
    cfg = dialectic.load_config()
    cfg["rate_limits"] = {"auto_routing": 2}  # tiny budget
    cfg["modes"]["auto_routing"] = "fast"     # but mode is fast
    dialectic.save_config(cfg)

    fast_count = 0
    for i in range(8):  # 4x the budget — would be throttled if fast counted
        d = dialectic.decide(
            site="auto_routing",
            thesis={"persona": "coder", "confidence": 0.8},
            antithesis={"persona": "research", "confidence": 0.6},
            heat=0.9,
        )
        if d.mode == "fast":
            fast_count += 1
    t("8 fast-mode calls all run (no rate-limit on fast)",
      fast_count == 8, detail=f"got {fast_count}")

    # Restore to default mode for downstream tests.
    dialectic._rate_limit_reset()
    cfg["modes"]["auto_routing"] = "fast"
    cfg.pop("rate_limits", None)
    dialectic.save_config(cfg)


def main():
    case_heat_score_formula()
    case_heat_gate_short_circuits()
    case_mode_lookup_chain()
    case_mode_off()
    case_mode_fast_auto_routing()
    case_mode_fast_path_gate()
    case_mode_skill_block()
    case_mode_cli_parsing()
    case_recursion_guard()
    case_calibration_table()
    case_no_anthropic_sdk_import()
    case_reply_footer()
    case_rate_limit_per_site()
    case_rate_limit_does_not_throttle_fast_off()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
