"""E2E: offline pairing with out-of-band fingerprint verification (ADR-2295, T-0311).

Two installations, each with its OWN sandbox (CORVIN_HOME, XDG_CONFIG_HOME, audit anchor
key, origins dir, endpoints dir — so its own instance id and its own audit chain). Every
pairing step runs through the real ``corvin_a2a.py`` CLI as a subprocess, exactly as an
operator runs it:

  A: ``pair peer-b <url> --offline-pair``           → origin PENDING, endpoint file printed
  B: ``pair-import <file> --json``                   → endpoint PENDING, B prints its id + fingerprint
  A: ``pair-confirm peer-b --peer-instance-id <B>``  → A prints its fingerprint
  A: ``pair-confirm ... --fingerprint <what B read>``→ origin confirmed (audited operator action)
  B: ``pair-confirm to-a --fingerprint <what A read>``→ endpoint confirmed

Installation A's receiver is a real ``a2a_http_server`` (HTTP on 127.0.0.1) bound to A's origins
dir and A's instance id, writing to A's audit chain; B sends with ``corvin_a2a.py send``. The
network manifest is pinned in-process so the attestation grace period can be over ("simulate the
clock") without touching the network.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import _test_isolation  # noqa: E402,F401 — never the live audit chain
import a2a_http_server  # noqa: E402
import a2a_manifest  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402

REPO = _here.parents[2]
CLI = REPO / "corvin_operator" / "voice" / "scripts" / "corvin_a2a.py"
PIN = REPO / "tests" / "e2e" / "fresh_system" / "a2a_member_pin"
_FORGE = REPO / "corvin_operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.append(str(_FORGE))

from forge import security_events as _se  # noqa: E402


def setUpModule() -> None:
    import a2a_test_tier

    a2a_test_tier.pin_member_tier()


def _manifest(mandatory_after: float) -> a2a_manifest.A2AManifest:
    return a2a_manifest.A2AManifest({"issued_at": 1, "attestation_mandatory_after": mandatory_after})


GRACE_OVER = lambda: _manifest(time.time() - 86400)            # noqa: E731
IN_GRACE = lambda: _manifest(time.time() + 30 * 86400)         # noqa: E731


class _Install:
    """One installation: its own home, config dir, anchor key, origins and endpoints."""

    def __init__(self, root: Path, name: str) -> None:
        self.root = root / name
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.anchor = self.root / "anchor" / "audit_anchor.key"
        self.origins = self.root / "origins"
        self.endpoints = self.root / "endpoints"
        for d in (self.home, self.xdg, self.anchor.parent, self.origins, self.endpoints):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def chain(self) -> Path:
        return self.home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"

    def env(self, **extra: str) -> dict:
        env = dict(os.environ)
        for k in ("FORGE_ROOT", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_A2A_ATTESTATION_DISABLED",
                  "CORVIN_TEST_MODE", "CORVIN_FEATURES_URL", "CORVIN_LICENSE_KEY"):
            env.pop(k, None)
        env.update({
            "CORVIN_HOME": str(self.home), "XDG_CONFIG_HOME": str(self.xdg),
            "CORVIN_AUDIT_ANCHOR_KEY": str(self.anchor),
            "REMOTE_ORIGINS_DIR": str(self.origins), "REMOTE_ENDPOINTS_DIR": str(self.endpoints),
            "PYTHONPATH": os.pathsep.join([
                str(PIN), str(_FORGE), str(_here), str(REPO / "core" / "plugins"), str(REPO),
            ]),
        })
        env.update(extra)
        return env

    def cli(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                              timeout=180, env=self.env(**extra), cwd=str(self.root))

    def iid(self) -> str:
        """This installation's instance id, created on first use as on a real install."""
        res = subprocess.run([sys.executable, "-c",
                              "import instance_identity as i; print(i.get_instance_id())"],
                             capture_output=True, text=True, timeout=60, env=self.env())
        assert res.returncode == 0, res.stderr[-2000:]
        return res.stdout.strip()

    def records(self, event: str) -> list[dict]:
        if not self.chain.exists():
            return []
        rows = [json.loads(l) for l in self.chain.read_text().splitlines() if l.strip()]
        return [r for r in rows if r.get("event_type") == event]

    def as_this(self):
        """In-process context of this installation: its home, its anchor key (the forge
        writer caches the key per process, so the cache is reset for the duration)."""
        import contextlib

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.home),
                                                         "CORVIN_AUDIT_ANCHOR_KEY": str(self.anchor)}))
        stack.enter_context(mock.patch.object(_se, "_ANCHOR_KEY_LOADED", False))
        stack.enter_context(mock.patch.object(_se, "_ANCHOR_KEY", None))
        return stack

    def verify(self) -> tuple[bool, list]:
        with self.as_this():
            return _se.verify_chain(self.chain)


