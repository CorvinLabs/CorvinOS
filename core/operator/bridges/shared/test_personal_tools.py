"""End-to-end tests for personal_tools (Layer 27).

Drives the module's public API against a tempdir sandbox. Verifies:
  - Validation: name shape rejection, namespace handling
  - save_from_body: writes registry + body, audit-first ordering
  - save_from_scope: copies a real task-scope tool body into me.*
  - list_personal: filters by namespace, sorts by last_used desc
  - get_personal: returns None when absent
  - remove: returns False on missing, True + audit on present
  - format_inject_block: heading + bullets + stale-flag rendering
  - Audit-chain integrity holds across save+remove cycle
  - The bullet body never leaks into audit details
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_FORGE_TOP = _HERE.parent.parent / "forge"
if str(_FORGE_TOP) not in sys.path:
    sys.path.insert(0, str(_FORGE_TOP))

import personal_tools as pt  # noqa: E402
from forge.security_events import verify_chain  # noqa: E402


def _read_jsonl(p: Path) -> list[dict]:
    out: list[dict] = []
    if not p.exists():
        return out
    with p.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pt-e2e-"))
        self.audit = pt._audit_path(corvin_home=self.tmp)
        self.audit.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── 1. Validation ─────────────────────────────────────────────────────────

class ValidationTests(_Base):
    def test_bare_name_accepted(self) -> None:
        self.assertEqual(pt.validate_personal_name("poke_api"), "me.poke_api")

    def test_prefixed_name_idempotent(self) -> None:
        self.assertEqual(pt.validate_personal_name("me.poke_api"), "me.poke_api")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name("")

    def test_uppercase_rejected(self) -> None:
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name("PokeAPI")

    def test_path_traversal_rejected(self) -> None:
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name("../../etc/passwd")
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name("a/b")

    def test_too_long_rejected(self) -> None:
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name("x" * 60)

    def test_starting_with_digit_rejected(self) -> None:
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name("1tool")

    def test_non_string_rejected(self) -> None:
        with self.assertRaises(pt.InvalidPersonalName):
            pt.validate_personal_name(42)  # type: ignore[arg-type]


# ── 2. save_from_body ─────────────────────────────────────────────────────

class SaveFromBodyTests(_Base):
    def test_writes_registry_and_body(self) -> None:
        body = "def run(x):\n    return {'doubled': x * 2}\n"
        t = pt.save_from_body(
            "doubler", description="doubles ints",
            impl_text=body, corvin_home=self.tmp,
        )
        self.assertEqual(t.name, "me.doubler")
        # Registry has the entry
        reg = json.loads(pt._registry_path(corvin_home=self.tmp).read_text())
        self.assertIn("me.doubler", reg)
        self.assertEqual(reg["me.doubler"]["scope"], "user")
        self.assertEqual(reg["me.doubler"]["meta"]["personal"], True)
        # Body landed
        body_path = pt._tools_dir(corvin_home=self.tmp) / "me.doubler.py"
        self.assertEqual(body_path.read_text(), body)

    def test_overwrite_required_for_replacement(self) -> None:
        body = "def run():\n    return {'k': 1}\n"
        pt.save_from_body("x", description="d", impl_text=body,
                          corvin_home=self.tmp)
        with self.assertRaises(pt.ToolAlreadyExists):
            pt.save_from_body("x", description="d2", impl_text=body,
                              corvin_home=self.tmp)
        # overwrite=True succeeds
        t2 = pt.save_from_body("x", description="d2", impl_text=body,
                               corvin_home=self.tmp, overwrite=True)
        self.assertEqual(t2.description, "d2")

    def test_empty_body_rejected(self) -> None:
        with self.assertRaises(pt.PersonalToolError):
            pt.save_from_body("x", description="d", impl_text="",
                              corvin_home=self.tmp)

    def test_empty_description_rejected(self) -> None:
        with self.assertRaises(pt.PersonalToolError):
            pt.save_from_body("x", description="   ",
                              impl_text="def run(): return {}",
                              corvin_home=self.tmp)

    def test_audit_event_emitted(self) -> None:
        pt.save_from_body("x", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        events = [e for e in _read_jsonl(self.audit)
                  if e.get("event_type") == "tool.user_saved"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["name"], "me.x")

    def test_audit_carries_no_body(self) -> None:
        body = "SECRET_BODY_TOKEN_DO_NOT_LEAK"
        pt.save_from_body("y", description="d",
                          impl_text=f"def run(): {body}",
                          corvin_home=self.tmp)
        for ev in _read_jsonl(self.audit):
            for v in (ev.get("details") or {}).values():
                if isinstance(v, str):
                    self.assertNotIn(body, v,
                                     f"body leaked into audit: {ev}")


# ── 3. save_from_scope ────────────────────────────────────────────────────

class SaveFromScopeTests(_Base):
    def _seed_task_tool(self, *, name: str, body: str,
                        description: str = "task tool",
                        chat_key: str = "anon") -> Path:
        # Plant a fake task-scope forge layout so save_from_scope can read it.
        task_forge = pt._scope_forge_dir(
            corvin_home=self.tmp, scope="task", chat_key=chat_key,
        )
        (task_forge / "tools").mkdir(parents=True, exist_ok=True)
        impl = task_forge / "tools" / f"{name}.py"
        impl.write_text(body)
        reg = task_forge / "registry.json"
        reg.write_text(json.dumps({
            name: {
                "name": name, "description": description,
                "runtime": "python", "impl_path": str(impl),
                "scope": "task", "created_at": time.time(),
                "sha256": "deadbeef",
            }
        }))
        return task_forge

    def test_copies_body_and_creates_personal(self) -> None:
        body = "def run(api_url):\n    import urllib.request\n    return {'status': 'OK'}\n"
        self._seed_task_tool(name="poke_api", body=body, chat_key="cx")
        t = pt.save_from_scope(
            "poke_api", source_scope="task", chat_key="cx",
            corvin_home=self.tmp,
        )
        self.assertEqual(t.name, "me.poke_api")
        self.assertEqual(t.saved_from_scope, "task")
        # Body is byte-identical
        body_path = pt._tools_dir(corvin_home=self.tmp) / "me.poke_api.py"
        self.assertEqual(body_path.read_text(), body)

    def test_explicit_alias(self) -> None:
        self._seed_task_tool(
            name="code.poke_api",
            body="def run(): return {}",
            chat_key="cx",
        )
        t = pt.save_from_scope(
            "code.poke_api", "myapi",
            source_scope="task", chat_key="cx",
            corvin_home=self.tmp,
        )
        self.assertEqual(t.name, "me.myapi")

    def test_default_alias_strips_namespace_prefix(self) -> None:
        # source "code.poke_api" → personal "poke_api"
        self._seed_task_tool(name="code.poke_api",
                             body="def run(): return {}",
                             chat_key="cx")
        t = pt.save_from_scope(
            "code.poke_api", source_scope="task", chat_key="cx",
            corvin_home=self.tmp,
        )
        self.assertEqual(t.name, "me.poke_api")

    def test_missing_source_raises(self) -> None:
        with self.assertRaises(pt.ToolNotFound):
            pt.save_from_scope("does_not_exist",
                               source_scope="task", chat_key="cx",
                               corvin_home=self.tmp)

    def test_unknown_scope_rejected(self) -> None:
        with self.assertRaises(pt.PersonalToolError):
            pt.save_from_scope("x", source_scope="invalid",
                               corvin_home=self.tmp)


# ── 4. list / get / remove ────────────────────────────────────────────────

class ListGetRemoveTests(_Base):
    def test_list_filters_by_namespace(self) -> None:
        # Add a non-me.* entry directly into the registry so we can
        # confirm it's NOT picked up.
        pt.save_from_body("a", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        # Inject a non-personal tool into the registry
        reg_path = pt._registry_path(corvin_home=self.tmp)
        reg = json.loads(reg_path.read_text())
        reg["public.foo"] = {"description": "shared", "scope": "user"}
        reg_path.write_text(json.dumps(reg))

        items = pt.list_personal(corvin_home=self.tmp)
        names = {t.name for t in items}
        self.assertEqual(names, {"me.a"})

    def test_list_sorted_by_last_used_desc(self) -> None:
        pt.save_from_body("old", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        pt.save_from_body("recent", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        # Stamp last_used_at on "recent"
        reg_path = pt._registry_path(corvin_home=self.tmp)
        reg = json.loads(reg_path.read_text())
        reg["me.recent"]["last_used_at"] = time.time()
        reg_path.write_text(json.dumps(reg))

        items = pt.list_personal(corvin_home=self.tmp)
        self.assertEqual(items[0].name, "me.recent")

    def test_get_returns_none_when_absent(self) -> None:
        self.assertIsNone(pt.get_personal("nope", corvin_home=self.tmp))

    def test_remove_returns_false_on_missing(self) -> None:
        self.assertFalse(pt.remove("nope", corvin_home=self.tmp))

    def test_remove_deletes_and_audits(self) -> None:
        pt.save_from_body("toremove", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        body_path = pt._tools_dir(corvin_home=self.tmp) / "me.toremove.py"
        self.assertTrue(body_path.exists())

        ok = pt.remove("toremove", corvin_home=self.tmp)
        self.assertTrue(ok)
        self.assertFalse(body_path.exists())
        events = [e for e in _read_jsonl(self.audit)
                  if e.get("event_type") == "tool.user_removed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["name"], "me.toremove")


# ── 5. format_inject_block ────────────────────────────────────────────────

class InjectBlockTests(_Base):
    def test_empty_when_no_tools(self) -> None:
        self.assertEqual(pt.format_inject_block(corvin_home=self.tmp), "")

    def test_renders_heading_and_bullets(self) -> None:
        pt.save_from_body("a", description="does the A thing",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        pt.save_from_body("b", description="does the B thing",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        block = pt.format_inject_block(corvin_home=self.tmp)
        self.assertIn(pt.INJECT_HEADING, block)
        self.assertIn("`me.a` — does the A thing", block)
        self.assertIn("`me.b` — does the B thing", block)

    def test_max_n_caps_listing(self) -> None:
        for i in range(5):
            pt.save_from_body(f"t{i}", description="d",
                              impl_text="def run(): return {}",
                              corvin_home=self.tmp)
        block = pt.format_inject_block(corvin_home=self.tmp, max_n=2)
        self.assertEqual(block.count("- `me."), 2)

    def test_stale_flag_for_unused_tools(self) -> None:
        pt.save_from_body("stale", description="old tool",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        # Set last_used_at to 100 days ago
        reg_path = pt._registry_path(corvin_home=self.tmp)
        reg = json.loads(reg_path.read_text())
        reg["me.stale"]["last_used_at"] = time.time() - 100 * 86400
        reg_path.write_text(json.dumps(reg))

        block = pt.format_inject_block(corvin_home=self.tmp)
        self.assertIn("unused 100d", block)


# ── 6. Audit chain integrity ──────────────────────────────────────────────

class ChainIntegrityTests(_Base):
    def test_chain_intact_after_save_then_remove(self) -> None:
        pt.save_from_body("a", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        pt.save_from_body("b", description="d",
                          impl_text="def run(): return {}",
                          corvin_home=self.tmp)
        pt.remove("a", corvin_home=self.tmp)
        ok, problems = verify_chain(self.audit)
        self.assertTrue(ok, msg=f"chain broken: {problems}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
