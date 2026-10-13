"""HTTP E2E proof for the Video Producer image-asset routes (ADR-2249 D3, T-0111).

Same harness as test_video_producer_styles_e2e: the real console router, real session + CSRF, two
tenants, the marketplace plugin's real asset store and the real tenant audit chain. Uploads are real
PNG files built with Pillow (carrying metadata that must not survive); nothing is mocked except, in one
test, the audit writer - to prove that an upload whose record cannot be written stores nothing.
"""
from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402
from test_video_producer_styles_e2e import _chain_records, _plugin  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731


def _png_with_metadata(w=320, h=200) -> bytes:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Author", "Jane Doe <jane@example.com>")
    info.add_text("GPS", "48.137,11.575")
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(buf, "PNG", pnginfo=info)
    raw = buf.getvalue()
    exif = b"MM\x00*EXIF-SECRET"
    chunk = struct.pack(">I", len(exif)) + b"eXIf" + exif + struct.pack(">I", zlib.crc32(b"eXIf" + exif))
    return raw[:-12] + chunk + raw[-12:] + b"TRAILING"


def _chunks(png: bytes) -> list:
    pos, out = 8, []
    while pos + 8 <= len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        out.append(png[pos + 4:pos + 8].decode("latin-1"))
        pos += 12 + n
    return out


SPOTS = [{"name": "save_button", "x": 0.82, "y": 0.9, "description": "the save button"},
         {"name": "menu", "x": 0.1, "y": 0.1, "description": "the main menu"}]


