"""ACO Layer 6 — Self-Improving Maintenance Loop (ADR-0178, Tier CONTRIBUTOR).

Turns an ACO L4 diagnosis into a CODE change on the repo — but ONLY for an instance
that holds a valid signed ``maintainer.commit`` capability, and ONLY through a full
gate chain. Deny-by-default at every step:

    diagnosis ─▶ [capability gate] ─▶ patch_source (injected) ─▶ [l6 gate chain]
              ─▶ branch + commit (tagged aco-l6) ─▶ route:
                   low-risk + fully-green + no-ack  ─▶ ff-merge main (opt-in)
                   else                              ─▶ PR-ready (human one-tap)

Hard rules enforced here in code:
  * No capability  → refuse (status="denied").  [the trust boundary]
  * No green tests → refuse (deny-by-default; a missing gate_runner == red).
  * Touch LICENSE/NOTICE/CLA*/audit.jsonl/policy.json/*.key → hard block.
  * Touch a compliance/security/protocol path or hit an ADR trigger → requires_ack
    (never auto-merged), regardless of class.
  * Direct-to-main and remote push are BOTH opt-in (default off) — the safe default
    leaves a branch + a PR-ready outcome for the maintainer to merge.

The PATCH GENERATOR is intentionally an injected callable (``patch_source``): the
deterministic safety machinery lives here; the judgement of *what to change* stays
with the maintainer/agent. With no patch_source, the loop escalates (PR-draft of
the diagnosis), never invents code.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import maintainer_capability as _cap

logger = logging.getLogger(__name__)

# Paths that must NEVER be auto-modified (hard block — not even with ack here;
# these are operator-only by CLAUDE.md red-lines).
_HARD_BLOCK = ("LICENSE", "NOTICE", "CLA.md", "CLA-SIGNATORIES.md", "CCLA.md",
               "CONTRIBUTING.md", "audit.jsonl", "policy.json")
_HARD_BLOCK_SUFFIX = (".key", ".pem")
# Path fragments that force human ack (compliance/security/protocol surfaces).
# SH-6: the ACO self-improvement modules are themselves the gate — a patch that
# edits them could weaken its own guard-rails, so they can NEVER auto-merge on
# tests-green alone; touching any of them forces a human ack (and, being non-low-
# risk-classed edits, they are never direct-mained either).
_ACK_FRAGMENTS = ("disclosure", "consent", "house_rules", "house-rules", "audit",
                  "egress", "license", "security_events", "path_gate", "a2a",
                  "protocol", "compliance", "erasure",
                  # ACO self-improvement code (aco/…) + bare basenames:
                  "aco/maintainer_capability", "aco/maintenance_loop",
                  "aco/reproduction", "aco/patch_generator", "aco/repair_actions",
                  "aco/maintainer_cli", "aco/diagnosis_synth",
                  "maintainer_capability", "maintenance_loop", "reproduction",
                  "patch_generator", "repair_actions", "maintainer_cli",
                  "diagnosis_synth")
# Low-risk classes eligible for direct-to-main (still need a fully-green gate).
_LOW_RISK_CLASSES = frozenset({"platform_path", "null_guard", "typo", "test_only",
                               "docstring", "log_message"})


@dataclass
class PatchEdit:
    path: str            # repo-relative
    new_content: str


@dataclass
class Patch:
    diagnosis_id: str
    summary: str
    risk_class: str
    edits: list[PatchEdit] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [e.path for e in self.edits]


@dataclass
class GateResult:
    passed: bool
    requires_ack: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class LoopResult:
    status: str                       # denied|no_patch|gate_blocked|gate_failed|
                                      # committed|merged|pushed|pr_ready
    detail: str = ""
    branch: str = ""
    commit: str = ""
    requires_ack: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)


# ── git helpers (all scoped to repo_dir) ──────────────────────────────────────

def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _within_repo(repo: Path, rel: str) -> Path:
    repo_r = repo.resolve()
    t = (repo_r / rel).resolve()
    if t != repo_r and repo_r not in t.parents:
        raise ValueError(f"patch path escapes repo: {rel}")
    return t


# ── gate chain ────────────────────────────────────────────────────────────────

def l6_gate(patch: Patch, *, gate_runner: Optional[Callable[[], tuple[bool, str]]]) -> GateResult:
    """Run all gates on a proposed patch. Deny-by-default: tests must pass, no
    hard-blocked path, ack required for sensitive surfaces."""
    reasons: list[str] = []
    requires_ack = False

    # 1) hard-blocked paths → fail outright (never auto-touch these). Matched
    # CASE-INSENSITIVELY: macOS/Windows filesystems are case-insensitive, so
    # `policy.JSON` / `secret.KEY` / `Notice` resolve to the real protected file
    # and MUST be blocked too (security review 2026-06-29).
    _hard = {h.casefold() for h in _HARD_BLOCK}
    _hsuf = tuple(s.casefold() for s in _HARD_BLOCK_SUFFIX)
    for p in patch.paths:
        base = p.replace("\\", "/").split("/")[-1].casefold()
        if base in _hard or base.endswith(_hsuf):
            reasons.append(f"hard-blocked path: {p}")
            return GateResult(False, requires_ack=False, reasons=reasons)

    # 2) compliance/security/protocol surfaces → require ack (never auto-merge).
    for p in patch.paths:
        low = p.lower()
        if any(frag in low for frag in _ACK_FRAGMENTS):
            requires_ack = True
            reasons.append(f"sensitive surface (ack required): {p}")
            break

    # 3) non-low-risk class → require ack.
    if patch.risk_class not in _LOW_RISK_CLASSES:
        requires_ack = True
        reasons.append(f"class '{patch.risk_class}' not in low-risk allowlist (ack required)")

    # 4) tests — deny-by-default: a missing runner == red.
    if gate_runner is None:
        reasons.append("no gate_runner → tests not green → blocked")
        return GateResult(False, requires_ack=requires_ack, reasons=reasons)
    try:
        ok, out = gate_runner()
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"gate_runner raised: {exc}")
        return GateResult(False, requires_ack=requires_ack, reasons=reasons)
    if not ok:
        reasons.append(f"tests red: {out[:200]}")
        return GateResult(False, requires_ack=requires_ack, reasons=reasons)

    reasons.append("tests green")
    return GateResult(True, requires_ack=requires_ack, reasons=reasons)


# ── the loop ──────────────────────────────────────────────────────────────────

def run_maintenance_loop(
    *,
    diagnosis: dict[str, Any],
    repo_dir: str | Path,
    patch_source: Optional[Callable[[dict], Optional[Patch]]] = None,
    capability_token: Optional[str] = None,
    public_key_bytes: Optional[bytes] = None,
    gate_runner: Optional[Callable[[], tuple[bool, str]]] = None,
    repro_runner: Optional[Callable[[Patch], Any]] = None,
    enable_direct_main: bool = False,
    enable_push: bool = False,
    now: Optional[int] = None,
) -> LoopResult:
    """Run one L6 iteration for a diagnosis. Returns a LoopResult; NEVER raises."""
    repo = Path(repo_dir)
    diag_id = str(diagnosis.get("id") or diagnosis.get("diagnosis_id") or "diag")
    tele: dict[str, Any] = {"diagnosis_id": diag_id, "ts": int(now or time.time())}

    # 0) SH-2 fail-closed: direct-to-main is ONLY ever permitted behind a
    # reproduction proof (ADR-0179 / CLAUDE.md red-line: "don't commit an auto-fix
    # that didn't pass the red→green reproduction gate"). A caller that enables
    # direct-main without wiring a repro_runner is a misconfiguration — never
    # silently merge/push an unproven fix. Downgrade to the safe default (branch +
    # PR awaiting human ack) rather than reaching main.
    if enable_direct_main and repro_runner is None:
        enable_direct_main = False
        enable_push = False
        tele["direct_main_refused"] = "no_repro_runner"

    # 1) capability gate — the trust boundary. Deny-by-default.
    verdict = _cap.is_contributor(capability_token, now=now) if public_key_bytes is None else \
        _cap.verify(capability_token, instance_id=_cap.current_instance_id(),
                    public_key_bytes=public_key_bytes, now=now)
    tele["capability"] = verdict.reason
    if not verdict.allowed:
        return LoopResult("denied", f"not a contributor: {verdict.reason}", telemetry=tele)

    # 2) patch source (injected). No source → escalate, never invent code.
    if patch_source is None:
        return LoopResult("no_patch", "no patch_source — escalate to human PR-draft",
                         telemetry=tele)
    try:
        patch = patch_source(diagnosis)
    except Exception as exc:  # noqa: BLE001
        return LoopResult("no_patch", f"patch_source raised: {exc}", telemetry=tele)
    if patch is None or not patch.edits:
        return LoopResult("no_patch", "patch_source returned nothing", telemetry=tele)

    # 3) gate chain (BEFORE writing anything that could reach main).
    gate = l6_gate(patch, gate_runner=gate_runner)
    tele["gate_passed"] = gate.passed
    tele["requires_ack"] = gate.requires_ack
    if not gate.passed:
        return LoopResult("gate_failed", "; ".join(gate.reasons),
                         requires_ack=gate.requires_ack, gate_reasons=gate.reasons, telemetry=tele)

    # 3.5) reproduction proof gate (ADR-0179) — the hard guarantee that code only
    # ever changes when the bug is PROVEN and the fix WORKS. Deny-by-default:
    #   * a runner present → the patch must show red→green→suite-green or we refuse.
    #   * a diagnosis that demands a proof but has no runner wired → refuse (never
    #     commit an unproven fix just because the proof harness is missing).
    if repro_runner is not None:
        try:
            repro = repro_runner(patch)
        except Exception as exc:  # noqa: BLE001
            return LoopResult("repro_failed", f"reproduction gate raised: {exc}",
                             requires_ack=gate.requires_ack, gate_reasons=gate.reasons,
                             telemetry=tele)
        tele["repro"] = repro.to_dict() if hasattr(repro, "to_dict") else {"proven": bool(repro)}
        if not getattr(repro, "proven", False):
            return LoopResult("repro_failed", getattr(repro, "detail", "not proven"),
                             requires_ack=gate.requires_ack, gate_reasons=gate.reasons,
                             telemetry=tele)
    elif diagnosis.get("requires_repro_test"):
        return LoopResult("repro_failed",
                         "diagnosis requires a reproduction proof but no repro_runner "
                         "was wired — refusing to commit an unproven fix",
                         requires_ack=gate.requires_ack, gate_reasons=gate.reasons, telemetry=tele)

    # 4) write edits + branch + commit (scoped to repo, never -A).
    branch = f"aco/l6/{diag_id}"

    # 4a) Refuse to operate on a dirty worktree — otherwise a stray uncommitted
    # change could be swept into the commit or block the branch switch and leave
    # the commit on the current branch (possibly main). (security review MED.)
    rc, porcelain = _git(repo, "status", "--porcelain")
    if rc != 0 or porcelain.strip():
        return LoopResult("gate_blocked", "worktree not clean — refusing to operate",
                         gate_reasons=gate.reasons, telemetry=tele)

    # 4b) Validate ALL edit paths BEFORE writing any (no partial writes on a later
    # path-escape), and resolve them once. (security review MED.)
    try:
        targets = [(_within_repo(repo, e.path), e) for e in patch.edits]
    except Exception as exc:  # noqa: BLE001
        return LoopResult("gate_blocked", f"patch path refused: {exc}",
                         gate_reasons=gate.reasons, telemetry=tele)

    # 4c) Create + SWITCH to the branch, then ASSERT HEAD actually moved there —
    # never commit on a branch we didn't intend (e.g. main). (security review MED.)
    rc, _ = _git(repo, "checkout", "-b", branch)
    if rc != 0:
        _git(repo, "checkout", branch)  # branch may already exist
    rc, cur = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or cur.strip() != branch:
        return LoopResult("gate_blocked",
                         f"could not switch to {branch} (HEAD={cur.strip()!r}) — aborting",
                         gate_reasons=gate.reasons, telemetry=tele)
    try:
        for t, e in targets:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(e.new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _git(repo, "checkout", "--", ".")  # roll back any partial writes
        return LoopResult("gate_blocked", f"patch write failed: {exc}",
                         branch=branch, gate_reasons=gate.reasons, telemetry=tele)
    _git(repo, "add", "--", *patch.paths)
    msg = (f"fix(aco-l6): {patch.summary}\n\n"
           f"Autonomous L6 maintenance fix for diagnosis {diag_id} "
           f"(class={patch.risk_class}). Gates: {'; '.join(gate.reasons)}.\n")
    rc, out = _git(repo, "commit", "-m", msg)
    if rc != 0:
        return LoopResult("gate_failed", f"commit failed: {out[:200]}",
                         branch=branch, gate_reasons=gate.reasons, telemetry=tele)
    rc, commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", f"aco-l6-{diag_id}")
    tele["commit"] = commit

    # 5) routing. requires_ack OR not-low-risk OR direct-main disabled → PR-ready.
    eligible_direct = (enable_direct_main and not gate.requires_ack
                       and patch.risk_class in _LOW_RISK_CLASSES)
    if not eligible_direct:
        return LoopResult("pr_ready",
                         "branch committed; PR awaiting maintainer ack"
                         + (" (ack required)" if gate.requires_ack else ""),
                         branch=branch, commit=commit, requires_ack=gate.requires_ack,
                         gate_reasons=gate.reasons, telemetry=tele)

    # direct-to-main: ff-merge locally (still no network unless enable_push).
    _git(repo, "checkout", "main")
    rc, out = _git(repo, "merge", "--ff-only", branch)
    if rc != 0:
        _git(repo, "checkout", branch)
        return LoopResult("pr_ready", f"ff-merge declined ({out[:120]}) → PR",
                         branch=branch, commit=commit, gate_reasons=gate.reasons, telemetry=tele)
    if not enable_push:
        return LoopResult("merged", "ff-merged into local main (push disabled)",
                         branch=branch, commit=commit, gate_reasons=gate.reasons, telemetry=tele)
    rc, out = _git(repo, "push", "origin", "main")
    status = "pushed" if rc == 0 else "merged"
    return LoopResult(status, f"push rc={rc}: {out[:120]}",
                     branch=branch, commit=commit, gate_reasons=gate.reasons, telemetry=tele)


# ── M6: convergence tracker ───────────────────────────────────────────────────

@dataclass
class ConvergenceTracker:
    """LDD-style: stop after K_MAX attempts per diagnosis; escalate on non-convergence
    instead of grinding. Tracks reopened-anomaly + regression signals."""
    k_max: int = 5
    attempts: dict[str, int] = field(default_factory=dict)

    def should_attempt(self, diagnosis_id: str) -> bool:
        return self.attempts.get(diagnosis_id, 0) < self.k_max

    def record(self, diagnosis_id: str) -> int:
        self.attempts[diagnosis_id] = self.attempts.get(diagnosis_id, 0) + 1
        return self.attempts[diagnosis_id]

    def exhausted(self, diagnosis_id: str) -> bool:
        return self.attempts.get(diagnosis_id, 0) >= self.k_max
