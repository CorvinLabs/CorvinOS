"""Plugin Forge orchestrator — describe a plugin, get a staged, reviewed scaffold (ADR-2217 D4).

The Skill-Creator phase contract on top of the existing Plugin-Builder:
the engine plans the idea (what `/plugin-builder`'s interview asks a human),
the Builder's own classifier and generators write docs + scaffold, an optional
console panel is generated in the ADR-2189 `panel/surface.yaml` shape, the
generated code is compiled (never executed in this process) and reviewed, and
the result is STAGED with a provenance record.

Staged means: not installed, not loaded, not in any plugin registry. ADR-0244
("the Builder emits artifacts, it never loads them") and ADR-2186/2188
(generated plugins are community code a human installs) bind this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import py_compile
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import artifact_review as ar

logger = logging.getLogger(__name__)

PLUGIN_PHASES = ("planning", "classification", "generation", "checks", "review", "staging")
PROVENANCE_FILE = "FORGE_PROVENANCE.json"
#: ADR-0701 vocabulary: a Builder-generated plugin carries ``provenance.generator:
#: plugin_builder``; ``surface`` says which Builder entry point produced it.
PROVENANCE_GENERATOR = "plugin_builder"
PROVENANCE_SURFACE = "plugin_forge"
PANEL_DIR = "panel"
MAX_PANEL_BYTES = 256 * 1024

_EXTERNAL_SCRIPT = re.compile(r"<script[^>]*\bsrc\s*=\s*[\"']?\s*(?:https?:)?//", re.IGNORECASE)
_BASE_TAG = re.compile(r"<base\b", re.IGNORECASE)
_HTML_FENCE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


class PluginCreatorError(Exception):
    """A plugin run failed; ``str()`` is shown to the operator."""


_PLAN_PROMPT = """You are planning a CorvinOS plugin for this request:

{request}

Reply with ONE JSON object and nothing else:
{{"plugin_name": "<short display name>",
  "problem": "<what problem it solves, 1-3 sentences>",
  "target_audience": "<who uses it>",
  "existing_solutions": "<what exists today, or empty>",
  "time_scope": "<MVP scope in one phrase>",
  "external_libraries": ["<pip package>", ...],
  "requires_auth": false,
  "requires_network_egress": false,
  "egress_hosts": ["<host>", ...],
  "platform_constraints": "<or empty>",
  "scope_notes": "<what is explicitly out of scope>"}}
Only list hosts and libraries the plugin truly needs."""

_PANEL_PROMPT = """Write the console panel for this CorvinOS plugin.

Plugin: {name}
Purpose: {problem}
Panel request: {panel_request}

