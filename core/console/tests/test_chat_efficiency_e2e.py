"""ADR-2324 - per-chat cost / token-efficiency metrics.

Two layers:

* ``ComputeTests`` - the pure aggregation: definitions, matched denominators, and the
  unknown-is-not-zero cases (missing usage, 0 output tokens, unknown model, truncated and
  withheld diffs, malformed records).
* ``HttpEndToEnd`` - the REAL ``GET /v1/console/chat/sessions/{sid}/efficiency`` route over
  HTTP, reading a turn log written by the REAL ``chat_runtime._append_turn`` (the writer the
  live turn path uses). Only the session cookie lookup is faked. A foreign / missing session
  must be a 404, never another chat's numbers.

Sandbox: CORVIN_HOME / XDG_CONFIG_HOME / HOME in a fresh temp dir, FORGE_ROOT unset.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for _p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / _p))
sys.path.insert(0, str(_REPO))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import chat_efficiency as ce  # noqa: E402

SONNET = "claude-sonnet-5"       # 0.002 in / 0.010 out per 1K
OPUS = "claude-opus-5"           # 0.005 in / 0.025 out per 1K


def _usage(i=100, o=1000, cr=0, cw=0):
    return {"input_tokens": i, "output_tokens": o,
            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}


def _turn(model=SONNET, usage=None, parts=None, ts=1.0, **extra):
    rec = {"role": "assistant", "ts": ts, "parts": parts or [{"kind": "text", "text": "x"}]}
    if usage is not False:
        rec["turn_usage"] = {"model": model, "routed_model": model,
                             "usage": usage if usage is not None else _usage()}
    rec.update(extra)
    return rec


def _diff_part(diff, **kw):
    return {"kind": "tool", "name": "Edit", "id": "t1", "diff": diff, "diff_truncated": False, **kw}


class ComputeTests(unittest.TestCase):
    def test_empty_chat_is_all_unavailable_not_zero(self):
        r = ce.compute_chat_efficiency([])
        self.assertEqual(r["coverage"]["turns"], 0)
        self.assertIsNone(r["cost"]["total_usd"])
        self.assertIsNone(r["cost"]["per_turn_usd"])
        self.assertIsNone(r["cache"]["hit_rate"])
        self.assertIsNone(r["code"]["output_tokens_per_changed_line"])
        self.assertEqual(r["rows"], [])

    def test_cost_matches_rate_card_for_all_four_token_kinds(self):
        # sonnet-5: in 0.002, out 0.010; cache write = 1.25 x in; cache read from the card.
        from core.learning.model_selection_learner import _turn_cost_usd, model_price_per_1k
        u = _usage(i=1000, o=2000, cr=50_000, cw=10_000)
        r = ce.compute_chat_efficiency([_turn(usage=u)])
        expect = _turn_cost_usd(model_price_per_1k(SONNET), 1000, 2000, 10_000, 50_000, SONNET)
        self.assertAlmostEqual(r["cost"]["total_usd"], expect, places=9)
        self.assertGreater(expect, (1000 / 1000) * 0.002 + 2 * 0.010)  # cache is not free
        self.assertAlmostEqual(r["cost"]["per_turn_usd"], expect, places=9)
        self.assertAlmostEqual(r["cost"]["per_1k_output_tokens_usd"], expect / 2, places=9)

    def test_cache_hit_rate_denominator_is_all_input_side_tokens(self):
        r = ce.compute_chat_efficiency([_turn(usage=_usage(i=100, cr=700, cw=200))])
        self.assertAlmostEqual(r["cache"]["hit_rate"], 0.7)

    def test_cache_saving_is_counterfactual_at_plain_input_rate(self):
        r = ce.compute_chat_efficiency([_turn(usage=_usage(i=0, o=0, cr=100_000, cw=0))])
        self.assertGreater(r["cost"]["cache_saving_usd"], 0)

    def test_unknown_model_is_counted_but_never_priced_or_zero(self):
        r = ce.compute_chat_efficiency([_turn(model="mystery-model-9"), _turn(model=SONNET)])
        c = r["coverage"]
        self.assertEqual((c["measured"], c["priced"], c["unpriced"]), (2, 1, 1))
        self.assertEqual(r["unpriced_models"], ["mystery-model-9"])
        # per-turn divides by PRICED turns only
        self.assertAlmostEqual(r["cost"]["per_turn_usd"], r["cost"]["total_usd"])
        by = {m["model"]: m for m in r["models"]}
        self.assertIsNone(by["mystery-model-9"]["cost_usd"])
        self.assertIsNotNone(by[SONNET]["cost_usd"])

    def test_only_unpriced_models_means_no_cost_at_all(self):
        r = ce.compute_chat_efficiency([_turn(model="mystery-model-9")])
        self.assertIsNone(r["cost"]["total_usd"])
        self.assertIsNone(r["cost"]["per_turn_usd"])
        self.assertIsNone(r["cost"]["cache_saving_usd"])

    def test_missing_usage_and_missing_fields_are_excluded_not_zeroed(self):
        partial = _usage()
        partial["cache_read_input_tokens"] = None   # CLI did not report it
        r = ce.compute_chat_efficiency([_turn(usage=False), _turn(usage=partial), _turn()])
        c = r["coverage"]
        self.assertEqual((c["turns"], c["without_usage"], c["incomplete_usage"], c["measured"]), (3, 1, 1, 1))
        self.assertEqual(r["tokens"]["output"], 1000)          # only the measured turn
        self.assertIsNone(r["rows"][0]["input_tokens"])         # not 0
        self.assertIsNone(r["rows"][1]["cache_read_input_tokens"])

    def test_zero_output_tokens_never_divides(self):
        d = _diff_part("@@ -0,0 +1,2 @@\n+a\n+b")
        r = ce.compute_chat_efficiency([_turn(usage=_usage(o=0), parts=[d])])
        self.assertEqual(r["code"]["lines_changed"], 2)
        self.assertIsNone(r["code"]["changed_lines_per_1k_output_tokens"])
        self.assertEqual(r["code"]["output_tokens_per_changed_line"], 0.0)

    def test_zero_changed_lines_never_divides(self):
        r = ce.compute_chat_efficiency([_turn()])
        self.assertEqual(r["code"]["lines_changed"], 0)
        self.assertIsNone(r["code"]["output_tokens_per_changed_line"])
        self.assertIsNone(r["code"]["cost_per_changed_line_usd"])

    def test_line_counts_skip_hunk_headers_and_count_both_directions(self):
        d = _diff_part("@@ -1,3 +1,3 @@\n keep\n-old\n+new\n+extra")
        r = ce.compute_chat_efficiency([_turn(usage=_usage(o=3000), parts=[d])])
        c = r["code"]
        self.assertEqual((c["lines_added"], c["lines_removed"], c["lines_changed"]), (2, 1, 3))
        self.assertAlmostEqual(c["output_tokens_per_changed_line"], 1000.0)
        self.assertAlmostEqual(c["changed_lines_per_1k_output_tokens"], 1.0)
        self.assertFalse(c["lines_lower_bound"])

    def test_truncated_diff_is_flagged_as_lower_bound(self):
        r = ce.compute_chat_efficiency([_turn(parts=[_diff_part("+a", diff_truncated=True)])])
        self.assertTrue(r["code"]["lines_lower_bound"])
        self.assertEqual(r["coverage"]["diff_truncated"], 1)

    def test_withheld_diff_removes_turn_from_line_ratios_only(self):
        withheld = {"kind": "tool", "name": "Edit", "id": "t2", "diff_withheld": "credential"}
        ok = _diff_part("+a\n+b")
        r = ce.compute_chat_efficiency([_turn(parts=[withheld]), _turn(parts=[ok])])
        self.assertEqual(r["coverage"]["diff_withheld"], 1)
        self.assertEqual(r["coverage"]["line_turns"], 1)
        self.assertEqual(r["code"]["lines_changed"], 2)
        # the withheld turn still counts for cost
        self.assertEqual(r["coverage"]["priced"], 2)
        self.assertIsNone(r["rows"][0]["lines_added"])

    def test_line_ratio_uses_output_of_matched_turns_only(self):
        withheld = {"kind": "tool", "name": "Edit", "id": "t2", "diff_withheld": "credential"}
        r = ce.compute_chat_efficiency([
            _turn(usage=_usage(o=9_000_000), parts=[withheld]),   # must not leak into the ratio
            _turn(usage=_usage(o=1000), parts=[_diff_part("+a")]),
        ])
        self.assertAlmostEqual(r["code"]["output_tokens_per_changed_line"], 1000.0)

    def test_notices_btw_user_turns_and_garbage_are_ignored(self):
        recs = [{"role": "user", "ts": 1, "parts": []}, _turn(notice="interruption"),
                _turn(btw=True), None, "x", 5, {"role": "assistant"}, _turn()]
        r = ce.compute_chat_efficiency(recs)
        self.assertEqual(r["coverage"]["turns"], 2)   # bare assistant record + the real one
        self.assertEqual(r["coverage"]["measured"], 1)

    def test_bool_negative_and_nan_counts_are_not_counts(self):
        bad = {"input_tokens": True, "output_tokens": -5, "cache_read_input_tokens": float("nan"),
               "cache_creation_input_tokens": 1.5}
        r = ce.compute_chat_efficiency([_turn(usage=bad)])
        self.assertEqual(r["coverage"]["incomplete_usage"], 1)
        self.assertEqual(r["tokens"]["output"], 0)

    def test_provider_prefixed_and_dated_ids_price_like_the_family(self):
        a = ce.compute_chat_efficiency([_turn(model="anthropic/claude-sonnet-5")])
        b = ce.compute_chat_efficiency([_turn(model="claude-sonnet-5-20260101")])
        self.assertIsNotNone(a["cost"]["total_usd"])
        self.assertAlmostEqual(a["cost"]["total_usd"], b["cost"]["total_usd"])

    def test_rows_are_capped_but_aggregates_cover_everything(self):
        r = ce.compute_chat_efficiency([_turn(ts=float(i)) for i in range(ce.MAX_TURN_ROWS + 25)])
        self.assertEqual(len(r["rows"]), ce.MAX_TURN_ROWS)
        self.assertEqual(r["coverage"]["turns"], ce.MAX_TURN_ROWS + 25)
        self.assertEqual(r["rows"][-1]["ts"], float(ce.MAX_TURN_ROWS + 24))

    def test_contract_flags(self):
        r = ce.compute_chat_efficiency([_turn()], history_truncated=True)
        self.assertEqual(r["schema"], 1)
        self.assertFalse(r["includes_delegated_workers"])
        self.assertEqual(r["window"]["scope"], "chat_lifetime")
        self.assertTrue(r["window"]["history_truncated"])


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "test-fp"
    csrf_token = "t"


class HttpEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        saved = {k: os.environ.get(k) for k in (
            "CORVIN_HOME", "XDG_CONFIG_HOME", "HOME", "USERPROFILE", "CORVIN_TENANT_ID",
            "CORVIN_AUDIT_ANCHOR_KEY", "FORGE_ROOT")}

        def _restore():
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(_restore)
        os.environ.update({"CORVIN_HOME": str(root / "corvin"), "XDG_CONFIG_HOME": str(root / "xdg"),
                           "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
                           "CORVIN_TENANT_ID": "_default",
                           "CORVIN_AUDIT_ANCHOR_KEY": str(root / "anchor.key")})
        os.environ.pop("FORGE_ROOT", None)
        from corvin_console import chat_runtime
        from corvin_console.routes import chat as chat_routes
        self.cr, self.routes = chat_runtime, chat_routes
        self.sess = chat_runtime.create_session("_default")
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.cookies.set("corvin_console_sid", "valid-sid")
        p = patch.object(chat_routes.session_auth, "load_session", return_value=_Rec())
        p.start()
        self.addCleanup(p.stop)

    def _url(self, sid=None):
        return f"/v1/console/chat/sessions/{sid or self.sess.sid}/efficiency"

    def test_fresh_chat_returns_an_honest_empty_payload(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["sid"], self.sess.sid)
        self.assertEqual(body["coverage"]["turns"], 0)
        self.assertIsNone(body["cost"]["total_usd"])

    def test_numbers_come_from_the_turn_log_written_by_the_real_persistence_path(self):
        self.cr._append_turn(self.sess, "user", [{"kind": "text", "text": "bitte aendern"}])
        self.cr._append_turn(
            self.sess, "assistant",
            [{"kind": "text", "text": "ok"}, _diff_part("@@ -1,1 +1,2 @@\n-a\n+b\n+c")],
            cli_spawned=True,
            turn_usage={"model": SONNET, "routed_model": SONNET,
                        "usage": _usage(i=500, o=3000, cr=4000, cw=500)})
        self.cr._append_turn(
            self.sess, "assistant", [{"kind": "text", "text": "ohne usage"}], cli_spawned=True)
        body = self.client.get(self._url()).json()
        self.assertEqual(body["coverage"]["turns"], 2)
        self.assertEqual(body["coverage"]["measured"], 1)
        self.assertEqual(body["coverage"]["without_usage"], 1)
        self.assertEqual(body["tokens"], {"input": 500, "output": 3000, "cache_read": 4000, "cache_write": 500})
        self.assertEqual(body["code"]["lines_changed"], 3)
        self.assertAlmostEqual(body["code"]["output_tokens_per_changed_line"], 1000.0)
        self.assertAlmostEqual(body["cache"]["hit_rate"], 4000 / 5000)
        self.assertGreater(body["cost"]["total_usd"], 0)
        # every section of the contract the UI reads is present
        for key in ("window", "coverage", "tokens", "cost", "cache", "code", "models",
                    "unpriced_models", "price_basis", "rows", "includes_delegated_workers"):
            self.assertIn(key, body)

    def test_unknown_session_is_404_not_someone_elses_numbers(self):
        self.assertEqual(self.client.get(self._url("does-not-exist")).status_code, 404)

    def test_other_tenants_session_is_404(self):
        other = self.cr.create_session("tenant-b")
        self.cr._append_turn(other, "assistant", [{"kind": "text", "text": "x"}],
                             turn_usage={"model": SONNET, "usage": _usage()})
        self.assertEqual(self.client.get(self._url(other.sid)).status_code, 404)


if __name__ == "__main__":
    unittest.main()
