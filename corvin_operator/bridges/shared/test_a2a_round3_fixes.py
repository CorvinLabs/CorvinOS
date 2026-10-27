"""Round-three review fixes: digit smuggling through the usage-id grammar, real ids still dropped,
and the outbox hold counting send time twice (ADR-2292, ADR-2300)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a2a_outbox  # noqa: E402
from a2a_usage import valid_model_id  # noqa: E402

SMUGGLED = [
    "gpt-1234k-5678m-9012t", "gpt-1.22.33.44-5.66.77.88", "gpt-123-456-789", "gpt-11-22-33-44",
    "gpt-12b-34b-56b-78b-90b",
    "gpt-19-85-03-14", "gpt-11-22-33-44",
]
REAL = [
    "claude-3-7-sonnet-latest", "gemma-3-27b-it", "gemma3:27b-it-qat", "glm-4.5-air", "open-mistral-nemo",
]


class UsageGrammarRound3(unittest.TestCase):
    def test_digit_runs_split_over_tokens_are_refused(self):
        for v in SMUGGLED:
            self.assertFalse(valid_model_id(v), v)

    def test_real_ids_stay_priced(self):
        for v in REAL:
            self.assertTrue(valid_model_id(v), v)


class OutboxHoldRound3(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("CORVIN_HOME")
        self._tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._old

    def test_hold_subtracts_the_send_time_already_spent(self):
        budget = a2a_outbox._send_budget_s()
        full = a2a_outbox.enqueue("ep", "t-full", "hi", deadline_s=600, maybe_delivered=True, now=1000.0)
        spent = a2a_outbox.enqueue("ep", "t-spent", "hi", deadline_s=600, maybe_delivered=True, now=1000.0,
                                   elapsed_s=budget)
        self.assertEqual(full["stale_until"], 1000.0 + budget + a2a_outbox.RELAY_GRACE_S)
        self.assertEqual(spent["stale_until"], 1000.0 + a2a_outbox.RELAY_GRACE_S)

    def test_garbage_elapsed_keeps_the_full_hold(self):
        budget = a2a_outbox._send_budget_s()
        e = a2a_outbox.enqueue("ep", "t-bad", "hi", deadline_s=600, maybe_delivered=True, now=1000.0,
                               elapsed_s="x")
        self.assertEqual(e["stale_until"], 1000.0 + budget + a2a_outbox.RELAY_GRACE_S)


if __name__ == "__main__":
    unittest.main()