class _ChainSE:
    """Real forge writer, pinned to one installation's chain (the receiver runs in-process)."""

    def __init__(self, inst: _Install) -> None:
        self._inst = inst

    def write_event(self, _path, event_type, **kw):
        with self._inst.as_this():
            self._inst.chain.parent.mkdir(parents=True, exist_ok=True)
            return _se.write_event(self._inst.chain, event_type, **kw)


class _Engine:
    name = "noop"
    capabilities: dict = {}

    def spawn(self, prompt, **_k):
        return iter(())

    def cancel(self) -> None:
        pass


def _json_tail(stdout: str) -> dict:
    return json.loads(stdout[stdout.index("{"):])


class _PairHarness(unittest.TestCase):
    """Two installations + A's receiver; the pairing steps as helpers (no tests here)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.a, self.b = _Install(root, "a"), _Install(root, "b")
        self._manifest = IN_GRACE
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(rtr, "_load_a2a_manifest", lambda: self._manifest()).start()
        self.server = None

    # ── installation A's receiver ─────────────────────────────────────────
    def _serve_a(self) -> str:
        if self.server is None:
            self.server = a2a_http_server.build_server(
                host="127.0.0.1", port=0, origins_dir=self.a.origins, engine_factory=lambda: _Engine(),
                instance_id=self.a.iid(), forge_se=_ChainSE(self.a))
            a2a_http_server.serve_in_thread(self.server)
            self.addCleanup(self.server.server_close)
            self.addCleanup(self.server.shutdown)
        h, p = self.server.server_address[:2]
        return f"http://{h}:{p}/v1/a2a/receive"

    def _send(self, endpoint: str = "to-a") -> subprocess.CompletedProcess:
        return self.b.cli("send", endpoint, "hello from b", "--timeout", "30")

    def _sent_ok(self, res: subprocess.CompletedProcess) -> bool:
        try:
            return bool(_json_tail(res.stdout).get("ok"))
        except ValueError:
            return False

    def _assert_sender_refused(self, res: subprocess.CompletedProcess) -> None:
        """B refused locally: nothing reached A, and B's chain names the reason."""
        out = _json_tail(res.stdout)
        self.assertFalse(out["ok"], out)
        self.assertIn("Offline pair not confirmed", out.get("error_detail") or "", out)
        self.assertFalse([r for r in self.a.records("A2A.envelope_received")
                          if r["details"].get("task_id") == out["task_id"]], "nothing reached A")
        self.assertIn("endpoint_offline_pair_unconfirmed",
                      [r["details"].get("reason") for r in self.b.records("A2A.response_rejected")])

    def _assert_receiver_refused(self, res: subprocess.CompletedProcess, reason: str) -> None:
        """A's receiver refused this very envelope, for ``reason``."""
        out = _json_tail(res.stdout)
        self.assertFalse(out["ok"], out)
        reasons = [r["details"].get("reason") for r in self.a.records("A2A.request_rejected")
                   if r["details"].get("task_id") == out["task_id"]]
        self.assertEqual(reasons, [reason])

    # ── the two-step flow, step by step ───────────────────────────────────
    def _step1_a_pairs(self) -> dict:
        url = self._serve_a()
        res = self.a.cli("pair", "peer-b", url, "--offline-pair", "--local-endpoint-id", "to-a")
        self.assertEqual(res.returncode, 0, res.stderr[-3000:])
        origin = json.loads((self.a.origins / "peer-b.json").read_text())
        self.assertEqual(origin["oob_state"], "pending")
        self.assertFalse(origin["enabled"], "a pending offline pair is not usable")
        handoff = _json_tail(res.stdout)
        self.assertEqual(handoff["oob_state"], "pending")
        return handoff

    def _step2_b_imports(self, handoff: dict, name: str = "handoff.json", *extra: str) -> dict:
        f = self.b.root / name
        f.write_text(json.dumps(handoff))
        res = self.b.cli("pair-import", str(f), "--json", *extra)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr[-3000:])
        out = _json_tail(res.stdout)
        ep = json.loads((self.b.endpoints / "to-a.json").read_text())
        self.assertEqual(ep["oob_state"], "pending")
        self.assertFalse(ep["enabled"])
        return out

    def _a_shows(self, b_iid: str) -> str:
        res = self.a.cli("pair-confirm", "peer-b", "--peer-instance-id", b_iid, "--json")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr[-3000:])
        return _json_tail(res.stdout)["fingerprint"]

    def _a_confirms(self, b_iid: str, fp: str) -> subprocess.CompletedProcess:
        return self.a.cli("pair-confirm", "peer-b", "--peer-instance-id", b_iid, "--fingerprint", fp)

    def _b_confirms(self, fp: str) -> subprocess.CompletedProcess:
        return self.b.cli("pair-confirm", "to-a", "--fingerprint", fp)

    def _full_pair(self) -> tuple[str, str]:
        handoff = self._step1_a_pairs()
        imp = self._step2_b_imports(handoff)
        fp_b, b_iid = imp["fingerprint"], imp["instance_id"]
        fp_a = self._a_shows(b_iid)
        self.assertEqual(fp_a, fp_b, "both installations compute the same fingerprint")
        r = self._a_confirms(b_iid, fp_b)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
        r = self._b_confirms(fp_a)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
        return fp_a, b_iid


