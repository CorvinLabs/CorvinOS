"""Round-two sender review: an unsigned legacy rejection supplies no chain tail; a revoked
endpoint gets its own public text."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import remote_trigger_sender as rts
from test_a2a_relay_hardening import _Instance, _pair


def _send(snd, kid, response):
    with mock.patch.dict(os.environ, {"CORVIN_A2A_ATTESTATION_DISABLED": "1"}), \
            mock.patch("corvin_core.feature_flags.is_enabled", return_value=True), \
            mock.patch("remote_trigger_sender._network_denied", lambda *a, **k: None), \
            mock.patch("remote_trigger_sender._record_feed_task", lambda *a, **k: None), \
            mock.patch("remote_trigger_sender._record_feed_response", lambda *a, **k: None), \
            mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None)), \
            mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                              staticmethod(lambda url, env, t: dict(response))):
        return snd.send(kid, "hello", timeout_s=5)


def test_unsigned_rejection_supplies_no_chain_tail_and_no_anchor_event():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a, b = _Instance(root, "a"), _Instance(root, "b")
        kid = _pair(a, b, "http://127.0.0.1:9")
        snd = a.sender()
        forged = {"task_id": "whatever", "status": "rejected", "data": {},
                  "receiver_chain_tail": "deadbeefdeadbeef" + "x" * 5000}
        res = _send(snd, kid, forged)
        events = [c.args[1] for c in snd._inst_forge_se.write_event.call_args_list]
        assert res.peer_chain_tail == ""
        assert "A2A.chain_anchor_verified" not in events


def test_revoked_endpoint_has_its_own_text():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a, b = _Instance(root, "a"), _Instance(root, "b")
        kid = _pair(a, b, "http://127.0.0.1:9")
        p = a.endpoints / f"{kid}.json"
        cfg = json.loads(p.read_text())
        cfg["_peer_revoked_at"] = 1.0
        p.write_text(json.dumps(cfg))
        p.chmod(0o600)
        res = a.sender().send(kid, "hello", timeout_s=5)
        assert not res.ok
        assert "ended this pairing" in res.error_detail
