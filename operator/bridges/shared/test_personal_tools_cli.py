"""End-to-end CLI tests for personal_tools (Layer 27).

Drives ``python operator/bridges/shared/personal_tools.py <sub>``
via subprocess against a tempdir sandbox.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PERSONAL = _HERE / "personal_tools.py"


def _run(args: list[str], home: Path, *,
         stdin: str | None = None,
         expect_ok: bool = True) -> dict:
    cmd = [sys.executable, str(_PERSONAL), "--corvin-home", str(home)] + args
    r = subprocess.run(cmd, input=stdin, capture_output=True,
                       text=True, timeout=15)
    if expect_ok and r.returncode != 0:
        raise AssertionError(
            f"unexpected exit={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
        )
    body = None
    out = r.stdout.strip()
    if out and out.startswith(("{", "[")):
        try:
            body = json.loads(out)
        except json.JSONDecodeError:
            pass
    return {"code": r.returncode, "body": body,
            "stdout": r.stdout, "stderr": r.stderr}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pt-cli-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class ListAndShowTests(_Base):
    def test_empty_list(self) -> None:
        out = _run(["list"], self.tmp)
        self.assertEqual(out["body"], [])

    def test_show_unknown_returns_1(self) -> None:
        out = _run(["show", "nope"], self.tmp, expect_ok=False)
        self.assertEqual(out["code"], 1)
        self.assertFalse(out["body"]["ok"])


class SaveBodyTests(_Base):
    def test_save_body_then_list(self) -> None:
        body = "def run(x):\n    return {'ok': x}\n"
        out = _run(
            ["save-body", "echo", "--description", "echoes input"],
            self.tmp, stdin=body,
        )
        self.assertTrue(out["body"]["ok"])
        self.assertEqual(out["body"]["tool"]["name"], "me.echo")

        listing = _run(["list"], self.tmp)
        self.assertEqual(len(listing["body"]), 1)
        self.assertEqual(listing["body"][0]["name"], "me.echo")
        self.assertEqual(listing["body"][0]["description"], "echoes input")

    def test_save_invalid_name_returns_1(self) -> None:
        out = _run(
            ["save-body", "BadName", "--description", "x"],
            self.tmp, stdin="def run(): return {}",
            expect_ok=False,
        )
        self.assertEqual(out["code"], 1)
        self.assertEqual(out["body"]["error"], "invalid-name")

    def test_save_overwrite_required(self) -> None:
        body = "def run(): return {}\n"
        _run(["save-body", "x", "--description", "first"],
             self.tmp, stdin=body)
        out = _run(
            ["save-body", "x", "--description", "second"],
            self.tmp, stdin=body, expect_ok=False,
        )
        self.assertEqual(out["code"], 1)
        self.assertEqual(out["body"]["error"], "exists")
        # With --overwrite it succeeds
        out2 = _run(
            ["save-body", "x", "--description", "second", "--overwrite"],
            self.tmp, stdin=body,
        )
        self.assertTrue(out2["body"]["ok"])


class SaveFromScopeCliTests(_Base):
    def _seed_task(self, name: str, body: str, chat_key: str = "cx") -> None:
        # Mirror the layout pt._scope_forge_dir expects for task-scope.
        task = (self.tmp / "tasks" / chat_key / "forge")
        (task / "tools").mkdir(parents=True, exist_ok=True)
        impl = task / "tools" / f"{name}.py"
        impl.write_text(body)
        (task / "registry.json").write_text(json.dumps({
            name: {
                "name": name, "description": f"task tool {name}",
                "runtime": "python", "impl_path": str(impl),
                "scope": "task", "created_at": time.time(),
                "sha256": "abcd",
            }
        }))

    def test_save_round_trip_via_cli(self) -> None:
        self._seed_task("poke_api",
                        "def run(): return {'ok': True}\n",
                        chat_key="cx")
        out = _run(
            ["save", "poke_api",
             "--from", "task", "--chat-key", "cx"],
            self.tmp,
        )
        self.assertTrue(out["body"]["ok"])
        self.assertEqual(out["body"]["tool"]["name"], "me.poke_api")

        # Show round-trips
        show = _run(["show", "me.poke_api"], self.tmp)
        self.assertEqual(show["body"]["name"], "me.poke_api")

    def test_save_missing_source_returns_1(self) -> None:
        out = _run(
            ["save", "ghost", "--from", "task", "--chat-key", "cx"],
            self.tmp, expect_ok=False,
        )
        self.assertEqual(out["code"], 1)
        self.assertEqual(out["body"]["error"], "not-found")


class RemoveCliTests(_Base):
    def test_rm_present(self) -> None:
        _run(["save-body", "tobenuked", "--description", "x"],
             self.tmp, stdin="def run(): return {}\n")
        out = _run(["rm", "me.tobenuked"], self.tmp)
        self.assertTrue(out["body"]["ok"])

    def test_rm_missing_returns_1(self) -> None:
        out = _run(["rm", "ghost"], self.tmp, expect_ok=False)
        self.assertEqual(out["code"], 1)


class InjectCliTests(_Base):
    def test_inject_empty(self) -> None:
        out = _run(["inject"], self.tmp)
        self.assertEqual(out["stdout"].strip(), "")

    def test_inject_renders_block(self) -> None:
        _run(["save-body", "alpha", "--description", "first one"],
             self.tmp, stdin="def run(): return {}\n")
        out = _run(["inject"], self.tmp)
        self.assertIn("Your personal tools", out["stdout"])
        self.assertIn("`me.alpha`", out["stdout"])

    def test_inject_max_caps(self) -> None:
        for i in range(4):
            _run(["save-body", f"t{i}", "--description", "x"],
                 self.tmp, stdin="def run(): return {}\n")
        out = _run(["inject", "--max", "2"], self.tmp)
        self.assertEqual(out["stdout"].count("- `me."), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
