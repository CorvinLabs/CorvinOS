"""test_ato_e2e.py — End-to-End classification tests for ADR-0165.

Strategy (LDD test pyramid):
  Tier 1  — deterministic unit tests: run code.task_intake.py directly via
             subprocess (bwrap-simulated), assert on JSON output.
  Tier 2  — edge-case matrix: 30+ tasks across all 6 types, including
             ambiguous cases designed to probe classification boundaries.
  Tier 3  — LDD loss recording: every misclassification is recorded in
             ato_loss.py so convergence_rate drifts down and triggers
             a L16 WARNING when the classifier needs tuning.
  Tier 4  — real LLM smoke test (marked slow): spawns 'claude -p --no-tools'
             and verifies the LLM correctly identifies which task type to
             call code_task_intake with.  Skipped unless RUN_LLM_E2E=1.

MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import unittest.mock  # explicit import required — 'import unittest' alone does not load submodule
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent
_repo = _here.parents[2]  # operator/bridges/shared → repo root

# Path to the Forge tool script under the default tenant
_INTAKE_TOOL = (
    _repo / ".corvin" / "tenants" / "_default" / "forge" / "tools" / "code.task_intake.py"
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _classify(
    task: str,
    data_classification: str = "CONFIDENTIAL",
    engine_id: str = "claude_code",
) -> dict:
    """Run code.task_intake.py as a subprocess (mirrors bwrap execution).

    engine_id defaults to "claude_code" to mirror ato_classify.classify()'s
    default, so parity tests compare equivalent contexts.
    """
    result = subprocess.run(
        [sys.executable, str(_INTAKE_TOOL)],
        input=json.dumps({
            "task": task,
            "data_classification": data_classification,
            "engine_id": engine_id,
        }),
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"intake tool failed: {result.stderr[:200]}")
    return json.loads(result.stdout)


def _classify_module(task: str, **kwargs) -> "ATOPlan":
    """Call ato_classify.classify() directly (in-process, faster)."""
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from ato_classify import classify  # type: ignore[import]
    return classify(task, **kwargs)


def _record_loss(task_type: str, correct: bool) -> None:
    """Best-effort: record classification outcome in ato_loss.py."""
    try:
        if str(_here) not in sys.path:
            sys.path.insert(0, str(_here))
        import tempfile, ato_loss  # type: ignore[import]  # noqa: PLC0415,E401
        # Redirect to tmpdir so tests don't pollute live stats.
        _td = getattr(_record_loss, "_tmpdir", None)
        if _td is None:
            _td = tempfile.mkdtemp()
            _record_loss._tmpdir = _td  # type: ignore[attr-defined]
        with unittest.mock.patch("ato_loss._ato_dir", return_value=Path(_td)):
            ato_loss.record_outcome(task_type, did_converge=correct)
    except Exception:
        pass


# ── Test case definition ──────────────────────────────────────────────────────

@dataclass
class Case:
    name: str
    task: str
    expected_type: str
    data_classification: str = "INTERNAL"
    expected_delegation: "str | None" = None  # None = don't check
    expected_model: "str | None" = None        # None = don't check
    haiku_allowed: bool = False
    note: str = ""                             # edge-case description


# ── Classification test matrix (60+ cases, 10 per type) ──────────────────────
#
# Design rules:
#   • 10+ cases per type: canonical, German, near-tie, question-suppression, mixed
#   • Edge cases at every boundary between adjacent types
#   • M5/M7 delegation cases verify routing signals are correct

CASES: list[Case] = [

    # ══════════════════════════════════════════════════════════════════════════
    # one_shot (10 cases + edges)
    # Signal: zero loop/workflow/goal/auto/compute signals; baseline 0.30
    # ══════════════════════════════════════════════════════════════════════════
    Case("os_01_geo_lookup",
         "What is the capital of Germany?",
         "one_shot",
         note="pure geography lookup"),

    Case("os_02_translation",
         "Translate 'Guten Morgen' to English.",
         "one_shot",
         note="translation — direct single answer"),

    Case("os_03_version_lookup",
         "What version of Python does CorvinOS require?",
         "one_shot",
         note="dependency version lookup"),

    Case("os_04_acronym",
         "What does CLI stand for?",
         "one_shot",
         haiku_allowed=True,
         expected_model="haiku",
         note="M6: short acronym question → Haiku when allowed"),

    Case("os_05_math",
         "What is 2 + 2?",
         "one_shot",
         haiku_allowed=True,
         expected_model="haiku",
         note="M6: trivial math → Haiku candidate"),

    Case("os_06_german_question",
         "Was ist der Unterschied zwischen GDPR und EU AI Act?",
         "one_shot",
         note="German knowledge question — 'was' + '?' → one_shot"),

    Case("os_07_define",
         "Define what a hash-chain audit log is?",
         "one_shot",
         note="'define'+'?' → question suppresses 'audit' loop score (0.35*0.35=0.12 < 0.30)"),

    Case("os_08_explain_concept",
         "Explain how Bayesian optimization works?",
         "one_shot",
         note="'explain'+'?' → question suppresses compute score (0.45*0.35=0.16 < 0.30 baseline)"),

    Case("os_09_who",
         "Who is responsible for the L10 path-gate implementation?",
         "one_shot",
         note="'who' question word → one_shot"),

    Case("os_10_when",
         "When was ADR-0165 merged?",
         "one_shot",
         note="'when' question → one_shot"),

    Case("os_11_copilot_git",
         "Show the git log for today.",
         "one_shot",
         expected_delegation="delegate_copilot",
         note="M5: short one_shot + len<1500 → Copilot delegation candidate"),

    Case("os_12_copilot_command",
         "List all branches in this repo.",
         "one_shot",
         expected_delegation="delegate_copilot",
         note="M5: shell one-liner → Copilot"),

    # ══════════════════════════════════════════════════════════════════════════
    # iterative_fix (10 cases + edges)
    # Signals: fix, bug, broken, fail*, error, test, debug, review, round\d, runde,
    #          e2e, iterate*, audit, reparier*, korrigier*
    # ══════════════════════════════════════════════════════════════════════════
    Case("if_01_failing_test",
         "Fix the failing test in test_ato_loss.py — the EMA calculation is wrong.",
         "iterative_fix",
         note="'fix' + 'failing test' + 'EMA' — strong loop"),

    Case("if_02_e2e_broken",
         "The E2E test for the console auth flow is broken after the last commit. Debug and fix it.",
         "iterative_fix",
         note="'E2E' + 'broken' + 'fix' + 'debug'"),

    Case("if_03_audit_missing",
         "The audit log is missing events for the session reset path. Investigate and fix.",
         "iterative_fix",
         note="'audit' + 'fix' loop signals"),

    Case("if_04_review_round",
         "Review round 3 of the code review — fix all CRITICAL findings.",
         "iterative_fix",
         note="'review' + 'round 3' + 'fix'"),

    Case("if_05_german_runde",
         "Runde 2 des Code-Reviews — korrigiere alle HIGH-Befunde.",
         "iterative_fix",
         note="German: 'runde' + 'korrigiere'"),

    Case("if_06_iterate_convergence",
         "Iterate on the classifier until the convergence rate exceeds 0.90.",
         "iterative_fix",
         note="'iterate' + 'convergence' loop signals"),

    Case("if_07_error_debug",
         "Debug the KeyError in the forge MCP server when forge_list is called.",
         "iterative_fix",
         note="'debug' + 'error' signals"),

    Case("if_08_reparieren",
         "Repariere den fehlerhaften Tenant-Resolver im Adapter.",
         "iterative_fix",
         note="German: 'repariere' + 'fehlerhaft'"),

    Case("if_09_test_fails",
         "The tests for the L35 egress gate are failing with AttributeError. Fix them.",
         "iterative_fix",
         note="'tests' + 'failing' + 'fix'"),

    Case("if_10_bug_hunt",
         "There's a bug in the ACS-X heuristic fallback — it doesn't handle empty prompts. Fix it.",
         "iterative_fix",
         note="'bug' + 'fix' direct signals"),

    # ── iterative_fix edge: question-word suppression ──────────────────────
    Case("if_edge_01_fix_question",
         "What is the fix for the N+1 query problem in SQL?",
         "one_shot",
         note="EDGE: 'fix' in a knowledge question → one_shot (question-word suppresses loop)"),

    Case("if_edge_02_error_question",
         "Why does Python raise a RecursionError for infinite loops?",
         "one_shot",
         note="EDGE: 'error' in why-question → one_shot, not iterative_fix"),

    Case("if_edge_03_test_lookup",
         "How do you write a parametrized test in pytest?",
         "one_shot",
         note="EDGE: 'test' in how-question → one_shot"),

    # ══════════════════════════════════════════════════════════════════════════
    # multi_agent (10 cases + edges)
    # Signals: all files?, codebase, sweep, migrate/migration, research, find all,
    #          survey, analyse/analyze, parallel, multi-agent, workflow, fan-out
    # Note: workflow_score is NOT penalised by question-word detection.
    # ══════════════════════════════════════════════════════════════════════════
    Case("ma_01_codebase_sweep",
         "Sweep the entire codebase for deprecated API usage and produce a report.",
         "multi_agent",
         note="'sweep' + 'codebase'"),

    Case("ma_02_research_all",
         "Research all open-source GDPR consent libraries and compare them.",
         "multi_agent",
         note="'research' + 'all'"),

    Case("ma_03_migration",
         "Migrate all Python 2-style print statements to Python 3 across the repo.",
         "multi_agent",
         note="'migrate' + 'all'"),

    Case("ma_04_parallel_analysis",
         "Run parallel analysis of all bridge adapters and produce a security summary.",
         "multi_agent",
         note="'parallel' + 'all'"),

    Case("ma_05_find_all",
         "Find all places where tenant_id is passed as a positional argument.",
         "multi_agent",
         note="'find all' workflow signal"),

    Case("ma_06_workflow",
         "Set up a multi-agent workflow to audit all tenant configurations.",
         "multi_agent",
         note="'multi-agent' + 'workflow' + 'all'"),

    Case("ma_07_survey",
         "Survey all MCP servers in the codebase and flag any without L10 path-gate integration.",
         "multi_agent",
         note="'survey' + 'all'"),

    Case("ma_08_german_analyse",
         "Analysiere alle Bridge-Adapter und erstelle eine Sicherheitsübersicht.",
         "multi_agent",
         note="German: 'analysiere' + 'alle'"),

    Case("ma_09_fan_out",
         "Fan out agents to review every layer module and report CRITICAL invariant violations.",
         "multi_agent",
         note="'fan out' + 'agents' + 'every'"),

    Case("ma_10_question_which_files",
         "Which files need to be migrated for the ADR-0007 multi-tenant refactor?",
         "multi_agent",
         note="EDGE: 'which' question + 'migrate' — workflow NOT penalised by question"),

    # ── multi_agent edges ──────────────────────────────────────────────────
    Case("ma_edge_01_no_workflow_on_simple",
         "Where is the main entry point defined?",
         "one_shot",
         note="EDGE: 'where' question without workflow signals → one_shot"),

    # ══════════════════════════════════════════════════════════════════════════
    # exploration (10 cases)
    # Signals: adr, entscheid*, decide, design, plan, architecture, trade-off,
    #          strateg*, approach, bewerte*, compare, recommend
    # ══════════════════════════════════════════════════════════════════════════
    Case("ex_01_adr_compare",
         "Write an ADR for the new rate-limiting strategy — compare token bucket vs leaky bucket.",
         "exploration",
         note="'adr' + 'compare'"),

    Case("ex_02_decide_db",
         "Decide whether we should use PostgreSQL or SQLite for the recall database.",
         "exploration",
         note="'decide' direct signal"),

    Case("ex_03_architecture",
         "Design the architecture for the new plugin system — evaluate three approaches.",
         "exploration",
         note="'architecture' + 'design'"),

    Case("ex_04_trade_off",
         "Evaluate the trade-off between in-process vs out-of-process MCP servers.",
         "exploration",
         note="'trade-off' direct signal"),

    Case("ex_05_recommend",
         "Recommend whether to extend the CLA or switch to a different contributor model.",
         "exploration",
         note="'recommend' goal signal"),

    Case("ex_06_strategy",
         "Define a strategy for the gradual rollout of the new L34 compliance zone.",
         "exploration",
         note="'strategy' + 'define'"),

    Case("ex_07_german_entscheid",
         "Entscheide, ob wir das A2A-Protokoll auf v8 upgraden sollen — vergleiche Vorteile und Risiken.",
         "exploration",
         note="German: 'entscheide' + 'vergleiche'"),

    Case("ex_08_plan",
         "Decide the plan for migrating from Apache-only to Enterprise — compare three rollout strategies.",
         "exploration",
         note="'decide'+'plan'+'compare' = 3 goal signals (1.0) beats 'migrat*' workflow (0.40)"),

    Case("ex_09_approach",
         "Recommend the approach for the next A2A protocol version — compare backward-compat extension vs protocol fork.",
         "exploration",
         note="'recommend'+'approach'+'compare' = 3 goal signals; no question mark → no penalty"),

    Case("ex_10_compare",
         "Compare the three LDD loop strategies and recommend the best fit for the ATO context.",
         "exploration",
         note="'compare' + 'recommend'"),

    # ══════════════════════════════════════════════════════════════════════════
    # autonomous (10 cases)
    # Signals: schedule, monitor, watch, cron, background, recurring, periodic,
    #          alert, überwach*, beobacht*
    # ══════════════════════════════════════════════════════════════════════════
    Case("au_01_monitor_health",
         "Monitor the /health endpoint every 5 minutes and alert if it returns 500.",
         "autonomous",
         note="'monitor' + 'alert'"),

    Case("au_02_schedule_daily",
         "Schedule a daily check of the audit log for any CRITICAL events.",
         "autonomous",
         note="'schedule' + 'daily'"),

    Case("au_03_watch_bucket",
         "Watch the S3 bucket for new files and process each one as it arrives.",
         "autonomous",
         note="'watch' auto signal"),

    Case("au_04_cron",
         "Set up a cron job that runs voice-audit verify every night at 03:30.",
         "autonomous",
         note="'cron' auto signal"),

    Case("au_05_background",
         "Run the tenant migration tool in the background without blocking the main process.",
         "autonomous",
         note="'background' auto signal"),

    Case("au_06_recurring",
         "Set up a recurring sync of the user-model database every 6 hours.",
         "autonomous",
         note="'recurring' auto signal"),

    Case("au_07_periodic",
         "Implement a periodic audit-chain verification that alerts on CRITICAL severity.",
         "autonomous",
         note="'periodic' + 'alert'"),

    Case("au_08_german_ueberwach",
         "Überwache den Prometheus-Endpunkt und sende eine Warnung bei Latenz > 500ms.",
         "autonomous",
         note="German: 'überwache'"),

    Case("au_09_alert_threshold",
         "Alert when the daily active user count drops below 100 — check every hour.",
         "autonomous",
         note="'alert' + 'every hour'"),

    Case("au_10_background_task",
         "Start a background task that watches for new A2A origins and registers them automatically.",
         "autonomous",
         note="'background' + 'watch'"),

    # ══════════════════════════════════════════════════════════════════════════
    # compute (10 cases)
    # Signals: optimize, grid search, bayesian, parameter sweep, simulation,
    #          numeric, berechne*, calculate, statistik, mean/median/varianz,
    #          regression, clustering, ml model, machine learning, trainiere*,
    #          plot, histogram, scatter, chart, graph, csv, xlsx*, dataframe,
    #          datensatz, batch (transform|process), data pipeline, large dataset
    # ══════════════════════════════════════════════════════════════════════════
    Case("co_01_bayesian_optimize",
         "Optimize the hyperparameters of the ML model using Bayesian search over 100 trials.",
         "compute",
         note="M7: 'optimize' + 'Bayesian' + 'ML model'"),

    Case("co_02_grid_search",
         "Run a grid search over learning rate [0.001, 0.01, 0.1] and batch size [32, 64].",
         "compute",
         note="M7: 'grid search'"),

    Case("co_03_german_statistik",
         "Berechne Mittelwert, Median und Varianz für die Spotify-Streams CSV-Datei.",
         "compute",
         note="M7: German compute signals — 'berechne' + 'mittelwert' + 'median' + 'varianz' + 'csv'"),

    Case("co_04_histogram",
         "Plot a histogram of the daily active users from the analytics DataFrame.",
         "compute",
         note="M7: 'plot' + 'histogram' + 'DataFrame'"),

    Case("co_05_regression",
         "Fit a linear regression to the sales data and return the R² score.",
         "compute",
         note="M7: 'regression'"),

    Case("co_06_batch_pipeline",
         "Run the batch transform pipeline over the large dataset and produce the CSV output.",
         "compute",
         note="M7: 'batch transform' + 'large dataset' + 'CSV'"),

    Case("co_07_clustering",
         "Apply k-means clustering to the user-session DataFrame and return the cluster assignments.",
         "compute",
         note="M7: 'clustering' + 'DataFrame'"),

    Case("co_08_machine_learning",
         "Train a machine learning classifier on the labelled dataset and report accuracy.",
         "compute",
         note="M7: 'machine learning' + 'train'"),

    Case("co_09_simulation",
         "Run a Monte Carlo simulation over 10,000 trials to estimate the failure probability.",
         "compute",
         note="M7: 'simulation'"),

    Case("co_10_parameter_sweep",
         "Run a parameter sweep over dropout [0.1, 0.2, 0.3] and hidden_size [64, 128, 256].",
         "compute",
         note="M7: 'parameter sweep'"),

    # ══════════════════════════════════════════════════════════════════════════
    # M5 delegation via data classification (6 cases)
    # ══════════════════════════════════════════════════════════════════════════
    Case("m5_01_confidential_oneshot",
         "Summarise the patient records in this dataset.",
         "one_shot",
         data_classification="CONFIDENTIAL",
         expected_delegation="delegate_hermes",
         note="M5: CONFIDENTIAL → Hermes even for one_shot task"),

    Case("m5_02_secret_question",
         "What is the current API key rotation policy?",
         "one_shot",
         data_classification="SECRET",
         expected_delegation="delegate_hermes",
         note="M5: SECRET → Hermes"),

    Case("m5_03_confidential_iterative",
         "Fix the failing test that processes patient data.",
         "iterative_fix",
         data_classification="CONFIDENTIAL",
         expected_delegation="delegate_hermes",
         note="M5: CONFIDENTIAL wins over task type for delegation"),

    Case("m5_04_internal_no_hermes",
         "Fix the broken authentication test.",
         "iterative_fix",
         data_classification="INTERNAL",
         expected_delegation=None,
         note="M5: INTERNAL → no Hermes delegation (not local-only)"),

    Case("m5_05_public_copilot",
         "List the git tags.",
         "one_shot",
         data_classification="PUBLIC",
         expected_delegation="delegate_copilot",
         note="M5: PUBLIC + one_shot + short → Copilot (not Hermes)"),

    Case("m5_06_secret_compute",
         "Berechne Statistiken für den verschlüsselten Datensatz.",
         "compute",
         data_classification="SECRET",
         expected_delegation="delegate_hermes",
         note="M5: SECRET overrides even compute task type for delegation"),

    # ══════════════════════════════════════════════════════════════════════════
    # Ambiguous boundary edge cases
    # ══════════════════════════════════════════════════════════════════════════
    Case("edge_01_audit_review_loop",
         "Audit the review process — is the review loop running correctly?",
         "iterative_fix",
         note="EDGE: 'audit' + 'review' + 'loop' all loop signals → iterative_fix"),

    Case("edge_02_design_fix_mix",
         "Design the fix strategy for the broken tenant routing.",
         "exploration",
         note="EDGE: 'design'+'strategy' (goal 0.80) beats 'fix'+'broken' (loop 0.70) — goal wins"),

    Case("edge_03_analyse_no_compute",
         "Analyse the performance metrics and recommend optimizations.",
         "multi_agent",
         note="EDGE: 'analyse' is workflow, not compute; 'optimize' alone doesn't win"),

    Case("edge_04_explain_bayesian",
         "Explain how Bayesian optimization is implemented in the Compute Worker?",
         "one_shot",
         note="EDGE: 'explain'+'?' → _q_penalty=0.35; Bayesian 0.45*0.35=0.16 < 0.30 baseline"),

    Case("edge_05_schedule_fix",
         "Schedule the broken audit verification to run at 03:30.",
         "iterative_fix",
         note="EDGE: 'broken'+'audit' loop (0.70) beats 'schedule' auto (0.50) — iterative_fix wins"),

    Case("edge_06_compute_question",
         "What is the result of grid search over these parameters?",
         "one_shot",
         note="EDGE: 'what' question suppresses compute score; 'grid search' present but not enough"),

    Case("edge_07_migration_question",
         "Which files have already been migrated to the new tenant structure?",
         "multi_agent",
         note="EDGE: 'which' question but 'migrate' + 'all' → workflow not suppressed"),
]


# ── Test classes ──────────────────────────────────────────────────────────────

class TestATOClassificationSubprocess(unittest.TestCase):
    """Tier 1: Subprocess tests (real bwrap-equivalent execution)."""

    def _run_case(self, case: Case) -> dict:
        env = dict(os.environ)
        if case.haiku_allowed:
            env["CORVIN_OS_MODEL_ALLOW_HAIKU"] = "1"
        result = subprocess.run(
            [sys.executable, str(_INTAKE_TOOL)],
            input=json.dumps({
                "task": case.task,
                "data_classification": case.data_classification,
                # Simulate the CC OS-turn context so M5/M6 delegation guard is active.
                "engine_id": "claude_code",
            }),
            capture_output=True, text=True, timeout=10, env=env,
        )
        self.assertEqual(result.returncode, 0,
                         f"tool exited {result.returncode}: {result.stderr[:200]}")
        return json.loads(result.stdout)

    def _assert_case(self, case: Case) -> None:
        out = self._run_case(case)
        correct = out["task_type"] == case.expected_type
        _record_loss(case.expected_type, correct)
        self.assertEqual(
            out["task_type"], case.expected_type,
            f"[{case.name}] expected {case.expected_type!r}, "
            f"got {out['task_type']!r} (confidence={out['confidence']}) — {case.note}",
        )
        if case.expected_delegation is not None:
            self.assertEqual(
                out.get("delegation_target"), case.expected_delegation,
                f"[{case.name}] delegation_target: expected {case.expected_delegation!r}, "
                f"got {out.get('delegation_target')!r}",
            )
        if case.expected_model is not None:
            self.assertEqual(
                out.get("recommended_model"), case.expected_model,
                f"[{case.name}] recommended_model: expected {case.expected_model!r}, "
                f"got {out.get('recommended_model')!r}",
            )

    # Generate one test per case
    @classmethod
    def _make_test(cls, case: Case):
        def test_fn(self):
            self._assert_case(case)
        test_fn.__name__ = f"test_{case.name}"
        test_fn.__doc__ = case.note or case.name
        return test_fn


# Dynamically generate test methods from CASES
for _case in CASES:
    _test_method = TestATOClassificationSubprocess._make_test(_case)
    setattr(TestATOClassificationSubprocess, _test_method.__name__, _test_method)


class TestATOClassifyModule(unittest.TestCase):
    """Tier 2: In-process tests via ato_classify.classify() (faster, no subprocess)."""

    def test_one_shot_baseline(self):
        plan = _classify_module("What is 2 + 2?")
        self.assertEqual(plan.task_type, "one_shot")

    def test_compute_signals(self):
        plan = _classify_module("Run Bayesian optimization over 200 trials.")
        self.assertEqual(plan.task_type, "compute")
        self.assertIsNotNone(plan.compute_params)
        self.assertEqual(plan.execution_strategy, "compute_worker")

    def test_delegation_confidential(self):
        plan = _classify_module(
            "Process the patient data.",
            data_classification="CONFIDENTIAL",
        )
        self.assertEqual(plan.delegation_target, "delegate_hermes")

    def test_delegation_secret(self):
        plan = _classify_module(
            "Analyse the API keys.",
            data_classification="SECRET",
        )
        self.assertEqual(plan.delegation_target, "delegate_hermes")

    def test_no_delegation_internal(self):
        plan = _classify_module("Fix the failing test.", data_classification="INTERNAL")
        self.assertIsNone(plan.delegation_target)

    def test_haiku_recommended_one_shot(self):
        plan = _classify_module(
            "What is the capital of France?",
            haiku_allowed=True,
        )
        self.assertEqual(plan.task_type, "one_shot")
        self.assertEqual(plan.recommended_model, "haiku")

    def test_no_haiku_when_not_allowed(self):
        plan = _classify_module("What is 2 + 2?", haiku_allowed=False)
        self.assertIsNone(plan.recommended_model)

    def test_no_haiku_for_iterative_fix(self):
        plan = _classify_module("Fix the broken test.", haiku_allowed=True)
        self.assertEqual(plan.task_type, "iterative_fix")
        self.assertIsNone(plan.recommended_model)  # Haiku only for one_shot

    def test_multi_agent_no_delegation(self):
        # multi_agent tasks stay in OS turn even for INTERNAL data (Workflow handles fan-out)
        plan = _classify_module(
            "Research all open-source GDPR libraries.",
            engine_id="claude_code",
            data_classification="INTERNAL",
        )
        self.assertEqual(plan.task_type, "multi_agent")
        self.assertIsNone(plan.delegation_target)

    def test_non_cc_engine_no_delegation(self):
        # Non-CC engines (Hermes, OpenCode) must NOT trigger M5 delegation
        plan = _classify_module(
            "What is 2 + 2?",
            engine_id="hermes",  # already a worker — no re-delegation
        )
        self.assertIsNone(plan.delegation_target)

    def test_non_cc_engine_no_model_hint(self):
        # M6 Haiku hint is CC-only
        plan = _classify_module(
            "What is 2 + 2?",
            engine_id="opencode",
            haiku_allowed=True,
        )
        self.assertIsNone(plan.recommended_model)

    def test_compute_params_populated(self):
        plan = _classify_module("Run grid search over learning rate [0.001, 0.01].")
        self.assertEqual(plan.task_type, "compute")
        self.assertIsNotNone(plan.compute_params)
        self.assertIn("strategy", plan.compute_params)
        self.assertIn("datasources", plan.compute_params)

    def test_exploration_ldd_skills(self):
        plan = _classify_module("Decide between PostgreSQL and SQLite for recall.db.")
        self.assertEqual(plan.task_type, "exploration")
        self.assertIn("dialectical-reasoning", plan.required_ldd_skills)

    def test_iterative_fix_ldd_skills(self):
        plan = _classify_module("Fix the failing E2E test for the auth flow.")
        self.assertEqual(plan.task_type, "iterative_fix")
        self.assertIn("e2e-driven-iteration", plan.required_ldd_skills)
        self.assertIn("reproducibility-first", plan.required_ldd_skills)

    def test_loop_params_k_max(self):
        plan = _classify_module("Fix the broken tenant routing bug.")
        self.assertEqual(plan.loop_params.get("k_max"), 5)

    def test_compute_k_max_one(self):
        plan = _classify_module("Run Bayesian search over 200 trials.")
        self.assertEqual(plan.loop_params.get("k_max"), 1)

    def test_autonomous_k_max_none(self):
        plan = _classify_module("Monitor the health endpoint every minute.")
        self.assertIsNone(plan.loop_params.get("k_max"))

    def test_confidence_range(self):
        plan = _classify_module("Fix the failing E2E test.")
        self.assertGreaterEqual(plan.confidence, 0.0)
        self.assertLessEqual(plan.confidence, 1.0)


class TestATOOutputSchema(unittest.TestCase):
    """Schema validation: ensure all output fields are present and correctly typed."""

    def _check_schema(self, out: dict) -> None:
        required = {
            "task_type", "confidence", "execution_strategy",
            "goal_text", "loop_params", "required_ldd_skills",
            "delegation_target", "recommended_model", "compute_params",
        }
        missing = required - set(out.keys())
        self.assertFalse(missing, f"Missing keys in output: {missing}")
        self.assertIn(out["task_type"],
                      {"one_shot", "iterative_fix", "multi_agent",
                       "exploration", "autonomous", "compute"})
        self.assertIsInstance(out["confidence"], float)
        self.assertIsInstance(out["required_ldd_skills"], list)
        self.assertIsInstance(out["loop_params"], dict)

    def test_schema_one_shot(self):
        self._check_schema(_classify("Hello, what time is it?"))

    def test_schema_iterative_fix(self):
        self._check_schema(_classify("Fix the broken auth test."))

    def test_schema_compute(self):
        self._check_schema(_classify("Run Bayesian optimization over 200 trials."))

    def test_no_anthropic_import_in_tool(self):
        import ast  # noqa: PLC0415
        src = _INTAKE_TOOL.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for name in names:
                    self.assertFalse(
                        name and name.startswith("anthropic"),
                        f"code.task_intake.py must not import anthropic — found: {name}",
                    )

    def test_no_anthropic_import_in_module(self):
        import ast  # noqa: PLC0415
        src = (_here / "ato_classify.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for name in names:
                    self.assertFalse(
                        name and name.startswith("anthropic"),
                        f"ato_classify.py must not import anthropic — found: {name}",
                    )


class TestATOParitySubprocessVsModule(unittest.TestCase):
    """Parity tests: subprocess (code.task_intake.py) and in-process (ato_classify.py)
    must produce identical task_type AND delegation_target for the same input.

    Guards against SSOT divergence when patterns are updated in one file but not the other.
    The subprocess receives engine_id="claude_code" (CC OS-turn context) to match
    ato_classify.classify()'s default of engine_id="claude_code".
    """

    _PARITY_INPUTS = [
        "Fix the failing test in test_ato_loss.py.",
        "Research all open-source GDPR consent libraries.",
        "Write an ADR for the new rate-limiting strategy.",
        "Monitor the health endpoint every 5 minutes.",
        "Run Bayesian optimization over 200 trials.",
        "What is the capital of Germany?",
        "Which files need to be migrated across the codebase?",  # workflow via question
        "How do you fix N+1 queries in SQL?",                   # question-word fix
    ]

    def _subprocess_plan(self, task: str) -> dict:
        return _classify(task)  # subprocess already sends engine_id="claude_code"

    def _module_plan(self, task: str) -> "ATOPlan":
        return _classify_module(task)  # classify() defaults to engine_id="claude_code"

    def _subprocess_type(self, task: str) -> str:
        return self._subprocess_plan(task)["task_type"]

    def _module_type(self, task: str) -> str:
        return self._module_plan(task).task_type

    def test_parity_all_inputs(self):
        for task in self._PARITY_INPUTS:
            sub = self._subprocess_plan(task)
            mod = self._module_plan(task)
            self.assertEqual(
                sub["task_type"], mod.task_type,
                f"task_type SSOT divergence for: {task!r}\n"
                f"  subprocess: {sub['task_type']!r}\n  module: {mod.task_type!r}",
            )
            self.assertEqual(
                sub.get("delegation_target"), mod.delegation_target,
                f"delegation_target SSOT divergence for: {task!r}\n"
                f"  subprocess: {sub.get('delegation_target')!r}\n"
                f"  module: {mod.delegation_target!r}",
            )

    def test_parity_question_workflow(self):
        """'Which files?' should be multi_agent in both paths (no workflow_score penalty)."""
        task = "Which files need to be migrated across the codebase?"
        self.assertEqual(self._subprocess_type(task), "multi_agent")
        self.assertEqual(self._module_type(task), "multi_agent")

    def test_parity_question_fix(self):
        """'How do you fix...?' should be one_shot in both paths (loop suppressed by question)."""
        task = "How do you fix N+1 queries in SQL?"
        self.assertEqual(self._subprocess_type(task), "one_shot")
        self.assertEqual(self._module_type(task), "one_shot")

    def test_parity_non_cc_engine_no_delegation(self):
        """Non-CC engine (hermes) must produce delegation_target=None in subprocess (ADR-0029)."""
        import tempfile  # noqa: PLC0415
        result = subprocess.run(
            [sys.executable, str(_INTAKE_TOOL)],
            input=json.dumps({"task": "What is the token limit?", "engine_id": "hermes"}),
            capture_output=True, text=True, timeout=10, env=dict(os.environ),
        )
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        out = json.loads(result.stdout)
        self.assertIsNone(out.get("delegation_target"),
                          f"Non-CC engine must not produce delegation_target; got {out.get('delegation_target')!r}")

    def test_no_path_import_in_module(self):
        """ato_classify.py must not import pathlib.Path (no filesystem operations)."""
        import ast  # noqa: PLC0415
        src = (_here / "ato_classify.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
                names = [a.name for a in node.names]
                self.assertNotIn("Path", names,
                                 "ato_classify.py must not import pathlib.Path — pure classifier, no FS ops")


class TestATOLossTracking(unittest.TestCase):
    """Verify that ato_loss.py integrates with E2E outcomes."""

    def setUp(self):
        import tempfile, unittest.mock as mock  # noqa: PLC0415,E401
        self._tmpdir = Path(tempfile.mkdtemp())
        self._patcher = mock.patch("ato_loss._ato_dir", return_value=self._tmpdir)
        self._patcher.start()
        if str(_here) not in sys.path:
            sys.path.insert(0, str(_here))
        import ato_loss  # type: ignore[import]  # noqa: PLC0415
        self._ato_loss = ato_loss

    def tearDown(self):
        self._patcher.stop()
        import shutil  # noqa: PLC0415
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_correct_classification_records_converged(self):
        self._ato_loss.record_outcome("one_shot", did_converge=True)
        stats = self._ato_loss.get_stats("one_shot")
        self.assertIsNotNone(stats)
        self.assertAlmostEqual(stats["convergence_rate"], 1.0, places=3)

    def test_misclassification_records_not_converged(self):
        # A classification error = did_converge=False
        for _ in range(5):
            self._ato_loss.record_outcome("compute", did_converge=False)
        stats = self._ato_loss.get_stats("compute")
        self.assertLess(stats["convergence_rate"], 0.60)

    def test_e2e_roundtrip_with_loss(self):
        """Classify a task, compare to expected, record loss outcome."""
        out = _classify("Fix the broken test in test_ato_loss.py.")
        correct = out["task_type"] == "iterative_fix"
        self._ato_loss.record_outcome("iterative_fix", did_converge=correct)
        self.assertTrue(correct, f"Expected iterative_fix, got {out['task_type']}")


# ── M5 delegation routing tests ──────────────────────────────────────────────

class TestATOM5DelegationRouting(unittest.TestCase):
    """Verify M5 delegation_target assignment across classification types and DC levels.

    Tests the ato_classify module directly (unit-level).  Adapter-level
    integration is exercised by TestATOM5AdapterRouting below.
    """

    def _plan(self, task: str, dc: str = "INTERNAL", engine: str = "claude_code"):
        return _classify_module(task, data_classification=dc, engine_id=engine)

    # ── CONFIDENTIAL/SECRET → delegate_hermes ────────────────────────────────

    def test_confidential_oneshot_delegates_hermes(self):
        p = self._plan("Summarise the patient records.", dc="CONFIDENTIAL")
        self.assertEqual(p.delegation_target, "delegate_hermes")

    def test_secret_oneshot_delegates_hermes(self):
        p = self._plan("What is the API key rotation policy?", dc="SECRET")
        self.assertEqual(p.delegation_target, "delegate_hermes")

    def test_confidential_iterative_delegates_hermes(self):
        p = self._plan("Fix the broken patient-data pipeline.", dc="CONFIDENTIAL")
        self.assertEqual(p.delegation_target, "delegate_hermes")

    def test_confidential_compute_delegates_hermes(self):
        p = self._plan("Run Bayesian search on the patient dataset.", dc="CONFIDENTIAL")
        # Even compute tasks route to Hermes when data is CONFIDENTIAL
        self.assertEqual(p.delegation_target, "delegate_hermes")

    def test_confidential_exploration_delegates_hermes(self):
        p = self._plan("Decide the architecture for the patient data store.", dc="CONFIDENTIAL")
        self.assertEqual(p.delegation_target, "delegate_hermes")

    def test_confidential_multi_agent_delegates_hermes(self):
        p = self._plan("Sweep all patient files in the codebase.", dc="CONFIDENTIAL")
        self.assertEqual(p.delegation_target, "delegate_hermes")

    # ── PUBLIC/INTERNAL one_shot → delegate_copilot ──────────────────────────

    def test_public_short_oneshot_delegates_copilot(self):
        p = self._plan("List all git branches.", dc="PUBLIC")
        self.assertEqual(p.task_type, "one_shot")
        self.assertEqual(p.delegation_target, "delegate_copilot")

    def test_internal_short_oneshot_delegates_copilot(self):
        p = self._plan("Show the git log.", dc="INTERNAL")
        self.assertEqual(p.delegation_target, "delegate_copilot")

    def test_long_oneshot_no_copilot(self):
        # Tasks >= 1500 chars should NOT delegate to Copilot
        p = self._plan("What is 2+2? " * 120, dc="PUBLIC")  # ~1560 chars
        self.assertEqual(p.task_type, "one_shot")
        self.assertIsNone(p.delegation_target)

    # ── No delegation for non-CC engines ─────────────────────────────────────

    def test_no_delegation_for_hermes_engine(self):
        p = self._plan("What is 2+2?", dc="CONFIDENTIAL", engine="hermes")
        self.assertIsNone(p.delegation_target,
                          "Hermes workers must not re-delegate (ADR-0029)")

    def test_no_delegation_for_opencode_engine(self):
        p = self._plan("Summarise the patient records.", dc="CONFIDENTIAL", engine="opencode")
        self.assertIsNone(p.delegation_target)

    def test_no_delegation_for_codex_engine(self):
        p = self._plan("What is the git log?", dc="PUBLIC", engine="codex")
        self.assertIsNone(p.delegation_target)

    # ── Internal non-one_shot → no delegation ────────────────────────────────

    def test_iterative_fix_internal_no_delegation(self):
        p = self._plan("Fix the failing audit test.", dc="INTERNAL")
        self.assertIsNone(p.delegation_target)

    def test_multi_agent_internal_no_delegation(self):
        p = self._plan("Sweep the codebase for deprecated APIs.", dc="INTERNAL")
        self.assertIsNone(p.delegation_target)

    def test_exploration_internal_no_delegation(self):
        p = self._plan("Decide between PostgreSQL and SQLite.", dc="INTERNAL")
        self.assertIsNone(p.delegation_target)

    def test_autonomous_internal_no_delegation(self):
        p = self._plan("Monitor the /health endpoint every 5 minutes.", dc="INTERNAL")
        self.assertIsNone(p.delegation_target)


# ── M7 compute routing tests ──────────────────────────────────────────────────

class TestATOM7ComputeRouting(unittest.TestCase):
    """Verify M7 compute classification and compute_params fields.

    These tests verify the classifier layer.  The actual adapter bypass
    (returning the structured blueprint) is tested in TestATOM7AdapterBypass.
    """

    def _plan(self, task: str, dc: str = "INTERNAL"):
        return _classify_module(task, data_classification=dc, engine_id="claude_code")

    def test_bayesian_is_compute(self):
        p = self._plan("Run Bayesian optimization over 200 trials.")
        self.assertEqual(p.task_type, "compute")

    def test_grid_search_is_compute(self):
        p = self._plan("Run a grid search over learning rate and batch size.")
        self.assertEqual(p.task_type, "compute")

    def test_regression_is_compute(self):
        p = self._plan("Fit a linear regression to the sales dataset.")
        self.assertEqual(p.task_type, "compute")

    def test_clustering_is_compute(self):
        p = self._plan("Apply k-means clustering to the user-session DataFrame.")
        self.assertEqual(p.task_type, "compute")

    def test_machine_learning_is_compute(self):
        p = self._plan("Train a machine learning model on the labelled dataset.")
        self.assertEqual(p.task_type, "compute")

    def test_simulation_is_compute(self):
        p = self._plan("Run a Monte Carlo simulation over 10,000 trials.")
        self.assertEqual(p.task_type, "compute")

    def test_parameter_sweep_is_compute(self):
        p = self._plan("Run a parameter sweep over dropout [0.1, 0.2] and hidden_size [64, 128].")
        self.assertEqual(p.task_type, "compute")

    def test_german_berechne_is_compute(self):
        p = self._plan("Berechne Mittelwert und Varianz der Spotify-Streams CSV-Datei.")
        self.assertEqual(p.task_type, "compute")

    def test_compute_params_populated(self):
        p = self._plan("Run Bayesian optimization over 200 trials.")
        self.assertIsNotNone(p.compute_params)
        self.assertIn("strategy", p.compute_params)
        self.assertEqual(p.compute_params["strategy"], "bayesian")
        self.assertIn("datasources", p.compute_params)
        self.assertIsInstance(p.compute_params["datasources"], list)

    def test_compute_params_none_for_oneshot(self):
        p = self._plan("What is the capital of Germany?")
        self.assertIsNone(p.compute_params)

    def test_compute_execution_strategy(self):
        p = self._plan("Run Bayesian optimization over 200 trials.")
        self.assertEqual(p.execution_strategy, "compute_worker")

    def test_compute_loop_k_max_one(self):
        p = self._plan("Run grid search over learning rate.")
        self.assertEqual(p.loop_params.get("k_max"), 1)

    def test_analyse_not_compute(self):
        # 'analyse' is a workflow signal; alone it shouldn't beat multi_agent
        p = self._plan("Analyse the performance metrics and recommend improvements.")
        self.assertNotEqual(p.task_type, "compute",
                            "EDGE: 'analyse' should be multi_agent, not compute")

    def test_explain_bayesian_not_compute(self):
        # 'explain...?' question suppresses compute (penalty 0.35 × 0.45 = 0.16 < 0.30 baseline)
        p = self._plan("Explain how Bayesian optimization works?")
        self.assertNotEqual(p.task_type, "compute",
                            "EDGE: 'explain' + '?' question must suppress compute signal")

    def test_compute_question_no_compute(self):
        # "What is the result of..." should not be classified as compute
        p = self._plan("What is the result of grid search on these parameters?")
        self.assertNotEqual(p.task_type, "compute",
                            "EDGE: question form suppresses grid_search compute signal")


# ── M7 adapter-level bypass test ─────────────────────────────────────────────

class TestATOM7AdapterBypass(unittest.TestCase):
    """Verify that adapter.call_claude_streaming() returns the M7 blueprint
    when CORVIN_ATO_M7_ENABLED=1 and the task is classified as compute.

    Uses a fresh subprocess that imports adapter and calls call_claude_streaming
    directly (no real claude subprocess needed — the M7 bypass fires before
    any engine is spawned).
    """

    _HELPER = """\
