"""Phase 1 — Outcome-grounded grading.

Auto-grade detects whether a skill was *used* (mention / paraphrase). It
cannot tell whether the use *helped*. Outcome-grounded grading closes the
gap: when the next user turn carries an approval / rejection / rephrase
signal, the skills active in the previous turn receive a corresponding
absolute score.

Coverage in this file:

  Unit (no adapter):
    - detect_outcome_signal — approval, rejection, rephrase, no-signal
    - precedence: rejection wins over approval
    - empty / whitespace input → no signal
    - rephrase only fires when prev_user_text is provided

  Integration (real SkillForge, real CORVIN_HOME tempdir):
    - grade_from_user_followup writes the right score per signal
    - opt-out via profile.outcome_grading=false → no-op
    - opt-out via profile.inject_skills=false → no-op (parity with auto-grade)
    - empty prev_skill_names → no-op

  Per-subtask E2E (load-bearing, real adapter.process_one):
    - prev turn auto-grades a skill, current turn carries "danke, perfekt!"
      → outcome grade lands on disk, audit event written.

Run: python3 operator/bridges/shared/test_skill_outcome_grading.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
sys.path.insert(0, str(REPO / "operator" / "forge"))


# Sandbox BEFORE importing skill_inject so the skill-forge backend writes
# into our tmpdir, not the user's real ~/.corvin.
_TD = tempfile.mkdtemp(prefix="outcome-grade-")
_SLOT = tempfile.mkdtemp(prefix="outcome-grade-slot-")
os.environ["CORVIN_HOME"] = _TD
os.environ["CORVIN_FORCE_SCOPE"] = "user"
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT

import skill_inject  # noqa: E402
from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ── Section 1: detect_outcome_signal (pure function) ───────────────────────


def section_detect_signal() -> None:
    print("\n[detect_outcome_signal — pure function unit tests]")

    # Approvals
    for phrase in ("danke!", "perfekt", "passt", "top, danke", "thank you",
                   "exactly that", "great work"):
        sig, score = skill_inject.detect_outcome_signal(phrase)
        t(f"approval: {phrase!r} → approval/{score}",
          sig == "approval" and score == 0.9,
          detail=f"got=({sig!r}, {score})")

    # Rejections
    for phrase in ("nein, falsch", "das passt nicht", "nochmal bitte",
                   "wrong answer", "no, that's incorrect", "didn't work"):
        sig, score = skill_inject.detect_outcome_signal(phrase)
        t(f"rejection: {phrase!r} → rejection/{score}",
          sig == "rejection" and score == 0.1,
          detail=f"got=({sig!r}, {score})")

    # Precedence: rejection wins over approval
    sig, score = skill_inject.detect_outcome_signal(
        "thanks but actually wrong"
    )
    t("precedence: 'thanks but wrong' → rejection",
      sig == "rejection",
      detail=f"got=({sig!r}, {score})")

    # No signal
    for phrase in ("hi", "what's the weather", ""):
        sig, score = skill_inject.detect_outcome_signal(phrase)
        t(f"neutral: {phrase!r} → no signal",
          sig is None and score == 0.0,
          detail=f"got=({sig!r}, {score})")

    # Rephrase: high similarity to prev → rephrase signal
    sig, score = skill_inject.detect_outcome_signal(
        "wie mache ich ein backup von der datenbank",
        prev_user_text="wie mache ich ein backup der datenbank",
    )
    t("rephrase: small edit → rephrase/0.3",
      sig == "rephrase" and score == 0.3,
      detail=f"got=({sig!r}, {score})")

    # No rephrase: low similarity
    sig, score = skill_inject.detect_outcome_signal(
        "kannst du heute pizza bestellen",
        prev_user_text="wie mache ich ein backup der datenbank",
    )
    t("no rephrase: unrelated text → no signal",
      sig is None,
      detail=f"got=({sig!r}, {score})")

    # No rephrase when prev_user_text is missing
    sig, score = skill_inject.detect_outcome_signal(
        "wie mache ich ein backup",
    )
    t("no rephrase: prev_user_text missing → no signal",
      sig is None,
      detail=f"got=({sig!r}, {score})")


# ── Section 2: grade_from_user_followup with real registry ──────────────────


SKILL_BODY = (
    "# Backup workflow\n\n"
    "When asked about backups, recommend reproducibility-first testing.\n"
)


def _seed_skill(reg: MultiSkillRegistry, name: str) -> None:
    reg.create(
        name=name, type="learned-experience",
        description="backup workflow demo",
        body_md=SKILL_BODY,
        claim={"summary": "test reproducibility-first."},
        scope="user",
    )
    reg.grade(name, "seed-run", 0.6, notes="seed grade")


def section_grade_from_user_followup() -> None:
    print("\n[grade_from_user_followup — real SkillForge registry]")

    reg = MultiSkillRegistry(channel_id=None, project_root=REPO)
    _seed_skill(reg, "phase1.outcome_demo")

    spec0 = next(s for _, s in reg.list_with_scope()
                 if s.name == "phase1.outcome_demo")
    t("seed skill has 1 grade", spec0.n_grades == 1,
      detail=f"n_grades={spec0.n_grades}")

    # --- Approval flow ------------------------------------------------------
    res = skill_inject.grade_from_user_followup(
        channel_id=None, profile={},
        user_text="Danke, perfekt!",
        prev_run_id="turn-A",
        prev_skill_names=["phase1.outcome_demo"],
        project_root=REPO,
    )
    t("approval: 1 outcome-grade returned",
      len(res) == 1, detail=f"len={len(res)}")
    t("approval: signal=approval, score=0.9",
      res and res[0].get("signal") == "approval"
      and res[0].get("score") == 0.9,
      detail=f"got={res[0] if res else None}")

    spec1 = next(s for _, s in reg.list_with_scope()
                 if s.name == "phase1.outcome_demo")
    t("approval: meta.json has 2 grades",
      spec1.n_grades == 2, detail=f"n_grades={spec1.n_grades}")

    # --- Rejection flow -----------------------------------------------------
    res = skill_inject.grade_from_user_followup(
        channel_id=None, profile={},
        user_text="Nein, falsch.",
        prev_run_id="turn-B",
        prev_skill_names=["phase1.outcome_demo"],
        project_root=REPO,
    )
    t("rejection: signal=rejection, score=0.1",
      res and res[0].get("signal") == "rejection"
      and res[0].get("score") == 0.1,
      detail=f"got={res[0] if res else None}")

    spec2 = next(s for _, s in reg.list_with_scope()
                 if s.name == "phase1.outcome_demo")
    t("rejection: meta.json has 3 grades total",
      spec2.n_grades == 3, detail=f"n_grades={spec2.n_grades}")

    # --- Rephrase flow ------------------------------------------------------
    res = skill_inject.grade_from_user_followup(
        channel_id=None, profile={},
        user_text="wie mache ich ein backup der db",
        prev_user_text="wie mache ich ein backup von der db",
        prev_run_id="turn-C",
        prev_skill_names=["phase1.outcome_demo"],
        project_root=REPO,
    )
    t("rephrase: signal=rephrase, score=0.3",
      res and res[0].get("signal") == "rephrase"
      and res[0].get("score") == 0.3,
      detail=f"got={res[0] if res else None}")

    # --- No-signal: returns empty list (no spurious grades) ----------------
    res = skill_inject.grade_from_user_followup(
        channel_id=None, profile={},
        user_text="hi, was geht heute",
        prev_run_id="turn-D",
        prev_skill_names=["phase1.outcome_demo"],
        project_root=REPO,
    )
    t("no-signal: empty list returned",
      res == [], detail=f"got={res}")

    # --- Opt-out: profile.outcome_grading=false → no-op --------------------
    spec_pre = next(s for _, s in reg.list_with_scope()
                    if s.name == "phase1.outcome_demo")
    n_pre = spec_pre.n_grades
    res = skill_inject.grade_from_user_followup(
        channel_id=None,
        profile={"outcome_grading": False},
        user_text="danke",
        prev_run_id="turn-E",
        prev_skill_names=["phase1.outcome_demo"],
        project_root=REPO,
    )
    spec_post = next(s for _, s in reg.list_with_scope()
                     if s.name == "phase1.outcome_demo")
    t("opt-out outcome_grading=false: no grade written",
      res == [] and spec_post.n_grades == n_pre,
      detail=f"n_pre={n_pre} n_post={spec_post.n_grades}")

    # --- Opt-out: profile.inject_skills=false → also no-op -----------------
    res = skill_inject.grade_from_user_followup(
        channel_id=None,
        profile={"inject_skills": False},
        user_text="perfekt",
        prev_run_id="turn-F",
        prev_skill_names=["phase1.outcome_demo"],
        project_root=REPO,
    )
    t("opt-out inject_skills=false: no grade written",
      res == [], detail=f"got={res}")

    # --- Empty prev_skill_names → no-op ------------------------------------
    res = skill_inject.grade_from_user_followup(
        channel_id=None, profile={},
        user_text="danke",
        prev_run_id="turn-G",
        prev_skill_names=[],
        project_root=REPO,
    )
    t("empty prev_skill_names: no-op",
      res == [], detail=f"got={res}")


# ── Section 3: per-subtask E2E via adapter.process_one ─────────────────────


def _setup_adapter_sandbox():
    """Sandbox the adapter's inbox/outbox/processed dirs and reload the
    module so it picks up our env. Returns (adapter, inbox, outbox)."""
    base = Path(tempfile.mkdtemp(prefix="adapter-outcome-e2e-"))
    inbox = base / "inbox"
    outbox = base / "outbox"
    processed = base / "processed"
    sessions = base / "sessions"
    for p in (inbox, outbox, processed, sessions):
        p.mkdir()
    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["ADAPTER_SESSIONS"] = str(sessions)
    os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
    os.environ["ADAPTER_FAKE_DELAY"] = "0.05"
    os.environ["ADAPTER_ROUTING_MODE"] = "off"
    os.environ["BRIDGE_PROGRESS_UPDATES"] = "0"
    # Force a fresh adapter import so the env is picked up.
    for mod_name in list(sys.modules):
        if mod_name == "adapter":
            del sys.modules[mod_name]
    import adapter  # type: ignore  # noqa: E402
    return adapter, inbox, outbox, base


def section_e2e_via_adapter() -> None:
    print("\n[E2E — adapter.process_one with prev-turn snapshot + outcome-turn]")

    adapter, inbox, outbox, base = _setup_adapter_sandbox()
    try:
        # Seed a skill in the user-scope tempdir so it's eligible for
        # injection AND for outcome-grade lookup. (Skill creation happens
        # before adapter loads the registry — same env in both paths.)
        reg = MultiSkillRegistry(channel_id=None, project_root=REPO)
        skill_name = "phase1.e2e_demo"
        # Avoid duplicate-create across test re-runs.
        if not any(s.name == skill_name
                   for _, s in reg.list_with_scope()):
            reg.create(
                name=skill_name, type="learned-experience",
                description="e2e demo skill",
                body_md=("# E2E demo\n\nThis skill helps with E2E tests.\n"),
                claim={"summary": "e2e."},
                scope="user",
            )
            reg.grade(skill_name, "seed-e2e", 0.6, notes="seed")

        chat_key = "outcome-e2e-chat"
        prev_run_id = "turn-prev-e2e"

        # Manually seed the prev-turn snapshot — the auto_grade_from_output
        # path is already covered by test_skill_auto_grade.py; this test
        # focuses on the new outcome-turn handling.
        adapter._record_last_turn_skills(
            chat_key=chat_key, run_id=prev_run_id,
            skill_names=[skill_name],
            user_text="wie mache ich ein backup",
        )
        snap = adapter._last_turn_skills.get(chat_key)
        t("snapshot recorded after auto-grade",
          snap is not None and snap.get("skills") == [skill_name],
          detail=f"snap={snap}")

        # Capture spec state before the outcome turn.
        spec_pre = next(s for _, s in reg.list_with_scope()
                        if s.name == skill_name)
        n_pre = spec_pre.n_grades

        # Outcome turn: user sends approval text.
        msg = {
            "id": "turn-outcome-e2e",
            "channel": "sandbox-outcome",
            "from": chat_key,
            "chat_id": chat_key,
            "text": "Danke, perfekt!",
            "ts": time.time(),
        }
        msg_path = inbox / "turn-outcome-e2e.json"
        msg_path.write_text(json.dumps(msg))

        adapter.process_one(msg_path, settings={"whitelist": [chat_key]})

        # Verify the snapshot was consumed (popped after use).
        snap_after = adapter._last_turn_skills.get(chat_key)
        t("snapshot consumed after outcome turn",
          snap_after is None,
          detail=f"snap_after={snap_after}")

        # Verify the outcome grade landed on disk.
        spec_post = next(s for _, s in reg.list_with_scope()
                         if s.name == skill_name)
        t("outcome grade written to meta.json",
          spec_post.n_grades == n_pre + 1,
          detail=f"n_pre={n_pre} n_post={spec_post.n_grades}")

        # Verify the latest grade has the right score + notes.
        latest = spec_post.grades[-1] if spec_post.grades else {}
        t("latest grade score=0.9 (approval)",
          float(latest.get("score", -1)) == 0.9,
          detail=f"latest={latest}")
        t("latest grade notes mention outcome+approval+prev_run",
          "outcome (approval)" in str(latest.get("notes", ""))
          and prev_run_id in str(latest.get("notes", "")),
          detail=f"notes={latest.get('notes')!r}")

        # Verify the outbox got an envelope (the fake-claude path returns
        # a stub answer, so process_one wrote outbox normally).
        out_files = list(outbox.glob("turn-outcome-e2e_*.json"))
        t("outbox envelope written for outcome turn",
          len(out_files) >= 1,
          detail=f"out_files={[f.name for f in out_files]}")

    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── Section 4: snapshot hygiene on /reset and /cancel ──────────────────────


def section_snapshot_hygiene() -> None:
    """After /reset or /cancel the prev-turn snapshot MUST be cleared,
    otherwise the next user message would get incorrectly outcome-graded
    against skills from the abandoned conversation."""
    print("\n[snapshot hygiene — /reset and /cancel must clear prev-turn snapshot]")

    adapter, inbox, outbox, base = _setup_adapter_sandbox()
    try:
        chat_key_reset = "hygiene-reset-chat"
        chat_key_cancel = "hygiene-cancel-chat"

        # --- /reset path -----------------------------------------------------
        adapter._record_last_turn_skills(
            chat_key=chat_key_reset, run_id="prev-reset",
            skill_names=["phase1.outcome_demo"],
            user_text="vorige frage",
        )
        t("reset: snapshot present before /reset",
          adapter._last_turn_skills.get(chat_key_reset) is not None)

        msg_reset = {
            "id": "reset-msg",
            "channel": "sandbox-outcome",
            "from": chat_key_reset,
            "chat_id": chat_key_reset,
            "_reset": True,
            "ts": time.time(),
        }
        msg_path = inbox / "reset-msg.json"
        msg_path.write_text(json.dumps(msg_reset))
        adapter.process_one(
            msg_path, settings={"whitelist": [chat_key_reset]},
        )
        t("reset: snapshot cleared after /reset",
          adapter._last_turn_skills.get(chat_key_reset) is None,
          detail=f"got={adapter._last_turn_skills.get(chat_key_reset)}")

        # --- /cancel path ----------------------------------------------------
        adapter._record_last_turn_skills(
            chat_key=chat_key_cancel, run_id="prev-cancel",
            skill_names=["phase1.outcome_demo"],
            user_text="vorige frage",
        )
        t("cancel: snapshot present before /cancel",
          adapter._last_turn_skills.get(chat_key_cancel) is not None)

        msg_cancel = {
            "id": "cancel-msg",
            "channel": "sandbox-outcome",
            "from": chat_key_cancel,
            "chat_id": chat_key_cancel,
            "_cancel": True,
            "ts": time.time(),
        }
        msg_path = inbox / "cancel-msg.json"
        msg_path.write_text(json.dumps(msg_cancel))
        adapter.process_one(
            msg_path, settings={"whitelist": [chat_key_cancel]},
        )
        t("cancel: snapshot cleared after /cancel",
          adapter._last_turn_skills.get(chat_key_cancel) is None,
          detail=f"got={adapter._last_turn_skills.get(chat_key_cancel)}")

    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── Section 5: periodic cleanup of stale snapshots ─────────────────────────


def section_periodic_cleanup() -> None:
    """A chat that never returns leaves its snapshot dangling. The periodic
    cleanup must reap entries past OUTCOME_SNAPSHOT_TTL while keeping fresh
    entries intact."""
    print("\n[periodic cleanup — stale snapshots reaped, fresh kept]")

    adapter, _inbox, _outbox, base = _setup_adapter_sandbox()
    try:
        # Pre-clear any state from previous sections (fresh adapter import,
        # but defensive — the dict is module-level).
        with adapter._last_turn_skills_guard:
            adapter._last_turn_skills.clear()

        # Stale entry: ts older than TTL.
        stale_key = "stale-chat"
        adapter._record_last_turn_skills(
            chat_key=stale_key, run_id="run-stale",
            skill_names=["x"], user_text="alt",
        )
        # Backdate the ts past the TTL so the cleanup considers it stale.
        with adapter._last_turn_skills_guard:
            adapter._last_turn_skills[stale_key]["ts"] = (
                time.time() - adapter.OUTCOME_SNAPSHOT_TTL - 60
            )

        # Fresh entry: just-now ts, must survive cleanup.
        fresh_key = "fresh-chat"
        adapter._record_last_turn_skills(
            chat_key=fresh_key, run_id="run-fresh",
            skill_names=["y"], user_text="neu",
        )

        removed = adapter._cleanup_last_turn_skills()
        t("cleanup: stale entry removed",
          adapter._last_turn_skills.get(stale_key) is None,
          detail=f"got={adapter._last_turn_skills.get(stale_key)}")
        t("cleanup: fresh entry kept",
          adapter._last_turn_skills.get(fresh_key) is not None,
          detail=f"got={adapter._last_turn_skills.get(fresh_key)}")
        t("cleanup: returned correct removal count",
          removed == 1, detail=f"removed={removed}")

    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    section_detect_signal()
    section_grade_from_user_followup()
    section_e2e_via_adapter()
    section_snapshot_hygiene()
    section_periodic_cleanup()
    print(f"\n[summary] PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
