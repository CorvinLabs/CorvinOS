"""A2A endpoint/origin files are HOST-scoped (one identity per instance): a console session of another
tenant must not be able to change them. The feed routes already refuse such a session
(``a2a_feed._a2a_tenant``); the pairing routes used ``rec.tenant_id`` for the audit only.

Real console router, real session cookies for two tenants, real CSRF check (``_sandbox``).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_a2a_endpoint_gdpr_role_route import _write_endpoint  # noqa: E402
from test_admin_route import _sandbox  # noqa: E402

_EP = "/v1/console/remote-trigger/endpoints"
_OR = "/v1/console/remote-trigger/origins"
_PAIR = "/v1/console/remote-trigger/pair"


class EndpointMutationsAreHostScopedTest(unittest.TestCase):
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

    def test_a_session_of_another_tenant_cannot_change_or_delete_an_endpoint(self):
        path = _write_endpoint(self.eps, "peer-a")
        before = path.read_text()
        with _sandbox(self.tmp, tenants=("_default", "other")) as (_host, _hcsrf, _home, clients):
            other, csrf = clients["other"]
            h = {"X-CSRF-Token": csrf}
            r = other.put(f"{_EP}/peer-a/gdpr-role", headers=h,
                          json={"organisation": "Acme", "gdpr_role": "processor"})
            self.assertEqual(r.status_code, 403, r.text)
            r = other.patch(f"{_EP}/peer-a", headers=h, json={"enabled": False})
            self.assertEqual(r.status_code, 403, r.text)
            r = other.delete(f"{_EP}/peer-a", headers=h)
            self.assertEqual(r.status_code, 403, r.text)
            r = other.delete(f"{_OR}/peer-a", headers=h)
            self.assertEqual(r.status_code, 403, r.text)
            r = other.delete(f"{_PAIR}/friendship/peer-a", headers=h)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(path.read_text(), before)

    def test_the_host_tenant_session_still_works(self):
        _write_endpoint(self.eps, "peer-a")
        with _sandbox(self.tmp, tenants=("_default", "other")) as (host, csrf, _home, _clients):
            r = host.put(f"{_EP}/peer-a/gdpr-role", headers={"X-CSRF-Token": csrf},
                         json={"organisation": "Acme", "gdpr_role": "processor"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(json.loads((self.eps / "peer-a.json").read_text())["gdpr_role"], "processor")


if __name__ == "__main__":
    unittest.main()