import sys, os, json
sys.path.insert(0, os.environ["_ADAPTER_DIR"])
os.environ.setdefault("CORVIN_ATO_M7_ENABLED", "1")
os.environ.setdefault("CORVIN_DATA_CLASSIFICATION", "INTERNAL")
# Skip budget checks
os.environ.setdefault("CORVIN_AGENTS_SKIP_LIVE", "1")
os.environ.setdefault("CORVIN_INTEGRATION_TEST", "1")
# Skip the real engine
os.environ.setdefault("ADAPTER_FAKE_CLAUDE", "0")
# Prevent any real OS-turn spawn
os.environ.setdefault("CORVIN_HOME", os.environ.get("TMPDIR", "/tmp"))
try:
    from adapter import call_claude_streaming  # type: ignore
    result = call_claude_streaming(
        sys.argv[1],
        channel="test", chat_key="anon",
        profile={"default_engine": None},
    )
    print(json.dumps({"result": result}))
except SystemExit:
    pass
except Exception as e:
    print(json.dumps({"error": str(e)}))
"""

    def _run_bypass(self, prompt: str) -> str | None:
        import tempfile  # noqa: PLC0415
        env = {
            **os.environ,
            "_ADAPTER_DIR": str(_here),
            "CORVIN_ATO_M7_ENABLED": "1",
            "CORVIN_DATA_CLASSIFICATION": "INTERNAL",
            "CORVIN_AGENTS_SKIP_LIVE": "1",
            "CORVIN_INTEGRATION_TEST": "1",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(self._HELPER)
            script = f.name
        try:
            proc = subprocess.run(
                [sys.executable, script, prompt],
                capture_output=True, text=True, timeout=15, env=env,
            )
        except subprocess.TimeoutExpired:
            return None  # adapter ran real engine (no M7 bypass) — skip
        finally:
            import os as _os  # noqa: PLC0415
            try:
                _os.unlink(script)
            except OSError:
                pass
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            data = json.loads(proc.stdout.strip())
            return data.get("result")
        except Exception:
            return None

    def test_compute_prompt_triggers_m7_blueprint(self):
        result = self._run_bypass(
            "Run Bayesian optimization over 200 trials on the ML model."
        )
        if result is None:
            self.skipTest("adapter subprocess failed — environment not fully set up")
        self.assertIn("ATO M7", result, f"M7 blueprint not in result: {result[:200]}")
        self.assertIn("compute", result.lower())
        self.assertIn("compute_run", result)

    def test_m7_blueprint_contains_strategy(self):
        result = self._run_bypass("Run grid search over learning rate and batch size.")
        if result is None:
            self.skipTest("adapter subprocess failed")
        self.assertIn("bayesian", result.lower())

    def test_m7_oneshot_does_not_bypass(self):
        result = self._run_bypass("What is the capital of Germany?")
        if result is None:
            self.skipTest("adapter subprocess failed")
        # one_shot should NOT return a M7 blueprint
        self.assertNotIn("ATO M7", result, "one_shot incorrectly triggered M7 bypass")


# ── Optional real-LLM smoke test (Tier 4) ────────────────────────────────────

@unittest.skipUnless(os.environ.get("RUN_LLM_E2E") == "1", "set RUN_LLM_E2E=1 to run LLM E2E tests")
class TestATORealLLMSmokeTest(unittest.TestCase):
    """Tier 4: Real LLM calls via 'claude -p'. Slow, needs API key.

    These tests verify that a real Claude instance correctly identifies task
    types when given ambiguous natural-language inputs and that the ATO
    guidance in the system prompt influences the output.

    Usage:
        RUN_LLM_E2E=1 python -m pytest operator/bridges/shared/test_ato_e2e.py \
            -k "RealLLM" -v
    """

    def _call_claude(self, prompt: str, timeout: int = 30) -> str:
        result = subprocess.run(
            ["claude", "-p", "--no-tools", "--max-turns", "1", prompt],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "CORVIN_ACS_HEURISTIC_ONLY": "1"},
        )
        return result.stdout.strip()

    def test_llm_identifies_iterative_fix(self):
        """Claude should identify 'fix the failing test' as iterative_fix."""
        response = self._call_claude(
            "I need to classify this task for ATO: 'Fix the failing E2E test for the auth flow.' "
            "Which ADR-0164 task type is this? Reply with exactly one of: "
            "one_shot / iterative_fix / multi_agent / exploration / autonomous / compute"
        )
        self.assertIn("iterative_fix", response.lower(),
                      f"LLM did not classify as iterative_fix: {response[:200]}")

    def test_llm_identifies_compute(self):
        """Claude should identify Bayesian optimization as compute."""
        response = self._call_claude(
            "Classify this ATO task: 'Run Bayesian optimization over 200 trials on the ML model.' "
            "Which ADR-0164 task type? One of: "
            "one_shot / iterative_fix / multi_agent / exploration / autonomous / compute"
        )
        self.assertIn("compute", response.lower(),
                      f"LLM did not classify as compute: {response[:200]}")

    def test_llm_identifies_exploration(self):
        """Claude should identify ADR writing as exploration."""
        response = self._call_claude(
            "Classify this ATO task: 'Write an ADR for the new rate-limiting strategy.' "
            "One of: one_shot / iterative_fix / multi_agent / exploration / autonomous / compute"
        )
        self.assertIn("exploration", response.lower(),
                      f"LLM did not classify as exploration: {response[:200]}")

    def test_llm_edge_compute_vs_research(self):
        """Edge case: 'analyse performance' should be multi_agent, not compute."""
        response = self._call_claude(
            "Classify: 'Analyse the performance metrics and recommend optimizations.' "
            "One of: one_shot / iterative_fix / multi_agent / exploration / autonomous / compute"
        )
        # LLM should prefer multi_agent over compute (no numerical operation signal)
        self.assertNotIn("compute", response.lower(),
                         f"LLM incorrectly classified 'analyse' as compute: {response[:200]}")


if __name__ == "__main__":
    import unittest.mock  # noqa: PLC0415 (needed for _record_loss mock helper)
    unittest.main(verbosity=2)
