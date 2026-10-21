"""The member-only network as the console reports it (gate 28ad07b54; fresh-system review 2026-10-21).

A Free install: ``friendship/create`` / ``friendship/import`` answer 402 ``license_limit`` /
``a2a.network`` / ``member_seat_required`` before any file is written; every read carries
``network.{allowed, reason, detail}``. Round 2: the reads are polled every 4 s per open peer chat
and used to write a deny pair to the chain each time — one evaluation per window now.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import pytest
from fastapi import HTTPException

_HERE = Path(__file__).resolve().parent
_CONSOLE_PARENT = _HERE.parent
if str(_CONSOLE_PARENT) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PARENT))

from corvin_console.routes import a2a_pair as ap  # type: ignore[import-not-found]  # noqa: E402  # isort: skip

# the route module puts corvin_operator/bridges/shared on sys.path — import the gate after it
import a2a_license_gate as gate  # type: ignore[import-not-found]  # noqa: E402  # isort: skip
from corvin_operator.license import capability_api  # noqa: E402  # isort: skip


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "fp-test"


@pytest.mark.licence_tier("free")
class FreeInstallNetworkGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-net-gate-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.od, self.ed, self.pd = root / "o", root / "e", root / "p"
        for d in (self.od, self.ed, self.pd):
            d.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(root / "home"),
            "VOICE_AUDIT_PATH": str(root / "audit.jsonl"),
            "REMOTE_ORIGINS_DIR": str(self.od),
            "REMOTE_ENDPOINTS_DIR": str(self.ed),
            "REMOTE_PENDING_FRIENDSHIPS_DIR": str(self.pd),
            "CORVIN_A2A_URL": "http://10.9.9.9:8765",
            "CORVIN_A2A_RELAY_URL": "off",
        })
        env.start()
        self.addCleanup(env.stop)
        for name in ("action_performed", "action_failed"):
            p = mock.patch.object(ap.console_audit, name, mock.MagicMock())
            p.start()
            self.addCleanup(p.stop)
        self.evaluations: list[str] = []
        real = capability_api.require_capability

        def counting(capability, requested=1, *, tenant_id, entry_point):
            self.evaluations.append(entry_point)
            return real(capability, requested, tenant_id=tenant_id, entry_point=entry_point)
        p = mock.patch.object(capability_api, "require_capability", counting)
        p.start()
        self.addCleanup(p.stop)
        gate.reset_allow_cache()
        self.addCleanup(gate.reset_allow_cache)

    def _402(self, fn, *a):
        with self.assertRaises(HTTPException) as cm:
            fn(*a)
        self.assertEqual(cm.exception.status_code, 402)
        d = cm.exception.detail
        self.assertEqual((d["error"], d["feature"], d["reason"]), ("license_limit", "a2a.network", "member_seat_required"))
        self.assertIn("member seat", d["msg"])

    def test_create_and_import_answer_402_and_write_nothing(self):
        self._402(ap.friendship_create, ap.FriendshipCreateRequest(label="x"), _Rec())
        self._402(ap.friendship_import, ap.FriendshipImportRequest(token="corvin-a2a:ft1:xx.yy"), _Rec())
        self.assertEqual(list(self.pd.glob("*")), [])
        self.assertEqual(list(self.od.glob("*")) + list(self.ed.glob("*")), [])

    def test_every_read_says_member_seat_required(self):
        conns = ap.friendship_connections(_Rec())
        self.assertEqual(conns["network"]["allowed"], False)
        self.assertEqual(conns["network"]["reason"], "member_seat_required")
        self.assertIn("member seat", conns["network"]["detail"])

    def test_polling_the_reads_evaluates_the_licence_once_per_window(self):
        for _ in range(30):   # two minutes of a 4-s feed poll
            ap.a2a_network_status()
        self.assertEqual(len(self.evaluations), 1, self.evaluations)
        # the status is the gate's verdict, not a second rule: a licence reload re-decides it
        gate.reset_allow_cache()
        ap.a2a_network_status()
        self.assertEqual(len(self.evaluations), 2)

    def test_an_unavailable_checker_is_a_refusal_not_a_pass(self):
        with mock.patch.object(gate, "network_status", side_effect=RuntimeError("broken")):
            status = ap.a2a_network_status()
        self.assertEqual((status["allowed"], status["reason"]), (False, "licence_check_unavailable"))
        self.assertTrue(json.dumps(status))  # serialisable as every read embeds it
