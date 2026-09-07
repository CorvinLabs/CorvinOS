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

import contextlib
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List

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


def _check_audit_unification() -> TripwireResult:
    """Exactly ONE hash chain per tenant — name every other live one, and link them.

    R4 (2026-09-07). The old check compared exactly two paths, the pre-ADR-0007
    ``<root>/global/forge/audit.jsonl`` and the tenant
    ``<root>/tenants/<tid>/global/forge/audit.jsonl``, and reported a bare
    "audit chains SPLIT" string when both had been written that day. The measured
    reality on the maintainer install was SIX distinct chain files for ONE tenant
    across two roots, none a symlink of another — because
    ``security_events.write_event`` took its path from the caller and every
    caller composed its own. A reader of any single chain saw a fraction of the
    events, so "the chain is the system's complete proof of work"
    (CLAUDE.md § Audit Chain as Ground Truth) did not hold for any of them.

    What this check now does:

    * enumerates every location this host knows (``forge.paths.all_audit_chains``)
      and reports the condition ``audit_chain_split`` naming each live sibling,
      its size and how recently it was written — an operator can act on that;
    * RECORDS THE SEAM. For every sibling holding records, one chained
      ``audit.chain_supersedes`` entry is appended to the canonical chain naming
      the sibling's path key, genesis and FINAL TAIL HASH, and the reverse
      pointer is written into the sibling's out-of-tree identity record. The
      historical chains are never merged, rewritten, reordered or deleted — they
      are append-only and hash-chained, and destroying that to tidy up would be a
      far worse compliance failure than the split. The seam makes them REACHABLE
      and VERIFIABLE from the canonical chain instead
      (``security_events.chain_seam_links``).

    Reporting-only, exactly like the old check: a split is a defect to fix, not a
    reason to refuse the boot and lock the operator out of their own install.
    """
    name = "audit_unification"
    try:
        audit = _audit_module()
        if audit is None:
            return TripwireResult(name, True, "unification check skipped: audit module not importable")
        active_path = Path(audit.audit_path())
        try:
            from forge.paths import all_audit_chains as _all_chains  # type: ignore[import-not-found]
            from forge.tenants import current_tenant as _current_tenant  # type: ignore[import-not-found]

            chains = _all_chains(_current_tenant())
        except Exception:  # noqa: BLE001 - stripped layout: nothing to compare
            return TripwireResult(name, True, f"audit chain is unified ({active_path})")

        canonical = chains["canonical"]
        try:
            canonical_real = canonical.resolve()
        except OSError:
            canonical_real = canonical

        now = time.time()
        live: list[tuple[str, Path, int, float]] = []   # sibling chains with content
        seen: set = {canonical_real}
        for label, p in chains.items():
            if label == "canonical":
                continue
            try:
                st = p.stat()
            except OSError:
                continue          # absent — nothing to reconcile
            try:
                real = p.resolve()
            except OSError:
                real = p
            if real in seen:
                continue          # one file, two names (ADR-0007 compat symlink)
            seen.add(real)
            if st.st_size == 0:
                continue
            live.append((label, p, st.st_size, now - st.st_mtime))

        if not live:
            return TripwireResult(name, True, f"audit chain is unified ({active_path})")

        # Seam: make every sibling reachable from the canonical chain. Never a
        # merge — one pointer record naming the sibling's final tail hash.
        seams = 0
        try:
            from forge.security_events import record_chain_supersession  # type: ignore[import-not-found]

            for _label, p, _sz, _age in live:
                if record_chain_supersession(canonical, p, reason="chain_convergence"):
                    seams += 1
        except Exception:  # noqa: BLE001 - a seam must never break the boot
            pass

        recent = [(l, p, sz, age) for (l, p, sz, age) in live if age < 86400]
        detail = ", ".join(f"{l}={sz}B/{age / 3600:.1f}h" for l, p, sz, age in sorted(live))
        if recent:
            _log.error(
                "audit_chain_split: %d sibling chain(s) for this tenant received writes in "
                "the last 24h alongside the canonical %s — writers disagree on the chain "
                "location. Route every writer through forge.paths.tenant_audit_chain(). "
                "Live siblings: %s. Seam records written into the canonical chain: %d "
                "(the historical chains are append-only and are never merged; follow "
                "security_events.chain_seam_links to verify them).",
                len(recent), canonical,
                ", ".join(str(p) for _l, p, _s, _a in recent), seams,
            )
            return TripwireResult(
                name, True,
                f"audit_chain_split: {len(recent)} sibling chain(s) written in the last "
                f"24h ({detail}); {seams} seam record(s) link them to {canonical}",
            )
        _log.warning(
            "audit_chain_split (historical): %d sibling chain(s) hold records but none was "
            "written in the last 24h — %s. %d seam record(s) link them to %s.",
            len(live), detail, seams, canonical,
        )
        return TripwireResult(
            name, True,
            f"audit_chain_split (historical, no recent writes): {detail}; "
            f"{seams} seam record(s) link them to {canonical}",
        )
    except Exception as exc:  # noqa: BLE001
        # If we can't check, don't fail boot — unification is not a blocker
        return TripwireResult(name, True, f"unification check skipped: {type(exc).__name__}")


