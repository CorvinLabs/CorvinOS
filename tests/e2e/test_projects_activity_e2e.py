"""T-0374/T-0375 / ADR-2320 / REVIEW-0060 - the project explorer's channel, over real HTTP (SSE) with real git projects.

Real: sessions for two tenants, the projects API, a real git repository, the real file watcher, real SSE streaming.
What must hold (each line is a loss of CONCEPT-0128 section 3 / the review's findings):
snapshot first with an epoch; a file written in a project appears as ``file_changed`` within 5 s and what was already changed before the
first look does not; a stream ends by itself within its lifetime (uvicorn holds a shutdown for open responses); another tenant gets 404;
the 9th stream gets 429; resume across a changed epoch is flagged and a gap is reported; an event carries a closed field set - no input, no
command body, no URL query, no search text, no file content - even when a raw input dict is handed to ``publish``; a turn finishes exactly once.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_projects_api_e2e as base  # noqa: E402
from test_projects_ops_e2e import _Base  # noqa: E402

from core.projects import activity  # noqa: E402

_URL = base._URL


def _git(root: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", *a], capture_output=True, check=True)


def _read_sse(client, url: str, until=None, limit_s: float = 14.0):
    """Collect (event, data) pairs from a real SSE response until `until(event, data)` is true or the stream ends."""
    out = []
    t0 = time.monotonic()
    with client.stream("GET", url) as r:
        out.append(("__status__", r.status_code))
        if r.status_code != 200:
            return out
        ev, data = None, []
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and ev:
                d = json.loads("".join(data)) if data else {}
                out.append((ev, d))
                if until and until(ev, d):
                    break
                ev, data = None, []
            if time.monotonic() - t0 > limit_s:
                break
    return out


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class ActivityChannel(_Base):
    def test_snapshot_is_cheap_isolated_and_carries_the_epoch(self):
        with self._ctx() as c:
            pid, root = self._new(c, "Snap probe")
            s = c.own.get(f"{_URL}/{pid}/activity").json()
            self.assertEqual((s["epoch"], s["reset"], s["agents"]), (activity.EPOCH, False, []))
            self.assertEqual(c.other.get(f"{_URL}/{pid}/activity").status_code, 404)
            self.assertEqual(c.own.get(f"{_URL}/nope/activity").status_code, 404)
            self.assertEqual(c.other.get(f"{_URL}/{pid}/activity/stream").status_code, 404)
            self.assertEqual(activity.stream_count("_default"), 0, "the snapshot must not open a stream or start a watcher")

    def test_a_file_written_after_the_first_look_is_pushed_within_five_seconds_and_an_old_change_is_not(self):
        with self._ctx() as c:
            pid, root = self._new(c, "Push probe")
            _git(root, "add", "-A")
            _git(root, "commit", "--allow-empty", "-qm", "baseline")
            (root / "old-change.txt").write_text("was there before anyone looked")
            wrote = {}

            def write():
                wrote["t"] = time.time()
                (root / "pushed.txt").write_text("hello\n")
            threading.Timer(1.5, write).start()
            got = _read_sse(c.own, f"{_URL}/{pid}/activity/stream", until=lambda e, d: e == "activity" and d.get("path") == "pushed.txt")
            self.assertEqual(got[0], ("__status__", 200))
            self.assertEqual(got[1][0], "snapshot")
            self.assertEqual((got[1][1]["epoch"], got[1][1]["reset"]), (activity.EPOCH, False))
            hit = [d for e, d in got if e == "activity" and d.get("path") == "pushed.txt"]
            self.assertTrue(hit, got)
            self.assertEqual((hit[0]["kind"], hit[0]["status"], hit[0]["area"]), ("file_changed", "untracked", "code"))
            self.assertTrue(hit[0]["id"].startswith(activity.EPOCH + ":"))
            # Detection latency from the server's own clock: Starlette's TestClient buffers a whole response, so receipt time here would
            # measure the stream's lifetime, not the product. Delivery over a real socket is proven live (curl -N / Playwright).
            self.assertLess(hit[0]["ts"] - wrote["t"], 5.0, "the watcher took longer than 5 s to notice the file")
            self.assertGreaterEqual(hit[0]["ts"], wrote["t"])
            self.assertFalse([d for e, d in got if e == "activity" and d.get("path") == "old-change.txt"], "the first look must be silent")

    def test_a_stream_ends_by_itself_so_it_can_never_hold_a_shutdown(self):
        with self._ctx() as c:
            pid, _root = self._new(c, "Life probe")
            t0 = time.monotonic()
            got = _read_sse(c.own, f"{_URL}/{pid}/activity/stream", limit_s=20)
            took = time.monotonic() - t0
            self.assertEqual(got[-1], ("end", {"reason": "lifetime"}), got[-3:])
            self.assertLess(took, activity.STREAM_LIFETIME_S + 3, f"the stream lived {took:.1f} s")
            self.assertEqual(activity.stream_count("_default"), 0, "a finished stream must unsubscribe")

    def test_the_ninth_stream_is_refused_with_a_real_429(self):
        import asyncio
        with self._ctx() as c:
            pid, _root = self._new(c, "Cap probe")
            loop = asyncio.new_event_loop()

            async def fill():
                return [activity.subscribe("_default", "cap-test", lambda: None) for _ in range(activity.MAX_STREAMS_PER_TENANT)]
            try:
                qs = loop.run_until_complete(fill())
                self.assertEqual(activity.stream_count("_default"), activity.MAX_STREAMS_PER_TENANT)
                r = c.own.get(f"{_URL}/{pid}/activity/stream")
                self.assertEqual(r.status_code, 429, r.text)
                self.assertEqual(r.json()["detail"]["reason_class"], "stream_limit")
                self.assertEqual(c.other.get(f"{_URL}/{pid}/activity/stream").status_code, 404)     # other tenants are not counted against ours
                for q in qs:
                    activity.unsubscribe("_default", "cap-test", q)
                self.assertEqual(activity.stream_count("_default"), 0)
            finally:
                for t in list(asyncio.all_tasks(loop)):
                    t.cancel()
                loop.close()

    def test_resume_across_a_changed_epoch_is_flagged_and_a_gap_is_reported(self):
        with self._ctx() as c:
            pid, _root = self._new(c, "Resume probe")
            for i in range(activity.MAX_EVENTS + 50):
                activity.publish("_default", pid, "tool_use", session_id="s1", name="Read", detail=f"f{i}.py")
            s = c.own.get(f"{_URL}/{pid}/activity", params={"after": f"{activity.EPOCH}:{activity.MAX_EVENTS + 50}"}).json()
            self.assertEqual((s["events"], s["gap"], s["reset"]), ([], False, False))        # up to date
            g = c.own.get(f"{_URL}/{pid}/activity", params={"after": f"{activity.EPOCH}:5"}).json()
            self.assertTrue(g["gap"], "events 6.. fell out of the ring buffer: the client must be told")
            self.assertEqual(g["oldest_seq"], 51)
            self.assertEqual(len(g["events"]), activity.MAX_EVENTS)
            old = c.own.get(f"{_URL}/{pid}/activity", params={"after": "1111111111111:250"}).json()
            self.assertTrue(old["reset"], "a cursor of another epoch means the console restarted")
            self.assertEqual(len(old["events"]), activity.MAX_EVENTS, "a reset sends the whole backlog, not events after a seq of another epoch")
            self.assertEqual(c.own.get(f"{_URL}/{pid}/activity", params={"after": "garbage"}).json()["reset"], False)


class ActivityPrivacy(unittest.TestCase):
    """The review's blocker: 'never prompt or file content' must be enforced, not claimed."""
    MARK = "TOPSECRET-MARKER-7731"

    def test_no_marker_survives_the_description_or_the_channel(self):
        m = self.MARK
        cases = [
            ("Bash", {"command": f"cat > report.md <<'EOF'\n{m}\nEOF"}, "cat"),
            ("Bash", {"command": f"/usr/bin/git commit -m '{m}'"}, "git"),
            ("Bash", {"command": f"curl -H 'Authorization: Bearer {m}' https://x"}, "curl"),
            ("Write", {"file_path": "/home/u/proj/report.md", "content": m}, "report.md"),
            ("Edit", {"file_path": "/home/u/proj/a.py", "old_string": m, "new_string": m}, "a.py"),
            ("WebFetch", {"url": f"https://example.com/p?token={m}"}, "example.com"),
            ("WebSearch", {"query": m}, ""),
            ("mcp__srv__tool", {"anything": m}, ""),
            ("Task", {"description": m, "prompt": m}, ""),
        ]
        tracker = activity.TurnTracker("t-priv", "p-priv", "sess1", "A chat title")
        tracker.start()
        for name, inp, want in cases:
            self.assertEqual(activity.describe_tool(name, inp), want, (name, inp))
            tracker.tool(name, inp, tool_id="tu1")
        tracker.finish()
        snap = json.dumps(activity.snapshot("t-priv", "p-priv"))
        self.assertNotIn(m, snap)
        self.assertNotIn("command", snap)

    def test_a_raw_input_dict_handed_to_publish_is_dropped_by_the_closed_field_set(self):
        """Mutation probe from the plan: a producer that passes the raw input must not get it onto the channel."""
        ev = activity.publish("t-priv2", "p", "tool_use", session_id="s", name="Write", detail="a.py",
                              input={"file_path": "/x/a.py", "content": self.MARK}, content=self.MARK, command=self.MARK, text=self.MARK)
        dumped = json.dumps(ev)
        self.assertNotIn(self.MARK, dumped)
        self.assertEqual(set(ev), {"seq", "id", "ts", "kind", "session_id", "name", "detail"})

    def test_publish_never_raises_into_a_turn(self):
        self.assertIsNone(activity.publish("t", "p", "not-a-kind"))                 # an unknown kind is refused, quietly
        for odd in (None, 5, object()):                                              # whatever a producer hands over, no exception escapes
            activity.publish(odd, "p", "tool_use", name=odd, detail=odd, links=odd)  # type: ignore[arg-type]


