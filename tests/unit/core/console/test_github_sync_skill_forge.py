"""The GitHub sync publishes the Skill-Forge tree, not ``<tenant>/skills/``.

Until 2026-09-20 ``GitHubRepoSync`` read ``<tenant>/skills/*.md`` — three
config files, zero skills — so every 5-minute run pushed an empty manifest
and a fresh release tag (``skills_synced: 0``, 288 tags a day). The real
skills live in ``<tenant>/skill-forge/skills/<name>/{SKILL.md,meta.json}``
(629 on the maintainer host).

1. ``collect_skills`` finds nested skills (and still accepts flat ``*.md``);
2. ``sync_skills_to_github_real`` replaces the repository's ``skills/`` tree,
   writes ``skills/<name>/SKILL.md`` + ``meta.json`` and a manifest WITHOUT a
   clock — so an unchanged set commits nothing and tags nothing;
3. ``GitHubRepoSync`` resolves the Skill-Forge directory of ITS tenant.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from core.console.corvin_console.routes.github_git_wrapper import (
    GitHubGitWrapper,
    collect_skills,
    sync_skills_to_github_real,
)


def _forge(tmp_path: Path) -> Path:
    root = tmp_path / "skill-forge" / "skills"
    for name in ("cel_b_skill", "cel_a_skill"):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody of {name}\n")
        (d / "meta.json").write_text(json.dumps({"name": name, "type": "learned-experience"}))
    (root / "no_skill_here").mkdir()  # a directory without SKILL.md is not a skill
    (root / "legacy-flat.md").write_text("# flat\n")
    (root / "notes.txt").write_text("ignored")
    return root


def test_collect_skills_finds_nested_skill_dirs_deterministically(tmp_path):
    skills = collect_skills(_forge(tmp_path))
    assert [s["name"] for s in skills] == ["cel_a_skill", "cel_b_skill", "legacy-flat"]
    a = skills[0]
    assert [rel for rel, _ in a["files"]] == ["skills/cel_a_skill/SKILL.md", "skills/cel_a_skill/meta.json"]
    assert a["size"] == sum(len(d) for _, d in a["files"]) and len(a["sha256"]) == 64
    # same tree → same digest (the manifest must not change when nothing did)
    assert collect_skills(_forge(tmp_path / "again")) [0]["sha256"] == a["sha256"]
    assert collect_skills(tmp_path / "missing") == []


def test_real_sync_replaces_the_tree_and_tags_only_on_change(tmp_path):
    root = _forge(tmp_path)
    work = tmp_path / "clone"
    (work / "skills" / "stale_skill").mkdir(parents=True)
    (work / "skills" / "stale_skill" / "SKILL.md").write_text("gone locally")

    def fake_enter(self):
        self.work_dir = work
        return self

    with patch.object(GitHubGitWrapper, "__enter__", fake_enter), \
         patch.object(GitHubGitWrapper, "__exit__", lambda self, *a: None), \
         patch.object(GitHubGitWrapper, "_run_git", return_value=(0, "", "")), \
         patch.object(GitHubGitWrapper, "clone", return_value={"success": True}), \
         patch.object(GitHubGitWrapper, "commit_and_push", return_value={"success": True, "branch": "main", "commit_hash": "abc123"}) as push, \
         patch.object(GitHubGitWrapper, "create_tag", return_value={"success": True, "tag": "release-x"}) as tag:
        result = sync_skills_to_github_real("https://github.com/example/tenant", root, "_default")

    assert result["success"] and result["skills_synced"] == 3 and result["changed"] is True
    assert result["tag"] == "release-x" and tag.call_count == 1
    assert "skills/cel_a_skill/SKILL.md" in result["files_written"]
    assert "skills/cel_a_skill/meta.json" in result["files_written"]
    assert (work / "skills" / "cel_b_skill" / "meta.json").read_text() == json.dumps({"name": "cel_b_skill", "type": "learned-experience"})
    assert not (work / "skills" / "stale_skill").exists(), "a skill deleted locally must leave the repository"
    manifest = json.loads((work / "skills" / "manifest.json").read_text())
    assert manifest["skills_count"] == 3 and manifest["source"] == "skill-forge"
    assert "synced_at" not in manifest and "generated_at" not in manifest
    assert manifest["skills"][0] == {
        "name": "cel_a_skill", "size": result and manifest["skills"][0]["size"], "sha256": manifest["skills"][0]["sha256"],
        "files": ["skills/cel_a_skill/SKILL.md", "skills/cel_a_skill/meta.json"],
    }
    assert "3 skills" in push.call_args.args[1]

    # unchanged set: commit_and_push reports nothing to commit → no tag
    with patch.object(GitHubGitWrapper, "__enter__", fake_enter), \
         patch.object(GitHubGitWrapper, "__exit__", lambda self, *a: None), \
         patch.object(GitHubGitWrapper, "_run_git", return_value=(0, "", "")), \
         patch.object(GitHubGitWrapper, "clone", return_value={"success": True}), \
         patch.object(GitHubGitWrapper, "commit_and_push", return_value={"success": True, "message": "No changes to commit", "commit_hash": None}), \
         patch.object(GitHubGitWrapper, "create_tag") as tag2:
        result2 = sync_skills_to_github_real("https://github.com/example/tenant", root, "_default")
    assert result2["success"] and result2["changed"] is False and result2["tag"] is None
    tag2.assert_not_called()


def test_repo_sync_reads_the_tenants_skill_forge_dir(tmp_path, monkeypatch):
    from core.console.corvin_console.routes import github_repo_sync as rs

    tenant = tmp_path / "tenants" / "_default"
    _forge(tenant)
    (tenant / "skills").mkdir()
    (tenant / "skills" / "registry.yaml").write_text("plugins: []\n")  # config, not a skill
    (tenant / "github-config.json").write_text(json.dumps({"url": "https://github.com/example/tenant", "auto_sync": True}))
    monkeypatch.setattr(rs, "_tenant_path", lambda tid: tenant, raising=False)
    monkeypatch.setattr("core.console.corvin_console.routes.github_sync._tenant_path", lambda tid: tenant)

    seen: dict = {}

    def fake_real(*, repo_url, skills_dir, tenant_id):
        seen["skills_dir"] = skills_dir
        return {"success": True, "files_written": [], "branch": "main", "commit": "x", "tag": None, "skills_synced": 3}

    monkeypatch.setattr("core.console.corvin_console.routes.github_git_wrapper.sync_skills_to_github_real", fake_real)
    sync = rs.GitHubRepoSync("_default")
    monkeypatch.setattr(sync, "_log_audit", lambda *a, **k: None)
    monkeypatch.setattr(sync, "_save_sync_state", lambda r: None)
    assert sync.sync_skills_to_github()["success"]
    assert seen["skills_dir"] == tenant / "skill-forge" / "skills"
