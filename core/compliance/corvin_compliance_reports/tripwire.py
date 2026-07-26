"""Boot tripwires for mandatory compliance mechanisms (ADR-0232, ADR-0233 D5).

A tripwire asserts that a mechanism the platform is not allowed to run without is
actually present and functioning — and **fails the boot closed** when it is not.
It is the structural answer to "what if someone pluginifies, disables, or breaks
the audit trail": the platform refuses to start rather than running without it.

Design notes:

* Tripwires assert on the **core** mechanism, never on a plugin.  An installed
  ``audit_backend`` is irrelevant here: the question is whether CORE still writes
  its own hash-chained record.  ADR-0233 D4/D5.
* They reuse the existing verifiers (``audit.audit_health_check``,
  ``audit.verify_audit``) rather than reimplementing chain logic — a second hash
  implementation would be a second thing to keep correct.
* ``assert_all()`` is what a boot sequence calls.  Individual tripwires are
  exposed for tests and for a diagnostic CLI.
* A *chain that verifies but is empty* is fine (fresh install).  A chain that
  cannot be written to, or that verifies as broken, is not.

Usage (boot):
    from corvin_compliance_reports.tripwire import assert_all
    assert_all()   # raises TripwireError -> boot aborts
"""
from __future__ import annotations

import logging
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

_log = logging.getLogger("corvin.compliance.tripwire")

# There is deliberately NO override switch here — no env var, no config key, no
# feature flag.  CLAUDE.md § Compliance Baseline: no "compliance-off mode" via any
# env var, and a default-off switch on a mandatory mechanism is the same violation
# as a kill flag.  A test or a dev box that needs a different audit location
# redirects the PATH (tests/conftest.py points VOICE_AUDIT_PATH at a tmpdir), which
# leaves the tripwire fully armed against whatever path is in effect.


class TripwireError(RuntimeError):
    """A mandatory compliance mechanism is missing or broken.  Boot must abort."""


@dataclass(frozen=True)
class TripwireResult:
    name: str
    ok: bool
    detail: str = ""


def _audit_module():
    """Import the bridge audit module without making core depend on the bridge.

    Mirrors the optional-import convention used by ``adapter.py`` for
    ``corvin_plugins``: the module lives in ``operator/bridges/shared`` and is not
    guaranteed to be importable in every packaging layout.
    """
    try:
        import audit as _audit  # type: ignore[import-not-found]

        return _audit
    except ImportError:
        pass

    repo_root = Path(__file__).resolve().parents[3]
    shared = repo_root / "operator" / "bridges" / "shared"
    if shared.is_dir() and str(shared) not in sys.path:
        # append, NOT insert(0): this directory also contains generic top-level
        # names (tests/, templates/) with no __init__.py, so putting it FIRST on
        # sys.path lets them shadow another package's `tests` — the same class as
        # the operator/ stdlib-shadow trap. Appending means existing paths win.
        sys.path.append(str(shared))
    try:
        import audit as _audit  # type: ignore[import-not-found]

        return _audit
    except ImportError:
        return None


def audit_writer_reachable() -> TripwireResult:
    """The core audit path must exist and be writable.

    Checked by writing and removing a probe file **next to** the audit log, never
    by appending to the log itself — a tripwire must not add records to a GDPR
    chain (and must not risk corrupting one).
    """
    name = "audit_writer_reachable"
    audit = _audit_module()
    if audit is None:
        return TripwireResult(name, False, "audit module not importable")

    try:
        path = Path(audit.audit_path())
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"audit_path() failed: {type(exc).__name__}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return TripwireResult(
            name, False, f"audit dir not creatable: {type(exc).__name__}"
        )

    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".tripwire-", suffix=".probe", delete=True
        ):
            pass
    except OSError as exc:
        return TripwireResult(
            name, False, f"audit dir not writable: {type(exc).__name__}"
        )

    return TripwireResult(name, True, str(path.parent))


#: How much of the tail must verify for the WRITER to count as sound right now.
#: The chain is append-only, so a historical break never repairs itself: gating boot
#: on the whole file means a platform that is permanently unbootable, and the only
#: way an operator gets it back is deleting or truncating the audit log — destroying
#: evidence, which is strictly worse for GDPR Art. 30 than a documented seam.
TAIL_RECORDS = 200

#: Cache: verifying 108k records costs ~0.9 s, and two tripwires read the result.
_verify_cache: dict = {}


