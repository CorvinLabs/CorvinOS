"""Skill Config Adapter & Optimizer (ADR-0549 Stages 3–4).

Phase 2 of CONCEPT-0029. Takes hypotheses from FeedbackInterpreter and tests them
on real tasks via an iterative optimizer (150-epoch loop). Successful changes are
applied; failures are rejected.

Stages:
  3. Optimizer tests hypotheses (1 per epoch, 50-run baseline, 50 inner-loop, 50 refinement)
  4. Announce changes + enable reversibility (config versioning)

Audit trail (emitted by the caller through ``on_config_change`` — the adapter
is pure state + persistence, the console route owns the audit write):
  - skill_config_updated (change applied / rolled back)
  - skill_version_created (version snapshot)

Persistence (2026-09-06 adversarial review, F7):
  * the config lives under the TENANT home (``core.paths.tenant.tenant_home``),
    so ``CORVIN_HOME`` and the repo-local ``.corvin`` are honoured — it used to
    be hard-wired to ``~/.corvin`` and silently wrote outside the live root;
  * every version is persisted WITH its config snapshot and reloaded on the
    next start, so ``rollback()`` works after a restart — it used to reload
    only the current config and every rollback failed with "Version not found";
  * the optimizer epoch counter is persisted, so a restart does not restart the
    50-epoch baseline phase;
  * (2026-09-07, F-K5) every mutation runs under an exclusive ``fcntl.flock``
    on ``<config>.lock`` and RELOADS the on-disk state inside the lock before
    mutating it, so two concurrent feedback requests (each console request
    builds its own adapter) cannot lose each other's epoch/version — the
    load→mutate→persist sequence used to be unguarded (lost-update race).

The config produced here is READ by ``DelegationRouterSkill`` via
:func:`load_skill_config` — that is what closes the loop (feedback → hypothesis
→ accepted config → the next routing decision uses it).

Epoch-test label (ADR-2305 Part 1, PLAN-0966 WP2): every accepted hypothesis carries
the suffix :data:`UNVERIFIED_CAUSALITY` in its returned reason and in the new
version's ``change_reason``. The label is removed only by a causal test (ADR-2305
Part 2), which does not exist yet; Phase 2 dual-write does not remove it, because
the epoch test compares two windows without a control group, on tenant-wide
outcomes, and applies a hypothesis only after measuring. Behaviour is otherwise
unchanged.

Learning brake (ADR-2304, PLAN-0966 WP3): ``OptimizerState.frozen`` (persisted
with the config). While frozen, BOTH writers (:meth:`SkillAdapter.apply_config_delta`,
:meth:`SkillAdapter.run_optimizer_epoch`) and the DoD weight writer
(``definition_of_done_verifier.weight_persistence``, via :func:`refuse_if_frozen`)
refuse with :class:`LearningFrozen` and record ``learning.freeze_refused_write``.
Freezing (:func:`freeze_learning`, called only by the learning host after a
committed ``skill.config_oscillation`` / ``skill.confidence_regression`` record)
and lifting (:meth:`SkillAdapter.unfreeze`, called only by the CSRF-protected
console route) are audit-first: no ``learning.frozen`` / ``learning.unfrozen``
record, no state change. The new state is written to a temp file before the record and
renamed after it; when only the rename fails, ``learning.freeze_not_applied`` /
``learning.unfreeze_not_applied`` follows on the chain and :class:`BrakeNotApplied` is
raised, so the chain never says "frozen" for a skill that silently is not. A freeze never
rolls back and never lifts itself. There is no switch: no flag and no environment variable
turns the brake off.

One canonical id per config file: ``os.x`` and ``os_x`` map to the same file name; the id
stored in the file wins and every other spelling is refused (:class:`SkillIdAlias`). A file
that exists but does not parse (including one malformed number) raises
:class:`BrakeStateUnreadable` - never mistaken for a bad skill id - and reads as frozen to
:func:`refuse_if_frozen`. Brake records carry closed shapes only: ``reason`` from the
anomaly names or ``brake_state_unreadable`` / ``unknown``, ``version_id`` ``v<N>`` /
``none`` / ``unparsed``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from core.tenants.validation import validate_tenant_id
from .feedback_loop import ConfigHypothesis

logger = logging.getLogger(__name__)

__all__ = [
    "SkillConfig",
    "SkillConfigVersion",
    "SkillAdapter",
    "SkillConfigLockBusy",
    "OptimizerState",
    "load_skill_config",
    "skill_config_path",
    "LOCK_TIMEOUT_SECONDS",
    "UNVERIFIED_CAUSALITY",
    "LearningFrozen",
    "NotFrozen",
    "BRAKE_EVENTS",
    "freeze_learning",
    "refuse_if_frozen",
    "valid_skill_id",
    "BrakeStateUnreadable",
    "BrakeNotApplied",
    "SkillIdAlias",
    "brake_lock",
    "record_freeze_failed",
]

#: ADR-2305 Part 1: suffix of every acceptance. Removed only by the causal test (Part 2).
UNVERIFIED_CAUSALITY = ":unverified_causality"


#: ADR-2304 audit events, content-free: ids and closed enums only. ``reason`` is the
#: anomaly event name, ``brake_state_unreadable`` or ``unknown`` (:func:`_closed_reason`),
#: ``version_id`` a generated ``v<N>`` id, ``none`` or ``unparsed`` (:func:`_closed_version`),
#: ``target`` one of ``skill_config`` / ``dod_weights``, ``cause`` one of
#: :data:`_FREEZE_FAILURE_CAUSES`, ``attempt`` the learning host's try number for this anomaly
#: episode (a retry after a ``learning.freeze_not_applied`` carries ``attempt`` > 1, so it never
#: reads as a second, independent freeze). Mirrored in ``forge.security_events.EVENT_SEVERITY``
#: / ``_EVENT_ALLOWLIST``.
BRAKE_EVENTS = {
    "learning.frozen": ("skill_id", "reason", "version_id", "attempt"),
    "learning.unfrozen": ("skill_id", "reason", "version_id"),
    "learning.freeze_refused_write": ("skill_id", "reason", "version_id", "target"),
    # the record committed, the state write did not: the chain says so instead of staying silent
    "learning.freeze_not_applied": ("skill_id", "reason", "version_id", "attempt"),
    "learning.unfreeze_not_applied": ("skill_id", "reason", "version_id"),
    # the freeze could not even be attempted (the config file does not parse); retried
    "learning.freeze_failed": ("skill_id", "reason", "cause", "attempt"),
}
_BRAKE_TARGETS = ("skill_config", "dod_weights")
_FREEZE_FAILURE_CAUSES = ("config_unreadable",)
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_VERSION_ID_RE = re.compile(r"^v[0-9]{1,9}$")
#: reasons a brake record may carry besides the freezing anomaly names
_EXTRA_REASONS = ("brake_state_unreadable", "unknown")


def valid_skill_id(skill_id: Any) -> bool:
    """A skill id that is safe as a config file name (no path separator, no ``..``)."""
    return isinstance(skill_id, str) and bool(_SKILL_ID_RE.match(skill_id)) and ".." not in skill_id


def _closed_reason(reason: Any) -> str:
    """A brake reason from the closed set (a file-sourced value never reaches the chain verbatim)."""
    if isinstance(reason, str) and (reason in _freeze_reasons() or reason in _EXTRA_REASONS):
        return reason
    return "unknown"


def _closed_version(version_id: Any) -> str:
    """A version id of the generated shape ``v<N>``, ``none``, or ``unparsed``."""
    if version_id is None or version_id == "none":
        return "none"
    if isinstance(version_id, str) and _VERSION_ID_RE.match(version_id):
        return version_id
    return "unparsed"


class LearningFrozen(RuntimeError):
    """A learned-config write was refused because the skill's learning is frozen (ADR-2304)."""

    def __init__(self, skill_id: str, reason: str):
        reason = _closed_reason(reason)
        super().__init__(f"learning of {skill_id!r} is frozen ({reason})")
        self.skill_id = skill_id
        self.reason = reason


