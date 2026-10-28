"""Self-tests of the A/B token benchmark harness. No network, no model calls: the `fake` runner is a fixture.

Run: pytest tests/benchmarking/token_ab   (or: python3 ab_bench.py selftest)
Live counterpart (real claude CLI, opt-in CLAUDE_LIVE_E2E=1): test_token_ab_live.py
"""
import json
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ab_bench as ab  # noqa: E402
import gen_corpus as gc  # noqa: E402

SMALL = ["--runner", "fake", "--profile", "smoke", "--reps", "2", "--tasks-per-category", "3"]


def run_fake(tmp, name, seed=1, extra=()):
    return ab.main(["run", *SMALL, "--seed", str(seed), "--run-id", name, "--results-dir", tmp, *extra])


class CorpusTests(unittest.TestCase):
    def test_pinned_hashes_reproduce(self):
        man = json.loads((HERE / "corpus" / "MANIFEST.json").read_text(encoding="utf-8"))
        for profile in ("smoke", "full", "hard"):
            self.assertEqual(gc.sha256(gc.dumps(gc.build(profile))), man["profiles"][profile]["sha256"], profile)

    def test_loader_verifies_hash_and_shape(self):
        tasks, digest = ab.load_corpus("smoke")
        self.assertEqual(len(tasks), 60)
        self.assertEqual(len({t["task_id"] for t in tasks}), 60)
        self.assertEqual({t["category"] for t in tasks}, set(gc.CATEGORIES))
        self.assertEqual(len(digest), 64)

    def test_tampered_corpus_is_refused(self):
        man_path = HERE / "corpus" / "MANIFEST.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        orig = ab.CORPUS_DIR
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "MANIFEST.json").write_text(json.dumps(man), encoding="utf-8")
            text = (orig / man["profiles"]["smoke"]["file"]).read_text(encoding="utf-8")
            (d / man["profiles"]["smoke"]["file"]).write_text(text.replace("ANSWER", "ANSWEr", 1), encoding="utf-8")
            ab.CORPUS_DIR = d
            try:
                with self.assertRaises(SystemExit):
                    ab.load_corpus("smoke")
            finally:
                ab.CORPUS_DIR = orig


class GraderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = ab.load_corpus("smoke")[0] + ab.load_corpus("hard")[0]  # the easy and the hard corpus

    def test_reference_passes_and_wrong_fails_for_every_task(self):
        for t in self.tasks:
            good, bad = ab.reference_text(t, True), ab.reference_text(t, False)
            self.assertTrue(ab.is_correct(t, good), t["task_id"])
            self.assertTrue(ab.visible_check(t, good), t["task_id"])
            self.assertFalse(ab.is_correct(t, bad), t["task_id"])

    def test_visible_check_is_not_the_oracle(self):
        """A wrong answer in the right format passes the visible check: a cascade cannot see it (that is the point)."""
        for t in (x for x in self.tasks if x["answer_kind"] != "code"):
            self.assertTrue(ab.visible_check(t, ab.reference_text(t, False)), t["task_id"])

    def test_missing_answer_line_fails_both(self):
        t = next(x for x in self.tasks if x["answer_kind"] == "int")
        self.assertFalse(ab.visible_check(t, "I think it is 4."))
        self.assertFalse(ab.is_correct(t, "I think it is 4."))

    def test_last_answer_line_wins(self):
        t = next(x for x in self.tasks if x["answer_kind"] == "int")
        self.assertTrue(ab.is_correct(t, f"ANSWER: 0\nrethinking\nANSWER: {t['expected']}"))


