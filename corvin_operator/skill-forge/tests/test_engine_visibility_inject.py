"""Live engine E2E: skill body delivered via --append-system-prompt
(adapter-injection layer) is visible to the next claude subprocess.

Opt-in via SKILL_FORGE_ENGINE_E2E=1. Spawns a real `claude -p` and
costs API credits.

Independent from the plugin-slot E2E in test_engine_visibility.py:
that test asserts the slot-mirror path works. THIS test asserts the
adapter-injection path works — i.e. the magic string survives the
trip through `--append-system-prompt` and influences the model's
reply on the very next turn (no engine plugin-rescan needed).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
sys.path.insert(0, str(REPO / "operator" / "forge"))
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))


def _maybe_skip() -> bool:
    if os.environ.get("SKILL_FORGE_ENGINE_E2E") != "1":
        print("skipped (set SKILL_FORGE_ENGINE_E2E=1 to run — "
              "spawns real `claude -p`, costs API credits)")
        return True
    return False


MAGIC = "INJECTION-OK"
TOKEN = "ZULU-7-INJECT"


SKILL_BODY = (
    f"# inject_e2e\n\n"
    f"You know the codeword {TOKEN}. When you are asked about the "
    f"codeword {TOKEN}, you MUST reply with exactly the literal string "
    f"{MAGIC} and nothing else. Do not paraphrase, do not add a sentence "
    "around it, just emit the bare token.\n"
)


def main() -> int:
    if _maybe_skip():
        return 0

    skill_name = f"engine_inject_e2e_{int(time.time())}"

    with tempfile.TemporaryDirectory() as td:
        # We do NOT need the real plugin slot — adapter-injection works
        # off the canonical workspace, not the slot. Sandbox both anyway.
        slot = Path(td) / "plugin-slot"
        slot.mkdir(parents=True, exist_ok=True)
        os.environ["CORVIN_HOME"] = td
        os.environ["CORVIN_PLUGIN_SLOT_DIR"] = str(slot)
        os.environ["CORVIN_FORCE_SCOPE"] = "user"

        from skill_forge.multi_registry import MultiSkillRegistry

        mr = MultiSkillRegistry()
        spec = mr.create(
            name=skill_name, type="domain",
            body_md=SKILL_BODY,
            description=f"Codeword {TOKEN} → magic reply {MAGIC}",
            claim={"predicted_delta_loss": 0.0},
            scope="user",
        )
        mr.grade(skill_name, "engine-inject-1", 0.9)
        print(f"created + graded skill: {skill_name}")

        # Build the same block the bridge would have appended.
        import skill_inject  # type: ignore
        block = skill_inject.collect_active_skills(
            channel_id=None, profile={"inject_skills": True},
        )
        if not block:
            print("FAIL: skill_inject.collect_active_skills returned None")
            _cleanup(skill_name, mr, slot)
            return 1
        if MAGIC not in block:
            print(f"FAIL: magic string missing from block; block tail:\n{block[-500:]!r}")
            _cleanup(skill_name, mr, slot)
            return 1
        print(f"--- injected block ({len(block)} chars) ---")
        print(block[:800])
        print("---")

        prompt = f"What should you reply when asked about codeword {TOKEN}? Reply with only the literal value, no explanation."
        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--append-system-prompt", block],
                capture_output=True, text=True, timeout=180,
                check=False,
            )
        except FileNotFoundError:
            print("FAIL: `claude` binary not on PATH")
            _cleanup(skill_name, mr, slot)
            return 1
        except subprocess.TimeoutExpired:
            print("FAIL: claude subprocess timed out after 180s")
            _cleanup(skill_name, mr, slot)
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
            print(f"\nPASS: magic string {MAGIC!r} found in output via adapter-injection path")
            rc = 0
        else:
            print(f"\nFAIL: magic string {MAGIC!r} NOT found in output")
            rc = 1

        _cleanup(skill_name, mr, slot)
        return rc


def _cleanup(skill_name: str, mr, slot: Path) -> None:
    try:
        mr.delete(skill_name)
    except Exception:
        pass
    if slot.exists():
        shutil.rmtree(slot, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