def audit_writer_reachable() -> TripwireResult:
    """The core audit WRITER must be loaded and its path must be writable.

    Two halves, both fail-closed:

    * ``audit.writer_available()`` — the hash-chained writer
      (``forge.security_events``) is actually imported. Without this half the
      probe below passed on a layout where ``audit_event`` was a silent no-op:
      the directory was writable, nothing was ever written to it, and
      ``verify_audit`` said ``(True, [])`` for the chain that did not exist
      (2026-09-03 finding A1). An audit module that does not expose the
      predicate is not the module this tripwire knows how to check — refuse.
    * the path — checked by writing and removing a probe file **next to** the
      audit log, never by appending to the log itself: a tripwire must not add
      records to a GDPR chain (and must not risk corrupting one).
    """
    name = "audit_writer_reachable"
    audit = _audit_module()
    if audit is None:
        return TripwireResult(name, False, "audit module not importable")

    writer_available = getattr(audit, "writer_available", None)
    if not callable(writer_available):
        return TripwireResult(
            name, False,
            "audit module exposes no writer_available() — not the core audit module",
        )
    try:
        if not writer_available():
            return TripwireResult(
                name, False,
                "audit writer unavailable (forge.security_events not loaded) — "
                "audit_event() would be a silent no-op",
            )
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"writer_available() raised {type(exc).__name__}")

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


def _corvin_root(audit) -> Path:
    """The runtime root the writer resolves against (the writer's own resolver)."""
    return Path(audit.corvin_root())


def audit_path_not_redirected() -> TripwireResult:
    """BLOCKING (F-A3): an env-only redirect of the chain must stay inside the root.

    ``VOICE_AUDIT_PATH`` / ``FORGE_ROOT`` exist so tests and ops tooling can
    sandbox the chain. They also let an attacker with env access point every
    tripwire at an EMPTY file while the real chain — the one this process would
    otherwise write to — is never looked at (``assert_all`` passed on a fresh,
    zero-record file).

    R2-A3 (2026-09-07). Two holes closed:

    * The ``PYTEST_CURRENT_TEST`` tolerance is REMOVED. A compliance tripwire
      whose docstring says "there is no override" must not have one, and that
      was one: exporting that variable disabled this check in any process, test
      or not. Tests now point ``CORVIN_HOME`` at their sandbox, which makes
      their redirect an ordinary in-root one.
    * "Under the root" is no longer enough. A redirect to a DIFFERENT file
      inside the root is the same attack with a shorter path — an empty file
      one directory over reads as a fresh install. The redirect must resolve to
      exactly the path the resolver would have chosen on its own; then it
      changes nothing and is accepted.
    """
    name = "audit_path_not_redirected"
    audit = _audit_module()
    if audit is None:
        return TripwireResult(name, False, "audit module not importable")
    redirect = getattr(audit, "audit_redirect", None)
    if not callable(redirect):
        return TripwireResult(name, False, "audit module exposes no audit_redirect() — not the core audit module")
    redirected, differs_from_default = redirect()
    if not redirected:
        return TripwireResult(name, True, "no env redirect")
    try:
        path = Path(audit.audit_path()).expanduser().resolve()
        root = _corvin_root(audit).expanduser().resolve()
    except Exception as exc:  # noqa: BLE001
        return TripwireResult(name, False, f"path resolution failed: {type(exc).__name__}")
    if not (root == path or root in path.parents):
        return TripwireResult(
            name, False,
            f"audit chain redirected by {redirected} OUTSIDE the CORVIN_HOME root — "
            "unset the redirect or point CORVIN_HOME at the same root",
        )
    if differs_from_default:
        return TripwireResult(
            name, False,
            f"audit chain redirected by {redirected} to a path INSIDE the CORVIN_HOME "
            "root that is not the resolver's own chain — the real chain would go "
            "unverified; unset the redirect or point CORVIN_HOME at the intended root",
        )
    return TripwireResult(
        name, True, f"redirect via {redirected} resolves to the default chain",
    )