class StatsTests(unittest.TestCase):
    def cmp(self, tok_x, tok_b, suc_x, suc_b, seed="s", cap=False):
        # cap=False tests the pure decision rule; the cap has its own tests (CapTests)
        return ab.compare(tok_x, tok_b, suc_x, suc_b, 0.05, 0.05, 1000, seed, cap_underpowered=cap)

    def test_identical_arms_are_inconclusive_never_adopted(self):
        c = self.cmp([100] * 40, [100] * 40, [1.0] * 40, [1.0] * 40)
        self.assertEqual(c["decision"], "INCONCLUSIVE")
        self.assertTrue(c["non_inferior"])
        self.assertFalse(c["saves"])

    def test_clear_saving_with_equal_success_is_adopted(self):
        c = self.cmp([40] * 60, [100] * 60, [0.9] * 60, [0.9] * 60)
        self.assertEqual(c["decision"], "ADOPT")
        self.assertAlmostEqual(c["savings"]["point"], 0.6)

    def test_large_regression_is_rejected_even_when_cheaper(self):
        c = self.cmp([10] * 60, [100] * 60, [0.5] * 60, [1.0] * 60)
        self.assertEqual(c["decision"], "REJECT")
        self.assertTrue(c["inferior"])

    def test_costlier_arm_is_rejected(self):
        c = self.cmp([200] * 60, [100] * 60, [1.0] * 60, [1.0] * 60)
        self.assertEqual(c["decision"], "REJECT")
        self.assertTrue(c["costs_more"])

    def test_small_noisy_regression_is_not_adopted(self):
        suc_x = [1.0] * 27 + [0.0] * 3
        c = self.cmp([40] * 30, [100] * 30, suc_x, [1.0] * 30)
        self.assertFalse(c["non_inferior"])
        self.assertNotEqual(c["decision"], "ADOPT")

    def test_bootstrap_is_seeded(self):
        a = self.cmp([40, 60, 50, 45] * 10, [100, 90, 110, 95] * 10, [1, 0, 1, 1] * 10, [1, 1, 1, 1] * 10, "x")
        b = self.cmp([40, 60, 50, 45] * 10, [100, 90, 110, 95] * 10, [1, 0, 1, 1] * 10, [1, 1, 1, 1] * 10, "x")
        c = self.cmp([40, 60, 50, 45] * 10, [100, 90, 110, 95] * 10, [1, 0, 1, 1] * 10, [1, 1, 1, 1] * 10, "y")
        self.assertEqual(a, b)
        self.assertNotEqual(a["savings"]["ci"], c["savings"]["ci"])

    def test_power_formula(self):
        self.assertEqual(ab.n_required(0.9, 0.1), 112)
        self.assertGreater(ab.n_required(0.9, 0.05), ab.n_required(0.9, 0.1))

    def test_small_corpus_is_flagged_underpowered(self):
        c = self.cmp([40] * 60, [100] * 60, [0.9] * 60, [0.9] * 60)
        self.assertTrue(c["power"]["underpowered"])  # 60 tasks cannot support delta=0.05 at p0=0.9 (unpaired)


