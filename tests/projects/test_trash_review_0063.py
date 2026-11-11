"""REVIEW-0063 m6/m8 - the trash of the managed workspace (ADR-2321 Decision 12).

m6: the listing, the count and the 30-day sweep must see every entry, whatever the directory order and however many foreign
names lie in ``.trash``. The directory order is forced (``os.scandir`` returns the junk / young names first) so the old
200-name window is hit deterministically.
m8: a sidecar's id must be the id the entry name carries, and the registry's ``trash_entry`` must name the entry restored.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_project_bundle import BundleBase  # noqa: E402

from core.projects import registry  # noqa: E402

DAY = 86400.0


def _stamp(ts: float) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))


class _Ordered:
    """``os.scandir`` with the entries of ``.trash`` in a chosen order (key over the name), everything else unchanged."""

    def __init__(self, trash: Path, key):
        self.trash, self.key, self.real = str(trash), key, os.scandir

    def __call__(self, p=".", *a):
        it = self.real(p, *a)
        if isinstance(p, (str, os.PathLike)) and os.path.abspath(os.fspath(p)) == self.trash:
            ents = sorted(it, key=lambda e: self.key(e.name))
            it.close()
            return _List(ents)
        return it


class _List:
    def __init__(self, ents):
        self.ents = ents

    def __iter__(self):
        return iter(self.ents)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


class TrashBase(BundleBase):
    def setUp(self):
        super().setUp()
        mr, _o = registry.ensure_managed_root("_default")
        self.trash = mr / ".trash"
        self.trash.mkdir(mode=0o700, exist_ok=True)
        self.now = time.time()

    def entry(self, pid: str, age_days: float, *, side_id: str | None = None, n: int = 0) -> str:
        ts = self.now - age_days * DAY
        name = f"{pid}-{_stamp(ts)}" + (f"-{n}" if n else "")
        d = self.trash / name
        (d / "project").mkdir(parents=True)
        (d / "trash.json").write_text(json.dumps({"schema": 1, "id": side_id or pid, "title": pid, "deleted_at": _stamp(ts),
                                                  "deleted_ts": ts, "original_path": "/x", "state": "trashed"}))
        return name

    def junk(self, k: int) -> None:
        for i in range(k):
            (self.trash / f"Junk_{i:04d}").mkdir()


class TrashWindow(TrashBase):
    def test_foreign_names_never_hide_entries_from_list_count_or_sweep(self):
        self.junk(300)
        old = [self.entry(f"old{i}", 60) for i in range(2)]
        young = self.entry("young", 1)
        order = _Ordered(self.trash, lambda n: (not n.startswith("Junk_"), n))     # junk first
        with mock.patch.object(self.svc.os, "scandir", order):
            listed = {r["entry"] for r in self.svc.list_trash("_default")["entries"]}
            count = self.svc.list_projects("_default")["trash_count"]
            swept = self.svc.sweep_trash("_default", now=self.now)
        self.assertEqual(listed, {*old, young})
        self.assertEqual(count, 3)
        self.assertEqual(swept["purged"], 2)
        self.assertTrue((self.trash / young).is_dir())
        self.assertFalse(any((self.trash / o).exists() for o in old))

    def test_old_entries_behind_many_young_ones_are_swept_within_the_bound(self):
        for i in range(400):
            self.entry(f"y{i:03d}", 10)
        old = [self.entry(f"o{i:02d}", 60) for i in range(40)]
        order = _Ordered(self.trash, lambda n: (n.startswith("o"), n))            # the young ones first
        ticks = 0
        with mock.patch.object(self.svc.os, "scandir", order):
            while any((self.trash / o).exists() for o in old) and ticks < 8:
                self.svc.sweep_trash("_default", now=self.now)
                ticks += 1
        self.assertFalse(any((self.trash / o).exists() for o in old), "old entries survived the bound")
        self.assertLessEqual(ticks, 4)                                             # 40 old at 10 per tick
        self.assertEqual(len([p for p in self.trash.iterdir() if p.name.startswith("y")]), 400)

    def test_the_listing_pages_through_every_entry_newest_first(self):
        names = [self.entry(f"p{i:03d}", 1 + i / 1000) for i in range(450)]
        first = self.svc.list_trash("_default")
        self.assertEqual((first["total"], len(first["entries"]), first["next_offset"]), (450, 200, 200))
        seen = [r["entry"] for r in first["entries"]]
        off = first["next_offset"]
        while off is not None:
            page = self.svc.list_trash("_default", offset=off)
            seen += [r["entry"] for r in page["entries"]]
            off = page["next_offset"]
        self.assertEqual(sorted(seen), sorted(names))
        self.assertEqual(seen[0], names[0])                                        # p000 is the newest (1 day old)


class SidecarBinding(TrashBase):
    def test_a_sidecar_naming_another_id_is_not_an_entry(self):
        planted = self.entry("beta", 2, side_id="alpha")
        self.assertNotIn(planted, {r["entry"] for r in self.svc.list_trash("_default")["entries"]})
        with self.assertRaises(self.svc.NotFound):
            self.svc.restore_project("_default", planted)
        self.assertIsNone(registry.get("_default", "alpha"))
        self.assertFalse((registry.known_managed_root("_default") / "alpha").exists())

    def test_restore_needs_the_entry_the_registry_names_for_a_trashed_project(self):
        plan = self.svc.plan_project("_default", template="plain", title="Alpha", storage="managed")
        pid = self.svc.create_project("_default", plan["plan_id"])["project"]["id"]
        real = self.svc.delete_project_files("_default", pid, confirm_id=pid)["trash_entry"]
        planted = self.entry(pid, 3, n=2)
        with self.assertRaises(self.svc.Conflict) as cm:
            self.svc.restore_project("_default", planted)
        self.assertEqual(cm.exception.reason_class, "id_taken")
        self.assertTrue(registry.is_trashed(registry.get("_default", pid)))
        out = self.svc.restore_project("_default", real)                           # the real one still restores
        self.assertEqual(out["id"], pid)


class BrokenSidecars(TrashBase):
    """REVIEW-0065 (REVIEW-0063 m6 residual): entries whose old NAME is past the TTL but whose sidecar is unreadable, names
    another id or disagrees with the name's time cost one read once - they are moved to ``.trash/.quarantine`` with one
    audit record per tick - and never hold the read budget of later ticks."""

    def _broken(self, k: int) -> list[str]:
        out = []
        for i in range(k):
            name = self.entry(f"b{i:03d}", 90)
            kind = i % 3
            if kind == 0:
                (self.trash / name / "trash.json").write_text("{not json")
            elif kind == 1:
                (self.trash / name / "trash.json").write_text(json.dumps({"id": "someone-else", "deleted_ts": 1}))
            else:                                                   # valid, but young by the sidecar while old by the name
                data = json.loads((self.trash / name / "trash.json").read_text())
                (self.trash / name / "trash.json").write_text(json.dumps({**data, "deleted_ts": self.now}))
            out.append(name)
        return out

    def test_two_hundred_broken_old_entries_do_not_block_real_expired_ones(self):
        broken = self._broken(200)
        real = [self.entry(f"r{i}", 45) for i in range(3)]           # newer names than the broken ones: behind them
        audits: list[tuple[str, dict]] = []
        orig = self.svc._audit

        def spy(tenant_id, event, **fields):
            audits.append((event, fields))
            return orig(tenant_id, event, **fields)
        with mock.patch.object(self.svc, "_audit", spy):
            first = self.svc.sweep_trash("_default", now=self.now)
            second = self.svc.sweep_trash("_default", now=self.now)
        self.assertEqual(first["quarantined"], 200)
        self.assertEqual(second["purged"], 3, second)
        self.assertFalse(any((self.trash / r).exists() for r in real))
        q = self.trash / ".quarantine"
        self.assertEqual(sorted(p.name for p in q.iterdir()), sorted(broken))   # moved, never deleted
        self.assertEqual(self.svc.trash_count("_default"), 0)
        recs = [f for e, f in audits if e == "project.trash_quarantined"]
        self.assertEqual(recs, [{"entries": 200, "outcome": "requested"}, {"entries": 200, "outcome": "applied"}])

    def test_a_quarantine_tick_is_bounded_by_the_read_budget(self):
        self._broken(self.svc.TRASH_SCAN_LIMIT + 50)
        first = self.svc.sweep_trash("_default", now=self.now)
        self.assertEqual(first["quarantined"], self.svc.TRASH_SCAN_LIMIT)
        second = self.svc.sweep_trash("_default", now=self.now)
        self.assertEqual(second["quarantined"], 50)

    def test_a_young_entry_with_a_broken_sidecar_is_left_alone(self):
        name = self.entry("fresh", 1)
        (self.trash / name / "trash.json").write_text("{not json")
        out = self.svc.sweep_trash("_default", now=self.now)
        self.assertEqual(out.get("quarantined", 0), 0)
        self.assertTrue((self.trash / name).is_dir())


if __name__ == "__main__":
    unittest.main()