class OfflinePairE2E(_PairHarness):
    # ── tests ─────────────────────────────────────────────────────────────
    def test_both_confirm_then_accepted_after_the_grace_period(self):
        fp, b_iid = self._full_pair()
        origin = json.loads((self.a.origins / "peer-b.json").read_text())
        self.assertEqual(origin["attestation"], "attested_out_of_band")
        self.assertEqual(origin["sender_instance_id_pin"], b_iid)
        self.assertTrue(origin["enabled"])
        self._serve_a()
        self._manifest = GRACE_OVER
        res = self._send()
        self.assertTrue(self._sent_ok(res), res.stdout + res.stderr[-3000:])
        recv = [r for r in self.a.records("A2A.envelope_received")
                if r["details"].get("origin_id") == "peer-b"]
        self.assertTrue(recv, "A recorded the inbound envelope")
        self.assertEqual(recv[-1]["details"].get("attestation"), "attested_out_of_band")

    def test_wrong_fingerprint_on_the_receiving_side_refuses_and_leaves_no_usable_pair(self):
        handoff = self._step1_a_pairs()
        imp = self._step2_b_imports(handoff)
        wrong = "0000-1111-2222-3333-4444"
        r = self._a_confirms(imp["instance_id"], wrong)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fingerprint", r.stderr.lower())
        origin = json.loads((self.a.origins / "peer-b.json").read_text())
        self.assertEqual(origin["oob_state"], "pending")
        self.assertFalse(origin["enabled"])
        self.assertEqual(self._b_confirms(imp["fingerprint"]).returncode, 0)
        self._serve_a()
        self._assert_receiver_refused(self._send(), "origin_offline_pair_unconfirmed")  # A never confirmed
        refused = self.a.records("a2a.offline_pair_refused")
        self.assertEqual(refused[-1]["details"]["reason"], "fingerprint_mismatch")

    def test_wrong_fingerprint_on_the_sending_side_refuses(self):
        handoff = self._step1_a_pairs()
        imp = self._step2_b_imports(handoff)
        fp_a = self._a_shows(imp["instance_id"])
        self.assertEqual(self._a_confirms(imp["instance_id"], imp["fingerprint"]).returncode, 0)
        r = self._b_confirms("ABCD-ABCD-ABCD-ABCD-ABCD")
        self.assertNotEqual(r.returncode, 0)
        ep = json.loads((self.b.endpoints / "to-a.json").read_text())
        self.assertEqual(ep["oob_state"], "pending")
        self._serve_a()
        self._assert_sender_refused(self._send())
        self.assertTrue(fp_a)

    def test_a_single_side_confirm_is_not_enough(self):
        # only the receiving side confirms
        handoff = self._step1_a_pairs()
        imp = self._step2_b_imports(handoff)
        self.assertEqual(self._a_confirms(imp["instance_id"], imp["fingerprint"]).returncode, 0)
        self._serve_a()
        res = self._send()
        self._assert_sender_refused(res)  # B never confirmed: B must not be able to send
        # flipping `enabled` by hand does not complete a pair either
        p = self.b.endpoints / "to-a.json"
        ep = json.loads(p.read_text())
        ep["enabled"] = True
        p.write_text(json.dumps(ep))
        p.chmod(0o600)
        self._assert_sender_refused(self._send())  # pending is refused on oob_state alone

    def test_only_the_sending_side_confirms_is_not_enough(self):
        handoff = self._step1_a_pairs()
        imp = self._step2_b_imports(handoff)
        fp_a = self._a_shows(imp["instance_id"])
        self.assertEqual(self._b_confirms(fp_a).returncode, 0)
        p = self.a.origins / "peer-b.json"
        o = json.loads(p.read_text())
        o["enabled"] = True       # by hand, without pair-confirm
        p.write_text(json.dumps(o))
        p.chmod(0o600)
        self._serve_a()
        res = self._send()
        self._assert_receiver_refused(res, "origin_offline_pair_unconfirmed")

    def test_a_fingerprint_from_a_previous_pairing_is_refused(self):
        old_fp, b_iid = self._full_pair()
        old_handoff = json.loads((self.b.endpoints / "to-a.json").read_text())
        # replaying the old endpoint file on B: its fingerprint is already used
        f = self.b.root / "old.json"
        old_handoff = dict(old_handoff, enabled=False, oob_state="pending")
        f.write_text(json.dumps(old_handoff))
        r = self.b.cli("pair-import", str(f), "--force", "--json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already used", r.stderr)
        # a fresh pairing: the old fingerprint does not confirm it
        res = self.a.cli("pair", "peer-b", "http://127.0.0.1:9", "--offline-pair",
                         "--local-endpoint-id", "to-a", "--force")
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        imp = self._step2_b_imports(_json_tail(res.stdout), "new.json", "--force")
        self.assertNotEqual(imp["fingerprint"], old_fp, "new keys, new fingerprint")
        r = self._a_confirms(b_iid, old_fp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(json.loads((self.a.origins / "peer-b.json").read_text())["oob_state"], "pending")

    def test_legacy_unconfirmed_offline_pair_keeps_todays_grace_behaviour(self):
        self._full_pair()
        p = self.a.origins / "peer-b.json"
        o = json.loads(p.read_text())
        for k in ("oob_state", "attestation", "oob_fingerprint", "oob_confirmed_at", "pairing_mode"):
            o.pop(k, None)                         # an offline pair from before ADR-2295
        p.write_text(json.dumps(o))
        p.chmod(0o600)
        self._serve_a()
        self.assertTrue(self._sent_ok(self._send()), "inside the grace period: accepted")
        self._manifest = GRACE_OVER
        self._assert_receiver_refused(self._send(), "network_attestation_required")  # as today

    def test_a_tampered_confirmed_origin_is_not_attested(self):
        self._full_pair()
        p = self.a.origins / "peer-b.json"
        o = json.loads(p.read_text())
        o["oob_fingerprint"] = "FFFF-FFFF-FFFF-FFFF-FFFF"
        p.write_text(json.dumps(o))
        p.chmod(0o600)
        self._serve_a()
        self._manifest = GRACE_OVER
        self._assert_receiver_refused(self._send(), "out_of_band_attestation_invalid")
        fails = [r for r in self.a.records("a2a.attestation_failed")
                 if r["details"].get("reason") == "out_of_band_attestation_invalid"]
        self.assertTrue(fails)

    def test_audit_shape_equals_an_online_pair_plus_fingerprint_and_both_operators(self):
        fp, _ = self._full_pair()
        online = self._online_pair_records()
        a_auth = self.a.records("a2a.pairing_authorized")
        self.assertEqual(len(a_auth), 1)
        b_auth = self.b.records("a2a.pairing_authorized")
        self.assertEqual(len(b_auth), 1)
        on_keys = set(online["details"])
        for rec in (a_auth[0], b_auth[0]):
            off_keys = set(rec["details"])
            self.assertEqual(off_keys - on_keys, {"fingerprint_prefix"}, (off_keys, on_keys))
            self.assertTrue(on_keys <= off_keys, (off_keys, on_keys))
            self.assertEqual(rec["details"]["pairing_mode"], "offline")
            self.assertEqual(rec["details"]["attestation"], "attested_out_of_band")
            self.assertEqual(rec["details"]["fingerprint_prefix"], fp.replace("-", "")[:8])
        self.assertEqual(online["details"]["pairing_mode"], "online")
        self.assertEqual(online["details"]["attestation"], "hosted_service")
        # audit-first start on both sides + each operator's confirm
        self.assertEqual(len(self.a.records("a2a.offline_pair_initiated")), 1)
        self.assertEqual(len(self.b.records("a2a.offline_pair_initiated")), 1)
        conf_a = self.a.records("a2a.offline_pair_confirmed")
        conf_b = self.b.records("a2a.offline_pair_confirmed")
        self.assertEqual([r["details"]["side"] for r in conf_a], ["receiver"])
        self.assertEqual([r["details"]["side"] for r in conf_b], ["sender"])
        for r in conf_a + conf_b:
            self.assertEqual(r["details"]["actor"], "operator")
        # no key material, no full fingerprint, in either chain
        origin = json.loads((self.a.origins / "peer-b.json").read_text())
        for inst in (self.a, self.b):
            blob = inst.chain.read_text()
            for secret in (origin["hmac_key"], origin["recv_key"], fp, fp.replace("-", "")):
                self.assertNotIn(secret, blob)
            ok, problems = inst.verify()
            self.assertTrue(ok, problems)

    # ── an online pair, against a local stand-in of the hosted service ────
    def _online_pair_records(self) -> dict:
        class _H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                if self.path == "/v1/pair/authorize":
                    body = json.dumps({"pairing_id": "pid-online-0001", "pairing_cert": "x.y.z"}).encode()
                    self.send_response(200)
                else:
                    body = b"{}"
                    self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        c = _Install(Path(self._tmp.name), "c")
        res = c.cli("pair", "peer-z", "http://127.0.0.1:9",
                    CORVIN_TEST_MODE="1", CORVIN_FEATURES_URL=f"http://127.0.0.1:{srv.server_address[1]}",
                    CORVIN_LICENSE_KEY="aGVhZGVy.cGF5bG9hZA.c2ln")
        self.assertEqual(res.returncode, 0, res.stderr[-3000:])
        recs = c.records("a2a.pairing_authorized")
        self.assertEqual(len(recs), 1)
        ok, problems = c.verify()
        self.assertTrue(ok, problems)
        return recs[0]


if __name__ == "__main__":
    unittest.main()