class RunFlowTests(unittest.TestCase):
    def test_same_seed_same_raw_bytes_different_seed_differs(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(run_fake(t, "a", 1), 0)
            self.assertEqual(run_fake(t, "b", 1), 0)
            self.assertEqual(run_fake(t, "c", 2), 0)
            raw = lambda n: ab.sha256_file(Path(t, n, "raw.jsonl"))
            self.assertEqual(raw("a"), raw("b"))
            self.assertNotEqual(raw("a"), raw("c"))

    def test_analysis_replays_bit_for_bit_from_raw_only(self):
        with tempfile.TemporaryDirectory() as t:
            run_fake(t, "a", 1)
            d = Path(t, "a")
            ab.main(["analyze", "--run-dir", str(d), "--bootstrap", "500"])
            first = ab.sha256_file(d / "summary.json")
            report = (d / "report.md").read_text(encoding="utf-8")
            (d / "summary.json").unlink()
            (d / "report.md").unlink()
            ab.main(["analyze", "--run-dir", str(d), "--bootstrap", "500"])
            self.assertEqual(first, ab.sha256_file(d / "summary.json"))
            self.assertEqual(report, (d / "report.md").read_text(encoding="utf-8"))
            self.assertIn("SIMULATION", report)

    def test_cascade_counts_every_attempt(self):
        with tempfile.TemporaryDirectory() as t:
            run_fake(t, "a", 1)
            recs = ab.load_raw(Path(t, "a"))
            casc = [r for r in recs if r["arm"] == "cascade"]
            self.assertTrue(any(len(r["calls"]) > 1 for r in casc), "fixture should force at least one escalation")
            for r in casc:
                self.assertEqual(r["tokens"]["total"], sum(c["input"] + c["output"] + c["cache_read"] + c["cache_write"] for c in r["calls"]))
            for r in recs:
                if r["arm"].startswith("direct") or r["arm"] == "route_rule":
                    self.assertEqual(len(r["calls"]), 1)

    def test_paired_design_every_task_on_every_arm(self):
        with tempfile.TemporaryDirectory() as t:
            run_fake(t, "a", 1)
            recs = ab.load_raw(Path(t, "a"))
            arms = {r["arm"] for r in recs}
            keys = {(r["task_id"], r["rep"]) for r in recs}
            self.assertEqual(len(recs), len(keys) * len(arms))

    def test_raw_is_never_overwritten_and_resume_equals_uninterrupted(self):
        with tempfile.TemporaryDirectory() as t:
            run_fake(t, "full", 1)
            self.assertEqual(run_fake(t, "cut", 1, ["--max-calls", "9"]), 3)
            with self.assertRaises(SystemExit):
                run_fake(t, "cut", 1)  # exists, no --resume
            self.assertEqual(run_fake(t, "cut", 1, ["--resume"]), 0)
            self.assertEqual(ab.sha256_file(Path(t, "full", "raw.jsonl")), ab.sha256_file(Path(t, "cut", "raw.jsonl")))

    def test_manifest_pins_models_corpus_and_harness(self):
        with tempfile.TemporaryDirectory() as t:
            run_fake(t, "a", 1)
            man = json.loads(Path(t, "a", "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(man["models"], json.loads((HERE / "config" / "models.json").read_text(encoding="utf-8"))["pins"])
            self.assertEqual(man["corpus"]["sha256"], ab.load_corpus("smoke")[1])
            self.assertEqual(man["harness_sha256"], ab.sha256_file(HERE / "ab_bench.py"))
            self.assertEqual(man["seed"], 1)


class LiveGuardTests(unittest.TestCase):
    PINS = {"pins": {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}}

    def test_live_refuses_unpinned_alias_and_missing_flags(self):
        with self.assertRaises(SystemExit):
            ab.check_live_ready({"pins": {}}, True, 10)
        with self.assertRaises(SystemExit):
            ab.check_live_ready({"pins": {**self.PINS["pins"], "opus": "opus-latest"}}, True, 10)
        with self.assertRaises(SystemExit):
            ab.check_live_ready(self.PINS, False, 10)  # no --allow-code-exec
        with mock.patch.object(ab, "bwrap_available", return_value=True):
            with self.assertRaises(SystemExit):
                ab.check_live_ready(self.PINS, True, None)  # no --max-calls
            ab.check_live_ready(self.PINS, True, 10)  # all set: no exception
        with mock.patch.object(ab, "bwrap_available", return_value=False):
            with self.assertRaises(SystemExit):
                ab.check_live_ready(self.PINS, True, 10)  # no sandbox: model-written code is not graded

    def test_envelope_parser(self):
        ok = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ANSWER: 4", "total_cost_usd": 0.0012,
                         "usage": {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 7}})
        text, usage, err = ab.parse_cli_envelope(ok)
        self.assertEqual((text, err), ("ANSWER: 4", None))
        self.assertEqual(usage, {"input": 3, "output": 5, "cache_read": 100, "cache_write": 7, "cost_usd": 0.0012})
        bad = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "usage": {"input_tokens": 1}})
        self.assertEqual(ab.parse_cli_envelope(bad)[2], "error_max_turns")
        self.assertEqual(ab.parse_cli_envelope("plain text")[2], "not_json")


