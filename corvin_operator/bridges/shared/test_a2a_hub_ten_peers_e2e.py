"""A2A product E2E: one user, ten agents, one token each, everything in the feed.

Operator requirement (2026-09-25): "the user creates a token and can talk to
every agent and watch it all freely in the live feed — with 10 connections."

Everything goes through real boundaries, nothing is imported and called:

* a real relay server process (``python -m a2a_relay``);
* the USER's instance is the real product — ``corvin_gateway.app`` under
  uvicorn with its own CORVIN_HOME and the operator's licence copied in —
  driven only over its console HTTP API with a real session + CSRF token,
  exactly as the Agent Hub does it;
* ten independent agent processes (``a2a_e2e_host.py``), each with its own
  CORVIN_HOME, audit chain, receiver and connectivity manager. They run the
  REAL worker path (framing, workspace, attachment drop + harvest) with a
  scripted model (``E2E_AGENT_MODE=echo``) that echoes the message marker,
  hashes every input file and renders its own reply image.

Topology: the user's hub has NO inbound route (relay only). Agents 1–5 are
directly reachable through their A2A ingress on the LAN address; agents 6–10
have no inbound route either. So both the direct path and the relay fan-out
with 11 registered instances are exercised.

Flow (one ordered scenario, state carries over):
  1. user creates 10 friendship tokens in the console; each agent imports one
  2. all 20 connection ends converge to ACTIVE + bidirectional
  3. user → every agent from the Live Feed composer: text + image; every reply
     carries the marker, the input's hash and a rendered image — visible in
     the user's feed and served safely as a blob
  4. every agent → user: text + attachment arrives in the user's feed
  5. feed integrity: one agent per friend, labels, no duplicates, the
     ``since`` cursor loses nothing under concurrent writes
  6. burst: 3 messages to every agent at once (30 concurrent)
  7. revoking one agent cuts exactly that agent off; the other nine keep working
  8. the live feed UI renders all agents and their images (Playwright, if present)

Run: python3 test_a2a_hub_ten_peers_e2e.py      (E2E_PEERS=N to scale)
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import hashlib
import http.cookiejar
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_PY = sys.executable
N_PEERS = int(os.environ.get("E2E_PEERS", "10"))
N_DIRECT = N_PEERS // 2
CONVERGE_S = 240.0
LIVE_LICENSE = _REPO / ".corvin" / "global" / "license.key"

_AGENT_PYTHONPATH = os.pathsep.join(str(p) for p in (
    _HERE, _REPO / "core" / "console", _REPO / "core" / "gateway",
    _REPO / "corvin_operator" / "forge", _REPO,
))
# Same module path the shipped corvin-webui unit uses.
_HUB_PYTHONPATH = os.pathsep.join(str(p) for p in (
    _REPO / "core" / "console", _REPO / "core" / "gateway", _REPO / "core" / "license",
    _REPO / "core" / "compliance", _REPO / "corvin_operator" / "forge",
    _REPO / "corvin_operator" / "skill-forge", _HERE, _REPO / "core" / "plugins",
))


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


def _png(seed: str, size: int = 20) -> bytes:
    rgb = hashlib.sha256(seed.encode()).digest()[:3]
    raw = b"".join(b"\x00" + rgb * size for _ in range(size))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _http(method: str, url: str, body: dict | None = None, timeout: float = 60,
          opener=None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    op = opener or urllib.request.build_opener()
    try:
        with op.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except ValueError:
            return exc.code, {"raw": raw[:300].decode(errors="replace")}


def _wait_http(url: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except urllib.error.HTTPError:
            return  # answering at all is enough
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError(f"{url} did not come up")


class _Proc:
    def __init__(self, root: Path, name: str) -> None:
        self.root = root / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.proc: subprocess.Popen | None = None

    def _launch(self, argv: list[str], env: dict[str, str]) -> None:
        log = open(self.root / "proc.log", "ab")  # noqa: SIM115
        self.proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     cwd=str(_HERE))

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def log_tail(self, n: int = 2500) -> str:
        try:
            return (self.root / "proc.log").read_text(errors="replace")[-n:]
        except OSError:
            return ""

    def base_env(self, relay_url: str, advertised: str) -> dict[str, str]:
        # Every instance is a separate installation: drop EVERY Corvin/A2A
        # variable the test process carries (pytest sandboxes set some
        # session-wide — a shared binding key or instance-id path would make
        # all 11 instances one identity), then set only this instance's own.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CORVIN_", "REMOTE_", "VOICE_", "FORGE_"))}
        env.update({
            "CORVIN_HOME": str(self.root / "home"),
            "VOICE_AUDIT_PATH": str(self.root / "home" / "tenants" / "_default"
                                    / "global" / "forge" / "audit.jsonl"),
            "REMOTE_ORIGINS_DIR": str(self.root / "origins"),
            "REMOTE_ENDPOINTS_DIR": str(self.root / "endpoints"),
            "REMOTE_PENDING_FRIENDSHIPS_DIR": str(self.root / "pending_friendships"),
            "REMOTE_PENDING_DIR": str(self.root / "pending_invites"),
            "CORVIN_A2A_RELAY_URL": relay_url,
            "CORVIN_A2A_URL": advertised,
            "CORVIN_TENANT_ID": "_default",
            # the name this instance shows to its peers
            "CORVIN_INSTANCE_LABEL": "user-hub" if self.name == "hub" else self.name,
        })
        return env


class _Hub(_Proc):
    """The user's instance: the real gateway + console."""

    def start(self, relay_url: str, advertised: str) -> None:
        home = self.root / "home"
        (home / "global").mkdir(parents=True, exist_ok=True)
        lic = home / "global" / "license.key"
        shutil.copyfile(LIVE_LICENSE, lic)
        lic.chmod(0o600)
        self.port = _free_port()
        env = self.base_env(relay_url, advertised)
        env.update({"PYTHONPATH": _HUB_PYTHONPATH, "CORVIN_A2A_INGRESS": "off"})
        self._launch([_PY, "-m", "uvicorn", "corvin_gateway.app:app", "--host", "127.0.0.1",
                      "--port", str(self.port), "--log-level", "warning"], env)
        self.base = f"http://127.0.0.1:{self.port}/v1/console"
        _wait_http(f"{self.base}/auth/whoami", timeout=120)
        jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with self.op.open(f"{self.base}/auth/local-login", timeout=30):
            pass
        _c, who = self.get("/auth/whoami")
        self.csrf = who["csrf_token"]

    def get(self, path: str, timeout: float = 60) -> tuple[int, dict]:
        return _http("GET", f"{self.base}{path}", opener=self.op, timeout=timeout)

    def mutate(self, method: str, path: str, body: dict | None = None,
               timeout: float = 90) -> tuple[int, dict]:
        return _http(method, f"{self.base}{path}", body, opener=self.op, timeout=timeout,
                     headers={"X-CSRF-Token": self.csrf})

    def raw(self, path: str) -> tuple[bytes, dict]:
        with self.op.open(f"{self.base}{path}", timeout=30) as r:
            # HTTP header names are case-insensitive (uvicorn sends them
            # lowercase): normalise so lookups don't depend on the server.
            return r.read(), {k.lower(): v for k, v in r.headers.items()}


