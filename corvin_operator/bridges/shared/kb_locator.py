"""Which knowledge base an agent session of a tenant writes to (ADR-2205 / ADR-2265).

Stdlib only, import-free of the console: the gate skills (``adr_gate``, ``concept_gate``) are
rendered with the result (``render``), so an agent is told the EXACT absolute ``kb.py`` path the
path gate pins (``path_gate._session_kb_pins``) instead of the maintainers' hard-coded
sibling ``Corvin-Knowledge`` checkout path.

Resolution mirrors ``corvin_console.kb_projection.kb_repo`` (the board's binding):

* the operator's KB binds to ONE tenant, ``CORVIN_KB_TENANT`` (default ``_default``):
  ``CORVIN_KB_REPO`` if set (an explicit KB is never replaced), else the sibling
  ``Corvin-Knowledge`` checkout when this process runs on the install's canonical home;
* every tenant otherwise has its own ``<CORVIN_HOME>/tenants/<tenant>/knowledge``, created by
  the Knowledge Graph plugin (a standard plugin, enabled at first boot).

A tenant never resolves another tenant's KB.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_TENANT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_HERE = Path(__file__).resolve()

PLACEHOLDERS = ("{{KB_PY}}", "{{KB_DIR}}", "{{PYTHON}}")


def _repo_root() -> Path | None:
    for parent in [_HERE, *_HERE.parents]:
        if (parent / ".corvin_repo").exists():
            return parent
    return None


def corvin_home() -> Path:
    """Same order as ``bridges/shared/paths.corvin_home``: CORVIN_HOME -> <checkout>/.corvin -> ~/.corvin."""
    env = (os.environ.get("CORVIN_HOME") or "").strip()
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    repo = _repo_root()
    return repo / ".corvin" if repo is not None else Path.home() / ".corvin"


def kb_tenant() -> str:
    return os.environ.get("CORVIN_KB_TENANT", "_default")


def session_tenant(tenant_id: str | None = None) -> str:
    t = (tenant_id or os.environ.get("CORVIN_TENANT_ID") or os.environ.get("CORVIN_ACS_TENANT_ID")
         or "_default").strip()
    if not _TENANT_RE.match(t) or ".." in t:
        raise ValueError(f"invalid tenant id {t!r}")
    return t


def _is_kb(p: Path) -> bool:
    return (p / "scripts" / "kb.py").is_file() and (p / "kb" / "_meta" / "sources.yaml").is_file()


def _canonical_home() -> bool:
    home = os.environ.get("CORVIN_HOME")
    if not home:
        return True
    try:
        h = Path(home).expanduser().resolve()
        cands = {(Path.home() / ".corvin").resolve()}
        repo = _repo_root()
        if repo is not None:
            cands.add((repo / ".corvin").resolve())
        return h in cands
    except OSError:
        return False


def tenant_kb_path(tenant_id: str | None = None) -> Path:
    return corvin_home() / "tenants" / session_tenant(tenant_id) / "knowledge"


def resolve(tenant_id: str | None = None) -> Path:
    """The KB directory of *tenant_id* (default: this session's tenant). Always a path; it may not
    exist yet (the board projector creates a tenant KB within seconds of the plugin's enable)."""
    t = session_tenant(tenant_id)
    if t == kb_tenant():
        env = (os.environ.get("CORVIN_KB_REPO") or "").strip()
        if env:
            return Path(env)
        repo = _repo_root()
        if repo is not None and _canonical_home():
            sib = repo.parent / "Corvin-Knowledge"
            if _is_kb(sib):
                return sib
    return tenant_kb_path(t)


def python_cmd() -> str:
    """How a Bash command names the interpreter: ``python`` on Windows (no python3 there), else
    ``python3``. Both match the path gate's pinned-tool pattern."""
    return "python" if sys.platform.startswith("win") else "python3"


def render(text: str, tenant_id: str | None = None) -> str:
    """Replace ``{{KB_PY}}`` / ``{{KB_DIR}}`` / ``{{PYTHON}}`` with this tenant's values. Forward
    slashes on every platform: Git-Bash eats unquoted backslashes, and the path gate accepts the
    ``C:/...`` spelling of the exact pinned path. Never raises: an unresolvable tenant leaves
    the text unchanged (the placeholders then say what is missing)."""
    if "{{" not in text:
        return text
    try:
        kb = resolve(tenant_id)
    except Exception:  # noqa: BLE001
        return text
    return (text.replace("{{KB_PY}}", (kb / "scripts" / "kb.py").as_posix())
                .replace("{{KB_DIR}}", kb.as_posix())
                .replace("{{PYTHON}}", python_cmd()))


__all__ = ["PLACEHOLDERS", "corvin_home", "kb_tenant", "python_cmd", "render", "resolve",
           "session_tenant", "tenant_kb_path"]