Rules: ONE self-contained HTML document (inline <style> and <script> only), no external
scripts, fonts or images, no <base> tag, no network calls, no cookies or storage access.
It runs in a sandboxed iframe with only "allow-scripts". Keep it under 200 lines.
Reply with the HTML document only, inside one ```html fenced block."""


def _str(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _tuple(value: Any, limit: int = 20) -> tuple:
    if not isinstance(value, list):
        return ()
    return tuple(_str(v, 200) for v in value if _str(v, 200))[:limit]


def lint_panel(html: str) -> List[str]:
    problems: List[str] = []
    if not html.strip():
        problems.append("panel HTML is empty")
    if len(html.encode("utf-8")) > MAX_PANEL_BYTES:
        problems.append(f"panel HTML exceeds {MAX_PANEL_BYTES // 1024} KiB")
    if _EXTERNAL_SCRIPT.search(html):
        problems.append("panel loads an external script")
    if _BASE_TAG.search(html):
        problems.append("panel sets a <base> tag")
    return problems


def _extract_html(reply: str) -> str:
    match = _HTML_FENCE.search(reply or "")
    return (match.group(1) if match else (reply or "")).strip()


def _surface_yaml(plugin_id: str, title: str) -> str:
    # ADR-2189 shape: the plugin names its entry; it declares NO sandbox tokens —
    # those are derived from the trust verdict at mount time (ADR-2189 D2).
    # JSON is valid YAML, so writing and reading it needs no YAML dependency.
    return json.dumps({
        "kind": "web_surface",
        "id": plugin_id.replace(".", "-"),
        "title": title,
        "entry": "index.html",
    }, indent=2) + "\n"


class PluginCreatorOrchestrator:
    """Six-phase plugin generation for one tenant."""

    def __init__(self, *, tenant_id: str,
                 progress_cb: Optional[Callable[[str, int, str], None]] = None,
                 client: Any = None, output_root: Optional[Path] = None):
        self.tenant_id = tenant_id
        self.progress_cb = progress_cb
        self.client = client if client is not None else ar.resolve_engine()
        self.engine_id = ar.engine_id(self.client)
        self._output_root = output_root

    def _progress(self, phase: str, pct: int, message: str) -> None:
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(phase, pct, message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("progress callback failed: %s", exc)

    def output_root(self) -> Path:
        if self._output_root is not None:
            return Path(self._output_root)
        from plugin_builder.turn import output_dir  # noqa: PLC0415 — the /plugin-builder dir
        return Path(output_dir(self.tenant_id))

    # -- phase 1 ------------------------------------------------------------

    def _idea_from_plan(self, data: Dict[str, Any], request: str):
        from plugin_builder.models import Constraints, DependencySpec, PluginIdea, ProblemStatement  # noqa: PLC0415
        hosts = _tuple(data.get("egress_hosts"))
        return PluginIdea(
            plugin_name=_str(data.get("plugin_name"), 60) or "Forged Plugin",
            problem=ProblemStatement(
                problem=_str(data.get("problem")) or request,
                target_audience=_str(data.get("target_audience"), 300),
                existing_solutions=_str(data.get("existing_solutions"), 500),
                time_scope=_str(data.get("time_scope"), 200) or "MVP",
            ),
            dependencies=DependencySpec(
                external_libraries=_tuple(data.get("external_libraries")),
                requires_auth=bool(data.get("requires_auth")),
                requires_network_egress=bool(data.get("requires_network_egress")) or bool(hosts),
                egress_hosts=hosts,
            ),
            constraints=Constraints(
                platform_constraints=_str(data.get("platform_constraints"), 500),
                mvp_only=True,
                scope_notes=_str(data.get("scope_notes"), 1000),
            ),
            raw_answers={"forge_request": request},
        )

    def _idea_deterministic(self, request: str):
        """The Builder's own engine-less extraction — what `/plugin-builder` runs today."""
        from plugin_builder.classifier import extract_dependency_hints, problem_statement_from_idea_text  # noqa: PLC0415
        from plugin_builder.models import Constraints, PluginIdea  # noqa: PLC0415
        deps, _resolved = extract_dependency_hints(request)
        words = re.findall(r"[A-Za-z0-9]+", request)[:4]
        return PluginIdea(
            plugin_name=" ".join(words) or "Forged Plugin",
            problem=problem_statement_from_idea_text(request),
            dependencies=deps,
            constraints=Constraints(),
            raw_answers={"forge_request": request},
        )

    # -- phase 4 ------------------------------------------------------------

    @staticmethod
    def compile_check(dest: Path) -> List[str]:
        """Byte-compile every generated .py — compiled, never imported or executed."""
        problems: List[str] = []
        for path in sorted(dest.rglob("*.py")):
            try:
                py_compile.compile(str(path), cfile=str(path) + ".forgecheck", doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"{path.relative_to(dest)}: {str(exc.msg).strip()[:300]}")
            finally:
                Path(str(path) + ".forgecheck").unlink(missing_ok=True)
        return problems

    # -- the run ------------------------------------------------------------

    async def create_plugin(self, user_request: str, *, panel_request: str = "") -> Dict[str, Any]:
        from plugin_builder.classifier import classify  # noqa: PLC0415
        from plugin_builder.generators.scaffold import write_artifacts  # noqa: PLC0415

        self._progress("planning", 10, f"Planning the plugin via {self.engine_id}…")
        if self.client is not None:
            idea = self._idea_from_plan(await asyncio.to_thread(
                ar.ask_json, self.client, _PLAN_PROMPT.format(request=user_request)), user_request)
        else:
            idea = self._idea_deterministic(user_request)

        self._progress("classification", 25, f"Classifying '{idea.plugin_name}'…")
        classification = classify(idea)

        self._progress("generation", 40, "Writing docs and scaffold…")
        from plugin_builder.generators.scaffold import _dirname, slugify_plugin_id  # noqa: PLC0415
        output = self.output_root()
        dest = output / _dirname(slugify_plugin_id(idea.plugin_name))
        if dest.exists():
            raise PluginCreatorError(
                f"A plugin directory '{dest.name}' already exists (from Plugin Forge or the chat "
                "Plugin Builder). Delete it or describe the plugin under another name.")
        try:
            result = await asyncio.to_thread(write_artifacts, idea, classification, output)
            if Path(result.dest).resolve() != dest.resolve():
                raise PluginCreatorError("the Plugin Builder wrote somewhere unexpected")
            return await self._finish(dest, result, idea, classification, user_request, panel_request)
        except Exception:
            # Nothing half-written may stay behind: a directory without provenance
            # would be unmanageable from Marketplace → Forged and block the name.
            shutil.rmtree(dest, ignore_errors=True)
            raise

    async def _finish(self, dest: Path, result: Any, idea: Any, classification: Any,
                      user_request: str, panel_request: str) -> Dict[str, Any]:
        try:
            from plugin_builder.generators.e2e_tests import generate_e2e_tests  # noqa: PLC0415
            if result.scaffold_files:
                generate_e2e_tests(classification, result.plugin_id,
                                   Path(result.scaffold_files[0]), dest)
        except Exception as exc:  # noqa: BLE001 — tests are additive; their absence is reported
            logger.warning("plugin forge: generated tests skipped: %s", exc)

        panel: Optional[Dict[str, Any]] = None
        problems: List[str] = []
        if panel_request.strip():
            if self.client is None:
                raise PluginCreatorError(
                    "A panel was requested, but no engine is available to write it.")
            self._progress("generation", 50, "Writing the console panel…")
            html = _extract_html(await asyncio.to_thread(
                ar.ask, self.client,
                _PANEL_PROMPT.format(name=idea.plugin_name, problem=idea.problem.problem,
                                     panel_request=panel_request), max_tokens=8000))
            problems.extend(f"panel: {p}" for p in lint_panel(html))
            if not problems:
                panel_dir = dest / PANEL_DIR
                panel_dir.mkdir(exist_ok=True)
                (panel_dir / "index.html").write_text(html, encoding="utf-8")
                (panel_dir / "surface.yaml").write_text(
                    _surface_yaml(result.plugin_id, idea.plugin_name), encoding="utf-8")
                panel = {"title": idea.plugin_name, "entry": f"{PANEL_DIR}/index.html"}

        self._progress("checks", 60, "Compiling the generated code…")
        problems.extend(await asyncio.to_thread(self.compile_check, dest))
        if problems:
            raise PluginCreatorError("Generated plugin failed its checks: " + "; ".join(problems))

        self._progress("review", 75, "Adversarial review (correctness, security, scope)…")
        artifact = "\n\n".join(
            f"### {p.relative_to(dest)}\n{p.read_text(encoding='utf-8', errors='replace')}"
            for p in sorted(dest.rglob("*")) if p.is_file() and p.suffix in {".py", ".md", ".html", ".yaml"}
        )
        findings = await ar.review(self.client, kind="plugin", name=result.plugin_id,
                                   purpose=idea.problem.problem, artifact=artifact)
        # No engine, no review: a 1.0 score for a review that never ran would be fabricated.
        score = (ar.quality(findings, converged=True, dimensions=len(ar.DIMENSIONS))
                 if self.client is not None else None)

        self._progress("staging", 90, f"Staging '{result.plugin_id}' (not installed)…")
        provenance = {
            "generator": PROVENANCE_GENERATOR,
            "surface": PROVENANCE_SURFACE,
            "engine": self.engine_id,
            "review_skipped": self.client is None,
            "created_at": time.time(),
            "plugin_id": result.plugin_id,
            "display_name": idea.plugin_name,
            # The request is free text that may name people: only a fingerprint is kept
            # (the generated docs restate the problem, as the chat Builder's always have).
            "request_sha256": hashlib.sha256(user_request.encode("utf-8")).hexdigest(),
            "request_chars": len(user_request),
            "kind": classification.kind.value,
            "tier": classification.tier.value,
            "plugin_type": classification.plugin_type,
            "risk_flags": list(classification.risk_flags),
            "egress_hosts": list(idea.dependencies.egress_hosts),
            "quality": score,
            "findings": ar.findings_out(findings),
            "panel": panel,
            "warnings": list(result.warnings),
            "installed": False,
            "origin_on_install": "community",
        }
        (dest / PROVENANCE_FILE).write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        return {
            "plugin_id": result.plugin_id,
            "dirname": dest.name,
            "display_name": idea.plugin_name,
            "kind": classification.kind.value,
            "tier": classification.tier.value,
            "quality": score,
            "findings": provenance["findings"],
            "files": sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()),
            "panel": panel,
            "review_skipped": provenance["review_skipped"],
        }
