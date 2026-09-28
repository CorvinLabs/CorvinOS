"""ADR-2092 E2E — the L5 routing record and outcome through the REAL console ``stream_turn``.

Reuses the ADR-0203 harness (fake claude subprocess, fake ACS runtime, pinned
house-rules classifier, fake license quota); the routing code under test — the
triage, ``delegation_policy.route_and_record`` and the outcome report inside
``_os_emit_completed`` — runs unpatched. Every assertion reads the tenant routing
ledger the turn wrote.
"""
from __future__ import annotations

from unittest.mock import patch

from test_delegation_routing_e2e import _drain, _StreamTurnE2EBase

FANOUT = ("Erstelle einen ausführlichen Reiseplan für zwei Wochen Japan "
          "und dann eine Packliste mit Empfehlungen für jede Jahreszeit "
          "sowie einen Überblick über die wichtigsten Etikette-Regeln")


class ConsoleRoutingLedgerE2E(_StreamTurnE2EBase):
    def _rows(self):
        from core.skills.os_skills.monitoring import routing_ledger

        return routing_ledger.join(routing_ledger.read_records("_default"))

    def test_smalltalk_turn_records_one_native_decision_and_its_outcome(self) -> None:
        events, spawn, _ = self._run_turn("wie spät ist es eigentlich?")
        self.assertTrue(spawn["hit"])
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["surface"], row["used"], row["phase"]), ("console", "native", "shadow"))
        self.assertIsNotNone(row["outcome"])
        self.assertEqual(row["outcome"]["used"], "native")
        self.assertEqual(events[-1].get("type"), "done")

    def test_fanout_turn_records_acs_as_the_served_engine(self) -> None:
        events, _spawn, _ = self._run_turn(FANOUT)
        self.assertTrue(self._took_acs(events))
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["used"], "acs")
        self.assertIsNotNone(rows[0]["outcome"])
        self.assertEqual(rows[0]["outcome"]["used"], "acs")

    def test_delegation_disabled_still_records_exactly_one_native_decision(self) -> None:
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        self._no_spawn_guard()
        with patch.object(self.cr, "_delegation_enabled", return_value=False):
            _drain(self.cr.stream_turn(self.sess, FANOUT))
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["bundled"], rows[0]["used"]), ("native", "native"))
        self.assertIsNotNone(rows[0]["outcome"])

    def test_three_turns_three_rows(self) -> None:
        for prompt in ("hallo", "wie spät ist es eigentlich?", FANOUT):
            self._run_turn(prompt)
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["turn_id"] for r in rows}), 3)
        self.assertTrue(all(r["outcome"] is not None for r in rows))

    def test_ledger_holds_no_prompt_text(self) -> None:
        self._run_turn(FANOUT)
        from core.skills.os_skills.monitoring import routing_ledger

        raw = routing_ledger.ledger_path("_default").read_text()
        self.assertNotIn("Japan", raw)
        self.assertNotIn("Reiseplan", raw)
