"""D-15 — FileSkillStore / SkillPersistence refuse path traversal."""
from __future__ import annotations

import pytest

from core.skills.persistence import SkillPersistence
from core.skills.skill import Skill
from core.skills.store import FileSkillStore


class TestFileSkillStore:

    @pytest.mark.parametrize("name", ["..", "../x", "a/b", ".", "-x", "a\\b"])
    def test_bad_name_rejected(self, tmp_path, name):
        store = FileSkillStore(tmp_path / "store")
        with pytest.raises(ValueError):
            store.load(name, "1")
        with pytest.raises(ValueError):
            store.exists(name, "1")
        with pytest.raises(ValueError):
            store.delete(name, "1")
        assert not list((tmp_path).glob("*.json"))

    def test_bad_version_rejected(self, tmp_path):
        store = FileSkillStore(tmp_path / "store")
        with pytest.raises(ValueError):
            store.save(Skill(name="ok", version="../v", body="b"))
        assert not (tmp_path / "v.json").exists()

    def test_good_roundtrip(self, tmp_path):
        store = FileSkillStore(tmp_path / "store")
        store.save(Skill(name="ok.skill", version="1.0", body="b"))
        assert store.exists("ok.skill", "1.0")
        assert store.load("ok.skill", "1.0").body == "b"
        assert (tmp_path / "store" / "ok.skill" / "1.0.json").exists()


class TestSkillPersistence:

    @pytest.mark.parametrize("tenant", ["../", "..", "a/b", "test", "global"])
    def test_bad_tenant_rejected(self, tmp_path, tenant):
        p = SkillPersistence(tmp_path / "base")
        with pytest.raises(ValueError):
            p.save_skill(tenant, "s", "1", {"x": 1})
        with pytest.raises(ValueError):
            p.load_skill(tenant, "s", "1")

    @pytest.mark.parametrize("name", ["../../../esc", "a/b", "..", "-esc"])
    def test_bad_skill_name_rejected(self, tmp_path, name):
        p = SkillPersistence(tmp_path / "base")
        with pytest.raises(ValueError):
            p.save_skill("t1", name, "1", {"x": 1})
        assert not list(tmp_path.glob("*esc*"))

    def test_good_roundtrip(self, tmp_path):
        p = SkillPersistence(tmp_path / "base")
        p.save_skill("t1", "s.k", "1.0", {"x": 1})
        assert p.load_skill("t1", "s.k", "1.0") == {"x": 1}
        assert p.delete_skill("t1", "s.k", "1.0") is True
