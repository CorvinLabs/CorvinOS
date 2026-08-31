"""Tests for AnthropicBatchEngine (ADR-0099).

Covers M1–M5 without network calls:
  - _expand_grid: cartesian product expansion
  - _fill_template: {{key}} substitution
  - _extract_loss: first_float / parse_float / json:<key>
  - Engine lifecycle: submit→status→result with mocked HTTP
  - Partial failure handling (some candidates errored)
  - Abort: marks job aborted, calls cancel endpoint
  - ComputeEngine protocol compliance
  - open_batches.json write/remove (L8 state file)
  - cancel_open_batches_for_session (L8 cleanup helper)
"""
from __future__ import annotations

import json
import sys
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.engines.anthropic_batch import (  # noqa: E402
    AnthropicBatchEngine,
    _expand_grid,
    _extract_loss,
    _fill_template,
    cancel_open_batches_for_session,
    _open_batches_path,
)
from corvin_compute.engine_protocol import ComputeSpec, GateAction  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_spec(**kwargs) -> ComputeSpec:
    defaults = dict(
        engine="anthropic_batch",
        tenant_id="test-tenant",
        budget={"max_iterations": 6},
        param_grid={"lr": [0.01, 0.1], "depth": [2, 4, 8]},
        loss_metric="loss",
        extra={
            "prompt_template": "lr={{lr}} depth={{depth}}",
            "model": "claude-haiku-4-5-20251001",
            "max_tokens_per_call": 16,
        },
    )
    defaults.update(kwargs)
    return ComputeSpec(**defaults)


def _fake_batch_submit_response(batch_id="msgbatch_test123456789") -> dict:
    return {
        "id": batch_id,
        "type": "message_batch",
        "processing_status": "in_progress",
        "request_counts": {"processing": 6, "succeeded": 0, "errored": 0,
                           "expired": 0, "canceled": 0},
    }


def _fake_batch_status_ended(batch_id="msgbatch_test123456789", errored=0) -> dict:
    return {
        "id": batch_id,
        "type": "message_batch",
        "processing_status": "ended",
        "request_counts": {
            "processing": 0,
            "succeeded": 6 - errored,
            "errored": errored,
            "expired": 0,
            "canceled": 0,
        },
    }


def _fake_results_jsonl(n=6, errored_indices: set[int] | None = None) -> list[dict]:
    """Return ABP-style result lines. errored_indices → those candidates error."""
    errored_indices = errored_indices or set()
    lines = []
    for i in range(n):
        if i in errored_indices:
            lines.append({
                "custom_id": f"c{i}",
                "result": {"type": "errored",
                           "error": {"type": "overloaded_error", "message": "busy"}},
            })
        else:
            loss = 0.5 - i * 0.05  # decreasing loss so best candidate is c5
            lines.append({
                "custom_id": f"c{i}",
                "result": {
                    "type": "succeeded",
                    "message": {
                        "content": [{"type": "text", "text": str(loss)}],
                    },
                },
            })
    return lines


# ── unit tests: helpers ───────────────────────────────────────────────────────

class TestExpandGrid(unittest.TestCase):
    def test_simple_product(self):
        grid = {"lr": [0.01, 0.1], "depth": [2, 4]}
        result = _expand_grid(grid)
        self.assertEqual(len(result), 4)
        self.assertIn({"lr": 0.01, "depth": 2}, result)
        self.assertIn({"lr": 0.1, "depth": 4}, result)

    def test_single_key(self):
        result = _expand_grid({"x": [1, 2, 3]})
        self.assertEqual([r["x"] for r in result], [1, 2, 3])

    def test_empty_grid_returns_one_empty_candidate(self):
        self.assertEqual(_expand_grid({}), [{}])

    def test_scalar_wrapped_in_list(self):
        result = _expand_grid({"x": 5})
        self.assertEqual(result, [{"x": 5}])


