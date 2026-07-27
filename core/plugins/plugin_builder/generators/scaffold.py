"""Write generated artifacts + a code scaffold to disk (ADR-0253 Phase 4).

This is the ONE module in the Plugin-Builder that touches the filesystem or
imports ``corvin_plugins`` / ``ops.launcher.corvin.plugin_cmd``. Everything
upstream (interview, classifier, the doc generators) is pure. That split
matters for ADR-0244's load-bearing constraint restated for this tool: the
Plugin-Builder emits artifacts and never loads them — this module is where
"emit" happens, and it stops exactly there.

Two scaffold strategies, chosen by classification:

* **Reuse `corvin plugin new`** — for ``PluginKind.PROVIDER`` whose
  ``plugin_type`` already ships an official template
  (``corvin_plugins.surface_map.buildable_types()``). This calls the SAME
  code path ``corvin plugin new`` uses, in-process, so the Plugin-Builder
  never carries a second copy of the AST-based ``plugin_id`` rewriting logic
  that mechanism depends on for correctness.
* **Builder-owned templates** — for everything else (MCP-Server, Skill, Hook,
  a Provider type with no official template yet, Integration, Custom). These
  live in ``plugin_builder/templates/`` and use plain placeholder substitution
  because the Plugin-Builder owns their exact shape — no AST games needed for
  a template only this module writes.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..generators.adr import generate_adr_doc
from ..generators.architecture import generate_architecture_doc
from ..generators.idea import generate_idea_doc
from ..generators.plan import generate_build_plan_doc
from ..models import Classification, PluginIdea, PluginKind

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_SEP_RUN_RE = re.compile(r"[-_.]{2,}")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")


def slugify_plugin_id(name: str) -> str:
    """A ``KNOWN_PLUGIN_TYPES``/``plugin_cmd``-charset-safe id from free text.

    Guarantees the result matches both ``manifest._PLUGIN_ID_RE`` and
    ``plugin_cmd._ID_RE`` — no leading/trailing separator, no doubled
    separator, lower-case alnum runs only. Falls back to a fixed default
    rather than ever returning an id that would fail those checks, because
    this function's whole job is to make that failure impossible upstream.
    """
    base = _NON_SLUG_RE.sub("-", name.strip().lower()).strip("-")
    plugin_id = f"community.{base}" if base else "community.my-plugin"
    plugin_id = _SEP_RUN_RE.sub("-", plugin_id)
    plugin_id = plugin_id[:64].rstrip("-._")
    if not plugin_id or not _ID_RE.match(plugin_id):
        return "community.my-plugin"
    return plugin_id


def _dirname(plugin_id: str) -> str:
    return plugin_id.replace(".", "_").replace("-", "_")


def _class_name(plugin_id: str) -> str:
    parts = re.split(r"[._-]+", plugin_id)
    return "".join(p.capitalize() for p in parts if p) + "Plugin"


def _display_name(plugin_name: str) -> str:
    """The ``__DISPLAY_NAME__`` substitution value — free text from the
    interview, so this MUST be safe to splice into both a Python string
    literal (``display_name = "__DISPLAY_NAME__"``) and a triple-quoted
    docstring's first line. Plain substitution, not a real templating engine
    with contextual escaping, so quotes/backslashes/newlines are stripped
    rather than escaped: a stripped character can't terminate either context
    early, where an escaped one still could (e.g. a literal backslash right
    before the closing quote)."""
    cleaned = re.sub(r'[\r\n"\\]', "", plugin_name)
    cleaned = " ".join(cleaned.split())
    return cleaned or "Untitled Plugin"


@dataclass(frozen=True)
class ScaffoldResult:
    dest: Path
    plugin_id: str
    classification: Classification
    doc_files: tuple[Path, ...]
    scaffold_files: tuple[Path, ...]
    warnings: tuple[str, ...]


def _find_launcher_root() -> Path | None:
    """Locate ``ops/launcher`` by walking up from this file, source-tree only.

    Returns ``None`` (never raises) when it can't be found — a pure wheel
    install without ``ops`` on the resolvable path degrades to the
    Builder-owned generic template instead of crashing the interview.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "ops" / "launcher"
        if (candidate / "corvin" / "plugin_cmd.py").is_file():
            return candidate
    return None


