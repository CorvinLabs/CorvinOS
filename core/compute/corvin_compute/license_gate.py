"""Compute-layer license gate (ADR-0013 + ADR-0017 Phase III).

Access matrix
─────────────
  free / no license        → trial mode: TRIAL_ITERATION_CAP total iterations,
                             all strategies available, 1 concurrent worker.
  pro / business tier      → full compute (all strategies, tenant caps)
  enterprise tier          → full compute + Compute Fabric
  expired license          → 30-day grace: full compute, audit warning
  expired past grace       → denied

Invocation contract
───────────────────
  • check_compute_access() is called ONLY before compute_run and compute_submit.
  • compute_status / compute_result / compute_abort are never gated so that
    in-flight jobs are never stranded by a license change mid-run.
  • record_trial_iteration() must be called by the MCP server after the worker
    accepts a compute_run submission (not on failure).

No phone-home. The license.jwt is verified against the RS256 public key pinned
in corvin-license/corvin_license/pubkey.pem. If corvin-license is not
installed, trial mode is enforced by iteration count alone.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


# ── Optional dep: corvin-license ─────────────────────────────────────────
# corvin-license ships in corvin-enterprise. When absent, fall through to
# trial mode — never crash the compute surface.
try:
    from corvin_license.verifier import (  # type: ignore[import]
        License as _License,
        LicenseClaimError,
        LicenseError,
        LicenseExpired,
        LicenseFileMalformed,
        LicenseFileMissing,
        LicenseSignatureError,
        load_license_from_disk,
    )
    from corvin_license.grace import (  # type: ignore[import]
        assess as _assess_grace,
        fingerprint_customer_id as _fingerprint_customer_id,
        mark_observed_expired as _mark_observed_expired,
        remember_valid_license as _remember_valid_license,
    )
    _LICENSE_PLUGIN_AVAILABLE = True
except ImportError:
    _LICENSE_PLUGIN_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────────

TRIAL_ITERATION_CAP: int = 500       # grid + random shared budget
TRIAL_BAYESIAN_CAP: int = 50         # separate Bayesian budget for evaluation
TRIAL_STRATEGIES_ALLOWED: frozenset[str] = frozenset({"grid", "random", "bayesian"})
TRIAL_MAX_CONCURRENT_RUNS: int = 1

COMPUTE_FLAG: str = "compute"
COMPUTE_FABRIC_FLAG: str = "compute_fabric"

UPGRADE_URL: str = "https://corvin.ai/compute-license"

_TRIAL_STATE_SCHEMA_VERSION = 2
_TRIAL_STATE_FILENAME = "trial_compute.json"


# ── Trial state ────────────────────────────────────────────────────────────

@dataclass
class TrialState:
    """Persisted trial iteration counters (grid/random + bayesian tracked separately)."""
    iterations_used: int = 0           # grid + random
    bayesian_iterations_used: int = 0  # bayesian has its own evaluation budget
    first_run_at: int | None = None
    schema_version: int = _TRIAL_STATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrialState":
        return cls(
            iterations_used=int(d.get("iterations_used", 0)),
            bayesian_iterations_used=int(d.get("bayesian_iterations_used", 0)),
            first_run_at=d.get("first_run_at"),
            schema_version=int(d.get("schema_version", _TRIAL_STATE_SCHEMA_VERSION)),
        )


def _trial_state_path(corvin_home: Path) -> Path:
    return corvin_home / "global" / "license" / _TRIAL_STATE_FILENAME


def _load_trial_state(corvin_home: Path) -> TrialState:
    path = _trial_state_path(corvin_home)
    if not path.exists():
        return TrialState()
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        # World-readable state file — treat as tampered; enforce cap immediately
        # so chmod 644 → rm trick can't reset the counter.
        return TrialState(iterations_used=TRIAL_ITERATION_CAP)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return TrialState.from_dict(raw)
    except (json.JSONDecodeError, OSError):
        return TrialState()


def _save_trial_state(state: TrialState, corvin_home: Path) -> None:
    """Atomic write, mode 0o600."""
    path = _trial_state_path(corvin_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(state.to_dict(), sort_keys=True, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".trial.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_trial_iteration(corvin_home: Path, strategy: str = "grid") -> None:
    """Increment the trial counter after a successful compute_run submission.

    CMP-04 (ADR-0146): the load-modify-save is serialized under an exclusive
    flock so two concurrent compute_run submissions cannot both read the same
    count, each +1, and save — losing one increment and soft-overrunning the
    trial cap. The flock makes the read-modify-write atomic across processes;
    _save_trial_state already does an atomic rename for the write itself.
    """
    lock_path = _trial_state_path(corvin_home).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl as _fcntl  # Unix-only; the deploy target is Linux
    except ImportError:
        _fcntl = None  # best-effort: fall back to unlocked RMW off-Linux
    _lf = open(lock_path, "w")
    try:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        if _fcntl is not None:
            _fcntl.flock(_lf.fileno(), _fcntl.LOCK_EX)
        state = _load_trial_state(corvin_home)
        if strategy == "bayesian":
            state.bayesian_iterations_used += 1
        else:
            state.iterations_used += 1
        if state.first_run_at is None:
            state.first_run_at = int(time.time())
        _save_trial_state(state, corvin_home)
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(_lf.fileno(), _fcntl.LOCK_UN)
        finally:
            _lf.close()


# ── Access result ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ComputeAccessResult:
    """Result of a compute license check.

    Fields
    ──────
    allowed                    Whether the call may proceed.
    mode                       "trial" | "licensed" | "grace" | "denied"
    tier                       "free" | "pro" | "business" | "enterprise"
    reason                     Populated when allowed=False or mode="grace".
    fabric_allowed             True when the compute_fabric flag is present.
    trial_iterations_remaining Populated in trial mode.
    trial_strategies_allowed   Populated in trial mode.
    """
    allowed: bool
    mode: str
    tier: str
    reason: str | None
    fabric_allowed: bool
    trial_iterations_remaining: int | None
    trial_strategies_allowed: frozenset[str] | None

    def as_audit_dict(self) -> dict[str, Any]:
        """Compact dict for audit-chain details — no PII."""
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "tier": self.tier,
            **({"reason": self.reason} if self.reason else {}),
            "fabric_allowed": self.fabric_allowed,
            **({"trial_remaining": self.trial_iterations_remaining}
               if self.trial_iterations_remaining is not None else {}),
        }

    def trial_watermark(self) -> dict[str, Any]:
        """Metadata appended to trial compute_run responses."""
        if self.mode != "trial":
            return {}
        return {
            "_corvin_trial": True,
            "iterations_remaining": self.trial_iterations_remaining,
            "strategies_available": sorted(self.trial_strategies_allowed or []),
            "upgrade": UPGRADE_URL,
        }


# ── Internal constructors ──────────────────────────────────────────────────

def _denied(reason: str, tier: str = "free") -> ComputeAccessResult:
    return ComputeAccessResult(
        allowed=False, mode="denied", tier=tier, reason=reason,
        fabric_allowed=False,
        trial_iterations_remaining=None, trial_strategies_allowed=None,
    )


def _trial(iterations_remaining: int) -> ComputeAccessResult:
    return ComputeAccessResult(
        allowed=True, mode="trial", tier="free", reason=None,
        fabric_allowed=False,
        trial_iterations_remaining=iterations_remaining,
        trial_strategies_allowed=TRIAL_STRATEGIES_ALLOWED,
    )


def _licensed(
    tier: str,
    *,
    fabric: bool = False,
    in_grace: bool = False,
) -> ComputeAccessResult:
    return ComputeAccessResult(
        allowed=True,
        mode="grace" if in_grace else "licensed",
        tier=tier,
        reason=(
            f"License expiring — renew at {UPGRADE_URL}" if in_grace else None
        ),
        fabric_allowed=fabric,
        trial_iterations_remaining=None,
        trial_strategies_allowed=None,
    )


# ── Trial helper ───────────────────────────────────────────────────────────

def _check_trial(corvin_home: Path) -> ComputeAccessResult:
    """Evaluate free-tier trial access."""
    state = _load_trial_state(corvin_home)
    remaining = max(0, TRIAL_ITERATION_CAP - state.iterations_used)
    if remaining <= 0:
        return _denied(
            f"Trial limit of {TRIAL_ITERATION_CAP} compute iterations reached. "
            f"Upgrade to a paid license at {UPGRADE_URL}",
        )
    return _trial(remaining)


# ── Main gate ──────────────────────────────────────────────────────────────

def _is_license_revoked() -> bool:
    """True when the propagated revocation cache marks this license revoked.

    RTL2-LIC-01 (ADR-0146): the sync heartbeat (corvin_license.sync) writes
    is_revoked into sync_cache.json within the 7-day propagation window, and the
    sibling corvin_license enforcement path already denies on it. The compute
    gate must honor it too, or a revoked-but-unexpired token keeps full compute +
    compute_fabric until the JWT's own exp passes. Guarded so the Apache-only
    build (no corvin_license) is unaffected — a missing plugin/cache simply means
    there is no revocation channel to honor (returns False).

    Fail-CLOSED on a CORRUPT cache: three cases are distinguished explicitly —
      • no plugin / unresolvable path  -> no revocation channel        -> False
      • cache file absent (first-run)  -> nothing revoked yet           -> False
      • cache file PRESENT but unparseable/unreadable -> fail CLOSED    -> True
    Only the present-but-corrupt case flips to fail-closed, so a fresh compute
    user (no cache yet) is never locked out, while a revoked token cannot regain
    compute by clobbering sync_cache.json into unparseable garbage (load_sync_cache
    would mask that into a non-revoked default — we parse the file ourselves).
    """
    try:
        from corvin_license import sync as _sync  # type: ignore[import]
    except Exception:  # noqa: BLE001 — no enterprise plugin -> no revocation channel
        return False
    try:
        cache_path = _sync._cache_path()
    except Exception:  # noqa: BLE001 — cannot even resolve the path -> no channel
        return False
    if not cache_path.exists():
        # Legitimate no-cache / first-run: there is no propagated revocation yet,
        # so do NOT fail-closed here (that would lock out every fresh compute user).
        return False
    # Cache file EXISTS -> it MUST parse cleanly. Read + parse directly (do NOT go
    # through load_sync_cache, which swallows corruption into a non-revoked default)
    # so a present-but-corrupt/unreadable sync_cache.json fails CLOSED: a revoked
    # token must not regain compute just because its cache got truncated/clobbered.
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return bool(_sync.SyncCache.from_dict(raw).is_revoked)
    except Exception:  # noqa: BLE001 — present-but-corrupt cache -> fail CLOSED
        return True


def check_compute_access(
    *,
    corvin_home: Path,
    now: int | None = None,
) -> ComputeAccessResult:
    """Determine whether the current deployment may call compute_run.

    This function NEVER raises. Any internal error (missing pubkey, corrupt
    state file, unexpected import failure) falls through to trial mode so
    a misconfigured license cannot make the compute surface unavailable.

    Call this before compute_run and compute_submit. Do NOT call it before
    compute_status / compute_result / compute_abort.
    """
    now_ = now or int(time.time())

    if not _LICENSE_PLUGIN_AVAILABLE:
        return _check_trial(corvin_home)

    # RTL2-LIC-01 (ADR-0146): honor propagated revocation BEFORE granting any
    # licensed/grace access. Fail-CLOSED on a confirmed revocation: deny outright
    # (a revoked token is a chargeback / ToS-breach / non-payment signal — do not
    # silently downgrade to trial, which would still grant metered compute).
    if _is_license_revoked():
        return _denied(
            f"License revoked. Contact billing or renew at {UPGRADE_URL}",
            tier="free",
        )

    try:
        lic = load_license_from_disk()
    except LicenseFileMissing:
        return _check_trial(corvin_home)
    except LicenseExpired as exc:
        return _handle_expired_license(exc, corvin_home=corvin_home, now=now_)
    except (LicenseSignatureError, LicenseClaimError, LicenseFileMalformed):
        return _check_trial(corvin_home)
    except Exception:
        return _check_trial(corvin_home)

    if lic.is_expired(now=now_):
        return _handle_expired_lic_object(lic, corvin_home=corvin_home, now=now_)

    # Active license — update grace anchor so renewals reset the timer.
    try:
        _remember_valid_license(
            valid_until=lic.valid_until,
            customer_fingerprint=_fingerprint_customer_id(lic.customer_id),
        )
    except Exception:
        pass

    if not lic.has_flag(COMPUTE_FLAG):
        # Installed license but tier does not include compute.
        return _check_trial(corvin_home)

    return _licensed(lic.tier, fabric=lic.has_flag(COMPUTE_FABRIC_FLAG))


def _handle_expired_license(
    exc: Any,
    *,
    corvin_home: Path,
    now: int,
) -> ComputeAccessResult:
    """Handle LicenseExpired raised during load."""
    try:
        fp = getattr(exc, "customer_fingerprint", None)
        grace = _assess_grace(valid_until=None, now=now)
        if grace.in_grace:
            tier = getattr(exc, "tier", "pro") or "pro"
            return _licensed(tier, in_grace=True)
        if fp:
            _mark_observed_expired(now=now, customer_fingerprint=fp)
        return _denied(
            f"License expired and grace period ended. Renew at {UPGRADE_URL}",
            tier=getattr(exc, "tier", "free") or "free",
        )
    except Exception:
        return _check_trial(corvin_home)


def _handle_expired_lic_object(
    lic: Any,
    *,
    corvin_home: Path,
    now: int,
) -> ComputeAccessResult:
    """Handle an expired License object (valid signature, exp passed)."""
    try:
        fp = _fingerprint_customer_id(lic.customer_id)
        grace = _assess_grace(valid_until=lic.valid_until, now=now)
        _remember_valid_license(valid_until=lic.valid_until, customer_fingerprint=fp)
        if grace.in_grace:
            return _licensed(
                lic.tier, fabric=lic.has_flag(COMPUTE_FABRIC_FLAG), in_grace=True,
            )
        _mark_observed_expired(now=now, customer_fingerprint=fp)
        return _denied(
            f"License expired and grace period ended. Renew at {UPGRADE_URL}",
            tier=lic.tier,
        )
    except Exception:
        return _check_trial(corvin_home)


# ── Strategy enforcement ───────────────────────────────────────────────────

def enforce_trial_strategy(args: dict[str, Any], corvin_home: Path) -> dict[str, Any]:
    """Return a (possibly modified) copy of compute_run args for trial mode.

    Raises ValueError when the requested strategy is not in
    TRIAL_STRATEGIES_ALLOWED, or when the strategy-specific trial budget
    is exhausted. The caller surfaces this as a typed MCP error.

    Bayesian runs against a separate TRIAL_BAYESIAN_CAP budget (50 iter)
    so evaluators can experience the full algorithm without consuming the
    shared grid/random budget.
    """
    strategy = args.get("strategy", "grid")
    if strategy not in TRIAL_STRATEGIES_ALLOWED:
        raise ValueError(
            f"strategy={strategy!r} requires a paid license "
            f"(trial supports: {sorted(TRIAL_STRATEGIES_ALLOWED)}). "
            f"Upgrade at {UPGRADE_URL}"
        )
    state = _load_trial_state(corvin_home)
    args = dict(args)
    budget = dict(args.get("budget") or {})
    if strategy == "bayesian":
        remaining = max(0, TRIAL_BAYESIAN_CAP - state.bayesian_iterations_used)
        if remaining <= 0:
            raise ValueError(
                f"Bayesian trial limit of {TRIAL_BAYESIAN_CAP} iterations reached. "
                f"Upgrade to a paid license at {UPGRADE_URL}"
            )
        if "max_iterations" not in budget or budget["max_iterations"] > remaining:
            budget["max_iterations"] = remaining
    else:
        remaining = max(0, TRIAL_ITERATION_CAP - state.iterations_used)
        if "max_iterations" not in budget or budget["max_iterations"] > remaining:
            budget["max_iterations"] = remaining
    args["budget"] = budget
    return args


__all__ = [
    "COMPUTE_FLAG",
    "COMPUTE_FABRIC_FLAG",
    "TRIAL_ITERATION_CAP",
    "TRIAL_BAYESIAN_CAP",
    "TRIAL_MAX_CONCURRENT_RUNS",
    "TRIAL_STRATEGIES_ALLOWED",
    "UPGRADE_URL",
    "ComputeAccessResult",
    "TrialState",
    "check_compute_access",
    "enforce_trial_strategy",
    "record_trial_iteration",
]
