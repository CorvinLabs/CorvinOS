"""Tests for entity_extract.py (ADR-0168 M1).

Covers:
  - Pass 1: domain-prefix forced routing (ATS:, A2A:, /create workflow, etc.)
  - Pass 2: keyword-cluster NER for all entity types
  - Slot filler: name, schedule, target, uid, execution_mode
  - Confidence threshold: below 0.60 → ENTITY_NONE
  - No anthropic import
  - EntityPlan.is_actionable contract
"""
import ast
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from entity_extract import (  # noqa: E402
    EntityPlan,
    extract,
    ENTITY_NONE,
    ENTITY_ATS_TASK,
    ENTITY_WORKFLOW,
    ENTITY_A2A,
    ENTITY_FORGE,
    ENTITY_SKILL,
    ENTITY_AUDIT,
    ENTITY_ERASURE,
    ENTITY_VAULT,
    ENTITY_ENGINE,
    ENTITY_RAG,
)


class TestPass1Prefixes(unittest.TestCase):
    """Pass 1: domain-prefix / slash-command forced routing."""

    def _forced(self, prompt: str, expected_type: str) -> None:
        plan = extract(prompt)
        self.assertEqual(plan.entity_type, expected_type,
                         f"prompt={prompt!r}: expected {expected_type}, got {plan.entity_type}")
        self.assertTrue(plan.forced, f"prompt={prompt!r}: expected forced=True")
        self.assertAlmostEqual(plan.confidence, 1.0)

    def test_ats_prefix(self):
        self._forced("ATS: create a database migration task", ENTITY_ATS_TASK)

    def test_ats_prefix_case_insensitive(self):
        self._forced("ats: run the health check", ENTITY_ATS_TASK)

    def test_a2a_prefix(self):
        self._forced("A2A: pair with instance corvin-b", ENTITY_A2A)

    def test_workflow_prefix(self):
        self._forced("Workflow: monitor health endpoint every 5 minutes", ENTITY_WORKFLOW)

    def test_forge_prefix(self):
        self._forced("Forge: create csv-parser tool", ENTITY_FORGE)

    def test_skill_prefix(self):
        self._forced("Skill: create a code-review skill", ENTITY_SKILL)

    def test_slash_create_workflow(self):
        self._forced('/create workflow name="my-flow" schedule="*/5 * * * *"', ENTITY_WORKFLOW)

    def test_slash_create_task(self):
        self._forced('/create task name="migration"', ENTITY_ATS_TASK)

    def test_slash_create_skill(self):
        self._forced('/create skill name="reviewer"', ENTITY_SKILL)

    def test_slash_create_tool(self):
        self._forced('/create tool name="csv-diff"', ENTITY_FORGE)

    def test_slash_erase(self):
        self._forced('/erase user uid=abc123', ENTITY_ERASURE)

    def test_slash_audit(self):
        self._forced('/audit last 50', ENTITY_AUDIT)


class TestPass2Keywords(unittest.TestCase):
    """Pass 2: keyword NER cluster matching."""

    def _type(self, prompt: str) -> str:
        return extract(prompt).entity_type

    def test_ats_task_keyword(self):
        self.assertEqual(self._type("Erstell mir einen ATS Task"), ENTITY_ATS_TASK)

    def test_bare_task_word_does_not_create_phantom_task(self):
        # ats_task is the only side-effecting route; bare "task"/"aufgabe" in a
        # normal sentence must NOT cross the actionable gate (security review
        # 2026-06-27, C2). Explicit intent goes via Pass-1 (/create task, ATS:).
        for benign in (
            "help me with this task, the task is to summarize the logs",
            "kannst du mir bei dieser aufgabe helfen, die aufgabe ist wichtig",
            "what is the task at hand and what task should I do first",
        ):
            plan = extract(benign)
            self.assertFalse(
                plan.is_actionable and plan.entity_type == ENTITY_ATS_TASK,
                f"phantom ats_task created for benign text: {benign!r} "
                f"(type={plan.entity_type}, conf={plan.confidence})",
            )

    def test_workflow_keyword(self):
        self.assertEqual(self._type("Ich brauche einen Workflow der Logs rotiert"), ENTITY_WORKFLOW)

    def test_awpkg_keyword(self):
        self.assertEqual(self._type("Erstell einen AWPKG Flow für den Export"), ENTITY_WORKFLOW)

    def test_a2a_keyword(self):
        self.assertEqual(self._type("Setup A2A mit dem zweiten Server"), ENTITY_A2A)

    def test_agent_to_agent(self):
        self.assertEqual(self._type("Konfiguriere Agent to Agent Mesh"), ENTITY_A2A)

    def test_forge_tool(self):
        self.assertEqual(self._type("Erstell ein Forge Tool für CSV-Parsing"), ENTITY_FORGE)

    def test_skill_keyword(self):
        self.assertEqual(self._type("Leg einen neuen Skill für Code Review an"), ENTITY_SKILL)

    def test_audit_keyword(self):
        self.assertEqual(self._type("Zeig mir die letzten Audit Logs"), ENTITY_AUDIT)

    def test_erasure_german(self):
        self.assertEqual(self._type("Lösche alle Daten des Nutzers"), ENTITY_ERASURE)

    def test_erasure_english(self):
        self.assertEqual(self._type("Erasure request for user uid=xyz"), ENTITY_ERASURE)

    def test_vault_keyword(self):
        self.assertEqual(self._type("Füge einen neuen Secret-Vault Eintrag hinzu"), ENTITY_VAULT)

    def test_rag_keyword(self):
        self.assertEqual(self._type("Lade ein Dokument in die Wissensbasis"), ENTITY_RAG)

    def test_engine_keyword(self):
        self.assertEqual(self._type("Welcher Worker-Engine ist aktiv?"), ENTITY_ENGINE)


