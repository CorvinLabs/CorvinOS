"""End-to-end test for the expense_approval_pipeline example workflow
(ADR-0188's second worked example — code/merge/retry in a plain, non-chat
`dag` workflow).

Run directly:  python3 core/workflows/tests/test_expense_approval_pipeline.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import DAGRunner, StubEngine, load_workflow, validate  # noqa: E402
from corvin_workflows.engines import EngineCall  # noqa: E402

WORKFLOW_PATH = _PKG_ROOT / "corvin_workflows" / "examples" / "expense_approval_pipeline.awp.yaml"


def _engine(vendor="Delta Airlines", category="travel", alcohol=False):
    def default(call: EngineCall) -> dict:
        if call.agent == "vendor_extractor":
            return {"vendor": vendor, "category": category}
        if call.agent == "policy_flagger":
            return {"alcohol_flag": alcohol}
        if call.agent == "reporter":
            return {"summary": "Expense reviewed."}
        raise RuntimeError(f"unexpected agent {call.agent!r}")
    return StubEngine(default=default)


class ExpenseApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = load_workflow(WORKFLOW_PATH)
        validate(self.doc)

    def test_workflow_loads_and_validates(self) -> None:
        self.assertEqual(self.doc.name, "expense_approval_pipeline")
        self.assertEqual(self.doc.engine, "dag")
        self.assertEqual(len(self.doc.graph), 6)

    def test_approved_within_threshold(self) -> None:
        engine = _engine(category="travel", alcohol=False)
        result = DAGRunner(self.doc, engine=engine).run(
            inputs={"receipt_text": "Delta Airlines - flight to SFO", "amount_usd": 1200.0}
        )
        self.assertEqual(result.state, "complete", result.error)
        decide = result.nodes["decide"].output
        self.assertTrue(decide["approved"])
        self.assertEqual(decide["threshold"], 2000.0)
        self.assertAlmostEqual(result.nodes["convert_currency"].output["amount_eur"], 1104.0)

    def test_rejected_over_threshold(self) -> None:
        engine = _engine(category="meals", alcohol=False)
        result = DAGRunner(self.doc, engine=engine).run(
            inputs={"receipt_text": "Dinner", "amount_usd": 400.0}
        )
        decide = result.nodes["decide"].output
        self.assertFalse(decide["approved"])
        self.assertIn("exceeds", decide["reason"])

    def test_alcohol_flag_forces_manual_review_regardless_of_amount(self) -> None:
        engine = _engine(category="meals", alcohol=True)
        result = DAGRunner(self.doc, engine=engine).run(
            inputs={"receipt_text": "Dinner + wine", "amount_usd": 20.0}
        )
        decide = result.nodes["decide"].output
        self.assertFalse(decide["approved"])
        self.assertIn("alcohol", decide["reason"])

    def test_merge_dict_union_combines_both_branches(self) -> None:
        engine = _engine(vendor="Uber", category="travel", alcohol=False)
        result = DAGRunner(self.doc, engine=engine).run(
            inputs={"receipt_text": "Uber ride", "amount_usd": 45.0}
        )
        merged = result.nodes["combined_context"].output["context"]
        self.assertEqual(merged["vendor"], "Uber")
        self.assertEqual(merged["category"], "travel")
        self.assertEqual(merged["alcohol_flag"], False)

    def test_retry_config_present_and_convert_currency_succeeds(self) -> None:
        node_spec = next(n for n in self.doc.graph if n["id"] == "convert_currency")
        self.assertEqual(node_spec["retry"]["max_retries"], 2)
        self.assertEqual(node_spec["retry"]["error_strategy"], "fail_branch")
        engine = _engine()
        result = DAGRunner(self.doc, engine=engine).run(
            inputs={"receipt_text": "x", "amount_usd": 100.0}
        )
        self.assertEqual(result.nodes["convert_currency"].attempts, 1)
        self.assertEqual(result.nodes["convert_currency"].status, "success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