#: How much of the tail must verify for the WRITER to count as sound right now.
#: The chain is append-only, so a historical break never repairs itself: gating boot
#: on the whole file means a platform that is permanently unbootable, and the only
#: way an operator gets it back is deleting or truncating the audit log — destroying
#: evidence, which is strictly worse for GDPR Art. 30 than a documented seam.
TAIL_RECORDS = 200

#: In-process cache: two tripwires read the same result within one boot.
_verify_cache: dict = {}


def _verify_chain(path: Path):
    """``(ok, problems, total_records)`` for the core chain, verified once per file
    state.  Uses the canonical verifier — a tail-only re-implementation would
    duplicate the MAC primitive, and a second copy of a compliance primitive is a
    second thing that can drift.

    ADR-0640 R4 — the walk is O(n) in a file that only ever grows. On the
    maintainer install a full walk is 315 MB / 588 827 records / ~5.7 s, paid
    TWICE per boot (here and again by the line count) and paid by every test that
    boots the console app. History verification is not skipped: the chain is
    append-only, so ``verify_audit_incremental`` re-hashes the already-verified
    prefix BYTES (~0.19 s) to prove it unchanged and only then reuses that
    prefix's verdict, walking the suffix. Any change to any prefix byte — an
    in-place edit, a truncation, a replacement, a prepend — misses the witness
    and forces a full walk. A missing or unreadable witness falls back to a full
    walk, never to trust. ``voice-audit verify`` and the daily ``verify --all``
    unit still do an unconditional full walk.
    """
    audit = _audit_module()
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _verify_cache:
        return _verify_cache[key]
    incremental = getattr(audit, "verify_audit_incremental", None)
    if callable(incremental):
        ok, problems, total = incremental(path)
    else:
        # Older bridge module on the path: full walk, never a free pass.
        ok, problems = audit.verify_audit(path)
        with path.open("rb") as fh:
            total = sum(1 for _ in fh)
    result = (ok, problems, total)
    _verify_cache.clear()
    _verify_cache[key] = result
    return result


SEAM_EVENT = "compliance.chain_discontinuity"