class _Agent(_Proc):
    def start(self, relay_url: str, advertised: str, ingress_port: int | None) -> None:
        self.port = _free_port()
        env = self.base_env(relay_url, advertised)
        env.update({"PYTHONPATH": _AGENT_PYTHONPATH, "E2E_AGENT_MODE": "echo",
                    "E2E_AGENT_NAME": self.name})
        if ingress_port:
            env.update({"CORVIN_A2A_INGRESS": "on", "CORVIN_A2A_INGRESS_PORT": str(ingress_port)})
        else:
            env["CORVIN_A2A_INGRESS"] = "off"
        self._launch([_PY, str(_HERE / "a2a_e2e_host.py"), "--control-port", str(self.port)], env)
        _wait_http(f"http://127.0.0.1:{self.port}/status", timeout=120)

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = 90) -> tuple[int, dict]:
        return _http(method, f"http://127.0.0.1:{self.port}{path}", body, timeout=timeout)

    def connection(self, kid: str) -> dict | None:
        _c, st = self.call("GET", "/status")
        return next((c for c in st.get("connections", []) if c["kid"] == kid), None)


def _linked(c: dict | None) -> bool:
    return bool(c and c.get("state") == "ACTIVE" and c.get("peer_knows_us")
                and c.get("peer_reports_reachable"))


