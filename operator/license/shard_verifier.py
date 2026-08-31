"""Multi-Shard License Identity (MSLI) — ADR-0154 M6.

Full license validity is reconstructed from four independent shards, each held
in a different subsystem. No single subsystem holds the full picture; an
attacker who forges only one shard is caught by the others.

  Shard A — JWT payload   : tier + validity     (validator / Ed25519)
  Shard B — instance_id   : local identity file (instance_identity / ADR-0153)
  Shard C — audit anchor  : chain DNA tier      (chain_dna / LSAD — ADR-0132 M2)
  Shard D — feature root  : OTA lattice key tier (feature_lattice — ADR-0154 M1)

This module is **read-only and best-effort**. It is wired into the boot
self-test at **WARNING** severity (never CRITICAL): a shard mismatch on a clean
free-tier install must never brick the adapter (CLAUDE.md: "Don't gate
Apache-core single-node"). The ``corvin-license-debug`` CLI runs the same checks
and prints a unified, operator-only diagnosis.

Free-tier consistency: on a no-license install every shard reports the *free*
identity and they agree — aggregate ``OK``. Mechanisms only diverge on a
genuine inconsistency (e.g. paid JWT but free audit DNA = swapped/forged state).

Must NOT ``import anthropic``.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("corvin.license.shard")

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


def _shared_on_path() -> None:
    here = Path(__file__).resolve()
    for p in (
        here.parents[1] / "bridges" / "shared",
        here.parents[1] / "forge" / "forge",
        here.parents[1],          # operator/ — enables `import license.validator`
        here.parent,              # operator/license — top-level feature_lattice etc.
    ):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _import_first(names: "tuple[str, ...]") -> "Any | None":
    """Return an already-loaded module under any of *names*, else import one.

    Reusing the live ``sys.modules`` entry is load-bearing: the validator wires
    the OTA root key into ``license.feature_lattice``; if the shard checker
    imported a *second* top-level copy it would read a stale free root and
    report false divergence. We therefore bind to whatever instance is already
    in play before importing fresh.
    """
    _shared_on_path()
    for name in names:
        mod = sys.modules.get(name)
        if mod is not None:
            return mod
    from importlib import import_module

    for name in names:
        try:
            return import_module(name)
        except Exception:  # noqa: BLE001
            continue
    return None


def _import_validator() -> "Any | None":
    """Best-effort import of the license validator. None if unavailable.

    validator.py uses package-relative imports, so it must load as
    ``license.validator`` (operator/ on path), not as a top-level module.
    """
    return _import_first(("license.validator", "validator"))


def _import_lattice() -> "Any | None":
    """Best-effort import of the OTA feature lattice. None if unavailable.

    Production wires the root key into ``license.feature_lattice`` (validator
    uses a package-relative import), so prefer that name to share state.
    """
    return _import_first(("license.feature_lattice", "feature_lattice"))


def _result(shard: str, name: str, status: str, detail: str) -> dict[str, str]:
    return {"shard": shard, "name": name, "status": status, "detail": detail}


# ── Shard A — JWT payload / tier ──────────────────────────────────────────────

def _check_shard_a() -> dict[str, str]:
    v = _import_validator()
    if v is None:
        return _result("A", "jwt_payload", WARN, "validator unavailable")
    try:
        loaded = v.is_loaded()
        tier = v.active_tier()
        if not loaded:
            return _result("A", "jwt_payload", OK, "free tier (no license) — consistent")
        return _result("A", "jwt_payload", OK, f"paid license active, tier={tier}")
    except Exception as exc:  # noqa: BLE001
        return _result("A", "jwt_payload", WARN, f"validator unavailable: {type(exc).__name__}")


# ── Shard B — instance_id ─────────────────────────────────────────────────────

def _check_shard_b(corvin_home: Path) -> dict[str, str]:
    iid_file = corvin_home / "global" / "instance_id.json"
    if not iid_file.exists():
        return _result("B", "instance_id", WARN, "instance_id.json absent (run corvin-instance-id show)")
    try:
        mode = iid_file.stat().st_mode & 0o777
        # Windows: NTFS has no POSIX group/other bits, so st_mode always looks
        # permissive there regardless of real ACLs — skip the check.
        if not sys.platform.startswith("win") and mode & 0o077:
            return _result("B", "instance_id", FAIL, f"instance_id.json mode 0o{mode:o} too permissive (want 0600)")
        import json

        data = json.loads(iid_file.read_text(encoding="utf-8"))
        iid = str(data.get("instance_id", ""))
        if not iid:
            return _result("B", "instance_id", FAIL, "instance_id.json missing instance_id field")
        return _result("B", "instance_id", OK, f"instance_id={iid[:8]}… mode=0600")
    except Exception as exc:  # noqa: BLE001
        return _result("B", "instance_id", FAIL, f"unreadable: {type(exc).__name__}")


# ── Shard C — audit chain DNA tier ────────────────────────────────────────────

def _check_shard_c(corvin_home: Path, paid_root_active: bool) -> dict[str, str]:
    _shared_on_path()
    try:
        import chain_dna  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return _result("C", "audit_dna", WARN, "chain_dna unavailable")
    tenant = "_default"
    try:
        import os

        tenant = (os.environ.get("CORVIN_TENANT_ID", "") or "_default").strip() or "_default"
    except Exception:  # noqa: BLE001
        pass
    chain = corvin_home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not chain.exists() or chain.stat().st_size == 0:
        return _result("C", "audit_dna", OK, "no audit chain yet — nothing to anchor")
    try:
        last_dna, _ = chain_dna.last_dna_in_chain(chain)
        if not last_dna:
            return _result("C", "audit_dna", OK, "chain has no DNA-bearing events (legacy/pre-LSAD)")
        chain_is_free = chain_dna.is_free_tier(last_dna)
        # Inconsistency: paid license but the chain's latest DNA is free-tier
        # (or vice versa). This is the cross-shard signal a single forged JWT
        # cannot satisfy. WARNING-only — historical tier transitions are benign.
        if paid_root_active and chain_is_free:
            return _result("C", "audit_dna", WARN, "paid license but latest audit DNA is free-tier — shard divergence")
        return _result("C", "audit_dna", OK, f"chain DNA tier consistent (free={chain_is_free})")
    except Exception as exc:  # noqa: BLE001
        return _result("C", "audit_dna", WARN, f"DNA read failed: {type(exc).__name__}")


# ── Shard D — feature root key tier ───────────────────────────────────────────

def _check_shard_d(tier_is_free: bool) -> dict[str, str]:
    fl = _import_lattice()
    if fl is None:
        return _result("D", "feature_root", WARN, "feature_lattice unavailable")
    try:
        # Self-consistency: the lattice must produce a stable proof, and the
        # installed root tier must match the active license tier.
        p1 = fl.session_lic_proof("shard-d-probe")
        p2 = fl.session_lic_proof("shard-d-probe")
        if p1 != p2:
            return _result("D", "feature_root", FAIL, "lattice proof non-deterministic — root key unstable")
        paid_root = fl.is_paid_root_active()
        if tier_is_free and paid_root:
            return _result("D", "feature_root", WARN, "free tier but paid feature root installed — shard divergence")
        if (not tier_is_free) and (not paid_root):
            return _result("D", "feature_root", WARN, "paid tier but free feature root installed — shard divergence")
        return _result("D", "feature_root", OK, f"feature root tier consistent (paid_root={paid_root})")
    except Exception as exc:  # noqa: BLE001
        return _result("D", "feature_root", WARN, f"lattice probe failed: {type(exc).__name__}")


# ── Public API ────────────────────────────────────────────────────────────────

def _resolve_corvin_home() -> Path:
    _shared_on_path()
    try:
        from paths import corvin_home  # type: ignore[import]

        return corvin_home()
    except Exception:  # noqa: BLE001
        import os

        ch = os.environ.get("CORVIN_HOME", "").strip()
        return Path(ch) if ch else (Path.home() / ".corvin")


def verify_shards(corvin_home: "Path | None" = None) -> dict[str, Any]:
    """Run all four shard checks and return a structured report.

    Returns ``{"aggregate": OK|WARN|FAIL, "shards": [...], "tier": str}``.
    Never raises — diagnostics must survive a half-broken install.
    """
    home = corvin_home or _resolve_corvin_home()

    # Determine tier from the active license for cross-shard comparison.
    tier_is_free = True
    paid_root_active = False
    tier_name = "free"
    try:
        from . import validator as v
    except Exception:  # noqa: BLE001
        try:
            from importlib import import_module

            v = import_module("validator")  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            v = None  # type: ignore[assignment]
    if v is not None:
        try:
            tier_name = v.active_tier()
            tier_is_free = tier_name == "free"
        except Exception:  # noqa: BLE001
            pass
    try:
        from . import feature_lattice as fl

        paid_root_active = fl.is_paid_root_active()
    except Exception:  # noqa: BLE001
        try:
            from importlib import import_module

            paid_root_active = import_module("feature_lattice").is_paid_root_active()
        except Exception:  # noqa: BLE001
            pass

    shards = [
        _check_shard_a(),
        _check_shard_b(home),
        _check_shard_c(home, paid_root_active),
        _check_shard_d(tier_is_free),
    ]
    statuses = {s["status"] for s in shards}
    aggregate = FAIL if FAIL in statuses else (WARN if WARN in statuses else OK)
    return {"aggregate": aggregate, "tier": tier_name, "shards": shards}


def selftest_warn() -> None:
    """Boot self-test hook: run shard checks, log at WARNING on divergence.

    Best-effort, never raises, never CRITICAL — a shard mismatch on free tier
    must not block boot.
    """
    try:
        report = verify_shards()
    except Exception as exc:  # noqa: BLE001
        log.debug("shard self-test failed to run (non-fatal): %s", exc)
        return
    if report["aggregate"] != OK:
        bad = [f"{s['shard']}:{s['name']}={s['status']}" for s in report["shards"] if s["status"] != OK]
        log.warning(
            "license: MSLI shard divergence (ADR-0154 M6) — %s. Run "
            "'corvin-license-debug' for details.",
            ", ".join(bad),
        )
