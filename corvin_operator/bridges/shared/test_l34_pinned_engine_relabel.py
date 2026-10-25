#!/usr/bin/env python3
"""L34 relabel pin for every bundled engine that calls a US cloud API (T-0226, ADR-2268).

ADR-0072 pinned ``claude_code`` only. The other bundled engines that reach a US API
(``acs``, ``acs_worker``, ``anthropic_batch``, ``codex_cli``, ``video_producer*``) could be
relabelled ``local`` / ``eu_cloud`` / egress ``none`` through
``spec.data_classification.engine_compliance`` and were then admitted for CONFIDENTIAL or
SECRET data. They are now rejected like ``claude_code``. ``copilot`` stays overridable: its
bundled note documents GitHub Enterprise Cloud EU residency and GHES on-premise as real cases.

A rejected relabel makes the loader raise, and ``spawn_gates._load_l34_guard`` turns a loader
failure into a refusal of every spawn of that tenant: fail-closed, never "allow".
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import data_classification as dc  # type: ignore  # noqa: E402
import spawn_gates  # type: ignore  # noqa: E402

PINNED = (
    "acs", "acs_worker", "anthropic_batch", "codex_cli",
    "video_producer", "video_producer_openai", "video_producer_edge",
)


def _cfg(engine_id: str, **fields) -> dict:
    return {"spec": {"data_classification": {"engine_compliance": [
        {"engine_id": engine_id, **fields}]}}}


class PinnedEngineRelabelTests(unittest.TestCase):
    def test_every_pinned_engine_is_a_bundled_us_cloud_engine(self) -> None:
        for eid in PINNED:
            ec = dc.DEFAULT_ENGINE_COMPLIANCE[eid]
            self.assertEqual((ec.locality, ec.network_egress), ("us_cloud", "external"), eid)

    def test_relabelling_to_a_safer_place_is_rejected(self) -> None:
        bad = [{"locality": "local"}, {"locality": "eu_cloud"},
               {"locality": "us_cloud", "network_egress": "none"},
               {"locality": "us_cloud", "network_egress": "local"},
               {"locality": "local", "network_egress": "none"}]
        for eid in PINNED:
            for fields in bad:
                with self.subTest(engine=eid, fields=fields):
                    with self.assertRaises(ValueError) as cm:
                        dc.DataFlowGuard.from_tenant_config(_cfg(eid, **fields))
                    self.assertIn(eid, str(cm.exception))

    def test_restating_the_truth_or_narrowing_is_accepted(self) -> None:
        for eid in PINNED:
            with self.subTest(engine=eid):
                g = dc.DataFlowGuard.from_tenant_config(
                    _cfg(eid, locality="us_cloud", network_egress="external", notes="same as bundled"))
                self.assertEqual(g.engine_compliance[eid].locality, "us_cloud")
                g = dc.DataFlowGuard.from_tenant_config(_cfg(eid, locality="unknown"))
                self.assertEqual(g.engine_compliance[eid].locality, "unknown")

    def test_copilot_and_unpinned_engines_stay_overridable(self) -> None:
        g = dc.DataFlowGuard.from_tenant_config(_cfg("copilot", locality="eu_cloud"))
        self.assertEqual(g.engine_compliance["copilot"].locality, "eu_cloud")
        g = dc.DataFlowGuard.from_tenant_config(
            _cfg("my_onprem_engine", locality="local", network_egress="local"))
        self.assertEqual(g.engine_compliance["my_onprem_engine"].locality, "local")

    def test_claude_code_pin_is_unchanged(self) -> None:
        for fields in ({"locality": "local"}, {"locality": "us_cloud", "network_egress": "none"}):
            with self.assertRaises(ValueError) as cm:
                dc.DataFlowGuard.from_tenant_config(_cfg("claude_code", **fields))
            self.assertIn("ADR-0072", str(cm.exception))

    def test_violations_are_listed_without_building_a_guard(self) -> None:
        cfg = {"spec": {"data_classification": {"engine_compliance": [
            {"engine_id": "acs_worker", "locality": "local", "network_egress": "none"},
            {"engine_id": "codex_cli", "locality": "eu_cloud"},
            {"engine_id": "copilot", "locality": "eu_cloud"},
            {"engine_id": "acs", "locality": "us_cloud"},
        ]}}}
        found = dc.pinned_relabel_violations(cfg)
        self.assertEqual(sorted((v["engine_id"], v["field"], v["value"]) for v in found),
                         [("acs_worker", "locality", "local"),
                          ("acs_worker", "network_egress", "none"),
                          ("codex_cli", "locality", "eu_cloud")])
        self.assertEqual(dc.pinned_relabel_violations({}), [])
        self.assertEqual(dc.pinned_relabel_violations({"spec": None}), [])


class RelabelRefusesAtTheRealGateTests(unittest.TestCase):
    """Through the real file and the real pre-spawn gate, not the loader alone."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cfg_path = self.home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
        self.cfg_path.parent.mkdir(parents=True)

    def _gate(self, engine_id: str, classification: str) -> "str | None":
        return spawn_gates.check_l34(engine_id, "acme", classification=classification,
                                     corvin_home=self.home)

    def test_a_relabelled_worker_cannot_receive_secret_data(self) -> None:
        self.cfg_path.write_text(
            "spec:\n  data_classification:\n    engine_compliance:\n"
            "      - engine_id: acs_worker\n        locality: local\n        network_egress: none\n",
            encoding="utf-8")
        self.cfg_path.chmod(0o600)
        refusal = self._gate("acs_worker", "SECRET")
        self.assertIsInstance(refusal, str, "a rejected relabel must refuse, not allow")
        self.assertTrue(refusal)

    def test_a_clean_policy_still_admits_internal_on_a_us_cloud_engine(self) -> None:
        self.cfg_path.write_text(
            "spec:\n  data_classification:\n    engine_compliance:\n"
            "      - engine_id: acs_worker\n        locality: us_cloud\n        network_egress: external\n",
            encoding="utf-8")
        self.cfg_path.chmod(0o600)
        self.assertIsNone(self._gate("acs_worker", "INTERNAL"))


if __name__ == "__main__":
    unittest.main()