@unittest.skipUnless(_lan_ip(), "needs a non-loopback interface")
@unittest.skipUnless(LIVE_LICENSE.exists(), "needs a Member licence (a2a_peers_max) to pair >1 peer")
class TestHubTenPeers(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="a2a-hub-10-"))
        cls.relay = _Proc(cls.tmp, "relay")
        rport = _free_port()
        cls.relay._launch([_PY, "-m", "a2a_relay", "--host", "127.0.0.1", "--port", str(rport)],
                          {**os.environ, "PYTHONPATH": _AGENT_PYTHONPATH})
        cls.relay_port = rport
        _wait_http(f"http://127.0.0.1:{rport}/healthz")
        relay_url = f"ws://127.0.0.1:{rport}/v1/a2a/relay/connect"
        ip = _lan_ip()
        cls.hub = _Hub(cls.tmp, "hub")
        cls.hub.start(relay_url, f"http://{ip}:{_free_port()}")  # closed port: relay only
        cls.agents: list[_Agent] = []
        for i in range(1, N_PEERS + 1):
            a = _Agent(cls.tmp, f"agent-{i:02d}")
            if i <= N_DIRECT:
                ing = _free_port()
                a.start(relay_url, f"http://{ip}:{ing}", ing)
            else:
                a.start(relay_url, f"http://{ip}:{_free_port()}", None)
            cls.agents.append(a)
        cls.kids: dict[str, str] = {}

    @classmethod
    def tearDownClass(cls) -> None:
        for p in [*getattr(cls, "agents", []), getattr(cls, "hub", None), cls.relay]:
            if p is not None:
                p.stop()
        if os.environ.get("E2E_KEEP") != "1":
            shutil.rmtree(cls.tmp, ignore_errors=True)
        else:
            print(f"\nkept: {cls.tmp}")

    # ── helpers ──
    def _await(self, pred, what: str, timeout: float = CONVERGE_S):
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = pred()
            if got:
                return got
            time.sleep(1.0)
        logs = "\n".join(f"--- {p.name} ---\n{p.log_tail(1500)}"
                         for p in [self.hub, *self.agents[:2], self.agents[-1]])
        self.fail(f"timed out: {what}\n{logs}")

    def _hub_connections(self) -> dict[str, dict]:
        _c, data = self.hub.get("/remote-trigger/pair/friendship/connections")
        return {c["kid"]: c for c in data.get("connections", [])}

    def _feed(self, after: int | None = None) -> dict:
        """The user's feed exactly as the UI reads it: newest page, or every
        message after a seq cursor (drained until has_more is false)."""
        if after is None:
            code, data = self.hub.get("/a2a/feed?limit=1000")
            self.assertEqual(code, 200, data)
            return data
        msgs, cursor = [], after
        while True:
            code, data = self.hub.get(f"/a2a/feed?after={cursor}&limit=100")
            self.assertEqual(code, 200, data)
            msgs.extend(data["messages"])
            cursor = max([cursor, data["last_seq"]])
            if not data["has_more"]:
                data["messages"] = msgs
                return data

    def _cursor(self) -> int:
        return int(self._feed()["last_seq"])

    @staticmethod
    def _own_bind_pub(p: "_Proc") -> str:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization as ser
        raw = (p.root / "home" / "global" / "remote_trigger" / "a2a_bind_key").read_bytes()
        return X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            ser.Encoding.Raw, ser.PublicFormat.Raw).hex()

    # ── the scenario ──
    def test_ten_agents_one_token_each_everything_in_the_feed(self):
        t0 = time.time()
        self._step1_pair()
        self._step2_converge()
        self._step3_user_to_every_agent()
        self._step3b_files_only()
        self._step4_every_agent_to_user()
        self._step5_feed_integrity()
        self._step6_burst()
        self._step7_revoke_one()
        self._step8_ui()
        self._step9_audit_is_complete()
        print(f"\nE2E complete: {N_PEERS} agents, {time.time() - t0:.0f}s")

    def _step1_pair(self):
        for a in self.agents:
            code, created = self.hub.mutate("POST", "/remote-trigger/pair/friendship/create",
                                            {"label": a.name})
            self.assertEqual(code, 200, f"create for {a.name}: {created}")
            self.assertTrue(created["token"].startswith("corvin-a2a:ft1:"), created)
            code, imported = a.call("POST", "/import",
                                    {"token": created["token"], "spawn_worker": True})
            self.assertEqual(code, 200, f"{a.name} import: {imported}")
            self.assertEqual(imported["kid"], created["kid"])
            self.kids[a.name] = created["kid"]
        self.assertEqual(len(set(self.kids.values())), N_PEERS, "every token must mint its own kid")
        print(f"\n[1] paired {N_PEERS} agents")

    def _step2_converge(self):
        def all_linked():
            hub = self._hub_connections()
            return all(_linked(hub.get(k)) for k in self.kids.values()) and all(
                _linked(a.connection(self.kids[a.name])) for a in self.agents)
        self._await(all_linked, f"all {2 * N_PEERS} connection ends ACTIVE + bidirectional")
        hub = self._hub_connections()
        for a in self.agents:
            self.assertEqual(hub[self.kids[a.name]]["label"], a.name)
        # Every connection end exchanged pairing binding keys (ADR-2064): a
        # leaked token cannot re-point or revoke any of these pairings.
        def all_bound():
            for p in [self.hub, *self.agents]:
                for f in (p.root / "endpoints").glob("*.json"):
                    if not json.loads(f.read_text()).get("_peer_bind_pub"):
                        return False
            return True
        self._await(all_bound, "binding keys exchanged on every connection end", 60)
        # ... and each stored key IS that peer's own key (not a shared one).
        own = {p.name: self._own_bind_pub(p) for p in [self.hub, *self.agents]}
        self.assertEqual(len(set(own.values())), len(own), "instances share a binding key")
        for a in self.agents:
            hub_view = json.loads((self.hub.root / "endpoints" / f"{self.kids[a.name]}.json").read_text())
            agent_view = json.loads((a.root / "endpoints" / f"{self.kids[a.name]}.json").read_text())
            self.assertEqual(hub_view["_peer_bind_pub"], own[a.name], f"hub stored a wrong key for {a.name}")
            self.assertEqual(agent_view["_peer_bind_pub"], own["hub"], f"{a.name} stored a wrong hub key")
            # Names: the hub calls the agent by ITS token label, the agent calls
            # the hub by the hub's own name (not by the label meant for itself).
            self.assertEqual(agent_view.get("label"), "user-hub", f"{a.name} mislabels the hub")
        print(f"[2] all {2 * N_PEERS} connection ends linked and key-bound")

    def _step3_user_to_every_agent(self):
        self.out_markers: dict[str, tuple[str, bytes]] = {}
        since = self._cursor()

        def send(a: _Agent):
            marker = f"e2e-{uuid.uuid4().hex[:12]}"
            img = _png(marker)
            self.out_markers[a.name] = (marker, img)
            return self.hub.mutate("POST", "/a2a/feed/send", {
                "peer_id": self.kids[a.name],
                "text": f"[{marker}] Hello {a.name}, please look at this picture.",
                "attachments": [{"name": "photo.png", "mime": "image/png", "content_b64": _b64(img)}],
                "timeout_s": 60,
            })

        with cf.ThreadPoolExecutor(N_PEERS) as ex:
            for code, body in ex.map(send, self.agents):
                self.assertEqual(code, 202, body)

        def replies():
            msgs = self._feed(since)["messages"]
            got = {}
            for a in self.agents:
                marker, _img = self.out_markers[a.name]
                task = next((m for m in msgs if m["kind"] == "task" and marker in m["text"]), None)
                if not task:
                    return None
                resp = next((m for m in msgs if m["kind"] == "response"
                             and m["task_id"] == task["task_id"]), None)
                if not resp:
                    return None
                got[a.name] = (task, resp)
            return got

        got = self._await(replies, f"replies from all {N_PEERS} agents in the user's feed", 180)
        for a in self.agents:
            task, resp = got[a.name]
            marker, img = self.out_markers[a.name]
            kid = self.kids[a.name]
            self.assertEqual((task["direction"], task["peer_id"]), ("out", kid))
            self.assertEqual([x["sha256"] for x in task["attachments"]],
                             [hashlib.sha256(img).hexdigest()])
            self.assertEqual(resp["status"], "ok", f"{a.name}: {resp}")
            out = resp["data"].get("output", "")
            self.assertIn(marker, out, f"{a.name} reply lost the marker: {resp}")
            self.assertIn(a.name, out)
            self.assertIn(hashlib.sha256(img).hexdigest()[:12], out,
                          f"{a.name} did not receive the image intact: {out}")
            [ratt] = resp["attachments"]
            self.assertEqual(ratt["name"], f"{a.name}-reply.png")
            blob, headers = self.hub.raw(
                f"/a2a/feed/blob/{ratt['sha256']}?name={ratt['name']}&mime=image/png")
            self.assertTrue(blob.startswith(b"\x89PNG"))
            self.assertEqual(hashlib.sha256(blob).hexdigest(), ratt["sha256"])
            self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        # The direct path is really used: no relay fallback toward the agents
        # with a working LAN ingress, and fallbacks toward the relay-only ones.
        fallbacks: dict[str, int] = {}
        for chain in (self.hub.root / "home").rglob("audit.jsonl"):
            for line in chain.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"A2A.relay_fallback_used"' in line:
                    ep = json.loads(line).get("details", {}).get("endpoint_id", "")
                    fallbacks[ep] = fallbacks.get(ep, 0) + 1
        direct = [a.name for a in self.agents[:N_DIRECT] if fallbacks.get(self.kids[a.name])]
        self.assertEqual(direct, [], f"direct-path agents fell back to the relay: {direct}")
        self.assertTrue(any(fallbacks.get(self.kids[a.name]) for a in self.agents[N_DIRECT:]),
                        "relay-only agents never used the relay — the topology is not what it claims")
        print(f"[3] user → {N_PEERS} agents: text + image out, reply + rendered image back "
              f"({N_DIRECT} direct, {N_PEERS - N_DIRECT} via relay)")

    def _step3b_files_only(self):
        # Round 8: a picture with no text is a normal message, not an
        # empty-instruction "injection attempt" — one direct, one via relay.
        since = self._cursor()
        targets = [self.agents[0], self.agents[-1]]
        imgs = {}
        for a in targets:
            img = _png(f"files-only-{a.name}")
            imgs[a.name] = img
            code, body = self.hub.mutate("POST", "/a2a/feed/send", {
                "peer_id": self.kids[a.name], "text": "",
                "attachments": [{"name": "only.png", "mime": "image/png", "content_b64": _b64(img)}],
                "timeout_s": 60,
            })
            self.assertEqual(code, 202, body)

        def replies():
            msgs = self._feed(since)["messages"]
            got = {}
            for a in targets:
                sha = hashlib.sha256(imgs[a.name]).hexdigest()
                task = next((m for m in msgs if m["kind"] == "task" and m["direction"] == "out"
                             and [x["sha256"] for x in m["attachments"]] == [sha]), None)
                resp = task and next((m for m in msgs if m["kind"] == "response"
                                      and m["task_id"] == task["task_id"]), None)
                if not resp:
                    return None
                got[a.name] = (task, resp)
            return got

        got = self._await(replies, "replies to the files-only messages", 120)
        for a in targets:
            task, resp = got[a.name]
            sha = hashlib.sha256(imgs[a.name]).hexdigest()
            self.assertEqual(task["text"], "", "the feed keeps what the user typed")
            self.assertEqual(resp["status"], "ok", f"{a.name} refused a files-only message: {resp}")
            self.assertIn(sha[:12], resp["data"].get("output", ""))
            for chain in (a.root / "home").rglob("audit.jsonl"):
                self.assertNotIn("empty_instruction", chain.read_text(encoding="utf-8", errors="replace"),
                                 f"{a.name} logged a false injection signal")
        print("[3b] files-only messages delivered (direct + relay), no false injection signal")

    def _step4_every_agent_to_user(self):
        since = self._cursor()
        self.in_markers = {}

        def send(a: _Agent):
            marker = f"e2e-{uuid.uuid4().hex[:12]}"
            doc = f"report from {a.name}\n".encode()
            self.in_markers[a.name] = (marker, doc)
            return a.call("POST", f"/send/{self.kids[a.name]}", {
                "text": f"[{marker}] Status update from {a.name}.",
                "attachments": [{"name": "report.txt", "mime": "text/plain",
                                 "sha256": hashlib.sha256(doc).hexdigest(), "content_b64": _b64(doc)}],
                "timeout_s": 60,
            })

        with cf.ThreadPoolExecutor(N_PEERS) as ex:
            results = list(ex.map(send, self.agents))
        for a, (code, body) in zip(self.agents, results):
            self.assertEqual(code, 200, body)
            self.assertTrue(body["ok"], f"{a.name} → user: {body}")

        msgs = self._await(lambda: (lambda m: m if all(
            any(x["kind"] == "task" and self.in_markers[a.name][0] in x["text"] for x in m)
            for a in self.agents) else None)(self._feed(since)["messages"]),
            "every agent's message in the user's feed", 60)
        for a in self.agents:
            marker, doc = self.in_markers[a.name]
            task = next(x for x in msgs if x["kind"] == "task" and marker in x["text"])
            self.assertEqual((task["direction"], task["peer_id"]), ("in", self.kids[a.name]))
            [att] = task["attachments"]
            blob, _h = self.hub.raw(f"/a2a/feed/blob/{att['sha256']}?name=report.txt&mime=text/plain")
            self.assertEqual(blob, doc)
            resp = next(x for x in msgs if x["kind"] == "response" and x["task_id"] == task["task_id"])
            self.assertEqual(resp["direction"], "out")
        print(f"[4] {N_PEERS} agents → user: messages + attachments in the feed")

    def _step5_feed_integrity(self):
        feed = self._feed()
        msgs, peers = feed["messages"], {p["peer_id"]: p for p in feed["peers"]}
        self.assertEqual(len({m["id"] for m in msgs}), len(msgs), "duplicate message ids")
        for a in self.agents:
            kid = self.kids[a.name]
            self.assertIn(kid, peers, f"{a.name} missing from the agent list")
            self.assertEqual(peers[kid]["label"], a.name)
            self.assertTrue(peers[kid]["can_send"] and peers[kid]["can_receive"])
            dirs = {(m["direction"], m["kind"]) for m in msgs if m["peer_id"] == kid}
            self.assertEqual(dirs, {("out", "task"), ("in", "response"),
                                    ("in", "task"), ("out", "response")},
                             f"{a.name} is split or incomplete in the feed: {dirs}")
        # The seq cursor must replay the full history without gaps, in small pages.
        seen, cursor = [], 0
        while True:
            code, page = self.hub.get(f"/a2a/feed?after={cursor}&limit=7")
            self.assertEqual(code, 200, page)
            seen.extend(m["id"] for m in page["messages"])
            cursor = page["last_seq"]
            if not page["has_more"]:
                break
        self.assertEqual(sorted(seen), sorted(m["id"] for m in msgs), "seq cursor lost messages")
        # The STORE's append order (on disk) must equal seq order — the API
        # sorts, so checking its output would prove nothing.
        feed_file = next((self.hub.root / "home").rglob("a2a_feed/messages.jsonl"))
        disk = [json.loads(l)["seq"] for l in feed_file.read_text().splitlines() if l.strip()]
        self.assertEqual(disk, sorted(disk), "append order != seq order in the store")
        self.assertEqual(len(disk), len(set(disk)), "duplicate seq in the store")
        print(f"[5] feed: {len(peers)} agents, {len(msgs)} messages, cursor gap-free")

    def _step6_burst(self):
        since = self._cursor()
        markers = []
        jobs = []
        # Round-robin, so the first wave of the hub's 16 send threads carries
        # TWO tasks for agents 1..6 at once — direct (ingress) AND relay-only
        # agents — whose worker spans must then overlap on the agent.
        for _k in range(3):
            for a in self.agents:
                m = f"e2e-{uuid.uuid4().hex[:12]}"
                markers.append(m)
                jobs.append({"peer_id": self.kids[a.name], "text": f"[{m}] [sleep=3] burst"})
        live_ids: set[str] = set()
        stop = threading.Event()

        def live_poll():
            # A live cursor advanced DURING the burst, exactly like the UI.
            cursor = since
            while not stop.is_set():
                code, page = self.hub.get(f"/a2a/feed?after={cursor}&limit=50")
                if code == 200:
                    live_ids.update(m["id"] for m in page["messages"])
                    cursor = max(cursor, page["last_seq"])
                    if page["has_more"]:
                        continue
                time.sleep(0.2)
            while True:  # final drain
                code, page = self.hub.get(f"/a2a/feed?after={cursor}&limit=50")
                live_ids.update(m["id"] for m in page["messages"])
                cursor = max(cursor, page["last_seq"])
                if not page["has_more"]:
                    break

        poller = threading.Thread(target=live_poll, daemon=True)
        poller.start()
        burst_t0 = time.monotonic()
        with cf.ThreadPoolExecutor(len(jobs)) as ex:
            for code, body in ex.map(lambda j: self.hub.mutate("POST", "/a2a/feed/send", j), jobs):
                self.assertEqual(code, 202, body)

        def done():
            msgs = self._feed(since)["messages"]
            tasks = {m["task_id"]: m for m in msgs if m["kind"] == "task"
                     and any(k in m["text"] for k in markers)}
            resps = [m for m in msgs if m["kind"] == "response" and m["task_id"] in tasks]
            return resps if len(tasks) == len(markers) and len(resps) == len(markers) else None

        resps = self._await(done, f"{len(markers)} concurrent replies", 240)
        burst_s = time.monotonic() - burst_t0
        # Concurrency measured where it happens: overlap of each task's whole
        # receive processing [A2A.envelope_received → A2A.response_signed] in
        # the agent's OWN audit chain. A receiver / relay listener handling
        # deliveries one at a time can never overlap two of them. (Worker spans
        # alone jitter with the live L44 classifier call that precedes them.)
        burst_tasks = {r["task_id"] for r in resps}
        overlaps = {}
        for a in self.agents[:6]:
            chain = a.root / "home" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            iv: dict[str, dict[str, float]] = {}
            for line in chain.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"A2A.envelope_received"' not in line and '"A2A.response_signed"' not in line:
                    continue
                rec = json.loads(line)
                tid = rec.get("details", {}).get("task_id")
                if tid in burst_tasks:
                    key = "start" if rec["event_type"] == "A2A.envelope_received" else "end"
                    iv.setdefault(tid, {})[key] = rec["ts"]
            events = sorted([(v["start"], 1) for v in iv.values() if "start" in v and "end" in v]
                            + [(v["end"], -1) for v in iv.values() if "start" in v and "end" in v])
            cur = best = 0
            for _t, d in events:
                cur += d
                best = max(best, cur)
            overlaps[a.name] = best
        self.assertTrue(all(v >= 2 for v in overlaps.values()),
                        f"an agent never processed two deliveries at once — serial: {overlaps}")
        bad = [r for r in resps if r["status"] != "ok"]
        self.assertEqual(bad, [], f"burst failures: {bad[:3]}")
        stop.set()
        poller.join(timeout=30)
        final_ids = {m["id"] for m in self._feed(since)["messages"]}
        self.assertEqual(final_ids - live_ids, set(), "the live cursor skipped messages mid-burst")
        # 30 × 3 s workers finishing in well under 30 × 3 s proves they ran concurrently.
        print(f"[6] burst: {len(markers)} round trips in {burst_s:.0f}s, per-agent concurrent deliveries "
              f"{sorted(set(overlaps.values()))}, live cursor gap-free")

        # A 35 s model turn (> the old fixed 30 s) sent WITHOUT an explicit
        # timeout must still arrive:
        # the default waits the envelope ttl + margin, not a fixed 30 s.
        slow = self.agents[min(4, N_PEERS - 1)]
        m = f"e2e-{uuid.uuid4().hex[:12]}"
        since2 = self._cursor()
        code, body = self.hub.mutate("POST", "/a2a/feed/send",
                                     {"peer_id": self.kids[slow.name], "text": f"[{m}] [sleep=35] slow"})
        self.assertEqual(code, 202, body)

        def slow_done():
            msgs = self._feed(since2)["messages"]
            t = next((x for x in msgs if x["kind"] == "task" and m in x["text"]), None)
            return t and next((x for x in msgs if x["kind"] == "response"
                               and x["task_id"] == t["task_id"]), None)

        r = self._await(slow_done, "reply to a 35 s task", 120)
        self.assertEqual(r["status"], "ok", f"slow task: {r}")
        self.assertIn(m, r["data"].get("output", ""))
        print(f"[6b] 35 s task without explicit timeout: ok after {r['duration_ms'] / 1000:.0f}s")

    def _step7_revoke_one(self):
        victim = self.agents[-1]
        kid = self.kids[victim.name]
        code, body = self.hub.mutate("DELETE", f"/remote-trigger/pair/friendship/{kid}")
        self.assertEqual(code, 200, body)
        self.assertTrue(body.get("peer_notified"), f"revoke notice did not reach the agent: {body}")
        self._await(lambda: (lambda c: c is not None and not c.get("peer_knows_us"))(
            victim.connection(kid)), "the revoked agent sees that the user revoked it", 30)
        code, body = self.hub.mutate("POST", "/a2a/feed/send", {"peer_id": kid, "text": "after revoke"})
        self.assertEqual(code, 404, f"send to a revoked agent must be refused: {body}")
        code, body = victim.call("POST", f"/send/{kid}", {"text": "[e2e-000000000000] after revoke"})
        self.assertEqual(code, 200, body)
        self.assertFalse(body["ok"], f"a revoked agent must not reach the user: {body}")
        def still(a):
            return a.name, a.call("POST", f"/send/{self.kids[a.name]}",
                                  {"text": f"[e2e-{uuid.uuid4().hex[:12]}] still here"})[1]
        with cf.ThreadPoolExecutor(N_PEERS) as ex:
            after = dict(ex.map(still, self.agents[:-1]))
        broken = {n: b for n, b in after.items() if not b.get("ok")}
        self.assertEqual(broken, {}, f"a revocation cut off other agents: {broken}")
        print(f"[7] revoked {victim.name}: cut off and informed; all {N_PEERS - 1} others still deliver")

    def _step8_ui(self):
        web = _REPO / "core" / "console" / "corvin_console" / "web-next"
        if not (web / "node_modules" / "playwright").exists() or not shutil.which("node"):
            print("[8] UI check skipped (no node/playwright)")
            return
        script = web / f".e2e-feed-{uuid.uuid4().hex[:6]}.mjs"
        script.write_text(r"""
import { chromium } from "playwright";
const [base, shot] = process.argv.slice(2);
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1440, height: 1000 } })).newPage();
const errors = [];
p.on("pageerror", (e) => errors.push(String(e)));
await p.goto(`${base}/v1/console/auth/local-login`);
await p.goto(`${base}/console/app/agent-hub`);
// The Live Feed need not be the default tab — open it explicitly.
await p.getByRole('tab', { name: 'Live Feed' }).click({ timeout: 30000 });
await p.waitForSelector('[data-testid="a2a-feed-peer"]', { timeout: 30000 });
await p.waitForTimeout(3000);
const peers = await p.locator('[data-testid="a2a-feed-peer"]').count();
const rail = await p.locator('[data-testid="a2a-feed-peer"]').allInnerTexts();
await p.locator('[data-testid="a2a-feed-peer"]').first().click();
await p.waitForTimeout(2500);
const msgs = await p.locator('[data-testid="a2a-feed-message"]').count();
const imgs = await p.$$eval('[data-testid="a2a-feed-scroll"] img',
  (els) => els.map((e) => [e.complete, e.naturalWidth]));
await p.screenshot({ path: shot });
console.log(JSON.stringify({ peers, msgs, imgs, errors, rail }));
await b.close();
""")
        shot = self.tmp / "feed.png"
        try:
            out = subprocess.run(["node", str(script), f"http://127.0.0.1:{self.hub.port}", str(shot)],
                                 cwd=str(web), capture_output=True, text=True, timeout=120)
        finally:
            script.unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        res = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["peers"], N_PEERS, res)
        # Distinguishable avatars (first line of each rail entry is the initials).
        avatars = [t.splitlines()[0] for t in res["rail"]]
        self.assertEqual(len(set(avatars)), len(avatars), f"indistinguishable avatars: {avatars}")
        # The revoked agent stays readable under its name, marked as removed.
        victim = self.agents[-1].name
        self.assertTrue(any(victim in t and "Connection removed" in t for t in res["rail"]),
                        f"revoked {victim} not shown as removed: {res['rail']}")
        self.assertFalse(any("**" in t for t in res["rail"]), "raw Markdown in the rail preview")
        self.assertGreaterEqual(res["msgs"], 4, res)
        self.assertTrue(res["imgs"] and all(c and w > 0 for c, w in res["imgs"]),
                        f"feed images did not render: {res['imgs']}")
        dest = Path(os.environ.get("E2E_SCREENSHOT", ""))
        if dest.name:
            shutil.copyfile(shot, dest)
        print(f"[8] UI: {res['peers']} agents in the rail, {res['msgs']} messages, "
              f"{len(res['imgs'])} images rendered")

    def _step9_audit_is_complete(self):
        """Every A2A exchange is in each instance's own hash chain, and the
        allowlist floor dropped no field of any A2A event — a dropped field is
        a silent gap in the GDPR Art. 30 record (2026-09-25 review)."""
        # Per agent: every task the user sent it is in ITS OWN chain, and every
        # chain verifies (hash links + anchors) under its own CORVIN_HOME.
        sent: dict[str, set[str]] = {}
        for m in self._feed()["messages"]:
            if m["direction"] == "out" and m["kind"] == "task":
                sent.setdefault(m["peer_id"], set()).add(m["task_id"])
        for a in self.agents:
            chain = a.root / "home" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            got = {json.loads(l)["details"].get("task_id") for l in chain.read_text().splitlines()
                   if '"A2A.envelope_received"' in l}
            missing = sent.get(self.kids[a.name], set()) - got
            self.assertEqual(missing, set(), f"{a.name}: tasks missing from its own audit chain")
        for p in [self.hub, *self.agents]:
            chain = p.root / "home" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            out = subprocess.run(
                [_PY, "-c", "import sys, json; from pathlib import Path; "
                            "from forge.security_events import verify_chain; "
                            "ok, probs = verify_chain(Path(sys.argv[1])); "
                            "print(json.dumps([ok, probs[:3]]))", str(chain)],
                env={**os.environ, "PYTHONPATH": _AGENT_PYTHONPATH, "CORVIN_HOME": str(p.root / "home"),
                     "VOICE_AUDIT_PATH": str(chain)},
                capture_output=True, text=True, timeout=120)
            ok, probs = json.loads(out.stdout.strip().splitlines()[-1])
            self.assertTrue(ok, f"{p.name}: audit chain does not verify: {probs}")
        dropped, count = [], 0
        for chain in self.tmp.rglob("audit.jsonl"):
            for line in chain.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                et = str(rec.get("event_type", ""))
                if not et.lower().startswith(("a2a.", "instance.")):
                    continue
                count += 1
                gone = (rec.get("details") or {}).get("_dropped_fields")
                if gone:
                    dropped.append((chain.parent.parent.parent.parent.parent.name, et, gone))
        self.assertGreater(count, 20 * N_PEERS, "far too few A2A audit events")
        self.assertEqual(dropped, [], f"audit fields dropped: {dropped[:5]}")
        print(f"[9] audit: every task in its receiver's own chain, {N_PEERS + 1} chains verify, "
              f"{count} A2A events, no field dropped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