def _verify_chain(path: Path):
    """``(ok, problems, total_records)`` for the core chain, verified once per file
    state.  Uses the canonical ``verify_audit`` — a tail-only re-implementation
    would duplicate the MAC primitive, and a second copy of a compliance primitive
    is a second thing that can drift."""
    audit = _audit_module()
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _verify_cache:
        return _verify_cache[key]
    ok, problems = audit.verify_audit(path)
    with path.open(encoding="utf-8", errors="replace") as fh:
        total = sum(1 for _ in fh)
    result = (ok, problems, total)
    _verify_cache.clear()
    _verify_cache[key] = result
    return result


def audit_chain_intact() -> TripwireResult:
    """BLOCKING: the audit writer must be sound RIGHT NOW.

    An absent or empty chain passes — a fresh install has nothing to verify yet.

    This deliberately asks "does the mechanism work now", not "has it ever been
    broken", and the distinction is load-bearing. It was written as a full-file
    verify, which on the maintainer's own machine turned a KNOWN, historical
    key-mismatch window (380 records, ~77 000 records ago) into a platform that
    refuses to boot at all — i.e. a compliance hardening that STOPS the audit trail
    it exists to protect. A break in an append-only file cannot be repaired, so
    that state is permanent.

    Historical breakage is not silenced: :func:`audit_chain_history_clean` reports
    it, ``assert_all`` writes it into the chain on every boot, and
    ``voice-audit verify`` still exits 1. What changes is only whether it takes the
    platform down. There is no env var or flag on either check.
    """
    name = "audit_chain_intact"
    audit = _audit_module()
    if audit is None:
        return TripwireResult(name, False, "audit module not importable")

    try:
        path = Path(audit.audit_path())
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"audit_path() failed: {type(exc).__name__}")

    if not path.exists() or path.stat().st_size == 0:
        return TripwireResult(name, True, "no chain yet (fresh install)")

    try:
        ok, problems, total = _verify_chain(path)
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"verify raised {type(exc).__name__}")

    if ok:
        return TripwireResult(name, True, "chain verifies")

    tail_start = max(1, total - TAIL_RECORDS + 1)
    recent = [pr for pr in problems if int(pr.get("line", 0)) >= tail_start]
    if recent:
        # The writer is producing records that do not chain. Every event from here
        # on is worthless as evidence — refuse to serve.
        return TripwireResult(
            name, False,
            f"{len(recent)} broken record(s) in the last {TAIL_RECORDS} — "
            "the audit writer is not sound",
        )
    return TripwireResult(
        name, True,
        f"last {TAIL_RECORDS} records verify (chain has "
        f"{len(problems)} historical break(s) — see audit_chain_history_clean)",
    )


def audit_chain_history_clean() -> TripwireResult:
    """REPORTING (never blocks boot): the whole chain must verify.

    A failure here is a permanent, unrepairable fact about the file. It is recorded
    into the chain by :func:`assert_all` on every boot, surfaced in the compliance
    report, and left as a non-zero exit in ``voice-audit verify``. It does not abort
    the boot, because refusing to boot neither repairs the past nor records the
    present — it only ends the trail.
    """
    name = "audit_chain_history_clean"
    audit = _audit_module()
    if audit is None:
        return TripwireResult(name, False, "audit module not importable")
    try:
        path = Path(audit.audit_path())
        if not path.exists() or path.stat().st_size == 0:
            return TripwireResult(name, True, "no chain yet (fresh install)")
        ok, problems, total = _verify_chain(path)
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"verify raised {type(exc).__name__}")

    if ok:
        return TripwireResult(name, True, f"all {total} records verify")
    lines = sorted(int(pr.get("line", 0)) for pr in problems)
    return TripwireResult(
        name, False,
        f"{len(problems)} broken record(s) at lines {lines[0]}..{lines[-1]} "
        f"of {total} — permanent, append-only",
    )