def _last_record(path: Path) -> dict | None:
    """The last JSON object in the chain file (best-effort, tail read)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            tail = fh.read().splitlines()
        for raw in reversed(tail):
            raw = raw.strip()
            if raw:
                rec = json.loads(raw)
                return rec if isinstance(rec, dict) else None
    except Exception:  # noqa: BLE001
        return None
    return None


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

    SEAM, NOT TRUNCATION (F-A13, 2026-09-07). A break inside the tail window used
    to be "healed" by deleting the broken tail — a boot-time rewrite of a GDPR
    Art. 30/32 trail. Now the break is SEALED: a chained
    ``compliance.chain_discontinuity`` seam record (line range of the break,
    ``seam: true``) is appended after the broken tail, and the writer counts as
    sound iff that record itself verifies against the file. Nothing is ever
    removed. Two shapes still refuse the boot: a whole-chain failure (the
    lost/rotated anchor-key shape — the operator restores the key, not the
    chain) and a current-state anchor problem (``tail_truncated``,
    ``anchor_key_insecure_mode``, ``mac_stripped_chain``).
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

    # Problems without a line number describe the chain's CURRENT state (an
    # out-of-tree anchor disagreeing with the file), not a historical record.
    structural = sorted({str(pr.get("issue")) for pr in problems if "line" not in pr})
    if structural:
        return TripwireResult(
            name, False,
            f"current-state chain problem(s) {structural} — restore the anchor key / "
            "chain backup (chmod 600 the key for anchor_key_insecure_mode); nothing is truncated",
        )

    tail_start = max(1, total - TAIL_RECORDS + 1)
    recent = [pr for pr in problems if int(pr.get("line", 0)) >= tail_start]
    if not recent:
        return TripwireResult(
            name, True,
            f"last {TAIL_RECORDS} records verify (chain has "
            f"{len(problems)} historical break(s) — see audit_chain_history_clean)",
        )

    if len(problems) >= total:
        return TripwireResult(
            name, False,
            f"{len(recent)} broken record(s) in the last {TAIL_RECORDS}; the ENTIRE "
            "chain fails to verify — this is a lost/rotated audit_anchor.key, not tail "
            "corruption. Restore ~/.config/corvin-voice/audit_anchor.key (nothing is "
            "truncated so authentic records are not destroyed)",
        )

    lines = sorted(int(pr.get("line", 0)) for pr in recent)
    last = _last_record(path)
    already_sealed = (
        isinstance(last, dict)
        and last.get("event_type") == SEAM_EVENT
        and isinstance(last.get("details"), dict)
        and last["details"].get("seam") is True
        and last["details"].get("last_break_line") == lines[-1]
    )
    if not already_sealed:
        try:
            audit.audit_event(
                SEAM_EVENT,
                details={
                    "tripwire": name,
                    "seam": True,
                    "broken_records": len(recent),
                    "first_break_line": lines[0],
                    "last_break_line": lines[-1],
                    "total_records": total,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return TripwireResult(name, False, f"seam record could not be written ({type(exc).__name__})")
        try:
            ok2, problems2, total2 = _verify_chain(path)
        except Exception as exc:  # noqa: BLE001
            return TripwireResult(name, False, f"verify raised {type(exc).__name__}")
        if total2 <= total:
            return TripwireResult(name, False, "seam record did not land — the audit writer is not sound")
        new_problems = [pr for pr in problems2 if "line" not in pr or int(pr.get("line", 0)) > total]
        if new_problems:
            return TripwireResult(
                name, False,
                f"the audit writer is not sound: the seam record itself does not verify "
                f"({sorted({str(pr.get('issue')) for pr in new_problems})})",
            )
        seam_line = total2
    else:
        seam_line = total
        if any(int(pr.get("line", 0)) == seam_line for pr in problems):
            return TripwireResult(name, False, "the audit writer is not sound: the existing seam record does not verify")
    return TripwireResult(
        name, True,
        f"{len(recent)} broken record(s) in the last {TAIL_RECORDS} (lines "
        f"{lines[0]}–{lines[-1]}) sealed by a chained seam record at line {seam_line}; "
        "nothing truncated — see audit_chain_history_clean",
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
    except ModuleNotFoundError as e:
        if e.name == "corvin_plugins":
            return TripwireResult(name, True, "plugin package not installed")
        # Package present, provider module missing: broken mechanism, not absence.
        return TripwireResult(name, False, f"audit provider module missing: {e}")
    except ImportError as e:
        return TripwireResult(name, False, f"audit provider import broken: {e}")

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


def _settings_candidates() -> List[Path]:
    home = Path.home()
    cands = [home / ".claude" / "settings.json"]
    roots = [Path.cwd(), Path(__file__).resolve().parents[3]]
    seen = set()
    for r in roots:
        for nm in ("settings.json", "settings.local.json"):
            p = r / ".claude" / nm
            if p not in seen:
                seen.add(p)
                cands.append(p)
    return cands


def _hooks_mention_path_gate(cfg: dict) -> bool:
    hooks = cfg.get("hooks") if isinstance(cfg, dict) else None
    if not isinstance(hooks, dict):
        return False
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and "path_gate" in str(h.get("command", "")):
                return True
    return False


def l10_hook_registered() -> TripwireResult:
    """REPORTING (F-A8): is the L10 path-gate wired as a Claude Code PreToolUse hook?

    ``operator/voice/hooks/hooks.json`` only takes effect when the ``voice``
    plugin is enabled in the operator's Claude Code, or when the hook is
    registered directly in a settings file. Neither is something the platform
    can do for the operator, so this probe REPORTS (it never blocks a boot):
    a boot without the hook is a boot where Claude Code's own file writes
    bypass the L10 gate.
    """
    name = "l10_hook_registered"
    checked: List[str] = []
    for p in _settings_candidates():
        if not p.is_file():
            continue
        checked.append(str(p))
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if _hooks_mention_path_gate(cfg):
            return TripwireResult(name, True, f"path_gate PreToolUse hook registered in {p}")
        enabled = cfg.get("enabledPlugins") if isinstance(cfg, dict) else None
        if isinstance(enabled, dict) and any(
            str(k).startswith("voice@") and v for k, v in enabled.items()
        ):
            return TripwireResult(name, True, f"voice plugin (carries hooks.json) enabled in {p}")
    return TripwireResult(
        name, False,
        "L10 path_gate is NOT registered as a Claude Code PreToolUse hook "
        f"(checked {len(checked)} settings file(s)) — enable the `voice` plugin "
        "(`/plugin install voice@claudeos-local`) or add the hook to ~/.claude/settings.json; "
        "see docs/claude-ref/layer-10-path-gate.md",
    )


#: Every tripwire the boot sequence runs, in order.  One per mandatory mechanism
#: of ADR-0232 § Mandatory, plus the two audit-specific ones.
#: Reporting-only tripwires: a failure is recorded and surfaced, never fatal.
#: These describe a permanent historical fact that refusing to boot cannot change.
REPORTING_ONLY: frozenset = frozenset({
    "audit_chain_history_clean", "audit_unification", "l10_hook_registered",
})

#: plugin_ids that ``bootstrap_global()`` itself put on the compliance boot
#: layer.  Written by the wheel's own boot code, read only here.
#:
#: The direction matters: the recorder is the code that HAS the authority to
#: grant, the checker is this module, and they are separate.  Nothing else may
#: add to this set.
_GRANTED_COMPLIANCE_IDS: set[str] = set()


def record_granted_compliance_plugin(plugin_id: str) -> None:
    """Record that ``bootstrap_global`` granted ``plugin_id`` the compliance layer.

    Called from ``corvin_plugins.bootstrap`` immediately after a successful
    registration on that layer.  Deliberately a plain function rather than a
    callback the registry could be handed: the tripwire must not be configurable
    by whatever it is checking.
    """
    _GRANTED_COMPLIANCE_IDS.add(plugin_id)


def compliance_layer_is_wheel_granted() -> TripwireResult:
    """No plugin sits on the compliance boot layer that the wheel did not grant.

    **Why this is worth writing when five review rounds concluded that in-process
    identity is not enforceable.** Every other guard in the plugin package asks a
    plugin something and can be lied to. This one asks nothing: it compares the
    registry's own state against a list the wheel's boot code wrote, at a moment
    when no plugin code has run since. It is the one check on this surface that
    is not in the attacker's conversational reach — and per ADR-0232/0233 it has
    no override, no env var and no flag.

    **What it catches.** A plugin that put itself on the compliance layer —
    through a class attribute, a re-registration, a thread that escaped the load
    context — appears in the registry and not in the granted set. Boot refuses.
    That is a check on the RESULT rather than on the intent, which is why it
    survives derivations that the intent-side guards do not.

    **What it does not catch.** Anything that writes
    ``_registry._boot_layers`` or this module's set directly. Same address space,
    same limits; see docs/claude-ref/layer-plugins.md § "The perimeter is
    attribution". The gain is real but bounded, and stating the bound is part of
    the check.

    **Vacuously true today.** ``_GLOBAL_SPECS`` is empty, so nothing is granted
    and nothing should be on the layer. It becomes load-bearing with the first
    real compliance plugin — the same shape as the guard tests in
    ``test_layered_boot.py``.
    """
    name = "compliance_layer_is_wheel_granted"
    try:
        from corvin_plugins.manifest import BootLayer
        from corvin_plugins.registry import get_registry
    except ImportError:
        return TripwireResult(name, True, "plugin package not installed")

    try:
        live = {
            getattr(p, "plugin_id", "")
            for p in get_registry().plugins_by_boot_layer(BootLayer.COMPLIANCE)
        }
    except Exception as exc:  # noqa: BLE001
        # Fail CLOSED: not being able to answer "who is on the compliance layer"
        # is itself a reason to refuse the boot.
        return TripwireResult(name, False, f"registry unreadable ({type(exc).__name__})")

    unexpected = sorted(live - _GRANTED_COMPLIANCE_IDS)
    if unexpected:
        # plugin_ids only — they are charset-validated identifiers, never free
        # text, and this string reaches the hash-chained record.
        return TripwireResult(
            name, False,
            f"{len(unexpected)} plugin(s) on the compliance boot layer that "
            f"bootstrap_global did not grant: {unexpected}",
        )
    return TripwireResult(name, True, f"{len(live)} granted compliance plugin(s)")


#: Tripwires that only make sense AFTER plugins have loaded.  ``assert_all()``
#: runs before that by design (a broken audit writer must stop the boot before
#: anything else happens), so running this there would be vacuously green.
POST_BOOT_TRIPWIRES: tuple[Callable[[], TripwireResult], ...] = (
    compliance_layer_is_wheel_granted,
)


TRIPWIRES: tuple[Callable[[], TripwireResult], ...] = (
    # L16 Audit trail
    audit_writer_reachable,
    audit_path_not_redirected,  # F-A3: env redirect must stay under CORVIN_HOME
    audit_chain_intact,
    audit_chain_history_clean,
    _check_audit_unification,  # ADR-0007: Multi-tenant audit chain migration
    core_audit_owns_the_trail,
    # L18 Consent gate
    consent_gate_denies_by_default,
    # L34 Flow guard
    flow_guard_present,
    # L44 House rules
    house_rules_gate_intact,
    # L36 Erasure orchestrator
    erasure_orchestrator_present,
    # L10 Path gate — reporting only (the hook lives in the operator's Claude Code)
    l10_hook_registered,
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


#: Chains this PROCESS has already asserted, by resolved path (R2-A11). Both
#: shipped hosts call ``assert_all()`` in their lifespan and then call
#: ``boot_platform()``, which asserts again — so every boot produced two full
#: tripwire runs: duplicate ``COMPLIANCE FINDING`` log sets and, worse, a second
#: ``compliance.chain_discontinuity`` seam record appended to the chain for the
#: same break. The host lifespan is authoritative; the second caller skips.
#:
#: Keyed by the chain PATH, not by a bare flag: a process that legitimately
#: moves to another chain (a test with its own ``CORVIN_HOME``, a tool that
#: verifies several tenants) must still get a real assertion for each one.
_ASSERTED_CHAINS: set = set()


def _current_chain_key() -> str:
    """Resolved path of the chain the tripwires would check right now, or ""."""
    audit = _audit_module()
    if audit is None:
        return ""
    try:
        return str(Path(audit.audit_path()).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return ""


def already_asserted() -> bool:
    """True when :func:`assert_all` has already PASSED for this chain in this
    process. Only ever suppresses a duplicate run — a failure never records,
    so a refused boot can never be skipped past."""
    key = _current_chain_key()
    return bool(key) and key in _ASSERTED_CHAINS


def reset_asserted() -> None:
    """Forget the per-process record (tests that re-boot in one interpreter)."""
    _ASSERTED_CHAINS.clear()


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
        # R2-A11: remember the chain that passed, so the second caller in the
        # same boot (host lifespan → boot_platform) does not re-run every probe
        # and append a second seam record for the same discontinuity.
        key = _current_chain_key()
        if key:
            _ASSERTED_CHAINS.add(key)
        return results

    summary = "; ".join(f"{r.name}: {r.detail}" for r in fatal)
    _log.critical("COMPLIANCE TRIPWIRE FAILED — refusing to boot: %s", summary)
    raise TripwireError(
        f"mandatory compliance mechanism unavailable — refusing to boot ({summary})"
    )


def assert_post_boot() -> List[TripwireResult]:
    """Run the tripwires that need the plugins to be loaded.  Raises on failure.

    Called from the gateway lifespan AFTER ``bootstrap_all()``. Same fail-closed
    semantics as :func:`assert_all` and the same absence of an override — the
    caller must let a failure propagate.

    Separate from ``assert_all`` because of WHEN, not because of severity:
    ``assert_all`` deliberately runs first, before any plugin exists, so a broken
    audit writer stops the boot before anything else happens. A check about
    loaded plugins asked at that moment would always pass.
    """
    results: List[TripwireResult] = []
    for probe in POST_BOOT_TRIPWIRES:
        try:
            results.append(probe())
        except Exception as exc:  # noqa: BLE001
            results.append(
                TripwireResult(getattr(probe, "__name__", "unknown"), False,
                               f"probe raised {type(exc).__name__}")
            )
    failed = [r for r in results if not r.ok]
    if not failed:
        return results
    summary = "; ".join(f"{r.name}: {r.detail}" for r in failed)
    _log.critical("POST-BOOT COMPLIANCE TRIPWIRE FAILED — refusing to serve: %s", summary)
    raise TripwireError(
        f"post-boot compliance check failed — refusing to serve ({summary})"
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
        # Chain findings keep their historical event name; other reporting-only
        # tripwires (e.g. the L10 hook probe) record a generic finding.
        event = (SEAM_EVENT if result.name.startswith("audit_")
                 else "compliance.tripwire_finding")
        audit.audit_event(
            event,
            details={"tripwire": result.name, "detail": result.detail},
        )
    except Exception as exc:  # noqa: BLE001
        _log.error("could not record compliance finding (%s)", type(exc).__name__)