class TestFillTemplate(unittest.TestCase):
    def test_basic_substitution(self):
        self.assertEqual(
            _fill_template("lr={{lr}} d={{depth}}", {"lr": 0.01, "depth": 4}),
            "lr=0.01 d=4",
        )

    def test_unknown_placeholder_left_in_place(self):
        result = _fill_template("lr={{lr}} other={{other}}", {"lr": 0.01})
        self.assertIn("{{other}}", result)

    def test_params_key_json_dumped(self):
        # The "params" key is injected by submit(), not _fill_template itself.
        params = {"lr": 0.01}
        result = _fill_template("{{params}}", {**params, "params": json.dumps(params)})
        self.assertIn('"lr"', result)


class TestExtractLoss(unittest.TestCase):
    def test_first_float(self):
        self.assertAlmostEqual(
            _extract_loss("The loss is 0.123 after training.", "first_float"),
            0.123,
        )

    def test_parse_float(self):
        self.assertAlmostEqual(
            _extract_loss("  3.14  ", "parse_float"), 3.14,
        )

    def test_json_key(self):
        self.assertAlmostEqual(
            _extract_loss('{"score": 0.42, "other": 1}', "json:score"), 0.42,
        )

    def test_none_on_parse_failure(self):
        self.assertIsNone(_extract_loss("no numbers here!", "parse_float"))

    def test_negative_float(self):
        val = _extract_loss("loss=-0.05", "first_float")
        self.assertAlmostEqual(val, -0.05)


# ── engine lifecycle tests ────────────────────────────────────────────────────

class TestAnthropicBatchEngineProtocol(unittest.TestCase):
    def test_implements_compute_engine_protocol(self):
        from corvin_compute.engine_protocol import ComputeEngine
        engine = AnthropicBatchEngine()
        self.assertIsInstance(engine, ComputeEngine)

    def test_engine_metadata(self):
        e = AnthropicBatchEngine()
        self.assertEqual(e.engine_id, "anthropic_batch")
        self.assertTrue(e.job_id_prefix.startswith("abatch_"))
        self.assertFalse(e.supports_gates)

    def test_gate_action_raises(self):
        from corvin_compute.engine_protocol import EngineDoesNotSupportGates
        e = AnthropicBatchEngine()
        with self.assertRaises(EngineDoesNotSupportGates):
            e.gate_action("abatch_fake", GateAction(action_type="resume"))


