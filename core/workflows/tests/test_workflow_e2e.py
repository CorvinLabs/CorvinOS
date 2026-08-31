"""End-to-end test for the news-sentiment-research workflow.

Drives the full L26 stack (loader → R1..R10 validator → DAGRunner →
StubEngine) over the bundled news_sentiment.awp.yaml. The Stub-Engine
returns deterministic canned responses keyed by (agent_name, iteration),
so the test asserts the orchestration shape (parallel level-0 fetchers,
multi-iteration delegation-loop, level-2 reporter) without spending
any tokens or hitting any network.

Run directly:  python3 core/workflows/tests/test_workflow_e2e.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Make the package importable when running the file standalone.
_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent  # core/workflows/
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import (  # noqa: E402
    DAGRunner,
    EngineCall,
    StubEngine,
    load_workflow,
    validate,
)
from corvin_workflows.validator import WorkflowInvalid  # noqa: E402


WORKFLOW_PATH = _PKG_ROOT / "corvin_workflows" / "examples" / "news_sentiment.awp.yaml"


# ---------------------------------------------------------------------------
# Canned data
# ---------------------------------------------------------------------------

FAKE_ARTICLES = [
    {"id": "A1", "title": "NVDA beats EPS, raises guidance", "sentiment_hint": 0.9},
    {"id": "A2", "title": "Analyst: NVDA priced for perfection", "sentiment_hint": 0.5},
    {"id": "A3", "title": "NVDA hit by supply chain rumors", "sentiment_hint": 0.3},
]
FAKE_POSTS = [
    {"id": "P1", "text": "loaded the boat on NVDA, lfg", "sentiment_hint": 0.85},
    {"id": "P2", "text": "NVDA puts looking juicy", "sentiment_hint": 0.2},
]


def build_stub_engine() -> StubEngine:
    """Construct the canned engine.

    Manager decisions:
      iter 1 → DELEGATE 2 workers (news_scorer, reddit_scorer)
      iter 2 → DELEGATE 1 worker (contradiction_checker)
      iter 3 → COMPLETE with aggregated score

    Workers + static agents (news_fetcher, reddit_fetcher, reporter):
      keyed by agent name only — same answer every time
    """

    def news_scorer(call: EngineCall) -> dict:
        # Average the sentiment_hint of the news articles supplied via state
        articles = (call.state.get("news_fetcher") or {}).get("articles") or []
        if not articles:
            return {"score": 0.0, "n": 0, "confidence": 0.0}
        score = sum(a.get("sentiment_hint", 0.5) for a in articles) / len(articles)
        return {
            "source": "news",
            "score": round(score, 3),
            "n": len(articles),
            "confidence": 0.8,
            "quotes": [a["title"] for a in articles[:2]],
        }

    def reddit_scorer(call: EngineCall) -> dict:
        posts = (call.state.get("reddit_fetcher") or {}).get("posts") or []
        if not posts:
            return {"score": 0.0, "n": 0, "confidence": 0.0}
        score = sum(p.get("sentiment_hint", 0.5) for p in posts) / len(posts)
        return {
            "source": "reddit",
            "score": round(score, 3),
            "n": len(posts),
            "confidence": 0.65,
            "quotes": [p["text"] for p in posts[:2]],
        }

    def contradiction_checker(call: EngineCall) -> dict:
        # Reads the previous iteration's worker results from _iterations
        iters = call.state.get("_iterations") or []
        scores = []
        for it in iters:
            for w in it.get("workers", []):
                if w.get("source") in ("news", "reddit"):
                    scores.append(w.get("score", 0.0))
        spread = (max(scores) - min(scores)) if len(scores) >= 2 else 0.0
        return {
            "kind": "contradiction_check",
            "spread": round(spread, 3),
            "verdict": "consistent" if spread < 0.4 else "contradictory",
        }

    def sentiment_manager(call: EngineCall) -> dict:
        it = call.iteration
        if it == 1:
            return {
                "decision": "DELEGATE",
                "workers": [
                    {"agent": "news_scorer", "instructions": "score news only"},
                    {"agent": "reddit_scorer", "instructions": "score reddit only"},
                ],
            }
        if it == 2:
            return {
                "decision": "DELEGATE",
                "workers": [
                    {
                        "agent": "contradiction_checker",
                        "instructions": "compare news vs reddit",
                    },
                ],
            }
        # Iteration 3 — synthesize and COMPLETE
        iters = call.state.get("_iterations") or []
        scores: list[float] = []
        quotes: list[str] = []
        for prev in iters:
            for w in prev.get("workers", []):
                if "score" in w:
                    scores.append(float(w["score"]))
                quotes.extend(w.get("quotes", []))
        agg = round(sum(scores) / len(scores), 3) if scores else 0.0
        return {
            "decision": "COMPLETE",
            "confidence": 0.86,
            "result": {
                "score": agg,
                "top_quotes": quotes[:5],
                "confidence": 0.86,
            },
        }

    def reporter(call: EngineCall) -> dict:
        sa = call.state.get("sentiment_analysis") or {}
        score = sa.get("score", 0.0)
        quotes = sa.get("top_quotes", [])
        ticker = call.inputs.get("ticker", "?")
        lines = [
            f"Sentiment report for {ticker}",
            f"Aggregate score: {score:.2f}",
            "Top quotes:",
            *(f"  - {q}" for q in quotes),
        ]
        return {"report_text": "\n".join(lines), "score": score}

    def default(call: EngineCall) -> dict:
        # Catches any unexpected agent — makes the failure loud.
        raise RuntimeError(f"StubEngine: unexpected agent {call.agent!r}")

    return StubEngine(
        responses={
            # Static fetchers — same response every iteration
            "news_fetcher": {"articles": FAKE_ARTICLES},
            "reddit_fetcher": {"posts": FAKE_POSTS},
        },
        # All other agents go through the callable below
        default=lambda call: (
            sentiment_manager(call)
            if call.agent == "sentiment_manager"
            else news_scorer(call)
            if call.agent == "news_scorer"
            else reddit_scorer(call)
            if call.agent == "reddit_scorer"
            else contradiction_checker(call)
            if call.agent == "contradiction_checker"
            else reporter(call)
            if call.agent == "reporter"
            else default(call)
        ),
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class LoadAndValidateTests(unittest.TestCase):
    def test_workflow_loads(self) -> None:
        doc = load_workflow(WORKFLOW_PATH)
        self.assertEqual(doc.name, "news_sentiment_research")
        self.assertEqual(doc.engine, "dag")
        self.assertEqual(len(doc.graph), 4)
        ids = [n["id"] for n in doc.graph]
        self.assertEqual(
            ids,
            ["news_fetcher", "reddit_fetcher", "sentiment_analysis", "reporter"],
        )

    def test_workflow_passes_r1_r10(self) -> None:
        doc = load_workflow(WORKFLOW_PATH)
        validate(doc)  # no exception → all 10 rules pass

    def test_validator_catches_cycle(self) -> None:
        doc = load_workflow(WORKFLOW_PATH)
        # Inject a cycle: reporter depends on news_fetcher AND news_fetcher
        # depends on reporter.
        for n in doc.graph:
            if n["id"] == "news_fetcher":
                n["depends_on"] = ["reporter"]
        with self.assertRaises(WorkflowInvalid) as ctx:
            validate(doc)
        self.assertEqual(ctx.exception.code, "R9")

    def test_validator_catches_unknown_node_type(self) -> None:
        doc = load_workflow(WORKFLOW_PATH)
        doc.graph[0]["type"] = "human_in_the_loop"  # not registered
        with self.assertRaises(WorkflowInvalid) as ctx:
            validate(doc)
        self.assertEqual(ctx.exception.code, "R7")

    def test_validator_catches_missing_manager(self) -> None:
        doc = load_workflow(WORKFLOW_PATH)
        for n in doc.graph:
            if n["id"] == "sentiment_analysis":
                n["config"]["manager"] = ""  # invalid
        with self.assertRaises(WorkflowInvalid) as ctx:
            validate(doc)
        self.assertEqual(ctx.exception.code, "R10")


class TopologicalOrderTests(unittest.TestCase):
    def test_levels_match_dependencies(self) -> None:
        from corvin_workflows.runner import _topo_levels

        doc = load_workflow(WORKFLOW_PATH)
        levels = _topo_levels(doc.graph)
        # 3 levels: [fetchers], [sentiment_analysis], [reporter]
        self.assertEqual(len(levels), 3)
        self.assertEqual(sorted(levels[0]), ["news_fetcher", "reddit_fetcher"])
        self.assertEqual(levels[1], ["sentiment_analysis"])
        self.assertEqual(levels[2], ["reporter"])


class EndToEndExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = load_workflow(WORKFLOW_PATH)
        validate(self.doc)
        self.engine = build_stub_engine()
        self.runner = DAGRunner(self.doc, engine=self.engine)

    def test_run_completes(self) -> None:
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        self.assertEqual(
            result.state,
            "complete",
            f"run did not complete: {result.error}\naudit={json.dumps(result.audit, indent=2)}",
        )
        self.assertIsNone(result.error)

    def test_all_four_nodes_executed(self) -> None:
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        self.assertEqual(
            set(result.nodes.keys()),
            {"news_fetcher", "reddit_fetcher", "sentiment_analysis", "reporter"},
        )
        for n in result.nodes.values():
            self.assertIsNone(n.error, f"node {n.node_id} failed: {n.error}")

    def test_fetchers_ran_at_level_zero(self) -> None:
        """The two fetchers are both at level 0 — both engine calls must happen
        before sentiment_analysis touches the engine."""
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        order = [c.agent for c in self.engine.history]
        # Both fetchers must precede the first sentiment_manager call
        first_mgr = order.index("sentiment_manager")
        self.assertLess(order.index("news_fetcher"), first_mgr)
        self.assertLess(order.index("reddit_fetcher"), first_mgr)

    def test_delegation_loop_ran_three_iterations(self) -> None:
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        sa = result.nodes["sentiment_analysis"].output
        self.assertEqual(len(sa["iterations"]), 3, sa)
        # Iter 1 → 2 workers, iter 2 → 1 worker, iter 3 → 0 workers (COMPLETE)
        self.assertEqual(len(sa["iterations"][0]["workers"]), 2)
        self.assertEqual(len(sa["iterations"][1]["workers"]), 1)
        self.assertEqual(len(sa["iterations"][2]["workers"]), 0)
        self.assertEqual(sa["workers_spawned"], 3)
        self.assertEqual(sa["terminal"]["state"], "complete")

    def test_delegation_loop_terminal_carries_score(self) -> None:
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        sa = result.nodes["sentiment_analysis"].output
        score = sa["result"]["score"]
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_reporter_sees_share_output_projection(self) -> None:
        """The sentiment_analysis node's share_output is [score, confidence,
        news, reddit, contradiction] — the reporter sees ONLY those keys
        in state, not the full iterations dump."""
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        # State projection that the reporter saw
        projection = result.final_state["sentiment_analysis"]
        self.assertEqual(
            set(projection.keys()),
            {"score", "confidence", "news", "reddit", "contradiction"},
            f"share_output projection drifted: {projection!r}",
        )
        # Iterations / terminal are NOT in the projection (would leak ballast)
        self.assertNotIn("iterations", projection)
        self.assertNotIn("terminal", projection)

    def test_reporter_output_carries_ticker(self) -> None:
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        report = result.nodes["reporter"].output["report_text"]
        self.assertIn("NVDA", report)
        self.assertIn("Aggregate score:", report)

    def test_audit_chain_has_terminal(self) -> None:
        result = self.runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        kinds = [a["event"] for a in result.audit]
        # Audit shape: run.started, run.level*, node.started/completed*, node.engine_call*, ...
        self.assertEqual(kinds[0], "run.started")
        self.assertEqual(kinds[-1], "run.terminal")
        # Exactly one delegation-loop iteration audit per loop run
        delegation_iters = [
            a for a in result.audit if a["event"] == "node.delegation_iteration"
        ]
        self.assertEqual(len(delegation_iters), 3)
        # And the iteration counter is monotonic
        self.assertEqual(
            [a["iteration"] for a in delegation_iters],
            [1, 2, 3],
        )

    def test_budget_caps_workers(self) -> None:
        """Drop max_total_workers to 2 — the delegation loop must abort with
        terminal.state == 'partial' instead of spawning a third worker."""
        doc = load_workflow(WORKFLOW_PATH)
        for n in doc.graph:
            if n["id"] == "sentiment_analysis":
                n["config"]["budget"]["max_total_workers"] = 2
        validate(doc)
        engine = build_stub_engine()
        runner = DAGRunner(doc, engine=engine)
        result = runner.run(inputs={"ticker": "NVDA", "window_days": 7})
        sa = result.nodes["sentiment_analysis"].output
        self.assertEqual(sa["workers_spawned"], 2)
        self.assertEqual(sa["terminal"]["state"], "partial")
        self.assertIn("max_total_workers", sa["terminal"]["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
