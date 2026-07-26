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


def audit_chain_intact() -> TripwireResult:
    """The existing core chain must verify.

    An absent or empty chain passes — a fresh install has nothing to verify yet.
    A chain with broken or tampered links fails the boot.
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
        ok, problems = audit.verify_audit(path)
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"verify raised {type(exc).__name__}")

    if not ok:
        return TripwireResult(name, False, f"{len(problems)} broken record(s)")
    return TripwireResult(name, True, "chain verifies")


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

    forbidden = [
        attr
        for attr in ("set_writer", "replace_writer", "set_audit_path", "disable_core")
        if hasattr(audit_backend, attr)
    ]
    if forbidden:
        return TripwireResult(
            name, False, f"audit provider exposes trail-owning API: {forbidden}"
        )
    return TripwireResult(name, True, "fan-out only")


#: Every tripwire the boot sequence runs, in order.
TRIPWIRES: tuple[Callable[[], TripwireResult], ...] = (
    audit_writer_reachable,
    audit_chain_intact,
    core_audit_owns_the_trail,
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
    if not failed:
        return results

    summary = "; ".join(f"{r.name}: {r.detail}" for r in failed)
    _log.critical("COMPLIANCE TRIPWIRE FAILED — refusing to boot: %s", summary)
    raise TripwireError(
        f"mandatory compliance mechanism unavailable — refusing to boot ({summary})"
    )
