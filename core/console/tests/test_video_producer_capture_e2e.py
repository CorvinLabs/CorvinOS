"""HTTP E2E proof for "Add website screenshot" (ADR-2249 D4, T-0113 part 1).

The real console router (TestClient, real session cookie + CSRF, temp CORVIN_HOME), the marketplace
plugin's real ``capture`` module (worker process, Corvin-owned proxy, headless Chrome), its real asset
store and the real tenant audit chain. Nothing is mocked.

* switch OFF (the default) -> 403, nothing captured, the refusal recorded;
* switch ON but no egress policy configured for the tenant -> 403;
* switch ON + policy -> a real capture of https://example.com becomes an asset (NETWORK: this one
  test fetches example.com through the capture proxy; it is the only outbound request of the file);
* a hostile URL is refused with NO asset and is indistinguishable - status, body, time - from an
  unreachable public address and from a target that HANGS after the handshake (T-0194: the floor is
  longer than the whole capture budget); a host the tenant's own egress policy forbids is refused at
  once and says so (the policy is no secret);
* caption, hotspots and the quota are refused BEFORE anything is fetched (no capture_started);
* every capture_started is followed by a capture_finished, also when the capture itself raises;
* hotspots are placed after the capture (PATCH /assets/{id}), audit-first, CSRF, tenant-scoped;
* the audit chain carries the host and a sha256 prefix, never the URL, its path or its query.

Two tests replace the plugin's ``capture_url`` in-process (a hang, an exception): what they prove is
the ROUTE's timing and audit discipline around it; the capture itself is proven by the real tests here
and by the hostile corpus in the plugin (tests/test_capture_policy.py).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import _route_module, _sandbox, _write_tenant_yaml  # noqa: E402
from test_video_producer_styles_e2e import _chain_records  # noqa: E402

import importlib  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731
_HAS_CHROME = any(shutil.which(b) for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))


def _capture(client, csrf, url, caption="The pricing page", headers=None, **extra):
    t0 = time.monotonic()
    r = client.post(f"{V}/capture", headers=H(csrf) if headers is None else headers,
                    json={"url": url, "viewport": "desktop", "caption": caption, **extra})
    return r, time.monotonic() - t0


def _capture_mod():
    return importlib.import_module(f"{_route_module()._PLUGIN_PKG}.capture")


def _enable(ca, csrf, home, tenant="tenant_a", **egress):
    _write_tenant_yaml(home, tenant, {"egress": {"enabled": True, "default_action": "allow", **egress}})
    assert ca.put(f"{V}/settings", headers=H(csrf), json={"capture_enabled": True}).status_code == 200


def _assets_on_disk(home: Path, tenant: str) -> list:
    root = home / "tenants" / tenant / "video_producer" / "assets"
    return sorted(p.name for p in root.iterdir() if p.name.startswith("ast_")) if root.exists() else []


class CaptureRoutesE2E(unittest.TestCase):
    def test_switch_is_off_by_default_and_refuses(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            s = ca.get(f"{V}/settings").json()
            self.assertIs(s["capture_enabled"], False)
            self.assertIs(s["capture_policy_configured"], False)
            self.assertIn(s["capture_host_problem"], (None, "no_browser", "unsupported"))
            if _HAS_CHROME:
                self.assertIsNone(s["capture_host_problem"])
            # CSRF first: without the token nothing happens and nothing is recorded
            r, _ = _capture(ca, csrf, "https://example.com/", headers={})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(_chain_records(home, "tenant_a", "video_producer.capture_finished"), [])
            r, _ = _capture(ca, csrf, "https://example.com/")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("off", r.json()["detail"])
            self.assertEqual(_assets_on_disk(home, "tenant_a"), [])
            rec = _chain_records(home, "tenant_a", "video_producer.capture_finished")
            self.assertEqual([(x["details"]["outcome"], x["details"]["refusal"]) for x in rec], [("refused", "switch_off")])
            self.assertEqual(_chain_records(home, "tenant_a", "video_producer.capture_started"), [])

    def test_switch_on_without_an_egress_policy_still_refuses(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            self.assertEqual(ca.put(f"{V}/settings", json={"capture_enabled": True}).status_code, 403)  # CSRF
            r = ca.put(f"{V}/settings", headers=H(csrf), json={"capture_enabled": True})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIs(r.json()["settings"]["capture_enabled"], True)
            self.assertIs(r.json()["settings"]["capture_policy_configured"], False)
            ch = _chain_records(home, "tenant_a", "video_producer.capture_setting_changed")
            self.assertEqual([x["details"]["enabled"] for x in ch], [True])
            # a policy that exists but is disabled is not a policy
            _write_tenant_yaml(home, "tenant_a", {"egress": {"enabled": False, "allowed_hosts": ["example.com"]}})
            r, _ = _capture(ca, csrf, "https://example.com/")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("egress policy", r.json()["detail"])
            self.assertEqual(_assets_on_disk(home, "tenant_a"), [])
            rec = _chain_records(home, "tenant_a", "video_producer.capture_finished")
            self.assertEqual([x["details"]["refusal"] for x in rec], ["no_egress_policy"])

    @unittest.skipUnless(_HAS_CHROME, "no Chrome/Chromium on this host")
    def test_switch_on_with_policy_captures_a_real_page_into_an_asset(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf), (cb, _csrf_b) = clients["tenant_a"], clients["tenant_b"]
            _write_tenant_yaml(home, "tenant_a", {"egress": {"enabled": True, "default_action": "deny",
                                                             "allowed_hosts": ["example.com"]}})
            self.assertEqual(ca.put(f"{V}/settings", headers=H(csrf), json={"capture_enabled": True}).status_code, 200)
            self.assertIs(ca.get(f"{V}/settings").json()["capture_policy_configured"], True)
            url = "https://example.com/?campaign=SECRET-QUERY-123"
            r, _ = _capture(ca, csrf, url, hotspots=[{"name": "heading", "x": 0.3, "y": 0.2, "description": "title"}])
            self.assertEqual(r.status_code, 201, r.text)
            asset = r.json()["asset"]
            self.assertEqual(asset["source"], "https://example.com/")
            self.assertEqual((asset["width"], asset["height"]), (1920, 1080))
            self.assertEqual([h["name"] for h in asset["hotspots"]], ["heading"])
            self.assertEqual(_assets_on_disk(home, "tenant_a"), [asset["id"]])
            img = ca.get(f"{V}/assets/{asset['id']}/image")
            self.assertEqual(img.status_code, 200)
            self.assertTrue(img.content.startswith(b"\x89PNG"))
            self.assertEqual(cb.get(f"{V}/assets").json()["assets"], [])

            started = _chain_records(home, "tenant_a", "video_producer.capture_started")
            done = _chain_records(home, "tenant_a", "video_producer.capture_finished")
            self.assertEqual(len(started), 1)
            self.assertEqual([(d["details"]["outcome"], d["details"].get("asset_id")) for d in done],
                             [("accepted", asset["id"])])
            import hashlib
            prefix = hashlib.sha256(url.encode()).hexdigest()[:12]
            for rec in started + done:
                self.assertEqual(rec["details"]["host"], "example.com")
                self.assertEqual(rec["details"]["url_sha256_prefix"], prefix)
                self.assertNotIn("_dropped_fields", rec["details"])
            chain = (home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl").read_text()
            for leak in ("SECRET-QUERY-123", "campaign", "https://example.com", "pricing page"):
                self.assertNotIn(leak, chain)
            acc = done[0]["details"]
            self.assertEqual(acc["final_host"], "example.com")
            self.assertGreaterEqual(acc["hosts_connected"], 1)

            # hotspots placed by clicking the stored picture afterwards: CSRF, tenant scope, audit-first
            spots = [{"name": "heading", "x": 0.3, "y": 0.2, "description": "title"},
                     {"name": "more_link", "x": 0.31, "y": 0.4, "description": ""}]
            self.assertEqual(ca.patch(f"{V}/assets/{asset['id']}", json={"hotspots": spots}).status_code, 403)
            self.assertEqual(cb.patch(f"{V}/assets/{asset['id']}", headers=H(_csrf_b),
                                      json={"hotspots": spots}).status_code, 404)
            bad = ca.patch(f"{V}/assets/{asset['id']}", headers=H(csrf), json={"hotspots": [{"name": "X!", "x": 0, "y": 0}]})
            self.assertEqual(bad.status_code, 422, bad.text)
            r = ca.patch(f"{V}/assets/{asset['id']}", headers=H(csrf), json={"hotspots": spots, "caption": "Example home"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual([h["name"] for h in r.json()["asset"]["hotspots"]], ["heading", "more_link"])
            self.assertEqual(ca.get(f"{V}/assets").json()["assets"][0]["caption"], "Example home")
            upd = _chain_records(home, "tenant_a", "video_producer.asset_updated")
            self.assertEqual([(u["details"]["hotspot_count"], u["details"]["caption_changed"]) for u in upd], [(2, True)])
            chain = (home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl").read_text()
            self.assertNotIn("Example home", chain)
            from forge import security_events as se
            ok, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok, problems)

    @unittest.skipUnless(_HAS_CHROME, "no Chrome/Chromium on this host")
    def test_hostile_urls_are_refused_like_unreachable_ones_with_no_asset(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            _write_tenant_yaml(home, "tenant_a", {"egress": {"enabled": True, "default_action": "allow",
                                                             "forbidden_hosts": ["blocked.example.org"]}})
            ca.put(f"{V}/settings", headers=H(csrf), json={"capture_enabled": True})
            # a dotless name ("localhost", the hex literal 0x7f000001) is refused by its SYNTAX, at once and
            # independent of any target - it says nothing about reachability
            for url in ("http://localhost/", "http://0x7f000001/", "http://localhost.:8765/"):
                r, dt = _capture(ca, csrf, url)
                self.assertEqual((r.status_code, r.json()["detail"]), (422, "Enter a plain http:// or https:// address"))
                self.assertLess(dt, 2.0)
            hostile = ["http://127.0.0.1:8765/v1/console/auth/local-login", "http://[::ffff:127.0.0.1]/",
                       "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://[64:ff9b::7f00:1]/",
                       "http://127.1/", "http://metadata.google.internal/"]
            unreachable = ["http://capture-e2e-probe.invalid/", "http://example.com:81/"]
            seen = {}
            for url in hostile + unreachable:
                r, dt = _capture(ca, csrf, url)
                seen[url] = (r.status_code, r.text, dt)
            statuses = {v[0] for v in seen.values()}
            bodies = {v[1] for v in seen.values()}
            self.assertEqual(statuses, {422}, seen)
            self.assertEqual(len(bodies), 1, seen)
            self.assertIn("cannot be captured", bodies.pop())
            times = [v[2] for v in seen.values()]
            print("capture-timing", {u: round(v[2], 3) for u, v in seen.items()})
            floor = _route_module()._CAPTURE_FAIL_FLOOR_S
            self.assertGreaterEqual(min(times), floor - 0.01, seen)
            self.assertLess(max(times) - min(times), 0.5, seen)
            self.assertEqual(_assets_on_disk(home, "tenant_a"), [])
            done = _chain_records(home, "tenant_a", "video_producer.capture_finished")
            done = [d for d in done if d["details"]["refusal"] != "invalid_url"]
            self.assertEqual(len(done), len(seen))
            self.assertEqual({d["details"]["outcome"] for d in done}, {"failed"})
            # the tenant's own policy is no secret: a forbidden host is refused at once, and says why
            r, dt = _capture(ca, csrf, "https://blocked.example.org/")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("egress policy does not allow", r.json()["detail"])
            self.assertLess(dt, 2.0)
            last = _chain_records(home, "tenant_a", "video_producer.capture_finished")[-1]["details"]
            self.assertEqual((last["outcome"], last["refusal"]), ("refused", "egress_denied"))
            # review 2: a trailing dot is the same host for the gate (it was 'default allow' before)
            r, dt = _capture(ca, csrf, "https://BLOCKED.example.org./")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertLess(dt, 2.0)
            last = _chain_records(home, "tenant_a", "video_producer.capture_finished")[-1]["details"]
            self.assertEqual((last["host"], last["refusal"]), ("blocked.example.org", "egress_denied"))
            # every capture that started (= reached the egress step) finished; the gate-refused ones never started
            fetched = [d for d in done if d["details"]["refusal"] != "egress_denied"]
            self.assertEqual(len(_chain_records(home, "tenant_a", "video_producer.capture_started")),
                             len([d for d in fetched if d["details"]["outcome"] != "refused"]))
            chain = (home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl").read_text()
            for leak in ("local-login", "meta-data", "capture-e2e-probe.invalid/"):
                self.assertNotIn(leak, chain)


    def test_input_and_quota_are_refused_before_anything_is_fetched(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            _enable(ca, csrf, home)
            calls = []
            cap = _capture_mod()
            real = cap.capture_url
            cap.capture_url = lambda *a, **k: calls.append(a) or real(*a, **k)
            try:
                r, dt = _capture(ca, csrf, "https://example.com/", caption="   ")
                self.assertEqual(r.status_code, 422, r.text)
                self.assertIn("caption", r.json()["detail"])
                too_many = [{"name": f"s{i}x", "x": 0.1, "y": 0.1, "description": ""} for i in range(9)]
                r, _ = _capture(ca, csrf, "https://example.com/", hotspots=too_many)
                self.assertEqual(r.status_code, 422, r.text)
                r, _ = _capture(ca, csrf, "https://example.com/", hotspots=[{"name": "Bad Name", "x": 0, "y": 0}])
                self.assertEqual(r.status_code, 422, r.text)
                am = importlib.import_module(f"{_route_module()._PLUGIN_PKG}.asset_store")
                old = am.MAX_ASSETS
                am.MAX_ASSETS = 0
                try:
                    r, _ = _capture(ca, csrf, "https://example.com/")
                finally:
                    am.MAX_ASSETS = old
                self.assertEqual(r.status_code, 409, r.text)
            finally:
                cap.capture_url = real
            self.assertEqual(calls, [], "a capture ran although its input or the quota was refused")
            self.assertEqual(_chain_records(home, "tenant_a", "video_producer.capture_started"), [])
            refusals = [d["details"]["refusal"] for d in _chain_records(home, "tenant_a", "video_producer.capture_finished")]
            self.assertEqual(refusals[0], "caption")
            self.assertEqual(refusals[-1], "quota")

    def test_a_target_that_hangs_answers_like_a_refusal_and_started_always_finishes(self):
        """In-process stand-ins for the plugin: one capture that hangs until its budget runs out, one that
        raises. The ROUTE must answer both exactly like a refused address and close every started record."""
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            _enable(ca, csrf, home)
            cap = _capture_mod()
            real = cap.capture_url
            floor = _route_module()._capture_fail_floor(cap)

            def hang(*a, **k):  # what a real hang measured: the deadline plus the 0.5 s kill grace
                time.sleep(cap.CAPTURE_DEADLINE_S + 0.5)
                return {"ok": False, "reason": "timeout", "png": None, "final_url": None,
                        "hosts": {"connected": 3}, "elapsed": cap.CAPTURE_DEADLINE_S + 0.5}

            def boom(*a, **k):
                raise RuntimeError("worker could not start")

            seen = {}
            try:
                for name, fn in (("hang", hang), ("boom", boom)):
                    cap.capture_url = fn
                    seen[name] = _capture(ca, csrf, "https://example.com/")
            finally:
                cap.capture_url = real
            seen["refused"] = _capture(ca, csrf, "http://10.0.0.1/")
            print("capture-timing", {k: round(v[1], 3) for k, v in seen.items()})
            self.assertEqual({v[0].status_code for v in seen.values()}, {422})
            self.assertEqual(len({v[0].text for v in seen.values()}), 1)
            times = [v[1] for v in seen.values()]
            self.assertGreaterEqual(min(times), floor - 0.01)
            self.assertLess(max(times) - min(times), 0.5, seen)
            started = _chain_records(home, "tenant_a", "video_producer.capture_started")
            done = _chain_records(home, "tenant_a", "video_producer.capture_finished")
            self.assertEqual(len(started), 3)
            self.assertEqual([d["details"]["refusal"] for d in done], ["timeout", "error", "refused"])
            # the audit timestamps do not tell them apart either (audit/tail shows ts to the tenant)
            ts = lambda r: float(r["ts"])  # noqa: E731 - the chain's unix timestamp
            spans = [ts(f) - ts(s_) for s_, f in zip(started, done)]
            print("audit-spans", [round(x, 3) for x in spans])
            self.assertLess(max(spans) - min(spans), 0.5, spans)
            self.assertGreaterEqual(min(spans), floor - 1.0, spans)
            self.assertEqual(done[0]["details"]["hosts_connected"], 3)


if __name__ == "__main__":
    unittest.main()
