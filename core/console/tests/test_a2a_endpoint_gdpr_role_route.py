"""ADR-2294: the console shows and edits an endpoint's organisation and GDPR role.

Real console router, real session cookie, real CSRF check, real tenant audit chain (``_sandbox``).
``PUT /v1/console/remote-trigger/endpoints/{id}/gdpr-role`` is the operator action; the listing
``GET /v1/console/remote-trigger/endpoints`` shows both fields (``unset`` when nothing was stated).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_EP = "/v1/console/remote-trigger/endpoints"


def _write_endpoint(d: Path, eid: str, **extra) -> Path:
    cfg = {"endpoint_id": eid, "url": "https://peer.example.com/v1/a2a/receive", "hmac_key": "a" * 64,
           "recv_key": "b" * 64, "instance_id": "iid-" + eid, "enabled": True, "label": "Peer " + eid, **extra}
    p = d / f"{eid}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)
    return p


class EndpointGdprRoleRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.eps = self.tmp / "endpoints"
        self.eps.mkdir()
        self._prev = os.environ.get("REMOTE_ENDPOINTS_DIR")
        os.environ["REMOTE_ENDPOINTS_DIR"] = str(self.eps)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("REMOTE_ENDPOINTS_DIR", None)
        else:
            os.environ["REMOTE_ENDPOINTS_DIR"] = self._prev

    def _listing(self, client) -> dict:
        r = client.get(_EP)
        self.assertEqual(r.status_code, 200, r.text)
        return {e["endpoint_id"]: e for e in r.json()["endpoints"]}

    def test_listing_shows_unset_until_an_operator_states_a_role(self):
        _write_endpoint(self.eps, "peer-a")
        # a friendship endpoint (the usual pairing) is editable here too
        _write_endpoint(self.eps, "peer-f", _friendship=True, state="ACTIVE")
        with _sandbox(self.tmp) as (client, csrf, home, _):
            rows = self._listing(client)
            self.assertEqual((rows["peer-a"]["organisation"], rows["peer-a"]["gdpr_role"]), ("", "unset"))
            r = client.put(f"{_EP}/peer-f/gdpr-role", headers={"X-CSRF-Token": csrf},
                           json={"organisation": "Acme Analytics GmbH", "gdpr_role": "processor"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"ok": True, "endpoint_id": "peer-f",
                                        "organisation": "Acme Analytics GmbH", "gdpr_role": "processor"})
            rows = self._listing(client)
            self.assertEqual((rows["peer-f"]["organisation"], rows["peer-f"]["gdpr_role"]),
                             ("Acme Analytics GmbH", "processor"))
            self.assertEqual(rows["peer-a"]["gdpr_role"], "unset")
            cfg = json.loads((self.eps / "peer-f.json").read_text())
            self.assertEqual((cfg["organisation"], cfg["gdpr_role"]), ("Acme Analytics GmbH", "processor"))
            self.assertEqual(cfg["hmac_key"], "a" * 64)
            self.assertTrue(cfg["_friendship"])

            recs = [e for e in _audit_events(home) if e.get("event_type") == "federation.endpoint_role_set"]
            self.assertEqual(len(recs), 1)
            # chain_dna / _-prefixed keys are added by the chain writer itself, not by the emitter
            d = {k: v for k, v in recs[0]["details"].items() if not k.startswith("_") and k != "chain_dna"}
            self.assertEqual(d["endpoint_id"], "peer-f")
            self.assertEqual(d["gdpr_role"], "processor")
            self.assertTrue(d["actor"].startswith("console:"), d)
            self.assertEqual(set(d), {"endpoint_id", "gdpr_role", "actor", "tenant_id"})
            self.assertNotIn("Acme", json.dumps(_audit_events(home)), "no free text in the chain")

    def test_write_needs_csrf_and_a_session(self):
        _write_endpoint(self.eps, "peer-a")
        with _sandbox(self.tmp) as (client, csrf, home, _):
            body = {"organisation": "Acme", "gdpr_role": "controller"}
            r = client.put(f"{_EP}/peer-a/gdpr-role", json=body)
            self.assertEqual(r.status_code, 403, r.text)
            r = client.put(f"{_EP}/peer-a/gdpr-role", json=body, headers={"X-CSRF-Token": "wrong"})
            self.assertEqual(r.status_code, 403, r.text)
            client.cookies.clear()
            r = client.put(f"{_EP}/peer-a/gdpr-role", json=body, headers={"X-CSRF-Token": csrf})
            self.assertIn(r.status_code, (401, 403), r.text)
            self.assertNotIn("gdpr_role", json.loads((self.eps / "peer-a.json").read_text()))

    def test_invalid_values_are_refused_with_422_and_change_nothing(self):
        path = _write_endpoint(self.eps, "peer-a")
        before = path.read_text()
        with _sandbox(self.tmp) as (client, csrf, home, _):
            h = {"X-CSRF-Token": csrf}
            for body in (
                {"organisation": "Acme", "gdpr_role": "data_processor"},
                {"organisation": "Acme", "gdpr_role": "Processor"},
                {"organisation": "Acme"},
                {"organisation": "", "gdpr_role": "processor"},
                {"organisation": "x" * 81, "gdpr_role": "processor"},
                {"organisation": "<b>Acme</b>", "gdpr_role": "processor"},
                {"organisation": "Acme‮", "gdpr_role": "processor"},
                {"gdpr_role": "processor"},
            ):
                r = client.put(f"{_EP}/peer-a/gdpr-role", json=body, headers=h)
                self.assertEqual(r.status_code, 422, (body, r.text))
            self.assertEqual(path.read_text(), before)
            self.assertEqual([e for e in _audit_events(home) if e.get("event_type") == "federation.endpoint_role_set"],
                             [])

    def test_unknown_and_unsafe_endpoint_ids(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            h = {"X-CSRF-Token": csrf}
            body = {"organisation": "Acme", "gdpr_role": "joint"}
            self.assertEqual(client.put(f"{_EP}/nope/gdpr-role", json=body, headers=h).status_code, 404)
            self.assertEqual(client.put(f"{_EP}/.hidden/gdpr-role", json=body, headers=h).status_code, 400)


if __name__ == "__main__":
    unittest.main()
