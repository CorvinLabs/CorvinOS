"""HTTP E2E proof for the Video Producer style routes (PLAN-0945 P2).

Same harness as test_video_producer_routes_e2e: the real console router, real session + CSRF, two
tenants, the real L44/L34/L35 gates and the real tenant audit chain; only the production runner is a
recorder. The PPTX importer is another module's job - the import-route plumbing is driven with a stub
importer module, plus one test against the real importer when it is present.
"""
from __future__ import annotations

import base64
import copy
import importlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _RecordingRunner, _route_module, _sandbox, _write_tenant_yaml  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731


def _png(size=(64, 64), color=(200, 30, 30, 255)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def _plugin(name: str):
    return importlib.import_module(f"{_route_module()._PLUGIN_PKG}.{name}")


def _wire(**over) -> dict:
    """A valid style draft on the wire, built through the plugin's own types."""
    sp, wt = _plugin("style_pack"), _plugin("web_templates")
    tokens = copy.deepcopy(wt.load_tokens())
    tokens["dark"].update(bg="#101820", bg_card="#18232e", border="#2a3a4a", accent="#3ba3ff",
                          accent_hi="#8cc8ff", glow="rgba(59, 163, 255, 0.16)")
    tokens["light"].update(bg="#ffffff", bg_card="#f4f6f8", border="#d8dee4", text="#1a2330",
                           text_muted="#566474", accent="#0b5cad", accent_hi="#1f7ad6",
                           glow="rgba(11, 92, 173, 0.18)")
    st = sp.validate_style(sp.Style(id="sty_00000000", name="Acme", tokens=tokens, wordmark="Acme Corp",
                                    decor="minimal", mark_png=_png()))
    wire = sp.draft_to_wire(st)
    wire.update(over)
    return wire


async def _no_previews(style, themes=None):
    return {"previews": [], "notes": []}


class StyleRoutesE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _commit(self, client, csrf, wire=None, **extra):
        self._benign_l44()
        return client.post(f"{V}/styles", json={"draft": wire or _wire(), **extra}, headers=H(csrf))

    def _job(self, client, csrf, **body):
        self._benign_l44()
        return client.post(f"{V}/jobs", json={"task": "Explain the quarterly review in two scenes.", **body}, headers=H(csrf))

    # ── lifecycle + isolation ────────────────────────────────────────────────
    def test_commit_list_use_in_a_job_default_and_delete(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            mod._previews = _no_previews
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]

            empty = ca.get(f"{V}/styles").json()
            self.assertEqual(empty["styles"], [])
            self.assertIsNone(empty["default_style_id"])
            self.assertEqual(empty["builtin"], {"id": "corvin", "name": "CorvinOS"})
            self.assertEqual(empty["limits"]["max_upload_bytes"], 25 * 1024 * 1024)

            r = self._commit(ca, csrf_a, set_default=True)
            self.assertEqual(r.status_code, 201, r.text)
            style = r.json()["style"]
            sid = style["id"]
            self.assertRegex(sid, r"^sty_[0-9a-f]{8}$")
            self.assertNotEqual(sid, "sty_00000000")
            self.assertEqual(style["name"], "Acme")
            self.assertTrue(style["mark_data_uri"].startswith("data:image/png;base64,"))
            listed = ca.get(f"{V}/styles").json()
            self.assertEqual([s["id"] for s in listed["styles"]], [sid])
            self.assertEqual(listed["default_style_id"], sid)
            self.assertTrue((home / "tenants" / "tenant_a" / "video_producer" / "styles" / sid / "style.json").is_file())

            # a job without style_id wears the tenant default; the validated object reaches the runner
            job = self._job(ca, csrf_a)
            self.assertEqual(job.status_code, 200, job.text)
            cfg = runner.calls[-1][2]
            self.assertEqual(cfg["web_style"].id, sid)
            # "corvin" is the built-in, explicitly
            self.assertEqual(self._job(ca, csrf_a, style_id="corvin").status_code, 200)
            self.assertNotIn("web_style", runner.calls[-1][2])
            # an explicit id works too
            self.assertEqual(self._job(ca, csrf_a, style_id=sid).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["web_style"].id, sid)

            # tenant B: cannot see, preview, use, default-to or delete A's style - same 404 as an unknown id
            self.assertEqual(cb.get(f"{V}/styles").json()["styles"], [])
            unknown = cb.delete(f"{V}/styles/sty_deadbeef", headers=H(csrf_b))
            foreign = cb.delete(f"{V}/styles/{sid}", headers=H(csrf_b))
            self.assertEqual((foreign.status_code, foreign.text), (unknown.status_code, unknown.text))
            self.assertEqual(foreign.status_code, 404)
            self.assertEqual(cb.get(f"{V}/styles/{sid}/preview").status_code, 404)
            self.assertEqual(cb.put(f"{V}/styles/default", json={"style_id": sid}, headers=H(csrf_b)).status_code, 404)
            n = len(runner.calls)
            self.assertEqual(self._job(cb, csrf_b, style_id=sid).status_code, 404)
            self.assertEqual(len(runner.calls), n, "a foreign style id must not start a job")
            self.assertTrue((home / "tenants" / "tenant_a" / "video_producer" / "styles" / sid).is_dir(), "B's delete touched A")

            # path-ish / malformed ids are the same 404, never a path
            for bad in ("../../etc", "sty_ZZZZZZZZ", "sty_1", "x" * 30):
                self.assertEqual(self._job(ca, csrf_a, style_id=bad).status_code, 404, bad)

            # delete clears the default; the next job is built-in again
            self.assertEqual(ca.delete(f"{V}/styles/{sid}", headers=H(csrf_a)).status_code, 204)
            self.assertIsNone(ca.get(f"{V}/styles").json()["default_style_id"])
            self.assertEqual(self._job(ca, csrf_a).status_code, 200)
            self.assertNotIn("web_style", runner.calls[-1][2])
            self.assertEqual(ca.delete(f"{V}/styles/{sid}", headers=H(csrf_a)).status_code, 404)

    def test_default_can_be_set_and_cleared(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            sid = self._commit(client, csrf).json()["style"]["id"]
            r = client.put(f"{V}/styles/default", json={"style_id": sid}, headers=H(csrf))
            self.assertEqual((r.status_code, r.json()), (200, {"default_style_id": sid}))
            r = client.put(f"{V}/styles/default", json={"style_id": None}, headers=H(csrf))
            self.assertEqual(r.json(), {"default_style_id": None})

    def test_revision_keeps_the_look_of_the_video_it_revises(self):
        from test_video_producer_revision_sources_e2e import _complete_job_with_storyboard

        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            sid = self._commit(client, csrf).json()["style"]["id"]
            base_a, base_b = "job_aaaaaaaa", "job_bbbbbbbb"
            for b in (base_a, base_b):
                _complete_job_with_storyboard(home / "tenants" / "_default", b, "Explain things.")
            # base_a was rendered with the style: the plugin leaves a snapshot with the video
            style = _plugin("style_store").StyleStore(str(home / "tenants" / "_default" / "video_producer")).load(sid)
            _plugin("style_store").write_style_snapshot(style, home / "tenants" / "_default" / "video_producer" / "videos" / base_a / "style")
            # now make a DIFFERENT style the tenant default: a revision must not pick it up
            other = self._commit(client, csrf, _wire(name="Other"), set_default=True).json()["style"]["id"]
            self.assertNotEqual(other, sid)

            self.assertEqual(self._job(client, csrf, base_job_id=base_a).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["web_style"].id, sid)
            # a base with no snapshot was built-in: it stays built-in despite the default
            self.assertEqual(self._job(client, csrf, base_job_id=base_b).status_code, 200)
            self.assertNotIn("web_style", runner.calls[-1][2])
            # an explicit id overrides
            self.assertEqual(self._job(client, csrf, base_job_id=base_b, style_id=other).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["web_style"].id, other)

    # ── hostile commits ──────────────────────────────────────────────────────
    def test_hostile_drafts_are_refused_and_nothing_is_stored(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _clients):
            styles_dir = home / "tenants" / "_default" / "video_producer" / "styles"
            svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').decode()
            low = _wire()
            low["tokens"]["dark"]["text"] = "#202830"  # unreadable on #101820
            font = _wire()
            font["tokens"]["typography"]["heading_family"] = "Comic Sans MS"
            cases = {
                "not an object": "nope",
                "svg as logo": _wire(mark_png_b64=svg),
                "not base64": _wire(mark_png_b64="!!!"),
                "low contrast": low,
                "unbundled font": font,
                "bad decor": _wire(decor="<script>"),
                "plate without safe rect": _wire(plate_png_b64=base64.b64encode(_png((1920, 1080))).decode()),
                "empty name": _wire(name=""),
            }
            for label, wire in cases.items():
                r = client.post(f"{V}/styles", json={"draft": wire}, headers=H(csrf))
                self.assertEqual(r.status_code, 422, (label, r.text))
            # a forged id / timestamp / provenance is ignored: the server makes them
            forged = _wire()
            forged["id"] = "sty_aaaaaaaa"
            forged["source"] = {"kind": "pptx", "sha256": "z" * 64, "imported_at": "1999-01-01"}
            r = self._commit(client, csrf, forged)
            self.assertEqual(r.status_code, 201, r.text)
            st = r.json()["style"]
            self.assertNotEqual(st["id"], "sty_aaaaaaaa")
            self.assertIsNone(st["source"]["sha256"])
            self.assertNotEqual(st["source"]["imported_at"], "1999-01-01")
            self.assertEqual(len(list(p for p in styles_dir.iterdir() if p.is_dir())), 1, "a refused draft left something behind")

            # huge base64 -> 413 before parsing
            big = {"draft": _wire(mark_png_b64="A" * (9 * 1024 * 1024))}
            self.assertEqual(client.post(f"{V}/styles", json=big, headers=H(csrf)).status_code, 413)
            # an oversized-but-under-the-body-cap logo is refused by validation
            over = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024)).decode()
            self.assertEqual(client.post(f"{V}/styles", json={"draft": _wire(mark_png_b64=over)}, headers=H(csrf)).status_code, 422)
            self.assertEqual(client.post(f"{V}/styles", content=b"{not json", headers={**H(csrf), "content-type": "application/json"}).status_code, 400)

    def test_quota_is_a_409(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            sm = _plugin("style_store")
            for _ in range(sm.MAX_STYLES):
                self.assertEqual(self._commit(client, csrf).status_code, 201)
            r = self._commit(client, csrf)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(len(client.get(f"{V}/styles").json()["styles"]), sm.MAX_STYLES)

    def test_the_acceptable_use_gate_sees_name_and_wordmark(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _clients):
            _write_tenant_yaml(home, "_default", {"egress": {"enabled": False}})
            wire = _wire()
            wire["brand"]["wordmark"] = "password = hunter2secret"
            r = self._commit(client, csrf, wire)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(client.get(f"{V}/styles").json()["styles"], [])

    # ── previews ─────────────────────────────────────────────────────────────
    def test_preview_routes_render_real_images_in_the_style_and_fail_soft(self):
        if _plugin_has_no_browser():
            self.skipTest("no Chromium on this host")
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            r = client.post(f"{V}/styles/preview", json={"draft": _wire()}, headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            pv = r.json()["previews"]
            self.assertEqual([p["template"] for p in pv], ["hero", "diagram", "quote"])
            for p in pv:
                self.assertTrue(p["data_uri"].startswith("data:image/jpeg;base64,"))
                raw = base64.b64decode(p["data_uri"].split(",", 1)[1])
                from PIL import Image

                im = Image.open(io.BytesIO(raw))
                self.assertEqual(im.size[0], 640)
                # the style's dark background (#101820) is what the slide is painted with
                px = im.convert("RGB").getpixel((5, im.size[1] - 5))
                self.assertLess(sum(abs(a - b) for a, b in zip(px, (0x10, 0x18, 0x20))), 40, px)
            # an invalid edited draft is a 422, not a render
            bad = _wire()
            bad["tokens"]["dark"]["text"] = "#202830"
            self.assertEqual(client.post(f"{V}/styles/preview", json={"draft": bad}, headers=H(csrf)).status_code, 422)
            # saved style
            sid = self._commit(client, csrf).json()["style"]["id"]
            saved = client.get(f"{V}/styles/{sid}/preview").json()
            self.assertEqual(len(saved["previews"]), 3)

    def test_previews_degrade_to_empty_with_a_note_when_the_renderer_fails(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            pv = _plugin("style_preview")
            wr = _plugin("web_renderer")

            async def boom(self):
                raise wr.WebRenderError("chromium could not be launched")

            orig = wr.WebSlideRenderer.__aenter__
            wr.WebSlideRenderer.__aenter__ = boom
            try:
                r = client.post(f"{V}/styles/preview", json={"draft": _wire()}, headers=H(csrf))
            finally:
                wr.WebSlideRenderer.__aenter__ = orig
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["previews"], [])
            self.assertTrue(r.json()["notes"])
            self.assertIs(pv.WebRenderError, wr.WebRenderError)

    def test_preview_concurrency_is_bounded(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            mod = _route_module()
            held = [mod._preview_slots.acquire(blocking=False) for _ in range(2)]
            try:
                self.assertEqual(held, [True, True])
                r = client.post(f"{V}/styles/preview", json={"draft": _wire()}, headers=H(csrf))
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["previews"], [])
                self.assertIn("busy", r.json()["notes"][0])
            finally:
                for _ in held:
                    mod._preview_slots.release()

    # ── import plumbing (stub importer) ──────────────────────────────────────
    def _stub_importer(self, behaviour):
        mod = _route_module()
        sp = _plugin("style_pack")
        stub = types.ModuleType(f"{mod._PLUGIN_PKG}.style_import_pptx")

        class PptxImportError(sp.StyleError):
            pass

        class ImportResult:
            def __init__(self, style, notes):
                self.style, self.notes = style, notes

        def import_pptx(data, filename=""):
            return behaviour(data, filename, PptxImportError, ImportResult)

        stub.PptxImportError, stub.ImportResult, stub.import_pptx = PptxImportError, ImportResult, import_pptx
        sys.modules[stub.__name__] = stub
        self.addCleanup(sys.modules.pop, stub.__name__, None)

    def test_import_route_plumbing_and_audit(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            mod._previews = _no_previews
            sp = _plugin("style_pack")
            seen = []

            def behaviour(data, filename, Err, Result):
                seen.append((len(data), filename))
                if b"BOOM" in data:
                    raise Err("This deck has no usable theme")
                st = sp.draft_from_wire(_wire(), style_id="sty_00000000", imported_at="2026-10-10T00:00:00Z")
                st.warnings = ["Font X is not bundled; using Y"]
                return Result(st, ["note one"])

            self._stub_importer(behaviour)
            ca, csrf_a = clients["tenant_a"]
            url = f"{V}/styles/import"
            up = lambda body, name="deck.pptx": ca.post(url, files={"file": (name, io.BytesIO(body), "application/octet-stream")}, headers=H(csrf_a))  # noqa: E731

            ok = up(b"PK\x03\x04" + b"x" * 100)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["draft"]["name"], "Acme")
            self.assertEqual(ok.json()["notes"], ["note one"])
            self.assertEqual(seen, [(104, "deck.pptx")])
            # the draft round-trips into a commit
            self.assertEqual(ca.post(f"{V}/styles", json={"draft": ok.json()["draft"]}, headers=H(csrf_a)).status_code, 201)

            self.assertEqual(up(b"%PDF-1.7 not a deck").status_code, 415)
            self.assertEqual(up(b"").status_code, 400)
            r = up(b"PK\x03\x04BOOM")
            self.assertEqual((r.status_code, r.json()["detail"]), (422, "This deck has no usable theme"))
            big = up(b"PK\x03\x04" + b"0" * (25 * 1024 * 1024 + 10))
            self.assertEqual(big.status_code, 413)
            self.assertEqual(len(seen), 2, "the importer must not see refused uploads")

            # nothing of the upload stays on disk
            for p in (home / "tenants" / "tenant_a").rglob("*"):
                self.assertNotIn("deck.pptx", p.name)

            recs = _chain_records(home, "tenant_a", "video_producer.style_imported")
            self.assertEqual([r["details"]["outcome"] for r in recs], ["accepted", "refused", "refused", "refused", "refused"])
            first = recs[0]["details"]
            self.assertEqual((first["bytes"], first["warning_count"]), (104, 1))
            self.assertRegex(first["sha256_prefix"], r"^[0-9a-f]{12}$")
            self.assertNotIn("deck", json.dumps(recs))

    def test_the_real_importer_refuses_a_zip_that_is_not_a_deck(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _clients):
            try:
                _plugin("style_import_pptx")
            except ImportError:
                self.skipTest("the PPTX importer is not in the plugin")
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("hello.txt", "not a deck")
            r = client.post(f"{V}/styles/import", files={"file": ("x.pptx", io.BytesIO(buf.getvalue()), "application/octet-stream")}, headers=H(csrf))
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(client.get(f"{V}/styles").json()["styles"], [], "an import must store nothing")
            self.assertEqual([x["details"]["outcome"] for x in _chain_records(home, "_default", "video_producer.style_imported")], ["refused"])

    def test_the_real_importer_on_a_real_deck_when_fixtures_exist(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            import importlib.util

            fx = Path(_route_module().__file__).resolve()
            pkg = sys.modules[_route_module()._PLUGIN_PKG]
            path = Path(pkg.__file__).resolve().parent.parent / "tests" / "style_fixtures.py"
            if not path.is_file():
                self.skipTest("tests/style_fixtures.py is not in the plugin yet")
            spec = importlib.util.spec_from_file_location("_style_fixtures_for_console", path)
            fixtures = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fixtures)
            deck = fixtures.make_deck()
            r = client.post(f"{V}/styles/import", files={"file": ("deck.pptx", io.BytesIO(deck), "application/octet-stream")}, headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            draft = r.json()["draft"]
            self.assertIn("tokens", draft)
            # import -> commit -> the stored style is what the importer read
            c = client.post(f"{V}/styles", json={"draft": draft}, headers=H(csrf))
            self.assertEqual(c.status_code, 201, c.text)
            self.assertEqual(c.json()["style"]["source"]["kind"], draft["source"]["kind"])

    # ── audit trail ──────────────────────────────────────────────────────────
    def test_audit_records_are_in_the_tenant_chain_and_the_chain_verifies(self):
        from forge import security_events as se

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            ca, csrf_a = clients["tenant_a"]
            sid = self._commit(ca, csrf_a).json()["style"]["id"]
            job_id = self._job(ca, csrf_a, style_id=sid).json()["job_id"]
            job2 = self._job(ca, csrf_a, style_id="corvin").json()["job_id"]
            self.assertEqual(ca.delete(f"{V}/styles/{sid}", headers=H(csrf_a)).status_code, 204)

            saved = _chain_records(home, "tenant_a", "video_producer.style_saved")
            self.assertEqual(len(saved), 1)
            self.assertEqual({k: saved[0]["details"][k] for k in ("style_id", "source_kind", "has_plate", "has_mark")},
                             {"style_id": sid, "source_kind": "tokens", "has_plate": False, "has_mark": True})
            self.assertEqual(saved[0]["details"]["tenant_id"], "tenant_a")
            applied = _chain_records(home, "tenant_a", "video_producer.style_applied")
            self.assertEqual([(a["details"]["job_id"], a["details"]["style_id"]) for a in applied], [(job_id, sid), (job2, "corvin")])
            deleted = _chain_records(home, "tenant_a", "video_producer.style_deleted")
            self.assertEqual(deleted[0]["details"]["style_id"], sid)
            for rec in saved + applied + deleted:
                self.assertNotIn("_dropped_fields", rec["details"], "an allowlisted field was dropped")
                self.assertNotIn("Acme", json.dumps(rec), "style text leaked into the chain")
            self.assertEqual(_chain_records(home, "tenant_b", "video_producer.style_saved"), [])
            ok, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok, problems)

    # ── erasure ──────────────────────────────────────────────────────────────
    def test_erasure_removes_the_tenant_video_store_including_styles(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            self._commit(ca, csrf_a)
            # B is written straight into its store: the audit writer refuses a tenant other than the
            # process tenant (CORVIN_TENANT_ID, the first sandbox tenant) - one console serves one tenant.
            sp = _plugin("style_pack")
            _plugin("style_store").StyleStore(str(home / "tenants" / "tenant_b" / "video_producer")).save(
                sp.draft_from_wire(_wire(), style_id="sty_bbbbbbbb", imported_at="2026-10-10T00:00:00Z"))
            self._job(ca, csrf_a)
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"))
            import erasure_handlers as eh

            a_root = home / "tenants" / "tenant_a" / "video_producer"
            self.assertTrue(any((a_root / "styles").iterdir()))
            # an unrelated subject: un-attributed jobs and styles stay
            res = eh.VideoProducerHandler(tenant_id="tenant_a").purge("some_user", "req-1")
            self.assertTrue(any((a_root / "styles").iterdir()))
            self.assertNotEqual(res.status.value if hasattr(res.status, "value") else res.status, "failed")
            # the tenant as subject: everything goes, the other tenant is untouched
            res = eh.VideoProducerHandler(tenant_id="tenant_a").purge("tenant_a", "req-2")
            self.assertFalse(a_root.exists())
            self.assertGreater(res.count, 0)
            self.assertEqual(len(cb.get(f"{V}/styles").json()["styles"]), 1)
            self.assertEqual(ca.get(f"{V}/styles").json()["styles"], [])
            chain_ids = {h.layer_id for h in eh.real_handler_chain(tenant_id="tenant_a")}
            self.assertIn("L-video-producer", chain_ids)
            self.assertIn("video_producer", eh.COVERED_DIRS["L-video-producer"])


def _plugin_has_no_browser() -> bool:
    import importlib.util

    return importlib.util.find_spec("playwright") is None


def _chain_records(home: Path, tenant: str, event: str) -> list:
    path = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event_type", rec.get("event")) == event:
            out.append(rec)
    return out


if __name__ == "__main__":
    unittest.main()