class NotFrozen(ValueError):
    """Unfreeze was asked for a skill whose learning is not frozen."""


class BrakeStateUnreadable(RuntimeError):
    """The skill's config file exists but does not parse (bad JSON, a non-object, an
    ``optimizer`` block that is not an object, or a malformed number). Not a bad skill id:
    the learning host retries the freeze, :func:`refuse_if_frozen` reads it as frozen."""


class BrakeNotApplied(RuntimeError):
    """The brake record committed but the state file could not be replaced. A compensating
    ``learning.freeze_not_applied`` / ``learning.unfreeze_not_applied`` record was attempted;
    the state is unchanged."""


class SkillIdAlias(ValueError):
    """``skill_id`` maps to a config file that belongs to another id (``os.x`` vs ``os_x``)."""

    def __init__(self, requested: str, canonical: str):
        super().__init__(f"skill id {requested!r} is an alias of {canonical!r}")
        self.requested = requested
        self.canonical = canonical


def _parse_optimizer(data: Any) -> dict:
    """The validated optimizer counters + brake state of a parsed config file.

    Raises ``ValueError`` / ``TypeError`` on any malformed shape. A file WITHOUT an
    ``optimizer`` block reads as never frozen: that is a legitimate file written before
    2026-09-06 (909bf4c99 added the block), and the brake only ever writes the block."""
    if not isinstance(data, dict):
        raise TypeError("config file is not an object")
    opt = data.get("optimizer", {})
    if opt is None:
        opt = {}
    if not isinstance(opt, dict):
        raise TypeError("optimizer block is not an object")
    frozen = opt.get("frozen", False) not in (False, None, 0)
    return {
        "epoch": int(opt.get("epoch", 1)),
        "baseline_success_rate": float(opt.get("baseline_success_rate", 0.0)),
        "current_success_rate": float(opt.get("current_success_rate", 0.0)),
        "hypotheses_tested": int(opt.get("hypotheses_tested", 0)),
        "hypotheses_accepted": int(opt.get("hypotheses_accepted", 0)),
        # Any non-false value reads as frozen: a hand-mangled flag must not lift the brake.
        "frozen": frozen,
        "frozen_reason": _closed_reason(opt.get("frozen_reason")) if frozen else None,
        "frozen_since": (str(opt["frozen_since"]) if frozen and opt.get("frozen_since") else None),
    }