class TestAnthropicBatchEngineLifecycle(unittest.TestCase):
    """Submit → status (in-progress) → status (ended) → result."""

    def _make_engine_with_mocks(self, errored_indices=None):
        engine = AnthropicBatchEngine()
        errored = errored_indices or set()

        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get_status = MagicMock(return_value=_fake_batch_status_ended(
            errored=len(errored)
        ))
        mock_get_jsonl = MagicMock(
            return_value=_fake_results_jsonl(6, errored)
        )
        return engine, mock_post, mock_get_status, mock_get_jsonl

    def test_submit_returns_job_id(self):
        engine, mock_post, _, _ = self._make_engine_with_mocks()
        spec = _make_spec()

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(spec)

        self.assertTrue(job_id.startswith("abatch_"))
        mock_post.assert_called_once()
        # Verify body had 6 requests (2 lr × 3 depth)
        call_body = mock_post.call_args[0][1]
        self.assertEqual(len(call_body["requests"]), 6)

    def test_submit_respects_max_iterations(self):
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        spec = _make_spec()
        spec.budget = {"max_iterations": 3}

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch.object(engine, "_assert_compliance", return_value=None):
            engine.submit(spec)

        call_body = mock_post.call_args[0][1]
        self.assertEqual(len(call_body["requests"]), 3)

    def test_status_running_before_ended(self):
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get = MagicMock(return_value=_fake_batch_submit_response())

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_get", mock_get), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(_make_spec())
            status = engine.status(job_id)

        self.assertEqual(status.state, "running")
        self.assertEqual(status.engine_id, "anthropic_batch")

    def test_result_succeeded_all_candidates(self):
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get = MagicMock(return_value=_fake_batch_status_ended(errored=0))
        mock_jsonl = MagicMock(return_value=_fake_results_jsonl(6))

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_get", mock_get), \
             patch("corvin_compute.engines.anthropic_batch._http_get_jsonl", mock_jsonl), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(_make_spec())
            result = engine.result(job_id, wait_s=0.1)

        self.assertEqual(result.state, "succeeded")
        self.assertFalse(result.result.get("partial"))
        self.assertEqual(result.result["failed_candidate_count"], 0)
        self.assertIsNotNone(result.result["best_loss"])
        self.assertLessEqual(
            result.result["best_loss"],
            result.result["top_k"][0]["loss"],
        )

    def test_result_partial_some_candidates_errored(self):
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get = MagicMock(return_value=_fake_batch_status_ended(errored=2))
        mock_jsonl = MagicMock(return_value=_fake_results_jsonl(6, errored_indices={1, 3}))

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_get", mock_get), \
             patch("corvin_compute.engines.anthropic_batch._http_get_jsonl", mock_jsonl), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(_make_spec())
            result = engine.result(job_id, wait_s=0.1)

        self.assertEqual(result.state, "partial")
        self.assertTrue(result.result["partial"])
        self.assertEqual(result.result["failed_candidate_count"], 2)
        self.assertEqual(result.result["succeeded_count"], 4)

    def test_result_failed_all_candidates_errored(self):
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get = MagicMock(return_value=_fake_batch_status_ended(errored=6))
        mock_jsonl = MagicMock(
            return_value=_fake_results_jsonl(6, errored_indices={0, 1, 2, 3, 4, 5})
        )

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_get", mock_get), \
             patch("corvin_compute.engines.anthropic_batch._http_get_jsonl", mock_jsonl), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(_make_spec())
            result = engine.result(job_id, wait_s=0.1)

        self.assertEqual(result.state, "failed")

    def test_abort_calls_cancel_endpoint(self):
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_cancel = MagicMock(return_value={"id": "msgbatch_test123456789"})

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_post_empty", mock_cancel), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(_make_spec())
            engine.abort(job_id)
            status = engine.status(job_id)

        self.assertEqual(status.state, "aborted")
        mock_cancel.assert_called_once()
        self.assertIn("/cancel", mock_cancel.call_args[0][0])

    def test_unknown_job_id_raises(self):
        from corvin_compute.engine_protocol import UnknownJobId
        engine = AnthropicBatchEngine()
        with self.assertRaises(UnknownJobId):
            engine.status("abatch_doesnotexist")

    def test_submit_raises_on_empty_param_grid(self):
        engine = AnthropicBatchEngine()
        spec = _make_spec()
        spec.param_grid = {}
        spec.budget = {"max_iterations": 0}

        with patch.object(engine, "_assert_compliance", return_value=None):
            with self.assertRaises((ValueError, Exception)):
                engine.submit(spec)

    def test_result_extractor_json_key_used(self):
        """result_extractor=json:score must be forwarded from extra to _BatchJob."""
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get = MagicMock(return_value=_fake_batch_status_ended(errored=0))
        jsonl_lines = [{
            "custom_id": "c0",
            "result": {
                "type": "succeeded",
                "message": {"content": [{"type": "text",
                                         "text": '{"score": 0.99}'}]},
            },
        }]
        mock_jsonl = MagicMock(return_value=jsonl_lines)
        spec = _make_spec(
            param_grid={"x": [1]},
            extra={"prompt_template": "x={{x}}", "result_extractor": "json:score",
                   "model": "claude-haiku-4-5-20251001", "max_tokens_per_call": 16},
        )

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_get", mock_get), \
             patch("corvin_compute.engines.anthropic_batch._http_get_jsonl", mock_jsonl), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(spec)
            result = engine.result(job_id, wait_s=0.1)

        self.assertEqual(result.state, "succeeded")
        self.assertAlmostEqual(result.result["best_loss"], 0.99)

    def test_minimise_false_sorts_descending(self):
        """minimise=False must pick highest-value candidate as best."""
        engine = AnthropicBatchEngine()
        mock_post = MagicMock(return_value=_fake_batch_submit_response())
        mock_get = MagicMock(return_value=_fake_batch_status_ended(errored=0))
        # Candidate c0 has value 0.1, c1 has 0.9 — best for maximise is c1
        jsonl_lines = [
            {"custom_id": "c0", "result": {"type": "succeeded",
             "message": {"content": [{"type": "text", "text": "0.1"}]}}},
            {"custom_id": "c1", "result": {"type": "succeeded",
             "message": {"content": [{"type": "text", "text": "0.9"}]}}},
        ]
        mock_jsonl = MagicMock(return_value=jsonl_lines)
        spec = _make_spec(
            param_grid={"x": [1, 2]},
            minimise=False,
        )

        with patch("corvin_compute.engines.anthropic_batch._http_post", mock_post), \
             patch("corvin_compute.engines.anthropic_batch._http_get", mock_get), \
             patch("corvin_compute.engines.anthropic_batch._http_get_jsonl", mock_jsonl), \
             patch.object(engine, "_assert_compliance", return_value=None):
            job_id = engine.submit(spec)
            result = engine.result(job_id, wait_s=0.1)

        self.assertEqual(result.state, "succeeded")
        self.assertAlmostEqual(result.result["best_loss"], 0.9)