class AssetRoutesE2E(unittest.TestCase):
    def _upload(self, client, csrf, data=None, caption="The settings page", hotspots=SPOTS, source="", headers=None):
        return client.post(f"{V}/assets", headers=H(csrf) if headers is None else headers,
                           files={"file": ("shot.png", data if data is not None else _png_with_metadata(), "image/png")},
                           data={"caption": caption, "hotspots": json.dumps(hotspots), "source": source})

    def test_upload_list_preview_delete_with_audit_first_and_tenant_isolation(self):
        from forge import security_events as se

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            empty = ca.get(f"{V}/assets").json()
            self.assertEqual(empty["assets"], [])
            self.assertEqual(empty["limits"]["max_assets"], 50)
            self.assertEqual(empty["limits"]["max_image_bytes"], 2 * 1024 * 1024)

            r = self._upload(ca, csrf_a, source="https://user:pw@Example.com/pricing?token=SECRET#x")
            self.assertEqual(r.status_code, 201, r.text)
            asset = r.json()["asset"]
            aid = asset["id"]
            self.assertRegex(aid, r"^ast_[0-9a-f]{8}$")
            self.assertEqual(asset["source"], "https://example.com/pricing")
            self.assertEqual([h["name"] for h in asset["hotspots"]], ["save_button", "menu"])
            self.assertNotIn("uploaded_by", asset)
            on_disk = home / "tenants" / "tenant_a" / "video_producer" / "assets" / aid
            doc = json.loads((on_disk / "asset.json").read_text())
            self.assertEqual(doc["uploaded_by"], "console-owner:tenant_a")
            raw = (on_disk / "image.png").read_bytes()
            self.assertLessEqual(len(raw), 2 * 1024 * 1024)
            self.assertEqual(set(_chunks(raw)) - {"IHDR", "IDAT", "IEND"}, set())
            for secret in (b"jane@example.com", b"EXIF-SECRET", b"TRAILING", b"48.137"):
                self.assertNotIn(secret, raw)

            self.assertEqual([a["id"] for a in ca.get(f"{V}/assets").json()["assets"]], [aid])
            img = ca.get(f"{V}/assets/{aid}/image")
            self.assertEqual(img.status_code, 200)
            self.assertEqual(img.headers["content-type"], "image/png")
            self.assertEqual(img.content, raw)
            self.assertIn("no-store", img.headers["cache-control"])

            # tenant B: cannot list, read or delete A's asset - the same 404 as an unknown id
            self.assertEqual(cb.get(f"{V}/assets").json()["assets"], [])
            for call in (lambda i: cb.get(f"{V}/assets/{i}/image"), lambda i: cb.delete(f"{V}/assets/{i}", headers=H(csrf_b))):
                foreign, unknown = call(aid), call("ast_deadbeef")
                self.assertEqual((foreign.status_code, foreign.text), (unknown.status_code, unknown.text))
                self.assertEqual(foreign.status_code, 404)
            self.assertEqual(ca.get(f"{V}/assets/..%2F..%2Fetc/image").status_code, 404)

            # CSRF: a mutation without the token is refused and changes nothing
            self.assertEqual(self._upload(ca, csrf_a, headers={}).status_code, 403)
            self.assertEqual(ca.delete(f"{V}/assets/{aid}").status_code, 403)
            self.assertEqual(len(ca.get(f"{V}/assets").json()["assets"]), 1)

            self.assertEqual(ca.delete(f"{V}/assets/{aid}", headers=H(csrf_a)).status_code, 204)
            self.assertFalse(on_disk.exists())
            self.assertEqual(ca.get(f"{V}/assets").json()["assets"], [])
            self.assertEqual(ca.get(f"{V}/assets/{aid}/image").status_code, 404)

            up = _chain_records(home, "tenant_a", "video_producer.asset_uploaded")
            self.assertEqual([(u["details"]["outcome"], u["details"].get("asset_id")) for u in up], [("accepted", aid)])
            self.assertEqual(up[0]["details"]["hotspot_count"], 2)
            self.assertTrue(up[0]["details"]["has_source"])
            acc = _chain_records(home, "tenant_a", "video_producer.asset_accessed")
            self.assertEqual([(a["details"]["asset_id"], a["details"]["purpose"]) for a in acc], [(aid, "preview")])
            dele = _chain_records(home, "tenant_a", "video_producer.asset_deleted")
            self.assertEqual([d["details"]["asset_id"] for d in dele], [aid])
            for rec in up + acc + dele:
                self.assertNotIn("_dropped_fields", rec["details"], "an allowlisted field was dropped")
                text = json.dumps(rec)
                for leak in ("settings page", "save button", "example.com", "console-owner", "SECRET"):
                    self.assertNotIn(leak, text)
            self.assertEqual(_chain_records(home, "tenant_b", "video_producer.asset_accessed"), [])
            ok, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok, problems)

    def test_refusals_are_safe_audited_and_store_nothing(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            gif = self._upload(ca, csrf, data=b"GIF89a" + b"\x00" * 200)
            self.assertEqual(gif.status_code, 422)
            self.assertIn("only PNG", gif.json()["detail"])
            self.assertEqual(self._upload(ca, csrf, caption="").status_code, 422)
            bad_spot = self._upload(ca, csrf, hotspots=[{"name": "x", "x": 2, "y": 0}])
            self.assertEqual(bad_spot.status_code, 422)
            self.assertEqual(self._upload(ca, csrf, hotspots="not json").status_code, 422)
            big = self._upload(ca, csrf, data=b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024 * 1024 + 10))
            self.assertEqual(big.status_code, 413)
            am = _plugin("asset_store")
            with mock.patch.object(am, "MAX_ASSETS", 1):
                self.assertEqual(self._upload(ca, csrf).status_code, 201)
                q = self._upload(ca, csrf)
                self.assertEqual(q.status_code, 409, q.text)
            refused = [r["details"]["refusal"] for r in _chain_records(home, "tenant_a", "video_producer.asset_uploaded")
                       if r["details"]["outcome"] == "refused"]
            self.assertEqual(refused, ["not_png", "caption", "hotspots", "hotspots", "too_large", "quota"])
            self.assertEqual(len(ca.get(f"{V}/assets").json()["assets"]), 1)

    def test_an_upload_whose_record_cannot_be_written_stores_nothing(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            mod = _route_module()
            with mock.patch.object(mod, "_audit", lambda *a, **k: False):
                r = self._upload(ca, csrf)
                self.assertEqual(r.status_code, 503, r.text)
            root = home / "tenants" / "tenant_a" / "video_producer" / "assets"
            self.assertEqual([p.name for p in root.iterdir()] if root.exists() else [], [".lock"] if root.exists() else [])
            aid = self._upload(ca, csrf).json()["asset"]["id"]
            with mock.patch.object(mod, "_audit", lambda *a, **k: False):
                self.assertEqual(ca.delete(f"{V}/assets/{aid}", headers=H(csrf)).status_code, 503)
                self.assertEqual(ca.get(f"{V}/assets/{aid}/image").status_code, 503)
            self.assertTrue((root / aid / "image.png").is_file())

    def test_per_user_erasure_removes_exactly_the_subjects_assets(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            ca, csrf = clients["tenant_a"]
            mine = self._upload(ca, csrf).json()["asset"]["id"]
            base = home / "tenants" / "tenant_a" / "video_producer"
            other = _plugin("asset_store").AssetStore(str(base)).add(
                _png_with_metadata(), caption="Someone else's", uploaded_by="user_b")["id"]
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"))
            import erasure_handlers as eh

            res = eh.VideoProducerHandler(tenant_id="tenant_a").purge("console-owner:tenant_a", "req-1")
            self.assertEqual(res.count, 2, res.reason)  # asset.json + image.png of the one asset
            self.assertFalse((base / "assets" / mine).exists())
            self.assertTrue((base / "assets" / other / "image.png").is_file())
            self.assertEqual([a["id"] for a in ca.get(f"{V}/assets").json()["assets"]], [other])
            eh.VideoProducerHandler(tenant_id="tenant_a").purge("tenant_a", "req-2")
            self.assertFalse((base / "assets").exists())


if __name__ == "__main__":
    unittest.main()