def core_audit_owns_the_trail() -> TripwireResult:
    """An installed audit plugin must be a secondary sink, not the trail.

    The registry exposes fan-out only; it has no way to intercept or replace the
    core write.  This tripwire pins that structurally: if the audit provider ever
    grows a ``set_writer``/``replace_writer``-shaped entry point, the boot fails
    until someone re-reads ADR-0233 D4.
    """
    name = "core_audit_owns_the_trail"
    try:
        from corvin_plugins.providers import audit_backend
    except ImportError:
        return TripwireResult(name, True, "plugin package not installed")

    # Single source of truth, shared with the provider's own test suite. NO inline
    # fallback list: a second copy here is exactly the drift this consolidation
    # removed. If the constant is missing, the module is not the one this tripwire
    # knows how to check — fail, don't guess.
    names = getattr(audit_backend, "TRAIL_OWNING_ATTRS", None)
    if not names:
        return TripwireResult(
            name, False, "audit provider exposes no TRAIL_OWNING_ATTRS to check against"
        )
    forbidden = [attr for attr in names if hasattr(audit_backend, attr)]
    if forbidden:
        return TripwireResult(
            name, False, f"audit provider exposes trail-owning API: {forbidden}"
        )
    return TripwireResult(name, True, "fan-out only")


def _shared_module(name: str):
    """Import a module from ``operator/bridges/shared`` (the gates live there)."""
    try:
        return __import__(name)
    except ImportError:
        pass
    repo_root = Path(__file__).resolve().parents[3]
    shared = repo_root / "operator" / "bridges" / "shared"
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.append(str(shared))
    return __import__(name)


def consent_gate_denies_by_default() -> TripwireResult:
    """L18: the consent gate must exist AND deny an unknown user (GDPR Art. 6, 7).

    Checked by asking about a uid that cannot have consented.  A gate that answers
    "granted" for an unknown principal is an auto-admit, which the compliance
    baseline forbids outright ("no auto-admit, no trusted-observer allowlist").
    """
    name = "consent_gate_denies_by_default"
    try:
        consent = _shared_module("consent")
    except ImportError:
        return TripwireResult(name, False, "consent module not importable")

    if not hasattr(consent, "is_granted"):
        return TripwireResult(name, False, "consent.is_granted is missing")
    if not getattr(consent, "DEFAULT_TTL_S", 0):
        return TripwireResult(name, False, "consent has no TTL cap")

    try:
        answer = consent.is_granted(
            "tripwire-probe", "tripwire-probe", "uid-that-never-consented"
        )
    except Exception as exc:  # noqa: BLE001
        # A gate that cannot answer is not a gate that admits — but it IS broken.
        return TripwireResult(name, False, f"is_granted raised {type(exc).__name__}")

    # is_granted returns (granted, reason). Unpacking is load-bearing: a truthiness
    # test on the tuple is ALWAYS true, which would make this tripwire fail every
    # boot — a fail-closed check with inverted logic is a denial of service, not a
    # safety net. Any other shape is treated as a broken gate rather than guessed at.
    if not (isinstance(answer, tuple) and len(answer) == 2):
        return TripwireResult(
            name, False, f"is_granted returned {type(answer).__name__}, expected a 2-tuple"
        )
    granted, reason = answer
    if not isinstance(granted, bool):
        return TripwireResult(
            name, False, f"is_granted's first element is {type(granted).__name__}"
        )
    if granted:
        return TripwireResult(name, False, f"consent gate ADMITS an unknown uid ({reason})")
    return TripwireResult(name, True, f"deny-by-default holds ({reason})")


def flow_guard_present() -> TripwireResult:
    """L34: the data-flow guard and its deny exception must exist (GDPR Art. 32).

    The classification matrix itself is covered by the L34 test suite; the tripwire
    asserts the mechanism is present and still raises rather than returning a
    permissive default, because a missing ``DataFlowDenied`` means every caller's
    ``except DataFlowDenied`` silently stops catching anything.
    """
    name = "flow_guard_present"
    try:
        dc = _shared_module("data_classification")
    except ImportError:
        return TripwireResult(name, False, "data_classification not importable")

    for attr in ("DataFlowGuard", "DataFlowDenied", "DataClassification"):
        if not hasattr(dc, attr):
            return TripwireResult(name, False, f"data_classification.{attr} is missing")
    if not issubclass(dc.DataFlowDenied, Exception):
        return TripwireResult(name, False, "DataFlowDenied is not raisable")
    return TripwireResult(name, True, "guard + deny path present")