def _freeze_reasons() -> frozenset:
    from core.learning.anomaly_monitor import FREEZES_LEARNING  # noqa: PLC0415

    return FREEZES_LEARNING


def _brake_emit(event: str, details: dict, *, tenant_id: str, severity: str) -> None:
    """One hash-chained brake record on the tenant chain; raises ``AuditWriteFailed``."""
    from core.deployment import audit_sink  # noqa: PLC0415

    audit_sink.register_events(BRAKE_EVENTS)
    details = dict(details)
    # closed shapes only (s4-5): a hand-edited file must not put text on the chain
    if "reason" in details:
        details["reason"] = _closed_reason(details["reason"])
    if "version_id" in details:
        details["version_id"] = _closed_version(details["version_id"])
    if "cause" in details and details["cause"] not in _FREEZE_FAILURE_CAUSES:
        raise ValueError("unknown freeze failure cause")
    if "attempt" in details:
        details["attempt"] = max(1, int(details["attempt"]))
    audit_sink.emit(event, details, tenant_id=tenant_id, severity=severity)


#: Deadline for the per-skill config flock. Same value and same contract as
#: ``core.infinite_session.event_store.LOCK_TIMEOUT_SECONDS`` and
#: ``corvin_plugins.state.LOCK_TIMEOUT_SECONDS``.
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_RETRY_INTERVAL_SECONDS = 0.02


class SkillConfigLockBusy(TimeoutError):
    """The per-skill config lock stayed held past ``LOCK_TIMEOUT_SECONDS``.

    Raised instead of blocking. The console routes that mutate a Skill config
    (``POST /learning/feedback``, ``POST /learning/config/rollback``) map this
    to HTTP 503 ``lock_busy`` — a wedged holder must never hang an ``async``
    handler, because that stalls the whole console event loop, not one socket.
    """


