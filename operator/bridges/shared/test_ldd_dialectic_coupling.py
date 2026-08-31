#!/usr/bin/env python3
"""E2E tests for the Layer-14 ↔ Layer-11 coupling.

When the dialectical_reasoning LDD layer is off — globally OR per
profile — every dialectic site degrades to mode=off, on every site.

Covers:
  - Master-off (cfg.enabled=False on ldd.json) → every site is mode=off
  - Per-layer off (cfg.layers.dialectical_reasoning=False) → same
  - Profile.ldd_enabled=False → every site is mode=off for that chat
  - Profile.ldd_layers.dialectical_reasoning=False → same
  - Re-enabling globally lifts the gate again
  - Profile per-site dialectic mode is RESPECTED even when LDD layer is
    off (explicit-opt-in beats master-gate, mirroring the existing
    profile.dialectic_mode_<site> semantics)

Run as: python3 operator/bridges/shared/test_ldd_dialectic_coupling.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Sandbox CORVIN_HOME so dialectic.json + ldd.json land in tempdir.
_TD = Path(tempfile.mkdtemp(prefix="ldd-dialectic-coupling-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"
# Don't let the shell's global LDD_AUTO_OPTIN=1 (~/.bashrc) override the
# file-based toggles under test — mirrors test_ldd_lib.py.
os.environ.pop("LDD_AUTO_OPTIN", None)

import dialectic  # noqa: E402
import ldd  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def reset_all():
    """Wipe both config files + caches so each block starts fresh."""
    for mod in (dialectic, ldd):
        p = mod._config_path()
        if p.exists():
            p.unlink()
        mod._CONFIG_CACHE = {}
        mod._CONFIG_MTIME = 0.0


# ── Test cases ─────────────────────────────────────────────────────────────


def case_baseline_layer_on_dialectic_active():
    print("\n[1] LDD on, every dialectic site uses its bundle default")
    reset_all()
    # Default-OFF since the policy switch: flip master + layer on
    # explicitly to test the "LDD on" baseline.
    ldd.set_master(True)
    ldd.set_layer("dialectical_reasoning", True)
    for site, spec in dialectic.SITES.items():
        m = dialectic.resolve_mode(site=site)
        t(f"site={site} → bundle default {spec['mode']!r}",
          m == spec["mode"], detail=f"got={m!r}")


def case_master_off_globally():
    print("\n[2] LDD master OFF → every dialectic site degrades to off")
    reset_all()
    ldd.set_master(False)
    for site in dialectic.SITES:
        m = dialectic.resolve_mode(site=site)
        t(f"site={site} → off (LDD master off)",
          m == "off", detail=f"got={m!r}")


def case_layer_off_globally():
    print("\n[3] LDD per-layer dialectical_reasoning=OFF → every site off")
    reset_all()
    ldd.set_layer("dialectical_reasoning", False)
    for site in dialectic.SITES:
        m = dialectic.resolve_mode(site=site)
        t(f"site={site} → off (LDD layer off)",
          m == "off", detail=f"got={m!r}")


def case_profile_master_off():
    print("\n[4] profile.ldd_enabled=False → every site off for that chat")
    reset_all()
    # Need a non-trivial global baseline so "untouched" below means something.
    ldd.set_master(True)
    ldd.set_layer("dialectical_reasoning", True)
    profile = {"ldd_enabled": False}
    for site in dialectic.SITES:
        m = dialectic.resolve_mode(site=site, profile=profile)
        t(f"site={site} → off (profile master off)",
          m == "off", detail=f"got={m!r}")
    # Other profile (no override) sees the bundle default again.
    m = dialectic.resolve_mode(site="auto_routing")
    t("global state untouched (auto_routing → bundle default fast)",
      m == "fast", detail=f"got={m!r}")


def case_profile_per_layer_off():
    print("\n[5] profile.ldd_layers.dialectical_reasoning=False → off for chat")
    reset_all()
    profile = {"ldd_layers": {"dialectical_reasoning": False}}
    for site in dialectic.SITES:
        m = dialectic.resolve_mode(site=site, profile=profile)
        t(f"site={site} → off (profile per-layer off)",
          m == "off", detail=f"got={m!r}")


def case_re_enable_lifts_gate():
    print("\n[6] flipping LDD layer back ON lifts the gate")
    reset_all()
    ldd.set_master(False)
    # confirm OFF first
    t("auto_routing OFF while LDD master off",
      dialectic.resolve_mode(site="auto_routing") == "off")
    ldd.set_master(True)
    # Default per-layer is also off since the policy switch, so re-enabling
    # requires both master AND the specific layer.
    ldd.set_layer("dialectical_reasoning", True)
    t("auto_routing back to fast after LDD master+layer ON",
      dialectic.resolve_mode(site="auto_routing") == "fast")


def case_explicit_profile_mode_beats_ldd_gate():
    print("\n[7] explicit profile.dialectic_mode_<site> beats LDD gate")
    reset_all()
    ldd.set_layer("dialectical_reasoning", False)  # gate would force off
    profile = {"dialectic_mode_auto_routing": "fast"}
    m = dialectic.resolve_mode(site="auto_routing", profile=profile)
    t("explicit per-site mode wins over LDD gate",
      m == "fast", detail=f"got={m!r}")
    # Sites without an explicit override stay gated.
    m2 = dialectic.resolve_mode(site="path_gate", profile=profile)
    t("non-overridden sites still off via LDD gate",
      m2 == "off", detail=f"got={m2!r}")


def case_decide_returns_thesis_when_gated():
    print("\n[8] decide() returns thesis-only with mode=off when gated")
    reset_all()
    ldd.set_layer("dialectical_reasoning", False)
    d = dialectic.decide(
        site="auto_routing",
        thesis={"persona": "browser", "confidence": 0.8},
        antithesis={"persona": "research", "confidence": 0.5},
        heat=0.9,  # would normally trigger fast mode
    )
    t("decide.choice == thesis when gated",
      d.choice == {"persona": "browser", "confidence": 0.8})
    t("decide.mode == off when gated",
      d.mode == "off")


def main():
    case_baseline_layer_on_dialectic_active()
    case_master_off_globally()
    case_layer_off_globally()
    case_profile_master_off()
    case_profile_per_layer_off()
    case_re_enable_lifts_gate()
    case_explicit_profile_mode_beats_ldd_gate()
    case_decide_returns_thesis_when_gated()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