@unittest.skipUnless(ab.bwrap_available(), "bubblewrap (bwrap) or /usr/bin/python3 not available")
class SandboxTests(unittest.TestCase):
    """T-0323: model-written code runs inside bubblewrap and fails closed. Real subprocess, real bwrap."""
    TESTS = [[[1, 2], 3], [[5, 5], 10]]

    def run_code(self, code, timeout=10):
        return ab.run_code_tests(code, self.TESTS, timeout=timeout, sandbox="bwrap")

    def test_correct_code_passes(self):
        self.assertEqual(self.run_code("def f(a, b):\n    return a + b\n"), (2, 2))

    def test_a_write_to_a_host_path_never_reaches_the_host(self):
        with tempfile.TemporaryDirectory() as host:
            marker = Path(host) / "escaped.txt"
            ok, _ = self.run_code(f"def f(a, b):\n    open({str(marker)!r}, 'w').write('x')\n    return a + b\n")
            self.assertFalse(marker.exists())
            self.assertEqual(ok, 0)  # the write fails inside the sandbox: counted as a failed test, never as a pass

    def test_the_work_directory_is_read_only(self):
        self.assertEqual(self.run_code("def f(a, b):\n    open('/work/new.txt', 'w').write('x')\n    return a + b\n")[0], 0)

    def test_a_socket_to_a_host_loopback_server_is_refused(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            ok, _ = self.run_code(f"import socket\n\ndef f(a, b):\n    socket.create_connection(('127.0.0.1', {port}), timeout=2)\n    return a + b\n")
            srv.settimeout(0.5)
            try:
                srv.accept()
                connected = True
            except socket.timeout:
                connected = False
        finally:
            srv.close()
        self.assertFalse(connected, "the sandbox reached a server on the host's loopback")
        self.assertEqual(ok, 0)

    def test_host_home_is_not_visible(self):
        ok, _ = self.run_code("import os\n\ndef f(a, b):\n    os.listdir('/home')\n    return a + b\n")
        self.assertEqual(ok, 0)

    def test_an_endless_loop_is_cut_and_fails_closed(self):
        t0 = time.monotonic()
        self.assertEqual(self.run_code("def f(a, b):\n    while True:\n        pass\n", timeout=3)[0], 0)
        self.assertLess(time.monotonic() - t0, 15)

    def test_explicit_bwrap_without_bwrap_refuses(self):
        with mock.patch.object(ab, "bwrap_available", return_value=False):
            with self.assertRaises(SystemExit):
                ab.run_code_tests("def f(a, b):\n    return a + b\n", self.TESTS, sandbox="bwrap")


class HolmTests(unittest.TestCase):
    def test_known_family(self):
        self.assertEqual(ab.holm_reject({"a": 0.001, "b": 0.02, "c": 0.04}, 0.05), {"a": True, "b": True, "c": True})
        # 0.02 > 0.05 / 3: the smallest p already fails, so nothing is rejected (a plain per-test 0.05 would reject all three)
        self.assertEqual(ab.holm_reject({"a": 0.02, "b": 0.03, "c": 0.04}, 0.05), {"a": False, "b": False, "c": False})
        self.assertEqual(ab.holm_reject({"a": 0.01, "b": 0.30}, 0.05), {"a": True, "b": False})

    def test_step_down_stops_at_the_first_failure(self):
        self.assertEqual(ab.holm_reject({"a": 0.001, "b": 0.5, "c": 0.0001}, 0.05), {"c": True, "a": True, "b": False})


def _synth_run(tmp, name, rules, *, min_tasks=10, delta=0.05, roles=None, per_cat=12, cap=False):
    """A run directory built from rules {arm: {category: (n_failures, cost)}} with a perfect baseline `base` (cost 100)."""
    run = Path(tmp) / name
    run.mkdir(parents=True)
    arms = ["base", *rules]
    lines = []
    for arm in arms:
        for cat in ("a", "b", "c"):
            fails, cost = (0, 100) if arm == "base" else rules[arm][cat]
            for i in range(per_cat):
                lines.append(json.dumps({"task_id": f"{cat}-{i:03d}", "category": cat, "label": "reasoning", "rep": 0, "arm": arm,
                                         "correct": i >= fails, "visible_pass": True, "stages": ["x"],
                                         "tokens": {"input": cost, "output": 0, "cache_read": 0, "cache_write": 0, "total": cost, "cost_usd": None},
                                         "calls": [{}]}))
    (run / "raw.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    man = {"run_id": name, "runner": "claude", "seed": 1, "reps": 1, "models": {}, "python": "3", "claude_version": None,
           "harness_sha256": "x", "config_sha256": "x", "corpus": {"profile": "smoke", "sha256": "x", "tasks_run": 3 * per_cat, "skipped": []},
           "arms": arms, "roles": roles or {}, "decision": {"delta": delta, "alpha": 0.05, "bootstrap": 500, "min_stratum_tasks": min_tasks, "baseline": "base",
                                                                              "cap_underpowered": cap}}
    (run / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return run


class ClassGateTests(unittest.TestCase):
    """T-0325 / ADR-2306 decision 5: Holm per category, worst-class guard, exploratory strata never adopt."""

    def analyze(self, rules, **kw):
        with tempfile.TemporaryDirectory() as t:
            return ab.analyze(_synth_run(t, "r", rules, **kw), 500)

    def test_every_class_established_is_adopted(self):
        s = self.analyze({"x": {"a": (0, 40), "b": (0, 40), "c": (0, 40)}}, per_cat=24)
        c = s["comparisons"]["x"]
        self.assertEqual(c["decision"], "ADOPT")
        self.assertTrue(c["class_gate"]["all_holm_non_inferior"] and not c["class_gate"]["guard_triggered"])
        self.assertTrue(all(st["adjusted_decision"] == "ADOPT" for k, st in c["strata"].items() if k.startswith("category:")))

    def test_worst_class_guard_rejects_a_cheaper_policy(self):
        s = self.analyze({"x": {"a": (0, 40), "b": (0, 40), "c": (6, 40)}})
        c = s["comparisons"]["x"]
        self.assertTrue(c["class_gate"]["guard_triggered"])
        self.assertEqual((c["class_gate"]["worst_class"], c["decision"]), ("category:c", "REJECT"))
        self.assertGreater(c["savings"]["point"], 0)  # cheaper overall, and still rejected

    def test_one_class_not_established_blocks_adopt_without_triggering_the_guard(self):
        s = self.analyze({"x": {"a": (0, 40), "b": (0, 40), "c": (1, 40)}}, per_cat=24)
        c = s["comparisons"]["x"]
        self.assertFalse(c["class_gate"]["guard_triggered"])
        self.assertFalse(c["class_gate"]["all_holm_non_inferior"])
        self.assertEqual(c["decision_before_class_gate"], "ADOPT")
        self.assertEqual(c["decision"], "INCONCLUSIVE")

    def test_small_categories_are_exploratory_and_never_adopt(self):
        s = self.analyze({"x": {"a": (0, 40), "b": (0, 40), "c": (0, 40)}}, min_tasks=100)
        c = s["comparisons"]["x"]
        self.assertFalse(c["class_gate"]["evaluated"])
        self.assertTrue(all(st["adjusted_decision"] == "EXPLORATORY" for k, st in c["strata"].items() if k.startswith("category:")))

    def test_an_underpowered_category_cannot_be_adopted_after_holm(self):
        rules = {"x": {"a": (0, 40), "b": (0, 40), "c": (0, 40)}}
        uncapped = self.analyze(rules, per_cat=24, cap=False)["comparisons"]["x"]["strata"]
        capped = self.analyze(rules, per_cat=24, cap=True)["comparisons"]["x"]["strata"]
        for k in ("category:a", "category:b", "category:c"):
            self.assertEqual(uncapped[k]["adjusted_decision"], "ADOPT", "control: the data alone adopt")
            self.assertFalse(uncapped[k]["class_capped_underpowered"])
            self.assertTrue(capped[k]["power"]["underpowered"])
            self.assertTrue(capped[k]["class_capped_underpowered"])
            self.assertEqual(capped[k]["adjusted_decision"], "INCONCLUSIVE")
            self.assertTrue(capped[k]["holm_non_inferior"], "the report still shows what Holm said")

    def test_a_capped_category_never_hides_a_reject(self):
        rules = {"x": {"a": (0, 40), "b": (0, 40), "c": (12, 40)}}
        c = self.analyze(rules, per_cat=24, cap=True)["comparisons"]["x"]
        self.assertEqual(c["strata"]["category:c"]["adjusted_decision"], "REJECT")
        self.assertFalse(c["strata"]["category:c"]["class_capped_underpowered"])

    def test_control_arm_reads_the_baseline_noise(self):
        same = self.analyze({"rt": {"a": (0, 100), "b": (0, 100), "c": (0, 100)}}, roles={"rt": "retest"}, per_cat=24)
        self.assertTrue(same["comparisons"]["rt"]["sanity"]["identical_policy_non_inferior"])
        noisy = self.analyze({"rt": {"a": (8, 100), "b": (8, 100), "c": (8, 100)}}, roles={"rt": "retest"}, per_cat=24)
        self.assertFalse(noisy["comparisons"]["rt"]["sanity"]["identical_policy_non_inferior"])
        self.assertIn("BELOW", noisy["comparisons"]["rt"]["sanity"]["reading"])

    def test_analysis_replays_byte_identically(self):
        with tempfile.TemporaryDirectory() as t:
            run = _synth_run(t, "r", {"x": {"a": (0, 40), "b": (1, 40), "c": (0, 40)}}, per_cat=24)
            one = json.dumps(ab.analyze(run, 500), sort_keys=True)
            self.assertEqual(one, json.dumps(ab.analyze(run, 500), sort_keys=True))

    def test_report_names_the_class_gate_and_the_control_arm(self):
        with tempfile.TemporaryDirectory() as t:
            run = _synth_run(t, "r", {"rt": {"a": (0, 100), "b": (0, 100), "c": (0, 100)}}, roles={"rt": "retest"}, per_cat=24)
            man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            report = ab.render_report(ab.analyze(run, 500), man)
        self.assertIn("Klassen-Gate", report)
        self.assertIn("Kontrollarm", report)
        self.assertIn("nach Holm", report)


class CategoryFilterTests(unittest.TestCase):
    """--categories limits a run to named categories (used for the targeted text_rules run)."""

    def test_only_the_named_category_runs_and_the_manifest_says_so(self):
        with tempfile.TemporaryDirectory() as t:
            rc = ab.main(["run", "--runner", "fake", "--profile", "hard", "--categories", "text_rules", "--reps", "1", "--seed", "1",
                          "--run-id", "c", "--results-dir", t, "--arms-config", str(HERE / "config" / "arms.text_rules.json")])
            self.assertEqual(rc, 0)
            recs = ab.load_raw(Path(t, "c"))
            self.assertEqual({r["category"] for r in recs}, {"text_rules"})
            self.assertEqual(len(recs), 12 * 3)  # 12 tasks x 3 arms
            man = json.loads(Path(t, "c", "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual((man["corpus"]["categories"], man["corpus"]["tasks_run"]), (["text_rules"], 12))

    def test_an_unknown_category_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(SystemExit):
                ab.main(["run", "--runner", "fake", "--profile", "hard", "--categories", "nope", "--run-id", "c", "--results-dir", t])


class CapTests(unittest.TestCase):
    """An ADOPT on too few tasks must not read as certainty (pilot 2026-10-27: 10 tasks, success 1.000 on all arms)."""

    def cmp(self, tok_x, tok_b, suc_x, suc_b, cap=True):
        return ab.compare(tok_x, tok_b, suc_x, suc_b, 0.05, 0.05, 1000, "s", cap_underpowered=cap)

    def test_adopt_on_too_few_tasks_is_capped_to_inconclusive(self):
        c = self.cmp([40] * 10, [100] * 10, [1.0] * 10, [1.0] * 10)
        self.assertTrue(c["power"]["underpowered"] and c["saves"] and c["non_inferior"])
        self.assertEqual((c["decision"], c["capped_underpowered"]), ("INCONCLUSIVE", True))

    def test_the_cap_can_be_switched_off(self):
        c = self.cmp([40] * 10, [100] * 10, [1.0] * 10, [1.0] * 10, cap=False)
        self.assertEqual((c["decision"], c["capped_underpowered"]), ("ADOPT", False))

    def test_an_adequately_powered_adopt_stays(self):
        n = ab.n_required(0.95, 0.05) + 5
        c = self.cmp([40] * n, [100] * n, [1.0] * n, [1.0] * n)
        self.assertEqual((c["decision"], c["capped_underpowered"]), ("ADOPT", False))

    def test_reject_is_never_capped(self):
        c = self.cmp([10] * 10, [100] * 10, [0.5] * 10, [1.0] * 10)
        self.assertEqual((c["decision"], c["capped_underpowered"]), ("REJECT", False))

    def test_analysis_and_report_name_the_cap(self):
        with tempfile.TemporaryDirectory() as t:
            run = _synth_run(t, "r", {"x": {"a": (0, 40), "b": (0, 40), "c": (0, 40)}}, cap=True)
            man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            s = ab.analyze(run, 500)
            report = ab.render_report(s, man)
        c = s["comparisons"]["x"]
        self.assertEqual((c["decision"], c["capped_underpowered"]), ("INCONCLUSIVE", True))
        self.assertIn("ADOPT gedeckelt", report)


class HardCorpusTests(unittest.TestCase):
    """Profile `hard`: built to separate the models, still with deterministic oracles."""

    @classmethod
    def setUpClass(cls):
        cls.tasks, _ = ab.load_corpus("hard")

    def test_shape(self):
        import hard_tasks as ht
        self.assertEqual(len(self.tasks), 60)
        self.assertEqual({t["category"] for t in self.tasks}, set(ht.HARD_CATEGORIES))
        self.assertEqual({t["label"] for t in self.tasks}, {"mechanical", "reasoning"})
        self.assertEqual(len({t["task_id"] for t in self.tasks}), 60)
        for t in (x for x in self.tasks if x["category"] == "code_hard"):
            self.assertEqual(len(t["tests"]), 24)

    def test_table_agg_expectations_recomputed_independently_from_the_prompt(self):
        import re
        seen = set()
        for t in (x for x in self.tasks if x["category"] == "table_agg"):
            head, _, rest = t["prompt"].partition(":\n")
            table, _, tail = rest.partition("\n\n")
            rows = [r.split(",") for r in table.splitlines()[1:]]
            q = tail.split(" Finish with")[0]
            m = re.match(r"What is the total salary of all rows with dept = (\w+), hire_year >= (\d+) and status = active\?", q)
            if m:
                want = sum(int(r[4]) for r in rows if r[1] == m[1] and int(r[3]) >= int(m[2]) and r[5] == "active")
                seen.add("sum")
            m2 = re.match(r"In how many distinct cities are there at least 2 rows with dept = (\w+) and salary > (\d+)\?", q)
            if m2:
                from collections import Counter
                cnt = Counter(r[2] for r in rows if r[1] == m2[1] and int(r[4]) > int(m2[2]))
                want = sum(1 for v in cnt.values() if v >= 2)
                seen.add("cities")
            m3 = re.match(r"Among the rows with status = active and city = (\w+), which id has the highest salary\?", q)
            if m3:
                want = max((r for r in rows if r[2] == m3[1] and r[5] == "active"), key=lambda r: int(r[4]))[0]
                seen.add("max")
            self.assertEqual(str(want), t["expected"], t["task_id"])
        self.assertEqual(seen, {"sum", "cities", "max"})

    def test_a_perfect_answer_is_graded_correct_and_a_flipped_one_is_not_for_every_task(self):
        for t in self.tasks:
            self.assertTrue(ab.is_correct(t, ab.reference_text(t, True)), t["task_id"])
            self.assertFalse(ab.is_correct(t, ab.reference_text(t, False)), t["task_id"])


class ReferenceFunctionTests(unittest.TestCase):
    """The expected values of the code_hard tasks come from these functions, so they are checked against hand-computed cases."""

    def test_hand_computed_cases(self):
        import hard_tasks as h
        self.assertEqual(h.merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]), [[1, 6], [8, 10], [15, 18]])
        self.assertEqual(h.merge_intervals([[1, 2], [2, 3]]), [[1, 3]])  # touching intervals merge
        self.assertEqual(h.merge_intervals([]), [])
        self.assertEqual(h.roman_to_int("MCMXCIV"), 1994)
        self.assertEqual(h.int_to_roman(1994), "MCMXCIV")
        self.assertEqual(h.eval_expr("2 + 3 * (4 - 1)"), 11)
        self.assertEqual(h.edit_distance("kitten", "sitting"), 3)
        self.assertEqual(h.edit_distance("", "abc"), 3)
        self.assertEqual(h.lcs_length("abcde", "ace"), 3)
        self.assertEqual(h.count_islands(["11000", "11000", "00100", "00011"]), 3)
        self.assertEqual(h.topo_lex(4, [[0, 1], [0, 2], [1, 3], [2, 3]]), [0, 1, 2, 3])
        self.assertEqual(h.topo_lex(3, [[2, 0], [1, 0]]), [1, 2, 0])  # lexicographically smallest, not insertion order
        self.assertEqual(h.topo_lex(2, [[0, 1], [1, 0]]), [])  # a cycle
        self.assertEqual(h.shortest_path(3, [[0, 1, 1], [1, 2, 2], [0, 2, 5]], 0, 2), 3)
        self.assertEqual(h.shortest_path(3, [[0, 1, 1]], 0, 2), -1)
        self.assertEqual(h.first_bracket_error("([)]"), 2)
        self.assertEqual(h.first_bracket_error("(("), 2)  # never closed: the length
        self.assertEqual(h.first_bracket_error("a(b)[c]"), -1)
        self.assertEqual(h.first_bracket_error("a)"), 1)
        self.assertEqual(h.rotate_matrix([[1, 2, 3], [4, 5, 6]]), [[4, 1], [5, 2], [6, 3]])
        self.assertEqual(h.spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9]]), [1, 2, 3, 6, 9, 8, 7, 4, 5])
        self.assertEqual(h.spiral_order([[1, 2, 3]]), [1, 2, 3])
        self.assertEqual(h.spiral_order([[1], [2], [3]]), [1, 2, 3])
        self.assertEqual(h.add_business_days("2026-10-23", 1), "2026-10-26")  # Friday + 1 weekday = Monday
        self.assertEqual(h.add_business_days("2026-10-26", 5), "2026-11-02")
        self.assertEqual(h.add_business_days("2026-10-24", 0), "2026-10-24")  # n = 0 returns the start unchanged

    def test_roman_roundtrip_for_every_value(self):
        import hard_tasks as h
        self.assertTrue(all(h.roman_to_int(h.int_to_roman(n)) == n for n in range(1, 4000)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
