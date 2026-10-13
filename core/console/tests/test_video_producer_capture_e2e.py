"""HTTP E2E proof for "Add website screenshot" (ADR-2249 D4, T-0113 part 1).

The real console router (TestClient, real session cookie + CSRF, temp CORVIN_HOME), the marketplace
plugin's real ``capture`` module (worker process, Corvin-owned proxy, headless Chrome), its real asset
store and the real tenant audit chain. Nothing is mocked.

* switch OFF (the default) -> 403, nothing captured, the refusal recorded;
* switch ON but no egress policy configured for the tenant -> 403;
* switch ON + policy -> a real capture of https://example.com becomes an asset (NETWORK: this one
  test fetches example.com through the capture proxy; it is the only outbound request of the file);
* a hostile URL is refused with NO asset and is indistinguishable - status, body, time - from an
  unreachable public address and from an address the egress policy does not allow;
* the audit chain carries the host and a sha256 prefix, never the URL, its path or its query.
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

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731
_HAS_CHROME = any(shutil.which(b) for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))


def _capture(client, csrf, url, caption="The pricing page", headers=None, **extra):
    t0 = time.monotonic()
    r = client.post(f"{V}/assets/capture", headers=H(csrf) if headers is None else headers,
                    json={"url": url, "viewport": "desktop", "caption": caption, **extra})
    return r, time.monotonic() - t0


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
            hostile = ["http://127.0.0.1:8765/v1/console/auth/local-login", "http://0x7f000001/",
                       "http://[::ffff:127.0.0.1]/", "http://169.254.169.254/latest/meta-data/",
                       "http://localhost/", "http://10.0.0.1/", "http://[64:ff9b::7f00:1]/"]
            unreachable = ["http://capture-e2e-probe.invalid/", "http://example.com:81/"]
            denied = ["https://blocked.example.org/"]
            seen = {}
            for url in hostile + unreachable + denied:
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
            self.assertEqual(len(done), len(seen))
            self.assertEqual({d["details"]["outcome"] for d in done}, {"failed"})
            chain = (home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl").read_text()
            for leak in ("local-login", "meta-data", "capture-e2e-probe.invalid/"):
                self.assertNotIn(leak, chain)


if __name__ == "__main__":
    unittest.main()
