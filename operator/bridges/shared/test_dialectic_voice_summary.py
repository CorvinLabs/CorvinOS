#!/usr/bin/env python3
"""test_dialectic_voice_summary.py — Layer 11 voice_summary site E2E.

Covers:

  L18/1  judge_summary(): mode=off → no-op, candidate ships unchanged
  L18/2  judge_summary(): cli + FAITHFUL verdict → candidate ships, audit
  L18/3  judge_summary(): cli + CORRECTED verdict → revised ships, audit
  L18/4  judge_summary(): cli + unparseable output → defaults to faithful
  L18/5  judge_summary(): cli timeout → defaults to faithful (ship candidate)
  L18/6  summarize.summarize() → judge integration (mode=off, no spawn)
  L18/7  summarize.summarize() → judge integration (mode=cli + CORRECTED)

The CLI subprocess is mocked via unittest.mock so the test never spawns a
real `claude -p` call. Audit chain hits the unified hash chain at
<corvin_home>/global/forge/audit.jsonl, which the test reads back to
verify the `decision.dialectical` event lands with the right site +
verdict.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# summarize.py lives at operator/voice/scripts/summarize.py — relative to
# this test file: ../../voice/scripts.
SCRIPTS = (ROOT.parent.parent / "voice" / "scripts").resolve()
sys.path.insert(0, str(SCRIPTS))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _set_sandbox(tmp: Path) -> None:
    os.environ["CORVIN_HOME"] = str(tmp / "corvin")
    (tmp / "corvin" / "global" / "forge").mkdir(parents=True, exist_ok=True)


def _fresh_dialectic():
    sys.modules.pop("dialectic", None)
    import dialectic  # type: ignore
    return dialectic


def _fresh_summarize():
    for mod in ("summarize", "dialectic"):
        sys.modules.pop(mod, None)
    import summarize  # type: ignore
    return summarize


def _audit_path(tmp: Path) -> Path:
    return tmp / "corvin" / "global" / "forge" / "audit.jsonl"


def _read_audit_events(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _set_site_mode(dialectic, site: str, mode: str) -> None:
    """Persist a site mode override into <corvin_home>/global/dialectic.json
    so that the next resolve_mode() call picks it up via mtime hot-reload.

    Also enables the dialectical_reasoning LDD layer (Layer-14 gate) so
    the config-level mode is not silently overridden when LDD defaults off.
    """
    cfg = dialectic.load_config()
    cfg.setdefault("modes", {})[site] = mode
    cfg["enabled"] = True
    dialectic.save_config(cfg)
    # Enable the dialectical_reasoning LDD layer so the Layer-14 gate
    # does not block the explicit mode set above.  Uses the same
    # CORVIN_HOME sandbox already wired by _set_sandbox().
    sys.modules.pop("ldd", None)
    import ldd as _ldd  # type: ignore  # noqa: PLC0415
    ldd_cfg = _ldd.load_config()
    ldd_cfg["enabled"] = True
    ldd_cfg.setdefault("layers", {})["dialectical_reasoning"] = True
    _ldd.save_config(ldd_cfg)
    sys.modules.pop("ldd", None)


# ─────────────────────────────────────────────────────────────────
# L18/1 — mode=off → no-op
# ─────────────────────────────────────────────────────────────────

def case_judge_off_returns_candidate() -> None:
    _section("L18/1: mode=off → judge is a no-op")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l1-"))
    try:
        _set_sandbox(tmp)
        dialectic = _fresh_dialectic()

        # Hard guarantee: subprocess.run must NOT be called when mode=off.
        with mock.patch("dialectic.subprocess.run") as fake_run:
            final, verdict, why = dialectic.judge_summary(
                source="some long source text",
                candidate="short summary",
                lang="de",
            )
        assert final == "short summary", final
        assert verdict == "skipped", verdict
        assert fake_run.call_count == 0, "subprocess must not run when mode=off"

        events = _read_audit_events(_audit_path(tmp))
        assert not [e for e in events
                    if e.get("event_type") == "decision.dialectical"
                    and e.get("details", {}).get("site") == "voice_summary"], \
            "no audit event for skipped judge"
        print("  pass — mode=off: candidate unchanged, no subprocess, no audit")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L18/2 — cli + FAITHFUL verdict
# ─────────────────────────────────────────────────────────────────

def case_judge_cli_faithful() -> None:
    _section("L18/2: cli + FAITHFUL verdict → candidate ships")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l2-"))
    try:
        _set_sandbox(tmp)
        dialectic = _fresh_dialectic()
        _set_site_mode(dialectic, "voice_summary", "cli")

        fake = mock.Mock()
        fake.stdout = "FAITHFUL | candidate covers the deadline and decision\n"
        fake.stderr = ""
        with mock.patch("dialectic.subprocess.run", return_value=fake) as run:
            final, verdict, why = dialectic.judge_summary(
                source="The deadline is May 10, decision: ship.",
                candidate="Deadline May 10, decision is to ship.",
                lang="en",
            )
        assert final == "Deadline May 10, decision is to ship.", final
        assert verdict == "faithful", verdict
        assert run.call_count == 1, "subprocess.run should fire exactly once"

        events = _read_audit_events(_audit_path(tmp))
        d_evts = [e for e in events
                  if e.get("event_type") == "decision.dialectical"
                  and e.get("details", {}).get("site") == "voice_summary"]
        assert d_evts, f"missing audit, got {[e.get('event_type') for e in events]}"
        assert d_evts[0]["details"]["choice"].startswith("faithful"), d_evts
        assert d_evts[0]["details"]["mode"] == "cli", d_evts
        print("  pass — FAITHFUL verdict ships candidate + audits cli mode")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L18/3 — cli + CORRECTED verdict
# ─────────────────────────────────────────────────────────────────

def case_judge_cli_corrected() -> None:
    _section("L18/3: cli + CORRECTED verdict → revised text ships")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l3-"))
    try:
        _set_sandbox(tmp)
        dialectic = _fresh_dialectic()
        _set_site_mode(dialectic, "voice_summary", "cli")

        fake = mock.Mock()
        fake.stdout = ("CORRECTED | Deadline May 10, decision: do NOT "
                       "ship — candidate misrepresented the stance.\n")
        fake.stderr = ""
        with mock.patch("dialectic.subprocess.run", return_value=fake):
            final, verdict, _why = dialectic.judge_summary(
                source="Decision: do NOT ship before May 10.",
                candidate="Decision is to ship May 10.",  # WRONG stance
                lang="en",
            )
        assert "do NOT ship" in final, final
        assert verdict == "corrected", verdict
        assert "May 10" in final, "corrected text must keep the date"

        events = _read_audit_events(_audit_path(tmp))
        d_evts = [e for e in events
                  if e.get("event_type") == "decision.dialectical"
                  and e.get("details", {}).get("site") == "voice_summary"]
        assert d_evts, "audit event missing"
        assert d_evts[0]["details"]["choice"].startswith("corrected"), d_evts
        # synthesis carries the verdict line
        assert "CORRECTED" in d_evts[0]["details"]["synthesis"], d_evts
        print("  pass — CORRECTED replaces candidate + audit shows corrected")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L18/4 — unparseable output defaults to faithful (ship candidate)
# ─────────────────────────────────────────────────────────────────

def case_judge_cli_unparseable_defaults_to_faithful() -> None:
    _section("L18/4: unparseable cli output → ship candidate (safe default)")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l4-"))
    try:
        _set_sandbox(tmp)
        dialectic = _fresh_dialectic()
        _set_site_mode(dialectic, "voice_summary", "cli")

        # Various unparseable shapes — all must default to faithful.
        for cli_out in ("nonsense without pipe", "", "JSON {\"foo\": 1}",
                         "MAYBE | something",  # unknown verdict token
                         "CORRECTED |",  # CORRECTED but empty body
                         ):
            fake = mock.Mock()
            fake.stdout = cli_out
            fake.stderr = ""
            with mock.patch("dialectic.subprocess.run", return_value=fake):
                final, verdict, _why = dialectic.judge_summary(
                    source="src", candidate="cand", lang="de",
                )
            assert final == "cand", f"unparseable={cli_out!r} → final={final!r}"
            assert verdict == "faithful", verdict
        print("  pass — 5 unparseable shapes all default to faithful (safe)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L18/5 — cli timeout defaults to ship-candidate
# ─────────────────────────────────────────────────────────────────

def case_judge_cli_timeout() -> None:
    _section("L18/5: cli timeout → ship candidate (no block)")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l5-"))
    try:
        _set_sandbox(tmp)
        dialectic = _fresh_dialectic()
        _set_site_mode(dialectic, "voice_summary", "cli")

        with mock.patch("dialectic.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("claude", 20)):
            final, verdict, _why = dialectic.judge_summary(
                source="x", candidate="y", lang="de",
            )
        assert final == "y", final
        assert verdict == "faithful", verdict

        with mock.patch("dialectic.subprocess.run",
                        side_effect=FileNotFoundError("no claude binary")):
            final, verdict, _why = dialectic.judge_summary(
                source="x", candidate="z", lang="de",
            )
        assert final == "z", final
        assert verdict == "faithful", verdict
        print("  pass — timeout AND missing-cli both default to ship candidate")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L18/6 — summarize.summarize() integration: mode=off no spawn
# ─────────────────────────────────────────────────────────────────

def case_summarize_judge_off_no_spawn() -> None:
    _section("L18/6: summarize.summarize() with judge mode=off — no extra spawn")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l6-"))
    try:
        _set_sandbox(tmp)
        # Force summarize backend to "naive" so we don't hit a real LLM.
        os.environ["VOICE_SUMMARIZE_BACKEND"] = "naive"
        summarize = _fresh_summarize()
        # Sanity: summarize imported the dialectic module
        assert summarize._dialectic is not None, \
            "summarize should have picked up the dialectic module"

        # Site mode is off by default → no subprocess from the judge.
        with mock.patch("dialectic.subprocess.run") as fake_run:
            out = summarize.summarize(
                "This is a longer text that needs summarizing for voice. " * 3,
                lang="de", max_chars=120, model="claude-haiku-4-5",
            )
        assert out, "summarize should return something"
        assert fake_run.call_count == 0, \
            f"judge must not spawn when mode=off, got {fake_run.call_count} calls"
        print("  pass — judge stays inert when site mode is off (default)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("VOICE_SUMMARIZE_BACKEND", None)


# ─────────────────────────────────────────────────────────────────
# L18/7 — summarize.summarize() integration: mode=cli + CORRECTED
# ─────────────────────────────────────────────────────────────────

def case_summarize_judge_cli_corrects() -> None:
    _section("L18/7: summarize.summarize() with judge mode=cli — replaces candidate")
    tmp = Path(tempfile.mkdtemp(prefix="voice-judge-l7-"))
    try:
        _set_sandbox(tmp)
        os.environ["VOICE_SUMMARIZE_BACKEND"] = "naive"
        summarize = _fresh_summarize()
        dialectic = summarize._dialectic
        _set_site_mode(dialectic, "voice_summary", "cli")

        fake = mock.Mock()
        fake.stdout = "CORRECTED | Korrigierte Zusammenfassung mit allen Fakten."
        fake.stderr = ""
        with mock.patch.object(dialectic.subprocess, "run", return_value=fake):
            out = summarize.summarize(
                "Source text with a deadline of May 10 and decision to ship.",
                lang="de", max_chars=120, model="claude-haiku-4-5",
            )
        assert out == "Korrigierte Zusammenfassung mit allen Fakten.", out

        events = _read_audit_events(_audit_path(tmp))
        d_evts = [e for e in events
                  if e.get("event_type") == "decision.dialectical"
                  and e.get("details", {}).get("site") == "voice_summary"]
        assert d_evts, f"audit missing, got {[e.get('event_type') for e in events]}"
        assert d_evts[0]["details"]["choice"].startswith("corrected"), d_evts
        print("  pass — full pipe-through corrects the candidate + audits")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("VOICE_SUMMARIZE_BACKEND", None)


# ─────────────────────────────────────────────────────────────────

def main() -> int:
    # The cli-judge cases mock ``dialectic.subprocess.run`` and assert it
    # fires, but ``_run_cli_judge`` short-circuits on ``_claude_authenticated()``
    # BEFORE the spawn when no OAuth session / ANTHROPIC_API_KEY exists (the
    # intended fresh-install fast-fail). On a CI box with no credentials the
    # judge would never reach the mocked subprocess and three cases would fail
    # spuriously. Establish the 'authenticated' precondition these cases assume;
    # the mode=off cases are unaffected (auth gates nothing when the judge is
    # inert). Restored below so the process env is not mutated for later suites.
    _orig_api_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "stub-key-for-judge-spawn"
    cases = [
        case_judge_off_returns_candidate,
        case_judge_cli_faithful,
        case_judge_cli_corrected,
        case_judge_cli_unparseable_defaults_to_faithful,
        case_judge_cli_timeout,
        case_summarize_judge_off_no_spawn,
        case_summarize_judge_cli_corrects,
    ]
    failures = 0
    for fn in cases:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL — {fn.__name__}: {e}")
            failures += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR — {fn.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failures += 1
    if _orig_api_key is None:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ["ANTHROPIC_API_KEY"] = _orig_api_key
    if failures:
        print(f"\n{failures} of {len(cases)} cases failed.")
        return 1
    print(f"\nAll {len(cases)} voice_summary judge cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
