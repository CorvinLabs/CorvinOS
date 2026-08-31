"""ADR-0156 M3 — Custom Layer Loader (Tier-A prompt injection + skill registration).

Wires active Tier-A custom layers into the adapter's prompt pipeline and
SkillForge session workspace.

Public API
==========
Two functions are called from ``_resolve_spawn_inputs`` in ``adapter.py``:

``load_tier_a_prompts(tenant_id) -> list[tuple[str, str]]``
    Returns a list of ``(content, position)`` pairs for every active
    Tier-A layer that has a ``system_prompt.md``.  Position is one of
    ``"after_persona"`` (default) or ``"last"``.

``load_tier_a_skills(tenant_id, channel_id) -> None``
    Copies each ``skills/*.md`` from active Tier-A layers into the
    SkillForge **session** workspace so they appear on the next adapter
    turn exactly like adapter-created skills.

Design contract (ADR-0156)
==========================
*  Fail-open: every function wraps its body in ``try/except Exception``
   so a broken custom layer NEVER crashes the adapter.
*  position ``"before_persona"`` is structurally forbidden (EU AI Act
   Art. 50 — disclosure card must precede any vendor content).
*  Skills are registered as ``scope="session"`` so they disappear when
   the session is reset (``/new`` / ``/clear`` / ``/reset``).
*  If SkillForge is not importable, ``load_tier_a_skills`` logs a WARNING
   and returns silently.
*  ``import anthropic`` is forbidden — CI AST lint enforces this.
*  Audit events are best-effort; any audit failure is swallowed.

Allowed audit detail keys: ``layer_name``, ``tenant_id``, ``channel``,
``reason``, ``count``.  NEVER manifest contents, skill bodies, or
system-prompt text.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("corvin.custom_layer_loader")

# ── constants ──────────────────────────────────────────────────────────────────

# Maximum bytes read from a single system_prompt.md (fail-safe — prevents
# a giant layer from blowing up the system prompt).
_PROMPT_CAP_BYTES = 64 * 1024  # 64 KiB

# Maximum bytes read from a single skill SKILL.md body (matches skill_inject cap).
_SKILL_BODY_CAP_BYTES = 4 * 1024  # 4 KiB

# Wrapper tags for custom-layer prompt blocks.
_BLOCK_OPEN = "<custom_layer name=\"{name}\">"
_BLOCK_CLOSE = "</custom_layer>"

# Default position when layer.corvin.yaml omits prompt.position.
_DEFAULT_POSITION = "after_persona"

# Valid positions — "before_persona" is structurally forbidden.
_VALID_POSITIONS = frozenset({"after_persona", "last"})

# Skill name sanitisation: only alphanumeric + '.' + '_' (matches registry).
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._]{0,127}$")


# ── path helpers ───────────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    try:
        from .paths import corvin_home as _ch  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import corvin_home as _ch  # type: ignore
    return _ch()


def _tenant_home(tenant_id: str | None) -> Path:
    try:
        from .paths import tenant_home as _th  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import tenant_home as _th  # type: ignore
    return _th(tenant_id)


# ── audit helper ───────────────────────────────────────────────────────────────

def _audit(event: str, *, layer_name: str = "", tenant_id: str = "",
           channel: str = "", reason: str = "", count: int | None = None) -> None:
    """Best-effort audit event on the L16 hash chain."""
    details: dict[str, Any] = {}
    if layer_name:
        details["layer_name"] = layer_name
    if reason:
        details["reason"] = reason
    if count is not None:
        details["count"] = count
    try:
        try:
            from . import audit as _a  # type: ignore
        except ImportError:
            import audit as _a  # type: ignore
        _a.audit_event(
            event,
            channel=channel,
            tenant_id=tenant_id,
            details=details,
        )
    except Exception:  # pragma: no cover — best-effort
        pass


# ── manifest reader ────────────────────────────────────────────────────────────

def _read_manifest(layer_root: Path) -> dict[str, Any]:
    """Return the parsed layer.corvin.yaml for *layer_root*, or {}."""
    manifest_path = layer_root / "layer.corvin.yaml"
    if not manifest_path.is_file():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log.warning("custom_layer_loader: cannot parse %s: %s", manifest_path, exc)
        return {}


def _prompt_position(manifest: dict[str, Any]) -> str:
    """Extract prompt.position from a manifest; default to 'after_persona'."""
    prompt_cfg = manifest.get("prompt")
    if isinstance(prompt_cfg, dict):
        pos = prompt_cfg.get("position", _DEFAULT_POSITION)
        if pos in _VALID_POSITIONS:
            return str(pos)
        # Gracefully fall through rather than crash — validator already
        # blocked invalid values at install time; this guard covers
        # hand-edited manifests or unit-test fixtures.
        log.warning(
            "custom_layer_loader: invalid prompt.position %r — using 'after_persona'", pos
        )
    return _DEFAULT_POSITION


# ── active Tier-A layer iterator ───────────────────────────────────────────────

def _active_tier_a_layers(
    tenant_id: str | None,
) -> list[tuple[str, Path]]:
    """Return ``[(name, root_path), ...]`` for every active Tier-A layer.

    Uses ``custom_layer_registry.load_registry`` (fail-open).  Skips any
    layer whose on-disk root is missing (defensive against partial installs).
    """
    try:
        try:
            from .custom_layer_registry import load_registry  # type: ignore
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from custom_layer_registry import load_registry  # type: ignore
        registry = load_registry(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("custom_layer_loader: registry load failed: %s", exc)
        return []

    result: list[tuple[str, Path]] = []
    for name, rec in registry.items():
        if not rec.active:
            continue
        if rec.tier != "A":
            continue
        if rec.root is None or not rec.root.is_dir():
            log.warning(
                "custom_layer_loader: Tier-A layer '%s' has no on-disk root — skipping",
                name,
            )
            continue
        result.append((name, rec.root))
    return result


# ── public: prompt injection ────────────────────────────────────────────────────

def load_tier_a_prompts(
    tenant_id: str | None = None,
) -> list[tuple[str, str]]:
    """Load system-prompt content for all active Tier-A layers.

    Returns
    -------
    list of ``(content, position)`` tuples, one per layer that has a
    ``system_prompt.md``.  The caller is responsible for inserting each
    block into the system prompt at the right position.

    Order: layers appear in the order ``load_registry`` returns them
    (currently insertion order of ``custom_layers.json``).

    On any per-layer failure the layer is skipped with a WARNING log; the
    adapter is never blocked.
    """
    effective_tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    results: list[tuple[str, str]] = []

    try:
        layers = _active_tier_a_layers(effective_tid)
    except Exception as exc:  # noqa: BLE001 — outer fail-open guard
        log.warning("custom_layer_loader: load_tier_a_prompts failed: %s", exc)
        return []

    for name, root in layers:
        try:
            prompt_path = root / "system_prompt.md"
            if not prompt_path.is_file():
                # Tier-A layers are not required to have a system_prompt.md
                # (they might be skill-only layers).
                continue

            raw = prompt_path.read_bytes()
            if len(raw) > _PROMPT_CAP_BYTES:
                log.warning(
                    "custom_layer_loader: '%s' system_prompt.md exceeds %d bytes "
                    "— truncating",
                    name, _PROMPT_CAP_BYTES,
                )
                raw = raw[:_PROMPT_CAP_BYTES]

            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            manifest = _read_manifest(root)
            position = _prompt_position(manifest)

            # Wrap in XML framing so the LLM knows which layer contributed
            # the block and the adapter can inspect/strip it in future.
            block = (
                _BLOCK_OPEN.format(name=name)
                + "\n"
                + text
                + "\n"
                + _BLOCK_CLOSE
            )
            results.append((block, position))

            _audit(
                "custom_layer.prompt_injected",
                layer_name=name,
                tenant_id=effective_tid,
                reason="tier_a_inject",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "custom_layer_loader: skipping '%s' prompt inject: %s", name, exc
            )

    return results


# ── public: skill registration ─────────────────────────────────────────────────

def load_tier_a_skills(
    tenant_id: str | None = None,
    channel_id: str | None = None,
) -> None:
    """Copy Tier-A layer skills into the SkillForge session workspace.

    Each ``skills/*.md`` file in every active Tier-A layer is registered
    as a ``session``-scoped skill via ``MultiSkillRegistry.create``.  This
    makes the skills available on the very next adapter turn (same
    mechanism as adapter-created skills) and they disappear automatically
    on ``/new`` / ``/clear`` / ``/reset``.

    Idempotent: if a skill of the same name is already in the session
    workspace it is overwritten (``overwrite=True``) so the layer can be
    upgraded without clearing the session.

    Parameters
    ----------
    tenant_id:
        Tenant identifier (defaults to ``CORVIN_TENANT_ID`` env or ``_default``).
    channel_id:
        ``"<channel>:<chat>"`` string passed to ``MultiSkillRegistry`` as
        ``channel_id`` so session-scope resolves to the current chat.
        May be ``None`` for non-chat contexts (skills won't land in any
        session workspace but the function still returns cleanly).
    """
    effective_tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"

    # Locate the skill-forge package — mirrors skill_inject.py.
    _HERE = Path(__file__).resolve().parent
    _SKILL_FORGE_TOP = _HERE.parent.parent / "skill-forge"
    if _SKILL_FORGE_TOP.is_dir() and str(_SKILL_FORGE_TOP) not in sys.path:
        sys.path.insert(0, str(_SKILL_FORGE_TOP))

    try:
        from skill_forge.multi_registry import MultiSkillRegistry  # type: ignore
    except ImportError:
        log.warning(
            "custom_layer_loader: skill-forge not available — "
            "Tier-A skills will not be registered"
        )
        return

    try:
        layers = _active_tier_a_layers(effective_tid)
    except Exception as exc:  # noqa: BLE001
        log.warning("custom_layer_loader: load_tier_a_skills layer fetch failed: %s", exc)
        return

    if not layers:
        return

    # Detect project_root for scope resolution (mirrors skill_inject._detect_project_root).
    _project_root: Path | None = None
    try:
        # Walk up from adapter.py location looking for a marker that
        # identifies the CorvinOS project root (same heuristic as paths.py).
        _candidate = _HERE
        for _ in range(6):
            if (_candidate / "operator").is_dir() or (_candidate / ".corvin").is_dir():
                _project_root = _candidate
                break
            _candidate = _candidate.parent
    except Exception:  # noqa: BLE001
        _project_root = None

    for name, root in layers:
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            continue

        skill_files = sorted(skills_dir.glob("*.md"))
        if not skill_files:
            continue

        registered = 0
        for skill_md_path in skill_files:
            try:
                skill_name = _sanitize_skill_name(name, skill_md_path.stem)
                body = _load_skill_body(skill_md_path)
                if not body:
                    continue

                # Build a MultiSkillRegistry scoped to the current session.
                reg = MultiSkillRegistry(
                    channel_id=channel_id,
                    project_root=_project_root,
                )
                reg.create(
                    scope="session",
                    name=skill_name,
                    type="system",
                    body_md=body,
                    description=f"Custom layer skill: {name}/{skill_md_path.stem}",
                    claim={"source": "custom_layer", "layer": name},
                    overwrite=True,
                    created_by="custom_layer_loader",
                )
                registered += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "custom_layer_loader: skipping skill '%s' from layer '%s': %s",
                    skill_md_path.name, name, exc,
                )

        if registered:
            _audit(
                "custom_layer.skills_registered",
                layer_name=name,
                tenant_id=effective_tid,
                channel=channel_id or "",
                reason="tier_a_skill_inject",
                count=registered,
            )


# ── helpers ────────────────────────────────────────────────────────────────────

def _sanitize_skill_name(layer_name: str, stem: str) -> str:
    """Derive a registry-safe skill name from a layer name + file stem.

    Format: ``<vendor>_<layer>__<stem>`` with non-alnum chars (except '.')
    replaced by ``_``.  The resulting name must pass SkillRegistry's check:
    alphanumeric + '.' + '_', max 128 chars.

    Examples
    --------
    ``("acme.search", "my-skill")`` → ``"acme_search__my_skill"``
    """
    safe_layer = re.sub(r"[^A-Za-z0-9._]", "_", layer_name)
    safe_stem = re.sub(r"[^A-Za-z0-9._]", "_", stem)
    combined = f"{safe_layer}__{safe_stem}"
    # Truncate to 128 chars preserving uniqueness (keep suffix).
    if len(combined) > 128:
        combined = combined[-128:]
    # Ensure it starts with alphanumeric.
    combined = re.sub(r"^[^A-Za-z0-9]+", "", combined) or "layer_skill"
    return combined


def _load_skill_body(path: Path) -> str:
    """Read a skill .md file, cap at _SKILL_BODY_CAP_BYTES, strip front-matter.

    Returns empty string if the file is unreadable or empty after stripping.
    """
    try:
        raw = path.read_bytes()
        if len(raw) > _SKILL_BODY_CAP_BYTES:
            log.warning(
                "custom_layer_loader: skill file '%s' exceeds %d bytes — truncating",
                path, _SKILL_BODY_CAP_BYTES,
            )
            raw = raw[:_SKILL_BODY_CAP_BYTES]
        text = raw.decode("utf-8", errors="replace")
        # Strip YAML front-matter (--- ... ---) identical to skill_inject logic.
        text = _strip_front_matter(text)
        return text.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("custom_layer_loader: cannot read skill file '%s': %s", path, exc)
        return ""


def _strip_front_matter(text: str) -> str:
    """Remove YAML front-matter block (``---\\n...\\n---``) from markdown text."""
    if not text.startswith("---"):
        return text
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text