# ── open_batches.json state tests (L8 cleanup) ────────────────────────────────

class TestOpenBatchesState(unittest.TestCase):

    def test_record_and_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session_key = "discord:123456"
            path = _open_batches_path(session_key, home / "tenants" / "_default")

            from corvin_compute.engines.anthropic_batch import (
                _record_open_batch, _remove_open_batch,
            )

            with patch("corvin_compute.engines.anthropic_batch._corvin_home",
                       return_value=home):
                _record_open_batch(session_key, "job1", "msgbatch_abc123456789012", 42)
                self.assertTrue(path.exists())
                entries = json.loads(path.read_text())
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["batch_id"], "msgbatch_abc123456789012")

                _remove_open_batch(session_key, "msgbatch_abc123456789012")
                self.assertFalse(path.exists())

    def test_cancel_open_batches_for_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session_key = "discord:99999"
            path = _open_batches_path(session_key, home / "tenants" / "_default")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([
                {"job_id": "abatch_1", "batch_id": "msgbatch_aaa000111222333",
                 "submitted_at": 0, "candidate_count": 10},
                {"job_id": "abatch_2", "batch_id": "msgbatch_bbb000111222333",
                 "submitted_at": 0, "candidate_count": 5},
            ]))
            mock_cancel = MagicMock(return_value={})

            with patch("corvin_compute.engines.anthropic_batch._corvin_home",
                       return_value=home), \
                 patch("corvin_compute.engines.anthropic_batch._http_post_empty",
                       mock_cancel):
                cancelled = cancel_open_batches_for_session(session_key)

            self.assertEqual(len(cancelled), 2)
            self.assertEqual(mock_cancel.call_count, 2)
            self.assertFalse(path.exists())

    def test_cancel_handles_missing_file_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = cancel_open_batches_for_session("discord:nonexistent")
            self.assertEqual(result, [])

    def test_cancel_continues_on_api_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session_key = "discord:77777"
            path = _open_batches_path(session_key, home / "tenants" / "_default")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([
                {"job_id": "abatch_x", "batch_id": "msgbatch_ccc000111222333",
                 "submitted_at": 0, "candidate_count": 1},
            ]))

            def fail_cancel(url):
                raise RuntimeError("network error")

            with patch("corvin_compute.engines.anthropic_batch._corvin_home",
                       return_value=home), \
                 patch("corvin_compute.engines.anthropic_batch._http_post_empty",
                       fail_cancel):
                # Must not raise — best-effort contract
                result = cancel_open_batches_for_session(session_key)
            self.assertEqual(result, [])


# ── data classification allow-list test ───────────────────────────────────────

class TestL34AllowList(unittest.TestCase):
    def test_anthropic_batch_in_default_compliance(self):
        sys.path.insert(0, str(
            Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        ))
        from data_classification import DEFAULT_ENGINE_COMPLIANCE
        self.assertIn("anthropic_batch", DEFAULT_ENGINE_COMPLIANCE)
        entry = DEFAULT_ENGINE_COMPLIANCE["anthropic_batch"]
        self.assertEqual(entry.locality, "us_cloud")
        self.assertEqual(entry.network_egress, "external")


if __name__ == "__main__":
    unittest.main()
