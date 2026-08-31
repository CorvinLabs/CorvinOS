"""user_style.py — autonomous user-style learner (Layer 26).

Closed-loop learner that observes outcome-grading signals
(``skill.outcome_graded`` events in the unified hash chain) and emits
short refinement bullets that get auto-injected into every turn until
they prove harmful — at which point they are auto-rolled-back.

Pipeline (run once per day via systemd timer)
---------------------------------------------

    1. ``aggregate_clusters(audit_path, since_days=14)``
       Walk the chain, count rejection/rephrase/approval signals per
       (skill_name) and build candidate ``Cluster`` objects.

    2. ``passes_confidence_gate(cluster)``
       True iff hits >= MIN_HITS AND negative_ratio > NEGATIVE_THRESHOLD.

    3. ``judge_overfit(cluster, judge_fn)``
       Independent second-opinion check (defaults to dialectic-CLI).
       FAITHFUL → promote to shadow; OVERFIT → silently drop.

    4. ``start_shadow(cluster, store)``
       Add to ``candidates.json`` with ``state="shadow"`` and a
       ``shadow_started_at`` timestamp.

    5. ``evaluate_shadow(candidate, audit_path, store)``
       After SHADOW_DAYS, compare with-bullet vs without-bullet
       rejection rates. Promote if ``with < without - MIN_DELTA``,
       reject otherwise.

    6. ``promote_bullet(candidate, store)`` — flip state, write to live
       bullets list, render ``style.md``.

    7. ``evaluate_live_bullet(bullet, audit_path, store)`` — over
       LIVE_WINDOW_DAYS, check rejection rate; auto-rollback when above
       ROLLBACK_THRESHOLD.

    8. ``rollback_bullet(bullet, store)`` — remove from live, add to
       cooldown.json with COOLDOWN_DAYS expiry.

A/B selector for the bridge (called per turn)
---------------------------------------------

    ``shadow_pick_for_turn(store, turn_seed) -> list[str]``

Returns the bullets that should be injected for the current turn.
Live bullets are always injected; shadow bullets are deterministically
included for half the turns (parity of sha256(turn_seed)) so the
``with``/``without`` cohorts are comparable.

Cost contract — IMPORTANT
-------------------------
This module MUST NOT import the Anthropic SDK. The judge call uses the
existing ``dialectic.py`` infrastructure (``cli`` mode = ``claude -p``
subprocess via the operator's Max-Abo).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

# ── Audit hash chain (best-effort import) ──────────────────────────────────
_audit_writer: Callable[..., Any] | None = None
try:
    _HERE = Path(__file__).resolve().parent
    _FORGE_TOP = _HERE.parent.parent / "forge"
    if _FORGE_TOP.is_dir() and str(_FORGE_TOP) not in sys.path:
        sys.path.insert(0, str(_FORGE_TOP))
    from forge.security_events import write_event as _audit_writer  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _audit_writer = None


# ── Tunables ───────────────────────────────────────────────────────────────

MIN_HITS = 5                # cluster needs ≥ this many outcome signals
NEGATIVE_THRESHOLD = 0.70   # rejection+rephrase share to trip the gate
SHADOW_DAYS = 3             # A/B observation window
LIVE_WINDOW_DAYS = 7        # rolling window for live evaluation
MIN_DELTA = 0.05            # min loss-improvement to promote shadow→live
ROLLBACK_THRESHOLD = 0.55   # negative_rate above which we rollback
COOLDOWN_DAYS = 30          # min wait before a removed cluster can return
HARD_CAP = 10               # max simultaneously-live bullets

# Signal scores from skill_inject (mirrored, not imported, to keep this
# module loadable without skill-forge present).
_SIGNAL_NEGATIVE = ("rejection", "rephrase")
_SIGNAL_POSITIVE = ("approval",)


# ── Storage layout ─────────────────────────────────────────────────────────

def _store_dir(*, corvin_home: Path | None = None) -> Path:
    """``<corvin_home>/global/user_style/``. Falls back to env / forge.paths / default."""
    if corvin_home is not None:
        return Path(corvin_home) / "global" / "user_style"
    # Check env var BEFORE forge.paths: forge.paths.corvin_home() ignores
    # CORVIN_HOME since Phase 7 removed env-based resolution there. Tests
    # set CORVIN_HOME to a temp dir for isolation, so env must win.
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env) / "global" / "user_style"
    try:
        from forge.paths import corvin_home as _ch  # type: ignore  # noqa: PLC0415
        return Path(_ch()) / "global" / "user_style"
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin" / "global" / "user_style"


def _audit_path(*, corvin_home: Path | None = None) -> Path:
    if corvin_home is not None:
        return Path(corvin_home) / "global" / "forge" / "audit.jsonl"
    # Same env-first ordering as _store_dir — see comment there.
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env) / "global" / "forge" / "audit.jsonl"
    try:
        from forge.paths import corvin_home as _ch  # type: ignore  # noqa: PLC0415
        return Path(_ch()) / "global" / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin" / "global" / "forge" / "audit.jsonl"


_STORE_LOCK = threading.RLock()


def _read_json(p: Path, default: Any) -> Any:
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class SignalCounts:
    """Aggregated counts for one cluster."""
    rejection: int = 0
    rephrase:  int = 0
    approval:  int = 0

    @property
    def total(self) -> int:
        return self.rejection + self.rephrase + self.approval

    @property
    def negative_ratio(self) -> float:
        return (self.rejection + self.rephrase) / self.total if self.total else 0.0


@dataclass
class Cluster:
    """A correction-pattern cluster keyed by skill name."""
    cluster_id: str
    skill_name: str
    counts: SignalCounts
    sample_run_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["counts"] = {
            "rejection": self.counts.rejection,
            "rephrase":  self.counts.rephrase,
            "approval":  self.counts.approval,
            "total":     self.counts.total,
            "negative_ratio": self.counts.negative_ratio,
        }
        return d


@dataclass
class Candidate:
    """A bullet living in one of three states: shadow / live / cooldown."""
    bullet_id: str
    cluster_id: str
    skill_name: str
    bullet_text: str
    state: str = "shadow"               # shadow | live | cooldown
    shadow_started_at: float = field(default_factory=time.time)
    live_started_at: float | None = None
    cooldown_until: float | None = None
    rolled_back_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Candidate":
        return cls(**d)


# ── Helpers ────────────────────────────────────────────────────────────────

def _cluster_id(skill_name: str) -> str:
    return "cl_" + hashlib.sha256(skill_name.encode("utf-8")).hexdigest()[:12]


def _bullet_id(skill_name: str, ts: float) -> str:
    raw = f"{skill_name}:{ts:.6f}".encode("utf-8")
    return "b_" + hashlib.sha256(raw).hexdigest()[:12]


def _iter_audit_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _audit(event_type: str, *, corvin_home: Path | None = None,
           details: dict | None = None) -> None:
    """Best-effort write into the unified hash chain."""
    if _audit_writer is None:
        return
    try:
        _audit_writer(
            _audit_path(corvin_home=corvin_home),
            event_type,
            tool="user_style",
            details=details or {},
        )
    except Exception:  # noqa: BLE001
        pass


# ── Phase 1 — aggregate ────────────────────────────────────────────────────

def aggregate_clusters(
    audit_path: Path | None = None,
    *,
    since_days: int = 14,
    now: float | None = None,
    corvin_home: Path | None = None,
) -> list[Cluster]:
    """Walk the audit chain, count outcome signals per skill_name."""
    if audit_path is None:
        audit_path = _audit_path(corvin_home=corvin_home)
    if now is None:
        now = time.time()
    horizon = now - since_days * 86400

    by_skill: dict[str, SignalCounts] = {}
    samples:  dict[str, list[str]]   = {}

    for ev in _iter_audit_events(audit_path):
        if ev.get("event_type") != "skill.outcome_graded":
            continue
        if float(ev.get("ts", 0)) < horizon:
            continue
        details = ev.get("details", {}) or {}
        signal = details.get("signal")
        skills = details.get("skills") or []
        run_id = details.get("prev_run_id", "")
        if signal not in ("approval", "rejection", "rephrase"):
            continue
        for name in skills:
            if not isinstance(name, str) or not name:
                continue
            counts = by_skill.setdefault(name, SignalCounts())
            if signal == "rejection":
                counts.rejection += 1
            elif signal == "rephrase":
                counts.rephrase += 1
            else:
                counts.approval += 1
            if run_id and len(samples.setdefault(name, [])) < 5:
                samples[name].append(run_id)

    clusters: list[Cluster] = []
    for name, counts in by_skill.items():
        clusters.append(Cluster(
            cluster_id=_cluster_id(name),
            skill_name=name,
            counts=counts,
            sample_run_ids=samples.get(name, []),
        ))
    clusters.sort(key=lambda c: (-c.counts.total, c.skill_name))
    return clusters


# ── Phase 2 — confidence gate ──────────────────────────────────────────────

def passes_confidence_gate(cluster: Cluster) -> bool:
    """≥ MIN_HITS samples AND negative_ratio > NEGATIVE_THRESHOLD."""
    return (
        cluster.counts.total >= MIN_HITS
        and cluster.counts.negative_ratio > NEGATIVE_THRESHOLD
    )


# ── Phase 3 — judge (drift defence) ────────────────────────────────────────

def _default_judge(cluster: Cluster, draft: str, *, timeout_s: int = 20) -> bool:
    """Spawn ``claude -p`` to judge whether the draft overfits noise.

    Returns True iff the judge says FAITHFUL. Any other / unparseable /
    timeout / missing-cli outcome returns False (conservative — better
    to skip a real signal than promote noise).
    """
    prompt = (
        "You are reviewing whether a behavioural rule about a coding "
        "assistant is justified by the data, or whether it overfits to "
        "random noise.\n\n"
        f"SKILL UNDER REVIEW: {cluster.skill_name}\n"
        f"REJECTION COUNT: {cluster.counts.rejection}\n"
        f"REPHRASE COUNT:  {cluster.counts.rephrase}\n"
        f"APPROVAL COUNT:  {cluster.counts.approval}\n"
        f"NEGATIVE RATIO:  {cluster.counts.negative_ratio:.2f}\n\n"
        f"PROPOSED RULE:\n{draft}\n\n"
        "Reply EXACTLY ONE LINE:\n"
        "  FAITHFUL  | <one-sentence why the rule is justified>\n"
        "  OVERFIT   | <one-sentence why this is just noise>\n"
    )
    try:
        from . import helper_model as _hm  # type: ignore
    except ImportError:
        try:
            import helper_model as _hm  # type: ignore
        except ImportError:
            _hm = None
    model_args = _hm.claude_args(_hm.SITE_USER_STYLE_JUDGE) if _hm else []
    try:
        cli = os.environ.get("CLAUDE_CLI") or (_hm.resolve_claude_bin() if _hm else "claude")
        r = subprocess.run(
            [cli, "-p", "--max-turns", "1", "--tools", "", *model_args],
            input=prompt, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return False
        head = out[0].strip().upper()
        return head.startswith("FAITHFUL")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def judge_overfit(cluster: Cluster, draft: str, *,
                  judge_fn: Callable[[Cluster, str], bool] | None = None) -> bool:
    """Returns True iff the judge says FAITHFUL (proceed to shadow)."""
    fn = judge_fn or _default_judge
    try:
        return bool(fn(cluster, draft))
    except Exception:  # noqa: BLE001
        return False


# ── Bullet draft generation ────────────────────────────────────────────────

def draft_bullet(cluster: Cluster) -> str:
    """Build a one-line bullet draft from cluster metadata.

    Deliberately conservative wording — the bullet is a *flag* for the
    LLM to slow down, not a directive. Real wording refinement happens
    in a future iteration; the structural pipeline is what matters here.
    """
    name = cluster.skill_name
    ratio = cluster.counts.negative_ratio
    return (
        f"When applying skill `{name}`, double-check the user's intent "
        f"before committing — recent feedback shows {ratio:.0%} "
        f"corrections after this skill fires "
        f"(n={cluster.counts.total})."
    )


# ── Phase 4–6 — shadow / promote ───────────────────────────────────────────

def _candidates_path(*, corvin_home: Path | None = None) -> Path:
    return _store_dir(corvin_home=corvin_home) / "candidates.json"


def _live_path(*, corvin_home: Path | None = None) -> Path:
    return _store_dir(corvin_home=corvin_home) / "live_bullets.json"


def _cooldown_path(*, corvin_home: Path | None = None) -> Path:
    return _store_dir(corvin_home=corvin_home) / "cooldown.json"


def _style_md_path(*, corvin_home: Path | None = None) -> Path:
    return _store_dir(corvin_home=corvin_home) / "style.md"


def load_candidates(*, corvin_home: Path | None = None) -> list[Candidate]:
    raw = _read_json(_candidates_path(corvin_home=corvin_home), [])
    return [Candidate.from_dict(d) for d in raw if isinstance(d, dict)]


def save_candidates(cands: list[Candidate], *,
                    corvin_home: Path | None = None) -> None:
    _write_json(
        _candidates_path(corvin_home=corvin_home),
        [c.to_dict() for c in cands],
    )


def load_live(*, corvin_home: Path | None = None) -> list[Candidate]:
    raw = _read_json(_live_path(corvin_home=corvin_home), [])
    return [Candidate.from_dict(d) for d in raw if isinstance(d, dict)]


def save_live(cands: list[Candidate], *,
              corvin_home: Path | None = None) -> None:
    _write_json(
        _live_path(corvin_home=corvin_home),
        [c.to_dict() for c in cands],
    )


def load_cooldown(*, corvin_home: Path | None = None) -> dict[str, float]:
    """{cluster_id: cooldown_until_ts} — entries past the timestamp can
    re-enter the pipeline."""
    return _read_json(_cooldown_path(corvin_home=corvin_home), {})


def save_cooldown(data: dict[str, float], *,
                  corvin_home: Path | None = None) -> None:
    _write_json(_cooldown_path(corvin_home=corvin_home), data)


def in_cooldown(cluster_id: str, *,
                corvin_home: Path | None = None,
                now: float | None = None) -> bool:
    cd = load_cooldown(corvin_home=corvin_home)
    until = cd.get(cluster_id)
    if until is None:
        return False
    return float(until) > (now if now is not None else time.time())


def start_shadow(cluster: Cluster, *,
                 corvin_home: Path | None = None,
                 now: float | None = None) -> Candidate:
    """Create a Candidate in shadow state. Idempotent on cluster_id."""
    if now is None:
        now = time.time()
    with _STORE_LOCK:
        cands = load_candidates(corvin_home=corvin_home)
        for c in cands:
            if c.cluster_id == cluster.cluster_id:
                return c  # already shadow / pending
        c = Candidate(
            bullet_id=_bullet_id(cluster.skill_name, now),
            cluster_id=cluster.cluster_id,
            skill_name=cluster.skill_name,
            bullet_text=draft_bullet(cluster),
            state="shadow",
            shadow_started_at=now,
        )
        cands.append(c)
        save_candidates(cands, corvin_home=corvin_home)
        _audit(
            "user_style.candidate_proposed",
            corvin_home=corvin_home,
            details={
                "bullet_id":  c.bullet_id,
                "cluster_id": c.cluster_id,
                "skill_name": c.skill_name,
                "negative_ratio": cluster.counts.negative_ratio,
                "total":     cluster.counts.total,
            },
        )
        return c


def shadow_pick_for_turn(turn_seed: str, *,
                         corvin_home: Path | None = None) -> tuple[list[str], list[str]]:
    """Return (live_bullets, shadow_bullets_for_this_turn).

    Live bullets are always injected. Shadow bullets are deterministically
    included for half the turns (sha256 parity) so that live-cohort and
    no-bullet-cohort sample sizes are comparable over the window.

    Caller (adapter) is expected to record which shadow bullets were
    actually injected for the turn so ``evaluate_shadow`` can attribute
    feedback signals correctly.
    """
    live = [c.bullet_text for c in load_live(corvin_home=corvin_home)]
    shadow_all = [
        c for c in load_candidates(corvin_home=corvin_home)
        if c.state == "shadow"
    ]
    h = hashlib.sha256(turn_seed.encode("utf-8")).digest()
    flip = h[0] & 1  # 0 or 1, deterministic per turn
    shadow_active = [c.bullet_text for c in shadow_all] if flip else []
    return (live, shadow_active)


def shadow_active_for_seed(turn_seed: str) -> bool:
    """Whether shadow bullets are injected for this turn (per-seed parity)."""
    h = hashlib.sha256(turn_seed.encode("utf-8")).digest()
    return bool(h[0] & 1)


def _signals_for_skill_in_window(
    audit_path: Path, skill_name: str, *,
    start_ts: float, end_ts: float,
    seed_filter: str | None = None,  # "with" | "without" | None
) -> SignalCounts:
    """Count outcome signals for ``skill_name`` in [start_ts, end_ts).

    When ``seed_filter`` is "with" or "without", only events whose
    ``run_id`` (or its hash) flips the parity bit accordingly are
    counted — used by shadow A/B evaluation.
    """
    counts = SignalCounts()
    for ev in _iter_audit_events(audit_path):
        if ev.get("event_type") != "skill.outcome_graded":
            continue
        ts = float(ev.get("ts", 0))
        if not (start_ts <= ts < end_ts):
            continue
        details = ev.get("details", {}) or {}
        if skill_name not in (details.get("skills") or []):
            continue
        run_id = details.get("prev_run_id", "")
        if seed_filter is not None and run_id:
            with_active = shadow_active_for_seed(run_id)
            if seed_filter == "with" and not with_active:
                continue
            if seed_filter == "without" and with_active:
                continue
        signal = details.get("signal")
        if signal == "rejection":
            counts.rejection += 1
        elif signal == "rephrase":
            counts.rephrase += 1
        elif signal == "approval":
            counts.approval += 1
    return counts


def evaluate_shadow(candidate: Candidate, audit_path: Path | None = None,
                    *,
                    now: float | None = None,
                    corvin_home: Path | None = None) -> str:
    """Decide whether a shadow candidate should be promoted.

    Returns one of "promote" | "reject" | "continue":
      * "continue"  — shadow window not yet over, keep observing
      * "promote"   — with-bullet cohort showed lower negative_ratio
                      by >= MIN_DELTA → flip to live
      * "reject"    — no improvement; drop with cooldown
    """
    if now is None:
        now = time.time()
    if audit_path is None:
        audit_path = _audit_path(corvin_home=corvin_home)
    elapsed = now - candidate.shadow_started_at
    if elapsed < SHADOW_DAYS * 86400:
        return "continue"

    with_counts = _signals_for_skill_in_window(
        audit_path, candidate.skill_name,
        start_ts=candidate.shadow_started_at, end_ts=now,
        seed_filter="with",
    )
    without_counts = _signals_for_skill_in_window(
        audit_path, candidate.skill_name,
        start_ts=candidate.shadow_started_at, end_ts=now,
        seed_filter="without",
    )
    # Need at least one signal in each cohort to decide.
    if with_counts.total == 0 or without_counts.total == 0:
        return "reject"
    delta = without_counts.negative_ratio - with_counts.negative_ratio
    if delta >= MIN_DELTA:
        return "promote"
    return "reject"


def promote_bullet(candidate: Candidate, *,
                   corvin_home: Path | None = None,
                   now: float | None = None) -> None:
    """Move a shadow candidate to live bullets and re-render style.md."""
    if now is None:
        now = time.time()
    with _STORE_LOCK:
        cands = load_candidates(corvin_home=corvin_home)
        cands = [c for c in cands if c.bullet_id != candidate.bullet_id]
        save_candidates(cands, corvin_home=corvin_home)

        live = load_live(corvin_home=corvin_home)
        # Hard cap: drop the lowest-scoring live bullet if needed.
        if len(live) >= HARD_CAP:
            live.sort(key=lambda c: c.live_started_at or 0.0)
            live = live[1:]
        candidate.state = "live"
        candidate.live_started_at = now
        live.append(candidate)
        save_live(live, corvin_home=corvin_home)
        _render_style_md(live, corvin_home=corvin_home)
        _audit(
            "user_style.bullet_promoted",
            corvin_home=corvin_home,
            details={
                "bullet_id":  candidate.bullet_id,
                "cluster_id": candidate.cluster_id,
                "skill_name": candidate.skill_name,
            },
        )


def reject_candidate(candidate: Candidate, *,
                     corvin_home: Path | None = None,
                     reason: str = "no-improvement",
                     now: float | None = None) -> None:
    """Drop a shadow candidate and put its cluster on cooldown."""
    if now is None:
        now = time.time()
    with _STORE_LOCK:
        cands = load_candidates(corvin_home=corvin_home)
        cands = [c for c in cands if c.bullet_id != candidate.bullet_id]
        save_candidates(cands, corvin_home=corvin_home)
        cd = load_cooldown(corvin_home=corvin_home)
        cd[candidate.cluster_id] = now + COOLDOWN_DAYS * 86400
        save_cooldown(cd, corvin_home=corvin_home)
        _audit(
            "user_style.candidate_rejected",
            corvin_home=corvin_home,
            details={
                "bullet_id":  candidate.bullet_id,
                "cluster_id": candidate.cluster_id,
                "skill_name": candidate.skill_name,
                "reason":     reason,
            },
        )


# ── Phase 7–8 — live evaluation + rollback ─────────────────────────────────

def evaluate_live_bullet(bullet: Candidate, audit_path: Path | None = None,
                         *,
                         now: float | None = None,
                         corvin_home: Path | None = None) -> str:
    """Returns "keep" | "rollback".

    Rollback when the live bullet's skill shows ``negative_ratio >
    ROLLBACK_THRESHOLD`` over the last LIVE_WINDOW_DAYS, AND the bullet
    has been live at least LIVE_WINDOW_DAYS (no premature rollback on
    insufficient data).
    """
    if now is None:
        now = time.time()
    if audit_path is None:
        audit_path = _audit_path(corvin_home=corvin_home)
    if bullet.live_started_at is None:
        return "keep"
    elapsed = now - bullet.live_started_at
    if elapsed < LIVE_WINDOW_DAYS * 86400:
        return "keep"
    window_start = now - LIVE_WINDOW_DAYS * 86400
    counts = _signals_for_skill_in_window(
        audit_path, bullet.skill_name,
        start_ts=window_start, end_ts=now,
    )
    if counts.total < MIN_HITS:
        return "keep"
    if counts.negative_ratio > ROLLBACK_THRESHOLD:
        return "rollback"
    return "keep"


def rollback_bullet(bullet: Candidate, *,
                    corvin_home: Path | None = None,
                    now: float | None = None) -> None:
    """Remove a live bullet, re-render style.md, register cooldown."""
    if now is None:
        now = time.time()
    with _STORE_LOCK:
        live = load_live(corvin_home=corvin_home)
        live = [c for c in live if c.bullet_id != bullet.bullet_id]
        save_live(live, corvin_home=corvin_home)
        _render_style_md(live, corvin_home=corvin_home)
        cd = load_cooldown(corvin_home=corvin_home)
        cd[bullet.cluster_id] = now + COOLDOWN_DAYS * 86400
        save_cooldown(cd, corvin_home=corvin_home)
        _audit(
            "user_style.bullet_rolled_back",
            corvin_home=corvin_home,
            details={
                "bullet_id":  bullet.bullet_id,
                "cluster_id": bullet.cluster_id,
                "skill_name": bullet.skill_name,
            },
        )


# ── style.md rendering (operator-readable, also ready for adapter inject) ──

def _render_style_md(live: list[Candidate], *,
                     corvin_home: Path | None = None) -> None:
    p = _style_md_path(corvin_home=corvin_home)
    if not live:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
        return
    lines = ["# Auto-learned user style", ""]
    for c in sorted(live, key=lambda x: x.live_started_at or 0.0):
        lines.append(f"- {c.bullet_text}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# ── Phase 9 — orchestration: daily sweep ───────────────────────────────────

def run_daily_sweep(*,
                    audit_path: Path | None = None,
                    corvin_home: Path | None = None,
                    judge_fn: Callable[[Cluster, str], bool] | None = None,
                    now: float | None = None) -> dict[str, Any]:
    """One sweep through the whole pipeline. Returns a summary dict."""
    if now is None:
        now = time.time()
    if audit_path is None:
        audit_path = _audit_path(corvin_home=corvin_home)

    summary: dict[str, Any] = {
        "clusters":             0,
        "candidates_proposed":  0,
        "candidates_promoted":  0,
        "candidates_rejected":  0,
        "bullets_rolled_back":  0,
    }

    # Re-evaluate live bullets first (rollback freezes hard cap).
    live = load_live(corvin_home=corvin_home)
    for b in list(live):
        verdict = evaluate_live_bullet(
            b, audit_path, now=now, corvin_home=corvin_home,
        )
        if verdict == "rollback":
            rollback_bullet(b, corvin_home=corvin_home, now=now)
            summary["bullets_rolled_back"] += 1

    # Re-evaluate pending shadow candidates.
    cands = load_candidates(corvin_home=corvin_home)
    for cand in list(cands):
        verdict = evaluate_shadow(
            cand, audit_path, now=now, corvin_home=corvin_home,
        )
        if verdict == "promote":
            promote_bullet(cand, corvin_home=corvin_home, now=now)
            summary["candidates_promoted"] += 1
        elif verdict == "reject":
            reject_candidate(cand, corvin_home=corvin_home, now=now)
            summary["candidates_rejected"] += 1

    # Aggregate fresh clusters from the chain and gate them.
    clusters = aggregate_clusters(
        audit_path, since_days=14, now=now, corvin_home=corvin_home,
    )
    summary["clusters"] = len(clusters)

    live_now = {b.cluster_id for b in load_live(corvin_home=corvin_home)}
    cands_now = {c.cluster_id for c in load_candidates(corvin_home=corvin_home)}

    for cl in clusters:
        if cl.cluster_id in live_now or cl.cluster_id in cands_now:
            continue  # already in pipeline
        if not passes_confidence_gate(cl):
            continue
        if in_cooldown(cl.cluster_id, corvin_home=corvin_home, now=now):
            continue
        draft = draft_bullet(cl)
        if not judge_overfit(cl, draft, judge_fn=judge_fn):
            _audit(
                "user_style.candidate_rejected",
                corvin_home=corvin_home,
                details={
                    "cluster_id": cl.cluster_id,
                    "skill_name": cl.skill_name,
                    "reason":     "judge-overfit",
                },
            )
            cd = load_cooldown(corvin_home=corvin_home)
            cd[cl.cluster_id] = now + COOLDOWN_DAYS * 86400
            save_cooldown(cd, corvin_home=corvin_home)
            continue
        start_shadow(cl, corvin_home=corvin_home, now=now)
        summary["candidates_proposed"] += 1

    return summary


__all__ = [
    "MIN_HITS", "NEGATIVE_THRESHOLD", "SHADOW_DAYS", "LIVE_WINDOW_DAYS",
    "MIN_DELTA", "ROLLBACK_THRESHOLD", "COOLDOWN_DAYS", "HARD_CAP",
    "SignalCounts", "Cluster", "Candidate",
    "aggregate_clusters", "passes_confidence_gate", "judge_overfit",
    "draft_bullet",
    "load_candidates", "save_candidates",
    "load_live", "save_live",
    "load_cooldown", "save_cooldown", "in_cooldown",
    "start_shadow", "shadow_pick_for_turn", "shadow_active_for_seed",
    "evaluate_shadow", "promote_bullet", "reject_candidate",
    "evaluate_live_bullet", "rollback_bullet",
    "run_daily_sweep",
]


# ── Operator CLI ───────────────────────────────────────────────────────────

def _cli_main(argv: list[str]) -> int:
    """Entry point for ``python -m user_style {sub}``.

    Subcommands:
      sweep [--no-judge]   run a single daily-sweep iteration; --no-judge
                           bypasses the CLI judge (treated as FAITHFUL)
      status               print counts of live / shadow / cooldown
      list-live            print live bullets, one per line
      list-shadow          print shadow candidates with elapsed time
      list-cooldown        print cluster_ids with remaining TTL
      reject-cluster <id>  remove a cluster from cooldown (operator override)

    All output is JSON for stable scripting (one object / array per call).
    """
    import argparse
    p = argparse.ArgumentParser(prog="user_style")
    p.add_argument("--corvin-home", default=None,
                   help="override <corvin_home> for testing")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_sweep = sub.add_parser("sweep")
    s_sweep.add_argument("--no-judge", action="store_true",
                         help="bypass the CLI judge (treat all as FAITHFUL)")

    sub.add_parser("status")
    sub.add_parser("list-live")
    sub.add_parser("list-shadow")
    sub.add_parser("list-cooldown")

    s_reset = sub.add_parser("reset-cooldown")
    s_reset.add_argument("cluster_id")

    args = p.parse_args(argv)
    home = Path(args.corvin_home) if args.corvin_home else None

    if args.cmd == "sweep":
        judge = (lambda c, d: True) if args.no_judge else None
        summary = run_daily_sweep(corvin_home=home, judge_fn=judge)
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0

    if args.cmd == "status":
        live = load_live(corvin_home=home)
        cands = load_candidates(corvin_home=home)
        cd = load_cooldown(corvin_home=home)
        now = time.time()
        active_cd = sum(1 for v in cd.values() if float(v) > now)
        print(json.dumps({
            "live":       len(live),
            "shadow":     sum(1 for c in cands if c.state == "shadow"),
            "cooldown":   active_cd,
            "hard_cap":   HARD_CAP,
        }, sort_keys=True, indent=2))
        return 0

    if args.cmd == "list-live":
        print(json.dumps([
            {
                "bullet_id":  c.bullet_id,
                "skill_name": c.skill_name,
                "live_age_h": round((time.time() - (c.live_started_at or 0)) / 3600, 1),
                "bullet":     c.bullet_text,
            }
            for c in load_live(corvin_home=home)
        ], indent=2))
        return 0

    if args.cmd == "list-shadow":
        print(json.dumps([
            {
                "bullet_id":     c.bullet_id,
                "skill_name":    c.skill_name,
                "shadow_age_h":  round(
                    (time.time() - c.shadow_started_at) / 3600, 1
                ),
                "bullet":        c.bullet_text,
            }
            for c in load_candidates(corvin_home=home)
            if c.state == "shadow"
        ], indent=2))
        return 0

    if args.cmd == "list-cooldown":
        cd = load_cooldown(corvin_home=home)
        now = time.time()
        out = []
        for cluster_id, until in sorted(cd.items()):
            remaining_h = (float(until) - now) / 3600
            if remaining_h <= 0:
                continue
            out.append({"cluster_id": cluster_id,
                        "ttl_h": round(remaining_h, 1)})
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd == "reset-cooldown":
        cd = load_cooldown(corvin_home=home)
        if args.cluster_id not in cd:
            print(json.dumps({"ok": False,
                              "error": "cluster not in cooldown"}))
            return 1
        cd.pop(args.cluster_id)
        save_cooldown(cd, corvin_home=home)
        print(json.dumps({"ok": True, "cluster_id": args.cluster_id}))
        return 0

    print(json.dumps({"ok": False, "error": f"unknown command: {args.cmd!r}"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli_main(sys.argv[1:]))