class TurnLifecycle(unittest.TestCase):
    def test_a_turn_finishes_exactly_once_and_the_agent_goes_idle(self):
        t = activity.TurnTracker("t-life", "p-life", "chat-1", "Plan the release")
        t.start()
        snap = activity.snapshot("t-life", "p-life")
        self.assertEqual([(a["state"], a["title"]) for a in snap["agents"]], [("working", "Plan the release")])
        t.tool("Read", {"file_path": "/x/README.md"}, tool_id="a")
        t.tool("Bash", {"command": "pytest -q"}, tool_id="b", sub=True)
        a = activity.snapshot("t-life", "p-life")["agents"][0]
        self.assertEqual((a["tool"], a["detail"], a["tools"]), ("Bash", "pytest", 2))
        t.reason = "done"
        t.finish()
        t.finish()                                                         # the wrapper's `finally` after a `done`
        evs = activity.snapshot("t-life", "p-life")["events"]
        self.assertEqual([e["kind"] for e in evs], ["turn_started", "tool_use", "tool_use", "turn_finished"])
        self.assertEqual(evs[-1]["reason"], "done")
        self.assertTrue(evs[2]["sub"] and not evs[1]["sub"])
        self.assertEqual(activity.snapshot("t-life", "p-life")["agents"][0]["state"], "idle")

    def test_a_closed_turn_says_closed_not_done(self):
        t = activity.TurnTracker("t-life", "p-closed", "chat-2", "x")
        t.start()
        t.finish()
        last = activity.snapshot("t-life", "p-closed")["events"][-1]
        self.assertEqual((last["kind"], last["reason"], last["ok"]), ("turn_finished", "closed", False))

    def test_idle_agents_expire_when_the_snapshot_is_read(self):
        t = activity.TurnTracker("t-life", "p-expire", "chat-3", "x")
        t.start()
        t.reason = "done"
        t.finish()
        ch = activity._channels[("t-life", "p-expire")]
        ch.agents["chat-3"]["finished"] -= activity.IDLE_AGENT_TTL_S + 5
        self.assertEqual(activity.snapshot("t-life", "p-expire")["agents"], [])


