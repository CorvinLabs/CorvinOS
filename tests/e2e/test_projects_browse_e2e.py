"""PLAN-0972 phase 2 / ADR-2322 - the server-side folder browser over real HTTP (T-0391).

``GET /v1/console/projects/-/browse/places``, ``GET /v1/console/projects/-/browse``, ``PUT …/browse/favourites``,
``DELETE …/browse/recent`` through the console router with real sessions, a real filesystem and the real audit chain of the
sandbox. Requests run concurrently in ONE event loop (httpx over the ASGI app), so "the loop is not blocked" is measured, not
assumed. Every test runs with HOME and XDG_DATA_HOME inside its own temp dir (``sandbox_home_env``); the real install is never
reached.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
from _projects_fixtures import sandbox_home_env, small_catalogue, write_template  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/projects"
_B = f"{_URL}/-/browse"
POSIX = os.name != "nt"


def _plant_project(d: Path, pid: str, tenant: str) -> None:
    from core.projects import manifest
    d.mkdir(parents=True, exist_ok=True)
    manifest.write(d, {"schema": manifest.SCHEMA, "id": pid, "title": pid.title(), "tenant": tenant,
                       "template": "adopted@1", "docs_mode": "plain", "features": {}})


def _register_foreign(d: Path, pid: str) -> None:
    """Another tenant's registered project. Written through the registry, not acme's HTTP adopt: the console process is bound to
    its boot tenant and the audit writer refuses a record of another tenant (AuditTenantMismatch) - as on a live install."""
    from core.projects import registry
    registry.register("acme", pid, d, "adopted@1")


class _NtOs:
    name = "nt"

    def __getattr__(self, k):
        return getattr(os, k)


class BrowseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = small_catalogue(self.tmp / "src")
        write_template(self.src, "plain", ["core"])
        self._env = sandbox_home_env(self.tmp)
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @contextlib.contextmanager
    def _ctx(self):
        outer = self
        with _sandbox(self.tmp / "s", tenants=("_default", "acme", "t3")) as (_c, _t, home, clients):
            from corvin_console import auth as _auth
            from corvin_console import projects_service as svc
            with mock.patch.object(svc, "template_source", lambda tenant: outer.src), \
                    mock.patch.object(svc, "plugin_state", lambda tenant, pid: "enabled"):
                svc.BROWSE_GATE.reset()

                class C:
                    pass
                c = C()
                c.home, c.svc, c.app = home, svc, clients["_default"][0].app
                c.own, c.csrf = clients["_default"]
                c.other, c.other_csrf = clients["acme"]
                c.h = {"X-CSRF-Token": c.csrf}

                def session(tenant="_default"):
                    rec = _auth.create_session(tenant_id=tenant, token_fingerprint="test-fp")
                    return rec.sid, _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
                c.session = session
                # warm-up: the first request of a fresh console app pays ~2 s of one-time setup on the event loop (measured,
                # any route); the bounds below are about steady state, so it is paid here, and it binds the managed workspace
                assert c.own.get(_URL).status_code == 200
                yield c
                svc.BROWSE_GATE.reset()

    def _client(self, c, sid=None):
        if sid is None:
            sid = c.own.cookies.get("corvin_console_sid")
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="http://127.0.0.1",
                                 cookies={"corvin_console_sid": sid})

    def _get(self, c, path, purpose="create", **q):
        return c.own.get(_B, params={"path": str(path), "purpose": purpose, **q})

    def _chain_text(self, c, tenant="_default") -> str:
        f = c.home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def _refusals(self, c, tenant="_default"):
        return [e for e in _audit_events(c.home, tenant)
                if (e.get("event_type") or e.get("event")) == "project.browse_refused"]


class Listing(BrowseBase):
    # AC1, AC4
    def test_corvin_home_is_a_row_never_entered_and_files_never_appear(self):
        with self._ctx() as c:
            (c.home / "tenants" / "_default" / "planted-child-xyz").mkdir()
            work = self.tmp / "work"
            (work / "a").mkdir(parents=True)
            (work / "planted-file-name.txt").write_text("x")
            answers = []
            r = self._get(c, self.tmp / "s")
            self.assertEqual(r.status_code, 200, r.text)
            answers.append(r.text)
            row = [e for e in r.json()["entries"] if e["name"] == "corvin_home"][0]
            self.assertEqual(row["refusal"], "corvin_home")
            for p in (c.home, c.home / "tenants", c.home / "tenants" / "_default"):
                r = self._get(c, p)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["entries"], [])
                self.assertEqual(r.json()["verdict"], {"ok": False, "reason_class": "corvin_home",
                                                       "message": "This is Corvin's own data; projects cannot live here."})
                answers.append(r.text)
            r = self._get(c, work)
            self.assertEqual([e["name"] for e in r.json()["entries"]], ["a"])
            answers.append(r.text)
            answers.append(c.own.get(f"{_B}/places").text)
            text = "\n".join(answers)
            self.assertNotIn("planted-child-xyz", text)
            self.assertNotIn("planted-file-name", text)

    # AC2
    def test_another_tenants_project_is_existence_only_and_not_entered(self):
        with self._ctx() as c:
            work = self.tmp / "work"
            _plant_project(work / "theirs", "theirs", "acme")
            (work / "theirs" / "foreign-child-xyz").mkdir()
            _register_foreign(work / "theirs", "theirs")
            row = [e for e in self._get(c, work).json()["entries"] if e["name"] == "theirs"][0]
            self.assertEqual(row["project"], {"state": "other"})
            self.assertEqual(row["refusal"], "other_tenant")
            for p in (work / "theirs", work / "theirs" / "foreign-child-xyz"):
                r = self._get(c, p, "adopt")
                self.assertEqual((r.status_code, r.json()["entries"], r.json()["verdict"]["reason_class"]),
                                 (200, [], "other_tenant"))
                self.assertNotIn("foreign-child-xyz", json.dumps(r.json()["entries"]))

    # AC3
    def test_a_symlink_is_flagged_and_never_followed(self):
        with self._ctx() as c:
            work = self.tmp / "work"
            (work / "target" / "behind-link-xyz").mkdir(parents=True)
            os.symlink(work / "target", work / "linked")
            row = [e for e in self._get(c, work).json()["entries"] if e["name"] == "linked"][0]
            self.assertEqual((row["symlink"], row["refusal"]), (True, "symlink"))
            r = self._get(c, work / "linked")
            self.assertEqual((r.json()["entries"], r.json()["verdict"]["reason_class"]), ([], "symlink"))
            self.assertNotIn("behind-link-xyz", r.text)

    # AC5b
    def test_the_managed_parent_and_a_second_workspace_are_withheld(self):
        with self._ctx() as c:
            own = Path(c.own.get(_URL).json()["managed_root"])
            from core.projects import registry
            foreign, _o = registry.ensure_managed_root("acme")
            (foreign / "foreign-planted-xyz").mkdir()
            (own / "own-planted-xyz").mkdir()
            parent = own.parent
            for p in (parent, foreign, foreign / "foreign-planted-xyz"):
                r = self._get(c, p)
                self.assertEqual((r.status_code, r.json()["entries"], r.json()["verdict"]["reason_class"]),
                                 (200, [], "other_tenant"), p)
                self.assertNotIn("foreign-planted-xyz", json.dumps(r.json()["entries"]))
                self.assertNotIn("own-planted-xyz", json.dumps(r.json()["entries"]))
            self.assertNotIn(foreign.name, self._get(c, parent).text)
            up = self._get(c, parent.parent, hidden="1").json()["entries"]
            rows = [e for e in up if e["path"] == str(own)]
            self.assertEqual([e["name"] for e in rows], ["Corvin workspaces"])
            self.assertNotIn(foreign.name, json.dumps(up))
            r = self._get(c, own)
            self.assertEqual([e["name"] for e in r.json()["entries"]], ["own-planted-xyz"])
            self.assertEqual(r.json()["verdict"]["reason_class"], "workspace_root")

    # AC5c
    def test_a_unc_path_is_refused_without_a_filesystem_call(self):
        with self._ctx() as c:
            from core.projects import registry
            calls = []
            real = {fn: getattr(os, fn) for fn in ("stat", "lstat", "scandir", "listdir", "access")}

            def spy(name):
                def _s(p, *a, **k):
                    if "uncshare" in str(p):
                        calls.append((name, str(p)))
                    return real[name](p, *a, **k)
                return _s
            with mock.patch.object(registry, "os", _NtOs()), \
                    mock.patch.multiple(os, **{fn: spy(fn) for fn in real}):
                for bad in ("\\\\uncshare\\s\\p", "\\\\?\\UNC\\uncshare\\s\\p", "//uncshare/s/p"):
                    r = c.own.get(_B, params={"path": bad, "purpose": "adopt"})
                    self.assertEqual(r.status_code, 422, r.text)
                    self.assertEqual(r.json()["detail"]["reason_class"], "path_rule")
                    self.assertIn("map the share to a drive letter", r.json()["detail"]["message"])
            self.assertEqual(calls, [])

    def test_the_input_rule_answers_422_with_the_sentence(self):
        with self._ctx() as c:
            for q in ({"path": "relative/x", "purpose": "create"}, {"path": "/a/../b", "purpose": "create"},
                      {"path": str(self.tmp), "purpose": "bogus"}, {"path": str(self.tmp), "purpose": "move"}):
                r = c.own.get(_B, params=q)
                self.assertEqual(r.status_code, 422, (q, r.text))
                self.assertEqual(r.json()["detail"], {"message": "The path must be absolute, without '..'.",
                                                      "reason_class": "path_rule"})

    def test_no_session_no_listing(self):
        with self._ctx() as c:
            import httpx as _h
            async def run():
                async with _h.AsyncClient(transport=_h.ASGITransport(app=c.app), base_url="http://127.0.0.1") as cl:
                    return (await cl.get(_B, params={"path": str(self.tmp), "purpose": "create"})).status_code
            self.assertEqual(asyncio.run(run()), 401)


class Bounds(BrowseBase):
    # AC5: a slow lister answers slow within 2.5 s; another path answers normally meanwhile
    def test_a_slow_lister_answers_slow_without_blocking_the_loop(self):
        with self._ctx() as c:
            from core.projects import browse as B
            (self.tmp / "slowdir").mkdir()
            (self.tmp / "fast" / "x").mkdir(parents=True)
            real = B.listing

            def lister(tenant, path, *a, **k):
                if "slowdir" in path:
                    time.sleep(3)
                return real(tenant, path, *a, **k)
            sid2, _ = c.session()

            async def run():
                async with self._client(c) as a, self._client(c, sid2) as b:
                    t0 = time.monotonic()

                    async def slow():
                        r = await a.get(_B, params={"path": str(self.tmp / "slowdir"), "purpose": "create"})
                        return r, time.monotonic() - t0

                    async def fast():
                        await asyncio.sleep(0.2)
                        r = await b.get(_B, params={"path": str(self.tmp / "fast"), "purpose": "create"})
                        return r, time.monotonic() - t0
                    return await asyncio.gather(slow(), fast())
            with mock.patch.object(B, "listing", lister):
                (rs, ts), (rf, tf) = asyncio.run(run())
            self.assertEqual(rs.status_code, 200)
            self.assertEqual(rs.json()["verdict"]["reason_class"], "slow")
            self.assertEqual(rs.json()["verdict"]["message"],
                             "This folder did not answer in time (network share?). Try again or choose another place.")
            self.assertLess(ts, 2.5)
            self.assertEqual([e["name"] for e in rf.json()["entries"]], ["x"])
            self.assertLess(tf, 1.5, "the second request was not held by the slow one")
            self._wait_free(c)

    def _wait_free(self, c, timeout=6.0):
        t0 = time.monotonic()
        while c.svc.BROWSE_GATE.busy()[0] and time.monotonic() - t0 < timeout:
            time.sleep(0.05)
        self.assertEqual(c.svc.BROWSE_GATE.busy(), (0, {}), "every slot returned")

    # AC5a
    def test_twenty_hung_listings_delay_neither_audit_nor_the_project_list(self):
        with self._ctx() as c:
            from core.projects import browse as B
            from core.task_tracking import service as tt
            (self.tmp / "hang").mkdir()
            (self.tmp / "ok" / "child").mkdir(parents=True)
            gate_ev = threading.Event()
            real = B.listing

            def lister(tenant, path, *a, **k):
                if "hang" in path:
                    gate_ev.wait(30)
                return real(tenant, path, *a, **k)
            tenants = ["_default", "acme", "t3"]
            sids = [c.session(tenants[i % 3])[0] for i in range(20)]
            sid_a2 = c.session("_default")[0]

            async def run():
                clients = [self._client(c, s) for s in sids]
                try:
                    out = []

                    async def one(cl, delay):
                        await asyncio.sleep(delay)
                        t = time.monotonic()
                        r = await cl.get(_B, params={"path": str(self.tmp / "hang"), "purpose": "create"})
                        out.append((r, time.monotonic() - t))
                    # four hang first (2 tenants x 2 slots), then 16 more
                    first = [asyncio.ensure_future(one(clients[i], 0)) for i in (0, 1, 3, 4)]     # _default, acme, _default, acme
                    await asyncio.sleep(0.3)
                    busy = c.svc.BROWSE_GATE.busy()
                    # the 16 others while the four hang - one after another, so each one's own latency is measured (fired all at
                    # once they queue behind each other's session lookup and audit write, which is not the property under test)
                    for i in range(20):
                        if i not in (0, 1, 3, 4):
                            await one(clients[i], 0)
                    rest = []
                    # while all hang: an audit write through asyncio.to_thread and GET /projects
                    t = time.monotonic()
                    await asyncio.to_thread(tt._chain, "_default", "project.browse_refused", {"reason_class": "slow"})
                    audit_s = time.monotonic() - t
                    async with self._client(c) as plain:
                        t = time.monotonic()
                        lst = await plain.get(_URL)
                        list_s = time.monotonic() - t
                    async with self._client(c, sid_a2) as a3:
                        third = await a3.get(_B, params={"path": str(self.tmp / "ok"), "purpose": "create"})
                    await asyncio.gather(*rest)
                    await asyncio.gather(*first)                # the four hung ones answer `slow` at their 2 s budget
                    gate_ev.set()                                # only now do their threads return and free the slots
                    return out, busy, audit_s, list_s, lst, third
                finally:
                    for cl in clients:
                        await cl.aclose()
            with mock.patch.object(B, "listing", lister):
                out, busy, audit_s, list_s, lst, third = asyncio.run(run())
                self._wait_free(c)
                after = c.own.get(_B, params={"path": str(self.tmp / "ok"), "purpose": "create"})
            self.assertEqual(busy, (4, {"_default": 2, "acme": 2}))
            self.assertEqual(len(out), 20)
            self.assertTrue(all(r.status_code == 200 and r.json()["verdict"]["reason_class"] == "slow" for r, _t in out))
            quick = [t for r, t in out if t < 0.1]
            self.assertGreaterEqual(len(quick), 16, sorted(t for _r, t in out))
            self.assertLess(audit_s, 0.2)
            self.assertLess(list_s, 0.2)
            self.assertEqual(lst.status_code, 200)
            self.assertEqual(third.json()["verdict"]["reason_class"], "slow", "the tenant's two slots are taken")
            self.assertEqual([e["name"] for e in after.json()["entries"]], ["child"], "the slots came back")

    # AC5a: per-tenant cap
    def test_a_third_listing_of_one_tenant_is_slow_while_another_tenant_lists(self):
        with self._ctx() as c:
            from core.projects import browse as B
            (self.tmp / "hang").mkdir()
            (self.tmp / "ok" / "child").mkdir(parents=True)
            ev = threading.Event()
            real = B.listing

            def lister(tenant, path, *a, **k):
                if "hang" in path:
                    ev.wait(30)
                return real(tenant, path, *a, **k)
            a1, a2, a3 = (c.session("_default")[0] for _ in range(3))
            b1 = c.session("acme")[0]

            async def run():
                async with self._client(c, a1) as x1, self._client(c, a2) as x2, self._client(c, a3) as x3, \
                        self._client(c, b1) as y1:
                    h = [asyncio.ensure_future(x.get(_B, params={"path": str(self.tmp / "hang"), "purpose": "create"}))
                         for x in (x1, x2)]
                    await asyncio.sleep(0.3)
                    t = time.monotonic()
                    r3 = await x3.get(_B, params={"path": str(self.tmp / "ok"), "purpose": "create"})
                    t3 = time.monotonic() - t
                    rb = await y1.get(_B, params={"path": str(self.tmp / "ok"), "purpose": "create"})
                    ev.set()
                    await asyncio.gather(*h)
                    return r3, t3, rb
            with mock.patch.object(B, "listing", lister):
                r3, t3, rb = asyncio.run(run())
            self.assertEqual(r3.json()["verdict"]["reason_class"], "slow")
            self.assertLess(t3, 0.1)
            self.assertEqual([e["name"] for e in rb.json()["entries"]], ["child"])
            self._wait_free(c)

    def test_a_late_result_is_served_to_the_next_request(self):
        with self._ctx() as c:
            from core.projects import browse as B
            (self.tmp / "late" / "kid").mkdir(parents=True)
            real = B.listing
            n = {"calls": 0}

            def lister(tenant, path, *a, **k):
                n["calls"] += 1
                if n["calls"] == 1:
                    time.sleep(2.4)
                return real(tenant, path, *a, **k)
            with mock.patch.object(B, "listing", lister):
                r1 = self._get(c, self.tmp / "late")
                self.assertEqual(r1.json()["verdict"]["reason_class"], "slow")
                self._wait_free(c)
                r2 = self._get(c, self.tmp / "late")
            self.assertEqual([e["name"] for e in r2.json()["entries"]], ["kid"])
            self.assertEqual(n["calls"], 1, "served from the late cache")

    # AC6
    def test_the_eleventh_request_in_a_second_is_429_and_another_session_is_not(self):
        with self._ctx() as c:
            (self.tmp / "t" / "x").mkdir(parents=True)
            s1, s2 = c.session()[0], c.session()[0]

            async def run():
                async with self._client(c, s1) as a, self._client(c, s2) as b:
                    rs = await asyncio.gather(*[a.get(_B, params={"path": str(self.tmp / "t"), "purpose": "create"})
                                                for _ in range(11)])
                    other = await b.get(_B, params={"path": str(self.tmp / "t"), "purpose": "create"})
                    return rs, other
            rs, other = asyncio.run(run())
            codes = [r.status_code for r in rs]
            self.assertEqual(codes.count(429), 1, codes)
            bad = [r for r in rs if r.status_code == 429][0]
            self.assertEqual(bad.json()["detail"], {"message": "Too many folder requests at once; wait a moment.",
                                                    "reason_class": "throttled"})
            self.assertEqual(other.status_code, 200)
            self._wait_free(c)


class VerdictAndAudit(BrowseBase):
    # AC7: the browse verdict equals the mutation's answer for the same path and purpose
    def test_verdict_equals_the_mutation_for_every_planted_path(self):
        with self._ctx() as c:
            work = self.tmp / "work"
            (work / "full" / "f").mkdir(parents=True)
            (work / "empty").mkdir()
            (work / ".hid" / "x").mkdir(parents=True)
            (work / "repo" / ".git" / "y").mkdir(parents=True)
            (work / "target").mkdir()
            os.symlink(work / "target", work / "lnk")
            _plant_project(work / "theirs", "theirs", "acme")
            _register_foreign(work / "theirs", "theirs")
            plan, r = None, None
            p = c.own.post(f"{_URL}/plan", json={"template": "plain", "title": "Mover"}, headers=c.h)
            self.assertEqual(p.status_code, 200, p.text)
            self.assertEqual(c.own.post(_URL, json={"plan_id": p.json()["plan_id"]}, headers=c.h).status_code, 201)
            cases = [work / "full", work / "empty" / "new", work / ".hid" / "x", work / "repo" / ".git" / "y", work / "lnk",
                     work / "lnk" / "deeper", work / "theirs", c.home / "tenants" / "x", work / "absent", work / "full" / "f"]

            def mutate(purpose, path):
                if purpose == "create":
                    r = c.own.post(f"{_URL}/plan", json={"template": "plain", "title": "Probe", "location": str(path)},
                                   headers=c.h)
                elif purpose == "adopt":
                    r = c.own.post(f"{_URL}/adopt", json={"location": str(path), "project_id": "probe-adopt"}, headers=c.h)
                else:
                    r = c.own.post(f"{_URL}/mover/move", json={"location": str(path)}, headers=c.h)
                if r.status_code in (200, 201):
                    return True, None, r
                return False, (r.json().get("detail") or {}).get("reason_class"), r

            seen = set()
            for path in cases:
                for purpose in ("create", "adopt", "move"):
                    q = {"path": str(path), "purpose": purpose}
                    if purpose == "move":
                        q["project_id"] = "mover"
                    c.svc.BROWSE_GATE.reset()                   # 30 requests from one session: the throttle is tested apart
                    v = c.own.get(_B, params=q).json()["verdict"]
                    ok, cls, resp = mutate(purpose, path)
                    self.assertEqual((v["ok"], v.get("reason_class")), (ok, cls), (str(path), purpose, resp.text))
                    if ok and purpose == "adopt":
                        c.own.delete(f"{_URL}/probe-adopt", headers=c.h)
                        with contextlib.suppress(OSError):
                            (Path(path) / "project.yaml").unlink()
                    if ok and purpose == "move":
                        back = c.own.post(f"{_URL}/mover/move", json={"location": str(self._managed(c) / "mover")},
                                          headers=c.h)
                        self.assertEqual(back.status_code, 200, back.text)
                    seen.add(cls)
            self.assertTrue({None, "not_empty", "hidden", "protected", "symlink", "other_tenant", "corvin_home",
                             "missing"} <= seen, seen)

    def _managed(self, c) -> Path:
        return Path(c.own.get(_URL).json()["managed_root"])

    # AC8
    def test_browse_refused_is_registered_carries_no_path_and_is_rate_limited(self):
        from forge import security_events as se
        self.assertEqual(se.EVENT_SEVERITY["project.browse_refused"], "WARNING")
        self.assertEqual(se._EVENT_ALLOWLIST["project.browse_refused"], frozenset({"reason_class", "tenant_id"}))
        with self._ctx() as c:
            before = len(self._refusals(c))
            for _ in range(20):
                c.own.get(_B, params={"path": "relative/x", "purpose": "create"})
            recs = self._refusals(c)[before:]
            self.assertEqual(len(recs), 1, recs)
            self.assertEqual((recs[0].get("details") or {}).get("reason_class"), "path_rule")
            sid2 = c.session()[0]

            async def other():
                async with self._client(c, sid2) as cl:
                    return await cl.get(_B, params={"path": str(c.home / "tenants"), "purpose": "create"})
            r = asyncio.run(other())
            self.assertEqual(r.json()["verdict"]["reason_class"], "corvin_home")
            recs = self._refusals(c)[before:]
            self.assertEqual([(e.get("details") or {}).get("reason_class") for e in recs], ["path_rule", "corvin_home"])
            # successful listings are not audited
            n = len(self._refusals(c))
            (self.tmp / "plain").mkdir()
            self._get(c, self.tmp / "plain")
            self.assertEqual(len(self._refusals(c)), n)
            text = self._chain_text(c)
            self.assertNotIn(str(self.tmp), text, "an audit record carries a path")

    # AC9
    def test_favourites_round_trip_and_recent_after_adopt(self):
        with self._ctx() as c:
            favs = [str(self.tmp / f"f{i}") for i in range(3)]
            for f in favs:
                Path(f).mkdir()
            r = c.own.put(f"{_B}/favourites", json={"paths": favs}, headers=c.h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual([p["path"] for p in r.json()["places"]], favs)
            self.assertTrue(all(p["kind"] == "favourite" for p in r.json()["places"]))
            f = c.home / "tenants" / "_default" / "projects" / "picker.json"
            self.assertEqual(json.loads(f.read_text())["favourites"], favs)
            if POSIX:
                self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            r = c.own.put(f"{_B}/favourites", json={"paths": [str(self.tmp / f"g{i}") for i in range(11)]}, headers=c.h)
            self.assertEqual((r.status_code, r.json()["detail"]), (422, {"message": "At most 10 favourites.",
                                                                          "reason_class": "path_rule"}))
            self.assertEqual(c.own.put(f"{_B}/favourites", json={"paths": favs}).status_code, 403, "CSRF required")
            folder = self.tmp / "adopt-me"
            folder.mkdir()
            self.assertEqual(c.own.post(f"{_URL}/adopt", json={"location": str(folder)}, headers=c.h).status_code, 201)
            pl = c.own.get(f"{_B}/places").json()["places"]
            self.assertIn({"kind": "recent", "path": str(folder)}, [{"kind": p["kind"], "path": p["path"]} for p in pl])
            self.assertEqual(pl[0]["kind"], "managed")
            r = c.own.delete(f"{_B}/recent", headers=c.h)
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("recent", [p["kind"] for p in r.json()["places"]])
            self.assertEqual(json.loads(f.read_text())["recent"], [])
            # the other tenant's store is its own
            self.assertNotIn("favourite", [p["kind"] for p in c.other.get(f"{_B}/places").json()["places"]])


if __name__ == "__main__":
    unittest.main()
