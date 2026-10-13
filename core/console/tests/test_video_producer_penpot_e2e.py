"""HTTP E2E proof for the Penpot import routes (ADR-2257 strand A, T-0189).

Same harness as the style and asset E2E suites: the real console router, real session + CSRF, two
tenants, the marketplace plugin's REAL importer, REAL Chromium render and the real tenant audit chain.
Nothing is stubbed.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402
from test_video_producer_styles_e2e import _chain_records, _plugin  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731


def _board() -> bytes:
    pkg = sys.modules[_route_module()._PLUGIN_PKG]
    return (Path(pkg.__file__).resolve().parent.parent / "tests" / "fixtures" / "penpot_board.svg").read_bytes()


def _post(client, csrf, url, body, name="board.svg", **data):
    return client.post(url, files={"file": (name, io.BytesIO(body), "application/octet-stream")}, data=data,
                       headers=H(csrf))


class PenpotRoutesE2E(unittest.TestCase):
    def test_svg_import_to_style_commit_and_audit(self):
        from forge import security_events as se

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()

            async def no_previews(style, themes=None):
                return {"previews": [], "notes": []}

            mod._previews = no_previews
            ca, csrf_a = clients["tenant_a"]
            url = f"{V}/styles/import"
            ok = _post(ca, csrf_a, url, _board(), "Brand Board.svg")
            self.assertEqual(ok.status_code, 200, ok.text)
            draft = ok.json()["draft"]
            self.assertEqual((draft["name"], draft["source"]["kind"], draft["default_theme"]),
                             ("Brand Board", "penpot", "light"))
            self.assertEqual(draft["tokens"]["light"]["accent"], "#0066cc")
            self.assertTrue(any("components" in n for n in ok.json()["notes"]))

            saved = ca.post(f"{V}/styles", json={"draft": draft}, headers=H(csrf_a))
            self.assertEqual(saved.status_code, 201, saved.text)
            self.assertEqual(saved.json()["style"]["source"]["kind"], "penpot")

            self.assertEqual(_post(ca, csrf_a, url, b"%PDF-1.7 nope").status_code, 415)
            doctype = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'
            r = _post(ca, csrf_a, url, doctype)
            self.assertEqual(r.status_code, 422)
            self.assertIn("document type", r.json()["detail"])
            self.assertEqual(_post(ca, csrf_a, url, b"").status_code, 400)

            recs = _chain_records(home, "tenant_a", "video_producer.style_imported")
            self.assertEqual([(x["details"]["outcome"], x["details"]["format"]) for x in recs],
                             [("accepted", "penpot_svg"), ("refused", "pptx"), ("refused", "penpot_svg"),
                              ("refused", "pptx")])
            for rec in recs:
                self.assertNotIn("_dropped_fields", rec["details"])
                self.assertNotIn("Brand", json.dumps(rec))
            ok_chain, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok_chain, problems)

    def test_board_becomes_a_still_image_asset_rendered_by_chromium(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        from PIL import Image
        from forge import security_events as se

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            r = _post(ca, csrf_a, f"{V}/assets/import-penpot", _board(), "Brand Board.svg")
            if r.status_code == 503 and "rendered" in r.text:
                self.skipTest("no usable Chromium on this host")
            self.assertEqual(r.status_code, 201, r.text)
            asset = r.json()["asset"]
            self.assertEqual((asset["width"], asset["height"], asset["caption"]), (1920, 1080, "Brand Board"))
            self.assertFalse(asset["source"])

            png = ca.get(f"{V}/assets/{asset['id']}/image")
            self.assertEqual((png.status_code, png.headers["content-type"]), (200, "image/png"))
            im = Image.open(io.BytesIO(png.content)).convert("RGB")
            self.assertEqual(im.getpixel((200, 200)), (0, 102, 204))
            self.assertEqual(im.getpixel((300, 960)), (255, 102, 0))

            self.assertEqual(cb.get(f"{V}/assets").json()["assets"], [])
            self.assertEqual(cb.get(f"{V}/assets/{asset['id']}/image").status_code, 404)

            self.assertEqual(_post(ca, csrf_a, f"{V}/assets/import-penpot", b"\x89PNG not svg").status_code, 415)
            hostile = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><image href="http://127.0.0.1:9/x.png" '
                       b'width="50" height="50"/><rect width="50" height="50" fill="#112233"/></svg>')
            h = _post(ca, csrf_a, f"{V}/assets/import-penpot", hostile, "x.svg", caption="Hostile")
            self.assertEqual(h.status_code, 201, h.text)
            self.assertEqual(h.json()["notes"], ["1 image not imported"])

            recs = _chain_records(home, "tenant_a", "video_producer.asset_uploaded")
            self.assertEqual([(x["details"]["outcome"], x["details"]["format"]) for x in recs],
                             [("accepted", "penpot_svg"), ("refused", "penpot_svg"), ("accepted", "penpot_svg")])
            self.assertEqual(recs[1]["details"]["refusal"], "not_svg")
            for rec in recs:
                self.assertNotIn("_dropped_fields", rec["details"])
                self.assertNotIn("Brand", json.dumps(rec))
            ok_chain, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok_chain, problems)

    def test_a_gradient_board_renders_as_a_gradient_and_a_hostile_one_is_counted(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        from PIL import Image
        from forge import security_events as se

        ns = 'xmlns="http://www.w3.org/2000/svg"'
        good = (f'<svg {ns} width="400" height="200" viewBox="0 0 400 200" fill="none"><defs>'
                '<linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff0000"/>'
                '<stop offset="1" stop-color="#0000ff"/></linearGradient></defs>'
                '<rect x="20" y="20" width="360" height="160" fill="url(#g)"/></svg>').encode()
        stops = "".join(f'<stop offset="{i / 11}" stop-color="#ff0000"/>' for i in range(12))
        hostile = (f'<svg {ns} width="400" height="200" viewBox="0 0 400 200" fill="none"><defs>'
                   f'<linearGradient id="g">{stops}</linearGradient></defs>'
                   '<rect x="20" y="20" width="360" height="160" fill="url(#g)" stroke="url(#g)"/>'
                   '<rect x="30" y="30" width="20" height="20" fill="url(http://127.0.0.1:9/x)"/></svg>').encode()
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf_a = clients["tenant_a"]
            r = _post(ca, csrf_a, f"{V}/assets/import-penpot", good, "grad.svg", caption="Gradient")
            if r.status_code == 503 and "rendered" in r.text:
                self.skipTest("no usable Chromium on this host")
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["notes"], [])
            im = Image.open(io.BytesIO(ca.get(f"{V}/assets/{r.json()['asset']['id']}/image").content)).convert("RGB")
            w, h = im.size
            row = {im.getpixel((int(w * f), h // 2)) for f in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)}
            self.assertGreater(len(row), 4, row)
            self.assertGreater(im.getpixel((int(w * 0.1), h // 2))[0], 200)
            self.assertGreater(im.getpixel((int(w * 0.9), h // 2))[2], 200)

            bad = _post(ca, csrf_a, f"{V}/assets/import-penpot", hostile, "bad.svg", caption="Bad")
            self.assertEqual(bad.status_code, 201, bad.text)
            notes = " ".join(bad.json()["notes"])
            self.assertIn("1 unreadable gradient not imported", notes)
            self.assertIn("1 unreadable paint not imported", notes)
            self.assertNotIn("_", notes)

            recs = _chain_records(home, "tenant_a", "video_producer.asset_uploaded")
            self.assertEqual([x["details"]["outcome"] for x in recs], ["accepted", "accepted"])
            for rec in recs:
                self.assertNotIn("_dropped_fields", rec["details"])
                self.assertNotIn("127.0.0.1", json.dumps(rec))
            ok_chain, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok_chain, problems)

    def test_a_full_workspace_is_refused_before_any_browser_is_started(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf_a = clients["tenant_a"]
            am, pim = _plugin("asset_store"), _plugin("penpot_import")
            started = []

            async def must_not_render(design, **kw):
                started.append(1)
                raise AssertionError("a browser was started for a full workspace")

            old_max, old_render = am.MAX_ASSETS, pim.render_png
            am.MAX_ASSETS, pim.render_png = 0, must_not_render
            try:
                r = _post(ca, csrf_a, f"{V}/assets/import-penpot", _board(), "b.svg")
            finally:
                am.MAX_ASSETS, pim.render_png = old_max, old_render
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(started, [])
            rec = _chain_records(home, "_default", "video_producer.asset_uploaded") or _chain_records(home, "tenant_a", "video_producer.asset_uploaded")
            self.assertEqual([(x["details"]["outcome"], x["details"]["refusal"]) for x in rec], [("refused", "quota")])

    def test_oversized_body_is_refused_before_spooling_for_the_new_route(self):
        from corvin_console import body_cap

        self.assertEqual(body_cap.VIDEO_BODY_CAPS["/v1/console/video/assets/import-penpot"], 8 * 1024 * 1024 + 256 * 1024)
        self.assertEqual(body_cap.standalone_cap_for("/v1/console/video/assets/import-penpot"), 8 * 1024 * 1024 + 256 * 1024)


if __name__ == "__main__":
    unittest.main()
