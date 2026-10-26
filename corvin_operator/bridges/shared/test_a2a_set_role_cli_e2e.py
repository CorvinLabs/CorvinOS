"""E2E: ``corvin-a2a set-role`` and ``list-endpoints`` through the real CLI process (ADR-2294).

The CLI runs as a SUBPROCESS exactly as an operator runs it, against a real endpoints directory and a real
(sandboxed) tenant audit chain. Covers: the role and organisation are stored through the shared writer,
``federation.endpoint_role_set`` is audited with actor ``cli`` and no free text, ``list-endpoints`` prints
organisation and role (an unset role as "unset - operator decision pending"), and the refusals (unknown
endpoint: exit 2; invalid role or organisation: nothing written, nothing audited).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

REPO = _here.parents[2]
CLI = REPO / "corvin_operator" / "voice" / "scripts" / "corvin_a2a.py"
PIN = REPO / "tests" / "e2e" / "fresh_system" / "a2a_member_pin"


def _write_endpoint(d: Path, eid: str, **extra) -> Path:
    p = d / f"{eid}.json"
    p.write_text(json.dumps({
        "endpoint_id": eid, "url": "https://peer.example.com/v1/a2a/receive", "hmac_key": "a" * 64,
        "recv_key": "b" * 64, "instance_id": "iid-" + eid, "enabled": True, **extra}))
    p.chmod(0o600)
    return p


class TestSetRoleCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.endpoints = self.tmp / "endpoints"
        self.endpoints.mkdir()
        self.home = self.tmp / "corvin_home"
        (self.home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        self.key = self.tmp / "anchor.key"
        self.key.write_bytes(os.urandom(32))
        self.key.chmod(0o600)

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("FORGE_ROOT", None)
        env.update({
            "REMOTE_ENDPOINTS_DIR": str(self.endpoints), "CORVIN_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.tmp / "xdg"), "CORVIN_AUDIT_ANCHOR_KEY": str(self.key),
            "CORVIN_TENANT_ID": "_default", "CORVIN_A2A_ATTESTATION_DISABLED": "1",
            "PYTHONPATH": os.pathsep.join(filter(None, [str(PIN), str(REPO), env.get("PYTHONPATH", "")])),
        })
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=120, env=env)

    def _chain(self) -> list[dict]:
        p = self.home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    def _role_events(self) -> list[dict]:
        return [r for r in self._chain() if r.get("event_type") == "federation.endpoint_role_set"]

    def test_set_role_stores_both_fields_and_audits_with_actor_cli(self):
        path = _write_endpoint(self.endpoints, "peer-a", label="Peer A")
        out = self._cli("set-role", "peer-a", "--role", "processor", "--organisation", "Acme Analytics GmbH")
        self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
        cfg = json.loads(path.read_text())
        self.assertEqual((cfg["organisation"], cfg["gdpr_role"]), ("Acme Analytics GmbH", "processor"))
        self.assertEqual(cfg["hmac_key"], "a" * 64)
        self.assertNotIn("a" * 64, out.stdout, "keys never print")
        recs = self._role_events()
        self.assertEqual(len(recs), 1)
        d = {k: v for k, v in recs[0]["details"].items() if not k.startswith("_") and k != "chain_dna"}
        self.assertEqual(d, {"endpoint_id": "peer-a", "gdpr_role": "processor", "actor": "cli",
                             "tenant_id": "_default"})
        self.assertNotIn("Acme", json.dumps(self._chain()))

    def test_endpoint_is_found_by_label_and_organisation_is_kept_when_omitted(self):
        path = _write_endpoint(self.endpoints, "peer-a", label="Peer A", organisation="Kept GmbH", gdpr_role="joint")
        out = self._cli("set-role", "Peer A", "--role", "controller")
        self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
        cfg = json.loads(path.read_text())
        self.assertEqual((cfg["organisation"], cfg["gdpr_role"]), ("Kept GmbH", "controller"))

    def test_first_role_without_an_organisation_is_refused(self):
        path = _write_endpoint(self.endpoints, "peer-a")
        before = path.read_text()
        out = self._cli("set-role", "peer-a", "--role", "processor")
        self.assertEqual(out.returncode, 2, msg=out.stdout + out.stderr)
        self.assertIn("--organisation", out.stderr)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self._role_events(), [])

    def test_unknown_endpoint_is_exit_2_and_writes_nothing(self):
        out = self._cli("set-role", "no-such-peer", "--role", "processor", "--organisation", "Acme")
        self.assertEqual(out.returncode, 2, msg=out.stdout + out.stderr)
        self.assertIn("error", out.stderr)
        self.assertEqual(list(self.endpoints.iterdir()), [])
        self.assertEqual(self._role_events(), [])

    def test_invalid_role_is_refused_and_writes_nothing(self):
        path = _write_endpoint(self.endpoints, "peer-a")
        before = path.read_text()
        for role in ("data_processor", "Processor", ""):
            out = self._cli("set-role", "peer-a", "--role", role, "--organisation", "Acme")
            self.assertNotEqual(out.returncode, 0, msg=role)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self._role_events(), [])

    def test_invalid_organisation_is_refused_and_writes_nothing(self):
        path = _write_endpoint(self.endpoints, "peer-a")
        before = path.read_text()
        for org in ("<b>Acme</b>", "x" * 81, "  "):
            out = self._cli("set-role", "peer-a", "--role", "processor", "--organisation", org)
            self.assertEqual(out.returncode, 2, msg=(org, out.stdout, out.stderr))
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self._role_events(), [])

    def test_list_endpoints_shows_organisation_and_role(self):
        _write_endpoint(self.endpoints, "peer-a", organisation="Acme GmbH", gdpr_role="processor")
        _write_endpoint(self.endpoints, "peer-b", label="Processor Inc")  # a name that says processor is no role
        out = self._cli("list-endpoints")
        self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
        lines = {ln.split()[0]: ln for ln in out.stdout.splitlines() if ln.strip()}
        self.assertIn("Acme GmbH", lines["peer-a"])
        self.assertIn("processor", lines["peer-a"])
        self.assertIn("unset - operator decision pending", lines["peer-b"])
        self.assertNotIn("a" * 64, out.stdout)


if __name__ == "__main__":
    unittest.main()
