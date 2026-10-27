"""A2A review (transport package): member gate on EVERY pairing writer, and the instance-pin reset.

Real console router, real session cookie, real CSRF check (``_sandbox``).

* Free install: ``pair/generate``, ``pair/redeem``, ``pair/accept``, ``pair/cli-invite``, ``pair/cli-accept``
  and ``friendship-token/confirm`` answer 402 ``member_seat_required`` and write no pairing file and no
  ``a2a.pairing_authorized`` record (until now only ``friendship/create`` and ``friendship/import`` did).
* ``PATCH .../endpoints/{id}`` with ``reset_instance_pin`` is refused (409) for a confirmed out-of-band
  pair (re-pair instead), works for an ordinary pairing, and the audit record names the reset.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_PAIR = "/v1/console/remote-trigger/pair"
_EP = "/v1/console/remote-trigger/endpoints"


def _dirs(tmp: Path):
    import os
    d = {"REMOTE_ORIGINS_DIR": tmp / "o", "REMOTE_ENDPOINTS_DIR": tmp / "e",
         "REMOTE_PENDING_FRIENDSHIPS_DIR": tmp / "p", "REMOTE_PENDING_DIR": tmp / "pi"}
    for k, v in d.items():
        v.mkdir(parents=True, exist_ok=True)
        os.environ[k] = str(v)
    os.environ["CORVIN_A2A_RELAY_URL"] = "off"
    return d


def _files(tmp: Path) -> list[str]:
    return sorted(str(p.relative_to(tmp)) for sub in ("o", "e", "p", "pi") for p in (tmp / sub).rglob("*")
                  if p.is_file())


BODIES = {
    "generate": ("POST", f"{_PAIR}/generate", {"label": "x", "url": "http://10.1.1.1:8765/v1/a2a/receive",
                                                 "console_url": "http://10.1.1.1:8765", "peer_origin_id": "peer"}),
    "redeem": ("POST", f"{_PAIR}/redeem", {"invite_code": "eA==", "our_url": "http://10.1.1.2:8765/v1/a2a/receive",
                                           "our_console_url": "http://10.1.1.2:8765", "our_origin_id": "me"}),
    "accept": ("POST", f"{_PAIR}/accept", {"accept_id": "abc", "ts": 1.0, "nonce": "n", "redeemer_instance_id": "i",
                                            "redeemer_url": "http://10.1.1.2:8765", "redeemer_label": "l",
                                            "origin_id_for_us": "me", "signature": "00"}),
    "cli-invite": ("POST", f"{_PAIR}/cli-invite", {"url": "http://10.1.1.1:8765", "origin_id": "peer"}),
    "cli-accept": ("POST", f"{_PAIR}/cli-accept", {"token": "corvin-a2a:inv1:xx.yy"}),
    "token-confirm": ("POST", f"{_PAIR}/friendship-token/confirm/pend-1", None),
}


@pytest.mark.licence_tier("free")
@pytest.mark.parametrize("name", sorted(BODIES))
def test_free_install_every_pairing_writer_is_refused_and_writes_nothing(tmp_path, name):
    _dirs(tmp_path)
    method, url, body = BODIES[name]
    with _sandbox(tmp_path) as (client, csrf, home, _):
        r = client.request(method, url, headers={"X-CSRF-Token": csrf}, json=body)
        assert r.status_code == 402, (name, r.status_code, r.text)
        d = r.json()["detail"]
        assert (d["feature"], d["reason"]) == ("a2a.network", "member_seat_required")
        assert _files(tmp_path) == []
        assert not [e for e in _audit_events(home) if "pairing_authorized" in json.dumps(e)]


@pytest.mark.licence_tier("member")
def test_member_install_pair_generate_still_works(tmp_path):
    _dirs(tmp_path)
    method, url, body = BODIES["generate"]
    with _sandbox(tmp_path) as (client, csrf, home, _):
        r = client.request(method, url, headers={"X-CSRF-Token": csrf}, json=body)
        assert r.status_code == 200, r.text


def _write_endpoint(tmp: Path, eid: str, **extra) -> Path:
    cfg = {"endpoint_id": eid, "url": "https://peer.example.com/v1/a2a/receive", "hmac_key": "a" * 64,
           "recv_key": "b" * 64, "instance_id": "iid-" + eid, "_peer_instance_id": "iid-" + eid,
           "_peer_bind_pub": "c" * 64, "enabled": True, **extra}
    p = tmp / "e" / f"{eid}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)
    return p


def _write_origin(tmp: Path, eid: str, **extra) -> Path:
    cfg = {"origin_id": eid, "hmac_key": "a" * 64, "recv_key": "b" * 64, "enabled": True,
           "_peer_instance_id": "iid-" + eid, **extra}
    p = tmp / "o" / f"{eid}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)
    return p


class ResetInstancePinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _dirs(self.tmp)

    def test_confirmed_out_of_band_pair_keeps_its_pin(self):
        ep = _write_endpoint(self.tmp, "oob", oob_state="confirmed", attestation="attested_out_of_band")
        _write_origin(self.tmp, "oob", oob_state="confirmed", attestation="attested_out_of_band")
        with _sandbox(self.tmp) as (client, csrf, home, _):
            r = client.patch(f"{_EP}/oob", headers={"X-CSRF-Token": csrf}, json={"reset_instance_pin": True})
            self.assertEqual(r.status_code, 409, r.text)
        cfg = json.loads(ep.read_text())
        self.assertEqual(cfg["instance_id"], "iid-oob")
        self.assertEqual(cfg["_peer_bind_pub"], "c" * 64)

    def test_attestation_on_the_origin_alone_also_protects_the_pin(self):
        ep = _write_endpoint(self.tmp, "oob2")
        _write_origin(self.tmp, "oob2", oob_state="confirmed", attestation="attested_out_of_band")
        with _sandbox(self.tmp) as (client, csrf, home, _):
            r = client.patch(f"{_EP}/oob2", headers={"X-CSRF-Token": csrf}, json={"reset_instance_pin": True})
            self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(json.loads(ep.read_text())["instance_id"], "iid-oob2")

    def test_ordinary_pairing_resets_and_the_audit_names_the_reset(self):
        ep = _write_endpoint(self.tmp, "plain")
        with _sandbox(self.tmp) as (client, csrf, home, _):
            r = client.patch(f"{_EP}/plain", headers={"X-CSRF-Token": csrf},
                             json={"reset_instance_pin": True, "label": "New"})
            self.assertEqual(r.status_code, 200, r.text)
            ev = [e for e in _audit_events(home) if "a2a.endpoint.updated" in json.dumps(e)]
        cfg = json.loads(ep.read_text())
        self.assertNotIn("instance_id", cfg)
        self.assertNotIn("_peer_bind_pub", cfg)
        self.assertTrue(ev, "no a2a.endpoint.updated record")
        blob = json.dumps(ev[-1])
        self.assertIn("reset_instance_pin", blob)
        self.assertIn("label", blob)