if __name__ == "__main__":
    unittest.main()


# ── live half: a REAL chat turn with the real CLI (CLAUDE_LIVE_E2E=1) ───────────────────────────────

import os  # noqa: E402
import shutil  # noqa: E402

import pytest  # noqa: E402

from test_learning_loop_routes_e2e import _sandbox  # noqa: E402

_live = pytest.mark.skipif(os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
                           reason="needs CLAUDE_LIVE_E2E=1 and the claude CLI")


@_live
class ActivityLive(_Base):
    MARK = "LEAKMARK-4408"

    def test_a_real_turn_shows_up_in_order_and_without_its_content(self):
        with _sandbox(self.tmp / "s") as (client, home, tenant_id, _emitter, _chain):
            spec = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text("spec:\n  default_engine: claude_code\n  engine_models:\n    claude_code:\n      os_model: haiku\n")
            csrf = client.headers.get("X-CSRF-Token") or getattr(client, "csrf", None)
            hdr = {"X-CSRF-Token": csrf} if csrf else {}
            from core.projects import registry
            root = self.tmp / "projects"
            root.mkdir(exist_ok=True)
            registry.set_projects_root(tenant_id, str(root))
            proj = root / "explorer-live"
            proj.mkdir()
            (proj / "README.md").write_text("# live\n")
            _git(proj, "init", "-q")
            _git(proj, "add", "-A")
            _git(proj, "commit", "-qm", "baseline")
            a = client.post(f"{_URL}/adopt", json={"location": str(proj)}, headers=hdr)
            self.assertEqual(a.status_code, 201, a.text)
            pid = a.json()["id"]
            self.assertEqual(client.put(f"{_URL}/{pid}/settings", json={"assistant_access": "write", "confirm": True}, headers=hdr).status_code, 200)
            r0 = client.post("/v1/console/chat/sessions", json={"title": "Explorer live"}, headers=hdr)
            sid = (r0.json().get("session") or {}).get("sid") or r0.json().get("sid") or r0.json().get("session_id")
            self.assertEqual(client.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=hdr).status_code, 200)
            target = proj / "explorer-live.txt"
            ask = f"Create the file {target} containing the text {self.MARK} using your Write tool. Then reply DONE."
            seen = []
            with client.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": ask})
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    m = ws.receive_json()
                    if m.get("type") == "tool_use":
                        seen.append(m.get("name"))
                    if m.get("type") == "done":
                        break
                    self.assertNotEqual(m.get("type"), "error", m)
            self.assertTrue(target.exists(), "the assistant did not write the file")
            snap = client.get(f"{_URL}/{pid}/activity").json()
            kinds = [e["kind"] for e in snap["events"]]
            self.assertEqual(kinds[0], "turn_started", kinds)
            self.assertEqual(kinds[-1], "turn_finished", kinds)
            self.assertEqual(snap["events"][-1]["reason"], "done")
            tools = [e["name"] for e in snap["events"] if e["kind"] == "tool_use"]
            self.assertEqual(tools, seen, "the explorer's tool rows must equal the turn's own tool_use messages, in order")
            self.assertIn("Write", tools)
            w = [e for e in snap["events"] if e["kind"] == "tool_use" and e["name"] == "Write"][0]
            self.assertEqual(w["detail"], "explorer-live.txt")
            dumped = json.dumps(snap)
            for secret in (self.MARK, "Create the file", "using your Write tool", str(proj)):
                self.assertNotIn(secret, dumped, f"{secret!r} leaked into the explorer's events")
            self.assertEqual([a["state"] for a in snap["agents"]], ["idle"])