@dataclass(frozen=True)
class SkillConfig:
    """Current Skill configuration (all tunable parameters)."""

    confidence_threshold: float = 0.70
    speed_weight: float = 0.50
    clarity_weight: float = 0.30
    exploration_rate: float = 0.20
    latency_penalty: float = 0.10

    def apply_delta(self, param: str, delta: float) -> SkillConfig:
        """Create new SkillConfig with one param changed."""
        if param not in self.__dataclass_fields__:
            raise ValueError(f"unknown skill config param: {param!r}")
        current = getattr(self, param)
        new_value = max(0.0, min(1.0, current + delta))  # Clamp to [0.0, 1.0]
        return SkillConfig(**{**self.__dict__, param: new_value})

    def to_dict(self) -> dict[str, float]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict | None) -> SkillConfig:
        data = data or {}
        known = {k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class SkillConfigVersion:
    """Immutable snapshot of a Skill config at a point in time."""

    version_id: str  # v1, v2, v3, ...
    skill_id: str
    timestamp: datetime
    config: SkillConfig
    change_reason: str = "baseline"
    improvement_pct: float = 0.0  # % improvement vs. baseline
    user_can_undo: bool = True


@dataclass
class OptimizerState:
    """Optimizer tracking (mutable during execution, then frozen)."""

    epoch: int = 1
    baseline_success_rate: float = 0.0
    current_success_rate: float = 0.0
    best_config: SkillConfig = field(default_factory=SkillConfig)
    config_versions: list[SkillConfigVersion] = field(default_factory=list)
    #: The version whose config is ``best_config`` NOW. Not ``config_versions[-1]``: a
    #: rollback restores an older config without deleting the newer versions.
    active_version: Optional[str] = None
    hypotheses_tested: int = 0
    hypotheses_accepted: int = 0
    mde_threshold: float = 0.05  # Minimum Detectable Effect
    #: ADR-2304 learning brake: set only by :func:`freeze_learning`, cleared only by
    #: :meth:`SkillAdapter.unfreeze` (a human, through the console route).
    frozen: bool = False
    frozen_reason: Optional[str] = None  # the anomaly event name
    frozen_since: Optional[str] = None   # ISO-8601 UTC


def _default_work_dir(tenant_id: str) -> Path:
    """``<tenant_home>/skills`` — honours CORVIN_HOME (never a bare ~/.corvin)."""
    try:
        from core.paths.tenant import tenant_home  # noqa: PLC0415

        return tenant_home(tenant_id) / "skills"
    except Exception:  # noqa: BLE001 — stripped install without core.paths
        root = os.environ.get("CORVIN_HOME")
        base = Path(root) if root else Path.home() / ".corvin"
        return base / "tenants" / tenant_id / "skills"


def skill_config_path(skill_id: str, tenant_id: str, work_dir: Optional[Path] = None) -> Path:
    """Where a Skill's learned config lives for a tenant (read-only helper)."""
    validate_tenant_id(tenant_id)
    base = Path(work_dir) if work_dir is not None else _default_work_dir(tenant_id)
    return base / f"{skill_id.replace('.', '_')}_config.json"


# Read-side cache for the router: (path) -> (mtime_ns, config, version_id)
_CONFIG_CACHE: dict[str, tuple[int, SkillConfig, Optional[str]]] = {}


def load_skill_config(
    skill_id: str, tenant_id: str, work_dir: Optional[Path] = None
) -> tuple[SkillConfig, Optional[str]]:
    """Return ``(config, current_version_id)`` for a Skill — the consumer side.

    Never raises and never creates directories: a Skill executing on the hot
    path must not fail or write because no config was learned yet. Missing or
    unreadable file → the default ``SkillConfig()`` and ``None``.
    """
    try:
        path = skill_config_path(skill_id, tenant_id, work_dir)
        mtime = path.stat().st_mtime_ns
    except (OSError, ValueError):
        return SkillConfig(), None
    key = str(path)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1], cached[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        config = SkillConfig.from_dict(data.get("config"))
        versions = data.get("versions") or []
        version_id = data.get("active_version")
        if not isinstance(version_id, str):  # written before active_version existed
            version_id = versions[-1].get("version_id") if versions else None
    except (OSError, ValueError, AttributeError, TypeError):
        return SkillConfig(), None
    _CONFIG_CACHE[key] = (mtime, config, version_id)
    return config, version_id


class SkillAdapter:
    """Autonomously tune Skill configs based on feedback (ADR-0549).

    Usage:
      1. Collect 50 tasks → compute baseline metrics
      2. For each hypothesis:
         - Test on 10 tasks with new config
         - If improvement >= MDE: accept + update
         - Else: reject + revert
      3. Continue until epoch 150
      4. Return final optimized config

    Reversibility: every version is stored WITH its config. Operator can
    rollback anytime, including after a restart.
    """

    def __init__(
        self,
        skill_id: str,
        tenant_id: str,
        work_dir: Optional[Path] = None,
        *,
        on_config_change: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        validate_tenant_id(tenant_id)
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.work_dir = Path(work_dir) if work_dir is not None else _default_work_dir(tenant_id)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._on_config_change = on_config_change

        # Load or initialize
        self.state = OptimizerState()
        self._load_or_init()

    # ── persistence ─────────────────────────────────────────────────────────

    @property
    def config_file(self) -> Path:
        return self.work_dir / f"{self.skill_id.replace('.', '_')}_config.json"

    @property
    def lock_file(self) -> Path:
        return self.config_file.with_suffix(".lock")

    @contextlib.contextmanager
    def _locked(self, *, timeout: Optional[float] = None) -> Iterator[None]:
        """BOUNDED exclusive advisory lock + reload: the only way state is mutated.

        The reload inside the lock is what makes this a fix and not a decoration:
        without it, two adapters that both loaded epoch N would serialise their
        WRITES but still both persist N+1.

        The acquisition is ``LOCK_EX | LOCK_NB`` with a hard deadline, the
        contract already used by ``core.infinite_session.event_store`` and
        ``corvin_plugins.state`` — NEVER a plain blocking ``LOCK_EX``. Both
        callers (:meth:`rollback`, :meth:`run_optimizer_epoch`) are reached from
        ``async def`` console handlers, so a blocking flock does not stall one
        request, it stalls the whole event loop: with the lock wedged by a
        foreign process, an unrelated anonymous ``GET /v1/console/version``
        timed out too (round-4 console review, F1). Refusing at the deadline is
        the only safe direction; the routes map :class:`SkillConfigLockBusy` to
        503 ``lock_busy``.
        """
        limit = LOCK_TIMEOUT_SECONDS if timeout is None else float(timeout)
        fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            deadline = time.monotonic() + limit
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SkillConfigLockBusy(
                            f"skill config lock busy for {self.skill_id!r}: still held "
                            f"after {limit:g}s — refusing to block the caller"
                        ) from None
                    time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
            self.state = OptimizerState()
            self._load_or_init()
            yield
        finally:
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load_or_init(self) -> None:
        """Load prior config, versions and epoch from disk, or start fresh."""
        if not self.config_file.exists():
            self.state.best_config = SkillConfig()
            return
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
            opt = _parse_optimizer(data)
            best = SkillConfig.from_dict(data.get("config"))
        except (OSError, ValueError, TypeError, AttributeError) as e:
            # Not a bad skill id (s2-5): the file is there and does not parse.
            raise BrakeStateUnreadable(
                f"Failed to read SkillAdapter config: {type(e).__name__}") from e
        stored = data.get("skill_id")
        if isinstance(stored, str) and stored != self.skill_id:
            # One canonical id per file (s2-4): ``os.x`` and ``os_x`` share a file name; the
            # id the file was written under wins and the alias is refused at every entry.
            raise SkillIdAlias(self.skill_id, stored if valid_skill_id(stored) else "unparsed")
        self.state.best_config = best
        self.state.epoch = opt["epoch"]
        self.state.baseline_success_rate = opt["baseline_success_rate"]
        self.state.current_success_rate = opt["current_success_rate"]
        self.state.hypotheses_tested = opt["hypotheses_tested"]
        self.state.hypotheses_accepted = opt["hypotheses_accepted"]
        self.state.frozen = opt["frozen"]
        self.state.frozen_reason = opt["frozen_reason"]
        self.state.frozen_since = opt["frozen_since"]
        versions: list[SkillConfigVersion] = []
        unparsed: list = []
        for v in data.get("versions") or []:
            try:
                versions.append(
                    SkillConfigVersion(
                        version_id=str(v["version_id"]),
                        skill_id=self.skill_id,
                        timestamp=datetime.fromisoformat(v["timestamp"]),
                        config=SkillConfig.from_dict(v.get("config")),
                        change_reason=str(v.get("change_reason", "")),
                        improvement_pct=float(v.get("improvement_pct", 0.0)),
                        user_can_undo=bool(v.get("user_can_undo", True)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                # Kept VERBATIM and written back: dropping it lost history and let the next id
                # collide with it (review SC-05).
                logger.warning("keeping malformed skill config version as-is: %r", v)
                unparsed.append(v)
        self.state.config_versions = versions
        self._unparsed_versions = unparsed
        active = data.get("active_version")
        self.state.active_version = active if isinstance(active, str) else (
            versions[-1].version_id if versions else None)

    def _persist(self) -> None:
        """Save config + versions + optimizer counters (atomic tmp → rename)."""
        self._commit_tmp(self._write_tmp())

    def _write_tmp(self) -> Path:
        """Write the current state to the temp file (no rename yet). Raises ``RuntimeError``."""
        data = {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "config": self.state.best_config.to_dict(),
            "active_version": self.state.active_version,
            "optimizer": {
                "epoch": self.state.epoch,
                "baseline_success_rate": self.state.baseline_success_rate,
                "current_success_rate": self.state.current_success_rate,
                "hypotheses_tested": self.state.hypotheses_tested,
                "hypotheses_accepted": self.state.hypotheses_accepted,
                "frozen": self.state.frozen,
                "frozen_reason": self.state.frozen_reason,
                "frozen_since": self.state.frozen_since,
            },
            "versions": [
                {
                    "version_id": v.version_id,
                    "timestamp": v.timestamp.isoformat(),
                    "change_reason": v.change_reason,
                    "improvement_pct": v.improvement_pct,
                    "user_can_undo": v.user_can_undo,
                    "config": v.config.to_dict(),
                }
                for v in self.state.config_versions
            ] + list(getattr(self, "_unparsed_versions", []) or []),
        }
        tmp = self.config_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (IOError, OSError) as e:
            raise RuntimeError(f"Failed to persist SkillAdapter config: {e}") from e
        return tmp

    def _commit_tmp(self, tmp: Path) -> None:
        """Rename the prepared temp file over the config. Raises ``RuntimeError``."""
        try:
            os.replace(tmp, self.config_file)
        except (IOError, OSError) as e:
            raise RuntimeError(f"Failed to persist SkillAdapter config: {e}") from e

    def _drop_tmp(self, tmp: Path) -> None:
        with contextlib.suppress(OSError):
            tmp.unlink()

    def _announce(self, change: str, **fields: Any) -> None:
        if self._on_config_change is None:
            return
        payload = {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "change": change,
            "config": self.state.best_config.to_dict(),
            "version_id": self.state.active_version
            or (self.state.config_versions[-1].version_id if self.state.config_versions else None),
            **fields,
        }
        try:
            self._on_config_change(payload)
        except Exception as e:  # noqa: BLE001 — the announcement must not undo the change
            logger.error("skill config change announcement failed: %s", e)

    # ── optimizer ───────────────────────────────────────────────────────────

    def run_optimizer_epoch(
        self,
        hypothesis: Optional[ConfigHypothesis],
        recent_successes: int,
        recent_total: int,
    ) -> tuple[bool, str]:
        """Run one optimizer epoch.

        Args:
            hypothesis: hypothesis to test (None = baseline phase)
            recent_successes: successes in last 10 tasks
            recent_total: total tasks in last 10

        Returns:
            (accepted, reason): whether hypothesis was accepted. An accepted reason ends in
            :data:`UNVERIFIED_CAUSALITY` (ADR-2305 Part 1: always, until the causal test exists).

        Raises:
            LearningFrozen: the skill's learning is frozen (ADR-2304); nothing changed,
                not even the epoch counter.
        """
        with self._locked():
            return self._run_optimizer_epoch_locked(hypothesis, recent_successes, recent_total)

    def _run_optimizer_epoch_locked(
        self,
        hypothesis: Optional[ConfigHypothesis],
        recent_successes: int,
        recent_total: int,
    ) -> tuple[bool, str]:
        if self.state.frozen:
            raise self._refuse_frozen("skill_config")
        label = UNVERIFIED_CAUSALITY
        self.state.epoch += 1
        recent_success_rate = recent_successes / recent_total if recent_total > 0 else 0.0

        # Phase 1–50: Baseline collection (no changes)
        if self.state.epoch <= 50:
            self.state.baseline_success_rate = recent_success_rate
            self._persist()
            return False, "baseline_collection_phase"

        # Phase 2–100: Test one hypothesis per epoch
        if self.state.epoch <= 100 and hypothesis:
            self.state.current_success_rate = recent_success_rate
            improvement = self.state.current_success_rate - self.state.baseline_success_rate
            mde = self.state.mde_threshold

            if improvement >= mde:
                # Accept: apply the change
                new_config = self.state.best_config.apply_delta(
                    hypothesis.param,
                    hypothesis.delta
                )
                self.state.best_config = new_config
                self.state.hypotheses_accepted += 1

                # Record version
                version = SkillConfigVersion(
                    version_id=self._next_version_id(),
                    skill_id=self.skill_id,
                    timestamp=datetime.now(timezone.utc),
                    config=new_config,
                    change_reason=hypothesis.reason + label,
                    improvement_pct=improvement * 100,
                )
                self.state.config_versions.append(version)
                self.state.active_version = version.version_id

                self._persist()
                self._announce(
                    "hypothesis_accepted",
                    hypothesis_id=hypothesis.hypothesis_id,
                    param=hypothesis.param,
                    delta=hypothesis.delta,
                    improvement_pct=improvement * 100,
                )
                return True, f"improvement_{improvement:.2%}_accepted{label}"
            else:
                # Reject: keep current config
                self.state.hypotheses_tested += 1
                self._persist()
                return False, f"improvement_{improvement:.2%}_below_mde_{mde}"

        # Phase 3–150: Refinement (test combinations)
        if self.state.epoch > 100:
            # For now, just converge (don't test more)
            self._persist()
            if self.state.epoch >= 150:
                return False, "convergence_reached"

        self._persist()
        return False, "no_action"

    def _next_version_id(self) -> str:
        """``v<max+1>`` over every known id (parsed AND kept-verbatim): ``v{len+1}`` re-issued an
        existing id once an entry was unreadable, so two versions shared one name (review SC-05)."""
        nums = []
        for vid in [v.version_id for v in self.state.config_versions] + [
                str((u or {}).get("version_id", "")) for u in getattr(self, "_unparsed_versions", []) or []
                if isinstance(u, dict)]:
            m = re.fullmatch(r"v([0-9]{1,9})", vid)   # ASCII digits only: '²'.isdigit() is True
            if m:
                nums.append(int(m.group(1)))
        return f"v{(max(nums) if nums else 0) + 1}"

    def rollback(self, to_version: str) -> SkillConfig:
        """Rollback to a prior version (user override, GDPR Art. 21)."""
        with self._locked():
            matches = [v for v in self.state.config_versions if v.version_id == to_version]
            if len(matches) > 1:
                raise ValueError(f"Version {to_version} is ambiguous ({len(matches)} entries)")
            for v in matches:
                if v.version_id == to_version:
                    self.state.best_config = v.config
                    self.state.active_version = v.version_id
                    self._persist()
                    self._announce("rollback", to_version=to_version)
                    return v.config
            raise ValueError(f"Version {to_version} not found")

    # ── learning brake (ADR-2304) ───────────────────────────────────────────

    def _current_version_id(self) -> str:
        return self.state.active_version or (
            self.state.config_versions[-1].version_id if self.state.config_versions else "none")

    def _refuse_frozen(self, target: str) -> LearningFrozen:
        """Record the refused write (best effort: the refusal holds either way) and
        return the exception the caller raises."""
        reason = self.state.frozen_reason or "unknown"
        _record_refusal(self.skill_id, self.tenant_id, reason=reason,
                        version_id=self._current_version_id(), target=target)
        return LearningFrozen(self.skill_id, reason)

    @property
    def frozen(self) -> bool:
        return self.state.frozen

    def _set_brake(self, frozen: bool, reason: Optional[str]) -> tuple:
        before = (self.state.frozen, self.state.frozen_reason, self.state.frozen_since)
        self.state.frozen = frozen
        self.state.frozen_reason = reason if frozen else None
        self.state.frozen_since = datetime.now(timezone.utc).isoformat() if frozen else None
        return before

    def _brake_transition(self, *, frozen: bool, reason: str, record: str, record_details: dict,
                          severity: str, not_applied: str) -> None:
        """Audit-first brake change that never leaves the chain and the file disagreeing silently.

        The new state is written to the temp file BEFORE the record (a failure there changes
        nothing and records nothing); the record is chained; then the temp file is renamed.
        Only that rename can fail after the record committed - then a compensating
        ``not_applied`` record is chained and :class:`BrakeNotApplied` raised (s2-3)."""
        before = self._set_brake(frozen, reason)
        try:
            tmp = self._write_tmp()
        except RuntimeError:
            self.state.frozen, self.state.frozen_reason, self.state.frozen_since = before
            raise
        try:
            _brake_emit(record, record_details, tenant_id=self.tenant_id, severity=severity)
        except BaseException:
            self._drop_tmp(tmp)
            self.state.frozen, self.state.frozen_reason, self.state.frozen_since = before
            raise
        try:
            self._commit_tmp(tmp)
        except RuntimeError as exc:
            self._drop_tmp(tmp)
            self.state.frozen, self.state.frozen_reason, self.state.frozen_since = before
            try:
                _brake_emit(not_applied, record_details, tenant_id=self.tenant_id, severity="ERROR")
            except Exception as rec_exc:  # noqa: BLE001 — logged; the caller still sees the failure
                logger.error("%s for %s did not commit (%s)", not_applied, self.skill_id,
                             type(rec_exc).__name__)
            raise BrakeNotApplied(f"{record} committed for {self.skill_id!r}, state not written") from exc

    def freeze(self, reason: str, *, attempt: int = 1) -> bool:
        """Freeze this skill's learned-config writes. Audit-first.

        Returns True when this call froze it, False when it was already frozen (nothing
        recorded). Raises ``AuditWriteFailed`` when ``learning.frozen`` did not commit —
        then nothing changed — and :class:`BrakeNotApplied` when it committed but the state
        file could not be replaced (``learning.freeze_not_applied`` follows it on the chain;
        the retry carries ``attempt`` + 1). No rollback is triggered.
        """
        if reason not in _freeze_reasons():
            raise ValueError(f"not a freezing anomaly: {reason!r}")
        with self._locked():
            if self.state.frozen:
                return False
            self._brake_transition(
                frozen=True, reason=reason, record="learning.frozen", severity="WARNING",
                not_applied="learning.freeze_not_applied",
                record_details={"skill_id": self.skill_id, "reason": reason,
                                "version_id": self._current_version_id(), "attempt": attempt})
            return True

    def unfreeze(self) -> str:
        """Lift the brake (a human, through the console route). Audit-first.

        Returns the reason the skill was frozen for. Raises :class:`NotFrozen` when it was
        not frozen, ``AuditWriteFailed`` when ``learning.unfrozen`` did not commit (then it
        stays frozen) and :class:`BrakeNotApplied` when it committed but the state file could
        not be replaced (``learning.unfreeze_not_applied`` follows; it stays frozen).
        """
        with self._locked():
            if not self.state.frozen:
                raise NotFrozen(f"learning of {self.skill_id!r} is not frozen")
            reason = self.state.frozen_reason or "unknown"
            self._brake_transition(
                frozen=False, reason=reason, record="learning.unfrozen", severity="INFO",
                not_applied="learning.unfreeze_not_applied",
                record_details={"skill_id": self.skill_id, "reason": reason,
                                "version_id": self._current_version_id()})
            return reason

    def get_current_config(self) -> SkillConfig:
        """Get current configuration."""
        return self.state.best_config

    def get_version_history(self) -> list[SkillConfigVersion]:
        """Get all versions (for dashboard)."""
        return list(self.state.config_versions)

    def apply_config_delta(
        self,
        param_deltas: dict[str, float],
        confidence_delta: float,
    ) -> dict[str, float]:
        """Apply configuration delta from feedback optimizer (ADR-0876).

        This method implements the feedback→config→execution closure: when an
        optimizer computes a delta from user feedback, this method atomically
        applies it, emits an audit event, and persists the new config.

        Args:
            param_deltas: Dict of parameter name → delta value. E.g.,
                {"confidence_threshold": +0.05, "speed_weight": -0.02}
            confidence_delta: Overall confidence improvement score (for audit).

        Returns:
            Updated config as dict (e.g., {"confidence_threshold": 0.75, ...})

        Raises:
            RuntimeError: if audit chain write fails (fail-closed — config is
                not applied if we cannot record it).
            SkillConfigLockBusy: if the config lock is held by another writer.

        Side effects:
            - Emits skill_config_updated audit event (hash-chained)
            - Persists new config to ~/.corvin/skills/<skill_id>_config.json
            - Announces the change via on_config_change callback (if set)
        """
        with self._locked():
            return self._apply_config_delta_locked(param_deltas, confidence_delta)

    def _apply_config_delta_locked(
        self,
        param_deltas: dict[str, float],
        confidence_delta: float,
    ) -> dict[str, float]:
        """Apply delta under the config lock (reload + mutate + persist + audit)."""
        # 1. Reload current config (in case another writer changed it)
        self._load_or_init()
        if self.state.frozen:  # ADR-2304: no config change, one refusal record
            raise self._refuse_frozen("skill_config")
        old_config = self.state.best_config

        # 2. Apply each delta to the current config
        updated = old_config
        for param, delta in param_deltas.items():
            if delta != 0.0:
                updated = updated.apply_delta(param, delta)

        # 3. Update state
        self.state.best_config = updated

        # 4. Create version entry
        version = SkillConfigVersion(
            version_id=self._next_version_id(),
            skill_id=self.skill_id,
            timestamp=datetime.now(timezone.utc),
            config=updated,
            change_reason="feedback_delta",
            improvement_pct=confidence_delta * 100,
            user_can_undo=True,
        )
        self.state.config_versions.append(version)
        self.state.active_version = version.version_id

        # 5. Emit audit event FIRST (fail-closed: no audit write → no persist)
        self._emit_config_update_audit_event(
            old_config=old_config,
            new_config=updated,
            param_deltas=param_deltas,
            confidence_delta=confidence_delta,
            version_id=version.version_id,
        )

        # 6. Persist config (audit succeeded, so this is safe)
        self._persist()

        # 7. Announce the change
        self._announce(
            "config_delta_applied",
            param_deltas=param_deltas,
            confidence_delta=confidence_delta,
            version_id=version.version_id,
        )

        return updated.to_dict()

    def _emit_config_update_audit_event(
        self,
        old_config: SkillConfig,
        new_config: SkillConfig,
        param_deltas: dict[str, float],
        confidence_delta: float,
        version_id: str,
    ) -> None:
        """Emit skill_config_updated event to audit chain (ADR-0314, ADR-0876).

        This is audit-first: if the chain write fails, we raise RuntimeError
        and the caller's transaction is aborted. The config is never persisted
        without a corresponding audit record.

        Raises:
            RuntimeError: if the audit chain write fails.
        """
        try:
            from core.learning.learning_events import EventType, LearningEvent
            from core.learning.event_store import EventStore
            from core.paths.tenant import tenant_home  # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(f"Cannot emit audit event: missing learning module: {e}") from e

        # Build skill_config_updated event
        signal = {
            "param_deltas": param_deltas,
            "confidence_before": old_config.confidence_threshold,
            "confidence_after": new_config.confidence_threshold,
            "confidence_delta": confidence_delta,
            "version_id": version_id,
            "params_before": old_config.to_dict(),
            "params_after": new_config.to_dict(),
        }

        event = LearningEvent.create(
            event_type=EventType.CONFIG_UPDATED,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal=signal,
            lom="core.skills.os_skills.skill_adapter:SkillAdapter.apply_config_delta",
        )

        # Write to audit chain (fail-closed)
        try:
            home = tenant_home(self.tenant_id)
            store = EventStore(home, tenant_id=self.tenant_id)
            store.write_event(event)
        except Exception as e:
            raise RuntimeError(f"Failed to emit config update audit event: {e}") from e


# ── learning brake: module-level entry points (ADR-2304) ────────────────────


def _record_refusal(skill_id: str, tenant_id: str, *, reason: str, version_id: str, target: str) -> None:
    if target not in _BRAKE_TARGETS:
        raise ValueError(f"unknown brake target: {target!r}")
    try:
        _brake_emit("learning.freeze_refused_write", {
            "skill_id": skill_id, "reason": reason, "version_id": version_id, "target": target,
        }, tenant_id=tenant_id, severity="WARNING")
    except Exception as exc:  # noqa: BLE001 — the write stays refused even when unrecorded
        logger.warning("learning.freeze_refused_write for %s did not commit (%s)",
                       skill_id, type(exc).__name__)


def freeze_learning(skill_id: str, tenant_id: str, *, reason: str, attempt: int = 1) -> bool:
    """Freeze ``skill_id``'s learned-config writes for ``tenant_id`` (the learning host's
    one call site, after the anomaly record committed). True = newly frozen, False =
    already frozen. Raises ``ValueError`` (bad id / reason; the host checks the id itself
    first), :class:`BrakeStateUnreadable` (the config file does not parse - retried),
    ``AuditWriteFailed`` (not recorded ⇒ not frozen; the host retries on its next check),
    :class:`BrakeNotApplied` (recorded, then compensated; retried with ``attempt`` + 1),
    :class:`SkillConfigLockBusy`."""
    if not valid_skill_id(skill_id):
        raise ValueError("skill id is not a safe config name")
    return SkillAdapter(skill_id, tenant_id).freeze(reason, attempt=attempt)


def record_freeze_failed(skill_id: str, tenant_id: str, *, reason: str, cause: str, attempt: int) -> None:
    """Chain ``learning.freeze_failed`` (a freeze that could not be attempted). Raises
    ``AuditWriteFailed`` when it does not commit."""
    _brake_emit("learning.freeze_failed", {
        "skill_id": skill_id, "reason": reason, "cause": cause, "attempt": attempt,
    }, tenant_id=tenant_id, severity="WARNING")


@contextlib.contextmanager
def brake_lock(skill_id: str, tenant_id: str, *, timeout: Optional[float] = None) -> Iterator[None]:
    """Hold the skill's config flock - the lock :meth:`SkillAdapter.freeze` takes - so a
    writer outside the adapter (the DoD weights) can make "check the brake, chain the change,
    store it" one step no freeze can land inside (s2-2). Bounded like ``_locked``: raises
    :class:`SkillConfigLockBusy`. Creates the skills directory and the lock file, never the
    config file. Not re-entrant: never call a function that takes it again while holding it."""
    if not valid_skill_id(skill_id):
        raise ValueError("skill id is not a safe config name")
    lock = skill_config_path(skill_id, tenant_id).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    limit = LOCK_TIMEOUT_SECONDS if timeout is None else float(timeout)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        deadline = time.monotonic() + limit
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SkillConfigLockBusy(
                        f"skill config lock busy for {skill_id!r}: still held after {limit:g}s"
                    ) from None
                time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
        yield
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def refuse_if_frozen(skill_id: str, tenant_id: str, *, target: str) -> None:
    """Raise :class:`LearningFrozen` (and record the refusal) when ``skill_id`` is frozen.

    For learned-config writers outside :class:`SkillAdapter` (the DoD weights). Reads the
    skill's config file only — never creates it. An unreadable file - including one with a
    malformed number the adapter itself would refuse to load - reads as frozen
    (``brake_state_unreadable``): a brake that cannot be read must not open. The file is the
    brake whatever id it was written under. Reason and version id reach the record and the
    exception in their closed shapes only.
    """
    path = skill_config_path(skill_id, tenant_id)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        opt = _parse_optimizer(data)
        frozen, reason = opt["frozen"], opt["frozen_reason"] or "unknown"
        version = data.get("active_version")
    except (OSError, ValueError, AttributeError, TypeError):
        frozen, reason, version = True, "brake_state_unreadable", None
    if not frozen:
        return
    _record_refusal(skill_id, tenant_id, reason=reason, version_id=_closed_version(version),
                    target=target)
    raise LearningFrozen(skill_id, reason)
