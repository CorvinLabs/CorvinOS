"""Zero-config A2A pairing — E2E across real processes.

Requirement (operator, 2026-09-24): "the user only enters the friendship
token and it works". This suite proves it through the real boundaries:

* a real relay server process (``python -m a2a_relay``),
* two independent Corvin instance processes (``a2a_e2e_host.py``), each with
  its own CORVIN_HOME (own instance id, own feature flags, own audit chain)
  running the real receiver, the real connectivity manager and the real
  console route functions the Agent Hub calls for create / import,
* real HTTP between the harness and the hosts, real WebSockets between the
  hosts and the relay.

Both instances are loopback-bound with NO inbound route at all (their
advertised URL points at a closed port) — the hardest topology that used to
be permanently broken: a half-open handshake plus the shared-kid relay
collision. Nothing is configured except what the token carries.

Scenarios:
  1. token only → both sides ACTIVE, peer_knows_us, messages both directions
  2. issuer offline while the token is imported → converges on its own
  3. address change after pairing → the peer learns the new address
  4. revocation on one side → the other side stops claiming "peer knows us"

Run: python3 test_a2a_zero_config_e2e.py   (needs fastapi, uvicorn, websockets)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_PY = sys.executable
_PYTHONPATH = os.pathsep.join(str(p) for p in (
    _HERE, _REPO / "core" / "console", _REPO / "core" / "gateway",
    _REPO / "corvin_operator" / "forge", _REPO,
))

CONVERGE_TIMEOUT_S = 120.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def _http(method: str, url: str, body: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _wait_http(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up")


class _Host:
    """One Corvin instance process."""

    def __init__(self, name: str, root: Path, relay_url: str, advertised_url: str) -> None:
        self.name = name
        self.root = root / name
        self.relay_url = relay_url
        self.advertised_url = advertised_url
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None

    def env(self) -> dict[str, str]:
        home = self.root / "home"
        # A separate installation per host: no inherited Corvin/A2A variable
        # (binding key, feed dir, instance-id path …) may be shared.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CORVIN_", "REMOTE_", "VOICE_", "FORGE_"))}
        env.update({
            "PYTHONPATH": _PYTHONPATH,
            "CORVIN_HOME": str(home),
            "VOICE_AUDIT_PATH": str(self.root / "audit.jsonl"),
            "REMOTE_ORIGINS_DIR": str(self.root / "origins"),
            "REMOTE_ENDPOINTS_DIR": str(self.root / "endpoints"),
            "REMOTE_PENDING_FRIENDSHIPS_DIR": str(self.root / "pending_friendships"),
            "REMOTE_PENDING_DIR": str(self.root / "pending_invites"),
            "CORVIN_A2A_RELAY_URL": self.relay_url,
            "CORVIN_A2A_URL": self.advertised_url,
            "CORVIN_A2A_INGRESS": "off",
            "CORVIN_TENANT_ID": "_default",
        })
        return env

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        log = open(self.root / "host.log", "ab")  # noqa: SIM115 — closed with the process
        self.proc = subprocess.Popen(
            [_PY, str(_HERE / "a2a_e2e_host.py"), "--control-port", str(self.port)],
            env=self.env(), stdout=log, stderr=subprocess.STDOUT, cwd=str(_HERE),
        )
        _wait_http(f"http://127.0.0.1:{self.port}/status", timeout=90)

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        return _http(method, f"http://127.0.0.1:{self.port}{path}", body)

    def connection(self, kid: str) -> dict | None:
        _c, st = self.call("GET", "/status")
        for c in st.get("connections", []):
            if c["kid"] == kid:
                return c
        return None

    def log_tail(self) -> str:
        try:
            return (self.root / "host.log").read_text(errors="replace")[-3000:]
        except OSError:
            return ""


def _fully_linked(c: dict | None) -> bool:
    return bool(c and c.get("state") == "ACTIVE" and c.get("peer_knows_us")
                and c.get("peer_reports_reachable"))


@unittest.skipUnless(_lan_ip(), "needs a non-loopback interface for the advertised address")
class TestZeroConfigPairing(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="a2a-zero-config-"))
        cls.relay_port = _free_port()
        cls.relay_log = open(cls.tmp / "relay.log", "ab")  # noqa: SIM115
        cls.relay = subprocess.Popen(
            [_PY, "-m", "a2a_relay", "--host", "127.0.0.1", "--port", str(cls.relay_port)],
            cwd=str(_HERE), env={**os.environ, "PYTHONPATH": _PYTHONPATH},
            stdout=cls.relay_log, stderr=subprocess.STDOUT,
        )
        _wait_http(f"http://127.0.0.1:{cls.relay_port}/healthz")
        cls.relay_url = f"ws://127.0.0.1:{cls.relay_port}/v1/a2a/relay/connect"
        # Advertised addresses are a real private IP on ports nothing listens
        # on: the address gate accepts them, every direct attempt is refused.
        ip = _lan_ip()
        cls.issuer_url = f"http://{ip}:{_free_port()}"
        cls.redeemer_url = f"http://{ip}:{_free_port()}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.relay.terminate()
        cls.relay.wait(timeout=10)
        cls.relay_log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.issuer = _Host(f"{case}-issuer", self.tmp, self.relay_url, self.issuer_url)
        self.redeemer = _Host(f"{case}-redeemer", self.tmp, self.relay_url, self.redeemer_url)
        self.issuer.start()
        self.redeemer.start()

    def tearDown(self) -> None:
        self.issuer.stop()
        self.redeemer.stop()

    # ── helpers ──
    def _pair(self) -> str:
        code, created = self.issuer.call("POST", "/create", {"label": "issuer"})
        self.assertEqual(code, 200, created)
        code, imported = self.redeemer.call("POST", "/import", {"token": created["token"]})
        self.assertEqual(code, 200, imported)
        return created["kid"]

    def _await(self, predicate, what: str, timeout: float = CONVERGE_TIMEOUT_S):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(1.0)
        self.fail(f"timed out waiting for {what}\n--- issuer log ---\n"
                  f"{self.issuer.log_tail()}\n--- redeemer log ---\n{self.redeemer.log_tail()}")

    def _await_linked(self, kid: str) -> None:
        self._await(lambda: _fully_linked(self.issuer.connection(kid))
                    and _fully_linked(self.redeemer.connection(kid)),
                    "both sides ACTIVE + peer_knows_us + peer_reports_reachable")

    # ── scenarios ──
    def test_1_token_only_pairs_both_directions_over_relay(self):
        kid = self._pair()
        self._await_linked(kid)
        ic, rc = self.issuer.connection(kid), self.redeemer.connection(kid)
        self.assertEqual(ic["via"], "relay")
        self.assertEqual(rc["via"], "relay")
        # Real signed task envelopes, both directions.
        code, sent = self.issuer.call("POST", f"/send/{kid}")
        self.assertEqual(code, 200, sent)
        self.assertTrue(sent["ok"], sent)
        code, sent = self.redeemer.call("POST", f"/send/{kid}")
        self.assertEqual(code, 200, sent)
        self.assertTrue(sent["ok"], sent)
        # One shared slot on the relay, fanned out to both instances.
        _c, health = _http("GET", f"http://127.0.0.1:{self.relay_port}/healthz")
        self.assertGreaterEqual(health["kids_registered"], 1)

    def test_2_issuer_offline_during_import_converges_without_help(self):
        code, created = self.issuer.call("POST", "/create", {"label": "issuer"})
        self.assertEqual(code, 200, created)
        self.issuer.stop()
        code, imported = self.redeemer.call("POST", "/import", {"token": created["token"]})
        self.assertEqual(code, 200, imported)
        self.assertFalse(imported["peer_knows_us"])  # nobody answered
        self.issuer.start()
        self._await_linked(created["kid"])

    def test_3_address_change_reaches_the_peer(self):
        kid = self._pair()
        self._await_linked(kid)
        new_url = f"http://{_lan_ip()}:{_free_port()}"
        self.redeemer.stop()
        self.redeemer.advertised_url = new_url
        self.redeemer.start()
        self._await(lambda: (self.issuer.call("GET", "/status")[1]
                             .get("peer_urls", {}).get(kid, "")
                             .startswith(new_url)),
                    "issuer stored the redeemer's new address")
        self._await_linked(kid)

    def test_4_revocation_is_reflected_on_the_other_side(self):
        kid = self._pair()
        self._await_linked(kid)
        code, _ = self.issuer.call("POST", f"/revoke/{kid}")
        self.assertEqual(code, 200)
        code, res = self.redeemer.call("POST", f"/refresh/{kid}")
        self.assertEqual(code, 200, res)
        self.assertFalse(res["peer_knows_us"], res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