class TestPass2BelowThreshold(unittest.TestCase):
    """Prompts with weak signals should return ENTITY_NONE."""

    def test_plain_question_is_none(self):
        plan = extract("Was ist das Wetter heute?")
        self.assertEqual(plan.entity_type, ENTITY_NONE)
        self.assertFalse(plan.is_actionable)

    def test_code_question_is_none(self):
        plan = extract("Erkläre mir wie Python Generatoren funktionieren.")
        self.assertEqual(plan.entity_type, ENTITY_NONE)

    def test_empty_string_is_none(self):
        plan = extract("")
        self.assertEqual(plan.entity_type, ENTITY_NONE)


class TestSlotFiller(unittest.TestCase):
    """Slot filler extracts parameters from natural language."""

    def test_name_quoted(self):
        plan = extract('Erstell einen Workflow namens "health-monitor"')
        self.assertEqual(plan.slots.get("name"), "health-monitor")

    def test_name_keyword(self):
        plan = extract("Erstell einen Workflow namens my-flow")
        self.assertEqual(plan.slots.get("name"), "my-flow")

    def test_schedule_every_n_minutes(self):
        plan = extract("Erstell einen Workflow der alle 5 Minuten läuft")
        self.assertEqual(plan.slots.get("schedule"), "*/5 * * * *")

    def test_schedule_every_hour(self):
        plan = extract("Erstell einen Workflow der jede Stunde läuft")
        self.assertEqual(plan.slots.get("schedule"), "0 */1 * * *")

    def test_target_extraction(self):
        plan = extract("Erstell einen ATS Task für den Auth-Service")
        self.assertEqual(plan.slots.get("target"), "Auth-Service")

    def test_uid_for_erasure(self):
        plan = extract("/erase user uid=u-abc123 from the system")
        self.assertEqual(plan.slots.get("subject_id"), "u-abc123")

    def test_background_mode(self):
        plan = extract("Erstell einen Workflow im Hintergrund")
        self.assertEqual(plan.slots.get("execution_mode"), "background")


class TestIsActionable(unittest.TestCase):
    """EntityPlan.is_actionable contract."""

    def test_forced_is_actionable(self):
        plan = extract("ATS: run task")
        self.assertTrue(plan.is_actionable)

    def test_high_confidence_is_actionable(self):
        plan = extract("Erstell einen Workflow und eine Pipeline für den Export")
        # workflow + pipeline → confidence ≥ 0.60
        if plan.entity_type != ENTITY_NONE:
            self.assertTrue(plan.is_actionable)

    def test_none_not_actionable(self):
        plan = extract("Wie geht es dir heute?")
        self.assertFalse(plan.is_actionable)


class TestRawTextTruncation(unittest.TestCase):
    """raw_text is capped at 200 chars (no PII leakage into audit)."""

    def test_long_prompt_truncated(self):
        long_prompt = "ATS: " + "x" * 500
        plan = extract(long_prompt)
        self.assertLessEqual(len(plan.raw_text), 200)


class TestNoAnthropicImport(unittest.TestCase):
    """entity_extract.py MUST NOT import anthropic (CI AST lint rule)."""

    def test_no_anthropic_import(self):
        src = (_HERE / "entity_extract.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name.startswith("anthropic"),
                        f"entity_extract.py must not import anthropic: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("anthropic"):
                    self.fail(f"entity_extract.py must not import from anthropic: {node.module}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