def house_rules_gate_intact() -> TripwireResult:
    """L44: the house-rules policy must load and its integrity must verify.

    Uses the module's own ``verify_policy_integrity`` (a file hash) rather than
    running the classifier — a tripwire must not need a model or the network.
    A tampered policy file is the failure this catches.
    """
    name = "house_rules_gate_intact"
    try:
        hr = _shared_module("house_rules")
    except ImportError:
        return TripwireResult(name, False, "house_rules not importable")

    if not hasattr(hr, "load_repo_policy"):
        return TripwireResult(name, False, "house_rules.load_repo_policy is missing")

    try:
        ok, detail = hr.verify_policy_integrity()
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(
            name, False, f"verify_policy_integrity raised {type(exc).__name__}"
        )
    if not ok:
        return TripwireResult(name, False, f"policy integrity failed: {detail}")
    return TripwireResult(name, True, "policy verifies")


def erasure_orchestrator_present() -> TripwireResult:
    """L36: the GDPR Art. 17 erasure path must exist and validate its subject id.

    An erasure orchestrator that accepts any subject id would delete against an
    unvalidated identifier, so the tripwire probes the validator too.
    """
    name = "erasure_orchestrator_present"
    try:
        eo = _shared_module("erasure_orchestrator")
    except ImportError:
        return TripwireResult(name, False, "erasure_orchestrator not importable")

    for attr in ("ErasureRequest", "ErasureResult", "validate_subject_id"):
        if not hasattr(eo, attr):
            return TripwireResult(
                name, False, f"erasure_orchestrator.{attr} is missing"
            )

    try:
        eo.validate_subject_id("")
    except Exception:
        return TripwireResult(name, True, "orchestrator present, validator rejects empty")
    return TripwireResult(name, False, "validate_subject_id ACCEPTS an empty subject")


#: Every tripwire the boot sequence runs, in order.  One per mandatory mechanism
#: of ADR-0232 § Mandatory, plus the two audit-specific ones.
#: Reporting-only tripwires: a failure is recorded and surfaced, never fatal.
#: These describe a permanent historical fact that refusing to boot cannot change.
REPORTING_ONLY: frozenset = frozenset({"audit_chain_history_clean"})

TRIPWIRES: tuple[Callable[[], TripwireResult], ...] = (
    # L16 Audit trail
    audit_writer_reachable,
    audit_chain_intact,
    audit_chain_history_clean,
    core_audit_owns_the_trail,
    # L18 Consent gate
    consent_gate_denies_by_default,
    # L34 Flow guard
    flow_guard_present,
    # L44 House rules
    house_rules_gate_intact,
    # L36 Erasure orchestrator
    erasure_orchestrator_present,
)


def check_all() -> List[TripwireResult]:
    """Run every tripwire and return the results.  Never raises."""
    results: List[TripwireResult] = []
    for probe in TRIPWIRES:
        try:
            results.append(probe())
        except Exception as exc:  # noqa: BLE001
            results.append(
                TripwireResult(getattr(probe, "__name__", "unknown"), False,
                               f"probe raised {type(exc).__name__}")
            )
    return results


def assert_all() -> List[TripwireResult]:
    """Run every tripwire; raise :class:`TripwireError` on the first failure set.

    Returns the results when everything passed, so a caller can log them.
    """
    results = check_all()
    failed = [r for r in results if not r.ok]

    # Reporting-only failures are recorded, loudly, and do not abort. Recording
    # happens BEFORE the blocking check raises, so a boot that is about to be
    # refused still leaves the discontinuity on the record.
    reported = [r for r in failed if r.name in REPORTING_ONLY]
    for r in reported:
        _log.critical("COMPLIANCE FINDING (non-fatal, permanent): %s: %s", r.name, r.detail)
        _record_finding(r)

    fatal = [r for r in failed if r.name not in REPORTING_ONLY]
    if not fatal:
        return results

    summary = "; ".join(f"{r.name}: {r.detail}" for r in fatal)
    _log.critical("COMPLIANCE TRIPWIRE FAILED — refusing to boot: %s", summary)
    raise TripwireError(
        f"mandatory compliance mechanism unavailable — refusing to boot ({summary})"
    )


def _record_finding(result: TripwireResult) -> None:
    """Write a non-fatal compliance finding INTO the chain.

    Every boot re-records it, so the finding cannot be forgotten by ignoring a log
    file. Detail text is generated here (counts and line numbers only), never from
    user data.
    """
    audit = _audit_module()
    if audit is None:
        return
    try:
        audit.audit_event(
            "compliance.chain_discontinuity",
            details={"tripwire": result.name, "detail": result.detail},
        )
    except Exception as exc:  # noqa: BLE001
        _log.error("could not record compliance finding (%s)", type(exc).__name__)
