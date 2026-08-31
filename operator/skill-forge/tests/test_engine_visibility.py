"""Live engine-visibility test (opt-in).

Confirms that a skill written via SkillRegistry into the plugin slot is
actually picked up by the next ``claude -p`` subprocess via the engine's
plugin-skill discovery — i.e. the round-trip of the whole
"slot mirror -> next subprocess sees it" pipeline this plugin promises.

This test is opt-in via ``SKILL_FORGE_ENGINE_E2E=1`` because:
  - it spawns a real ``claude`` subprocess, which costs API credits;
  - it touches the *real* plugin slot directory (the engine only loads
    skills from the canonical plugin-source path).

When the env is not set, the test prints a skip message and exits 0.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
SLOT_REAL = REPO / "operator" / "skill-forge" / "skills" / "dyn"

sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
sys.path.insert(0, str(REPO / "operator" / "forge"))


def _maybe_skip() -> bool:
    if os.environ.get("SKILL_FORGE_ENGINE_E2E") != "1":
        print("skipped (set SKILL_FORGE_ENGINE_E2E=1 to run — "
              "spawns real `claude -p`, costs API credits)")
        return True
    return False


MAGIC = "42-FORGE-OK"
TOKEN = "engine_e2e_secret_token"


SKILL_BODY = (
    f"# engine_e2e\n\n"
    f"When asked about {TOKEN}, reply with the literal string {MAGIC} "
    "and nothing else. Do not paraphrase, do not add a sentence around "
    "it, just emit the bare token.\n"
)


def main() -> int:
    if _maybe_skip():
        return 0

    # Use a deterministic-but-unique name so reruns don't collide on disk.
    skill_name = f"engine_e2e_{int(time.time())}"

    # Engine reads from the REAL plugin slot — no env override here.
    SLOT_REAL.mkdir(parents=True, exist_ok=True)

    # We do NOT redirect CORVIN_PLUGIN_SLOT_DIR — the engine needs the
    # real path. We isolate the canonical workspace via CORVIN_HOME.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.environ.pop("CORVIN_PLUGIN_SLOT_DIR", None)
        os.environ["CORVIN_HOME"] = td
        os.environ["CORVIN_FORCE_SCOPE"] = "user"

        from skill_forge.registry import SkillRegistry, plugin_slot_dir
        # Patch the resolution: we want to write to the real repo slot,
        # not the CORVIN_HOME-derived one. Force it explicitly.
        os.environ["CORVIN_PLUGIN_SLOT_DIR"] = str(SLOT_REAL)

        print(f"slot dir resolves to: {plugin_slot_dir()}")
        print(f"creating skill: {skill_name}")

        # Use a registry directly under CORVIN_HOME.
        ws = Path(td) / "global" / "skill-forge"
        reg = SkillRegistry(ws)
        reg.create(
            name=skill_name, type="domain",
            body_md=SKILL_BODY,
            description=(
                f"Test skill: when prompted about {TOKEN}, "
                f"reply with {MAGIC}."
            ),
            claim={"predicted_delta_loss": 0.0},
        )

        slot_md = SLOT_REAL / skill_name / "SKILL.md"
        if not slot_md.exists():
            print(f"FAIL: slot SKILL.md not written at {slot_md}")
            return 1
        print(f"slot SKILL.md written at: {slot_md}")

        # Now spawn `claude -p` and ask the question.
        prompt = f"What is {TOKEN}? Reply with only the literal value."
        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True, text=True, timeout=120,
                check=False,
            )
        except FileNotFoundError:
            print("FAIL: `claude` binary not on PATH")
            _cleanup(skill_name, ws, reg)
            return 1
        except subprocess.TimeoutExpired:
            print("FAIL: claude subprocess timed out after 120s")
            _cleanup(skill_name, ws, reg)
            return 1

        out = result.stdout or ""
        err = result.stderr or ""
        print(f"--- claude stdout ({len(out)} chars) ---")
        print(out[:1500])
        if err.strip():
            print(f"--- claude stderr ({len(err)} chars) ---")
            print(err[:500])

        ok = MAGIC in out
        if ok:
            print(f"\nPASS: magic string {MAGIC!r} found in output")
            rc = 0
        else:
            print(f"\nFAIL: magic string {MAGIC!r} NOT found in output")
            rc = 1

        _cleanup(skill_name, ws, reg)
        return rc


def _cleanup(skill_name: str, ws: Path, reg) -> None:
    try:
        reg.delete(skill_name)
    except Exception:
        pass
    slot_dir = SLOT_REAL / skill_name
    if slot_dir.exists():
        shutil.rmtree(slot_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
