"""Shared fixtures for the projects tests: a sandboxed CORVIN_HOME and a small fragment catalogue."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import yaml


class Sandbox:
    """CORVIN_HOME + a projects root inside a temp dir; restores the environment on close."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "corvin_home"
        self.root = self.tmp / "projects"
        self.root.mkdir()
        self._saved = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", *_HOME_KEYS)}
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.update(home_env_for(self.tmp))       # the temp dir is the own home (ADR-2321 Decision 10a, REVIEW-0062 N1)
        for t in ("_default", "acme"):
            (self.home / "tenants" / t).mkdir(parents=True)

    def close(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_root(self, tenant: str = "_default"):
        from core.projects import registry
        registry.set_projects_root(tenant, str(self.root))


def write_fragment(src: Path, fid: str, *, texts: dict[str, str] | None = None, **spec) -> None:
    """``texts``: template files to create (relative name -> content); everything else is fragment.yaml."""
    d = src / "fragments" / fid
    d.mkdir(parents=True, exist_ok=True)
    spec = {"id": fid, "version": 1, "title": fid.title(), **spec}
    for name, body in (texts or {}).items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")
    (d / "fragment.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


def write_template(src: Path, tid: str, features: list[str], offered: list[str] | None = None, **extra) -> None:
    (src / "templates").mkdir(parents=True, exist_ok=True)
    (src / "templates" / f"{tid}.yaml").write_text(yaml.safe_dump(
        {"id": tid, "version": 1, "title": tid, "features": features, "offered": offered or features, **extra},
        sort_keys=False), encoding="utf-8")


def small_catalogue(src: Path) -> Path:
    """core <- kb <- tasks ; core <- claude <- acs ; videos (suggests kb). Enough to exercise every rule."""
    write_fragment(src, "core", texts={"files/readme.md": "# {{project.title}}\n"},
                   **{"files": [{"src": "files/readme.md", "dst": "README.md", "kind": "seed"}],
                      "claude": "files/claude.md", "actions": ["git_init"]})
    (src / "fragments" / "core" / "files" / "claude.md").write_text("## Core\nProject {{project.id}}.\n", encoding="utf-8")
    write_fragment(src, "kb", requires=["core"], requires_plugins=["corvin_knowledge"],
                   **{"files": [], "kb": {"initiative": "{{project.title}}", "epic": "Setup"},
                      "seed_tasks": [{"title": "Capture the goal", "dod": "an idea exists"}],
                      "claude": "files/claude.md"}, texts={"files/claude.md": "## Knowledge base\nUse docs/.\n"})
    write_fragment(src, "tasks", requires=["kb"], suggests=["gates"])
    write_fragment(src, "gates", requires=["kb"])
    write_fragment(src, "claude", requires=["core"], texts={"files/x.md": "x"},
                   **{"files": [{"src": "files/x.md", "dst": "notes/x.md", "kind": "seed"}]})
    write_fragment(src, "acs", requires=["claude"], gitignore=[".corvin/forge/"],
                   mcp={"corvin_orchestration": {"command": "{{paths.python}}", "args": ["-m", "json.tool"],
                                                 "env": {"CORVIN_HOME": "{{paths.corvin_home}}"}}})
    write_fragment(src, "videos", requires=["core"], suggests=["kb"],
                   texts={"files/i.md": "# Videos of {{project.id}}\n"},
                   **{"files": [{"src": "files/i.md", "dst": "videos/INDEX.md", "kind": "seed"}]})
    write_template(src, "demo", ["core", "kb", "tasks", "videos"],
                   ["core", "kb", "tasks", "gates", "claude", "acs", "videos"])
    return src


def ctx_for(pid: str, root: Path, tenant: str = "_default") -> dict[str, str]:
    return {"project.id": pid, "project.title": pid.title(), "project.tenant": tenant,
            "paths.python": "/usr/bin/python3", "paths.repo": "/opt/corvinos", "paths.corvin_home": "/opt/corvin_home",
            "paths.docs": str(root / "docs"), "paths.kb_engine": "/opt/knowledge/scripts/kb.py"}


_HOME_KEYS = ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME")


def home_env_for(tmp: Path) -> dict[str, str]:
    """ADR-2321 Decision 10a / REVIEW-0062 N1: the test's temp dir is the OWN home, so the location rule judges a projects root
    under it like any folder in a home (no test-only mode in the rule). On every platform ``XDG_DATA_HOME``; on Windows also the
    profile and the two AppData variables."""
    tmp = Path(tmp)
    env = {"HOME": str(tmp), "XDG_DATA_HOME": str(tmp / "data")}
    if os.name == "nt":
        env.update(USERPROFILE=str(tmp), LOCALAPPDATA=str(tmp / "AppData" / "Local"),
                   APPDATA=str(tmp / "AppData" / "Roaming"))
    return env


class _HomeEnv:
    def __init__(self, tmp: Path):
        self.env = home_env_for(tmp)
        self.saved: dict[str, str | None] = {}

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in _HOME_KEYS}
        os.environ.update(self.env)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return False


def sandbox_home_env(tmp: Path) -> _HomeEnv:
    """Context manager: point HOME (USERPROFILE, LOCALAPPDATA, APPDATA on Windows) and XDG_DATA_HOME into *tmp*; restored on exit.
    Every test that builds its own projects sandbox with ``tempfile.mkdtemp()`` instead of :class:`Sandbox` uses it."""
    return _HomeEnv(tmp)