def _scaffold_via_corvin_plugin_new(
    plugin_id: str, plugin_type: str, output_dir: Path
) -> tuple[Path | None, tuple[Path, ...], tuple[str, ...]]:
    """Reuse the real ``corvin plugin new`` code path. See module docstring."""
    launcher = _find_launcher_root()
    if launcher is None:
        return None, (), (
            "ops/launcher was not found relative to this install — falling "
            "back to the Plugin-Builder's own generic provider template.",
        )
    inserted = str(launcher) not in sys.path
    if inserted:
        sys.path.insert(0, str(launcher))
    try:
        import argparse
        import contextlib
        import io

        from corvin.plugin_cmd import cmd_new  # type: ignore[import-not-found]
    except ImportError as exc:
        return None, (), (
            f"corvin_plugins/plugin_cmd not importable ({type(exc).__name__}) "
            "— falling back to the Plugin-Builder's own generic provider "
            "template.",
        )
    finally:
        # The import above is cached in sys.modules once it succeeds, so the
        # path entry is only ever needed for this one call — leaving it in
        # place would permanently shadow any bare `import corvin` for the
        # rest of the process's life (see operator/-stdlib-shadow incident).
        if inserted:
            try:
                sys.path.remove(str(launcher))
            except ValueError:
                pass

    ns = argparse.Namespace(
        plugin_type=plugin_type, plugin_id=plugin_id, output=str(output_dir)
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_new(ns)
    dest = output_dir / _dirname(plugin_id)
    if rc != 0 or not dest.is_dir():
        return None, (), (
            f"`corvin plugin new {plugin_type} {plugin_id}` failed — falling "
            f"back to the generic template. Output was:\n{buf.getvalue()}",
        )
    files = tuple(sorted(dest.glob("*")))
    warnings: tuple[str, ...] = ()
    if "WARNING" in buf.getvalue():
        warnings = (buf.getvalue().strip(),)
    return dest, files, warnings


_KIND_TEMPLATE_FILE = {
    PluginKind.MCP_SERVER: "mcp_server_plugin.py",
    PluginKind.SKILL: "skill_plugin.md",
    PluginKind.HOOK: "hook_plugin.py",
}

#: Best-effort default ctx_handle per plugin_type, mirroring
#: corvin_plugins.surface_map.SURFACES — duplicated here (not imported) so the
#: generic-provider fallback still works when corvin_plugins is unavailable.
_CTX_HANDLE_GUESS: dict[str, str] = {
    "data_connector": "data_connector_registry",
    "stt_provider": "stt_registry",
    "router_backend": "router_registry",
    "summary_provider": "summary_registry",
    "notification_backend": "notification_registry",
    "recall_backend": "recall_registry",
    "audit_backend": "audit_registry",
    "user_backend": "user_registry",
    "compute_engine": "compute_registry",
    "worker_engine": "engine_factory",
    "bridge_channel": "channel_registry",
}


def _scaffold_via_builder_template(
    idea: PluginIdea, classification: Classification, plugin_id: str, output_dir: Path
) -> tuple[Path, tuple[Path, ...], tuple[str, ...]]:
    """Fill in one of ``plugin_builder/templates/`` with placeholder substitution."""
    dest = output_dir / _dirname(plugin_id)
    dest.mkdir(parents=True, exist_ok=False)

    kind = classification.kind
    template_name = _KIND_TEMPLATE_FILE.get(kind, "provider_plugin_generic.py")
    src = (_TEMPLATES_DIR / template_name).read_text(encoding="utf-8")

    plugin_type = classification.plugin_type or "data_connector"
    replacements = {
        "__PLUGIN_ID__": plugin_id,
        "__DISPLAY_NAME__": _display_name(idea.plugin_name),
        "__PLUGIN_TYPE__": plugin_type,
        "__CTX_HANDLE__": _CTX_HANDLE_GUESS.get(plugin_type, "data_connector_registry"),
        "__EXTENSION_POINT__": classification.extension_point or "engine.model_selection",
    }
    for token, value in replacements.items():
        src = src.replace(token, value)

    out_name = "plugin.md" if template_name.endswith(".md") else "plugin.py"
    out_path = dest / out_name
    out_path.write_text(src, encoding="utf-8")

    warnings: tuple[str, ...] = ()
    if kind not in _KIND_TEMPLATE_FILE and classification.plugin_type is None:
        warnings = (
            "No plugin_type was classified — the generic provider template "
            "was written with a placeholder `data_connector` type; correct "
            "it before relying on the manifest.",
        )
    return dest, (out_path,), warnings


def write_artifacts(
    idea: PluginIdea, classification: Classification, output_dir: Path
) -> ScaffoldResult:
    """Write Idea/Architecture/ADR/Plan docs + a code scaffold under ``output_dir``.

    Refuses to overwrite an existing destination (same discipline as
    ``corvin plugin new``) — a second run for the same idea needs a fresh
    ``output_dir`` or a renamed idea, never a silent merge.
    """
    plugin_id = slugify_plugin_id(idea.plugin_name)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    dest: Path | None = None
    scaffold_files: tuple[Path, ...] = ()

    buildable = False
    if classification.kind == PluginKind.PROVIDER and classification.plugin_type:
        try:
            from corvin_plugins.surface_map import buildable_types

            buildable = classification.plugin_type in buildable_types()
        except ImportError:
            buildable = False

    if buildable:
        dest, scaffold_files, w = _scaffold_via_corvin_plugin_new(
            plugin_id, classification.plugin_type, output_dir  # type: ignore[arg-type]
        )
        warnings.extend(w)

    if dest is None:
        dest, scaffold_files, w = _scaffold_via_builder_template(
            idea, classification, plugin_id, output_dir
        )
        warnings.extend(w)

    docs_dir = dest / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_sources = {
        "plugin-idea.md": generate_idea_doc(idea, classification),
        "plugin-architecture.md": generate_architecture_doc(idea, classification),
        f"plugin-adr-{plugin_id.replace('.', '-')}.md": generate_adr_doc(
            idea, classification, plugin_id
        ),
        "build-plan.md": generate_build_plan_doc(idea, classification),
    }
    doc_files = []
    for name, content in doc_sources.items():
        path = docs_dir / name
        path.write_text(content, encoding="utf-8")
        doc_files.append(path)

    return ScaffoldResult(
        dest=dest,
        plugin_id=plugin_id,
        classification=classification,
        doc_files=tuple(doc_files),
        scaffold_files=scaffold_files,
        warnings=tuple(warnings),
    )


__all__ = ["ScaffoldResult", "slugify_plugin_id", "write_artifacts"]
