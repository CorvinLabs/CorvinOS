#!/usr/bin/env python3
"""resolver.py — load + resolve cowork personas.

Eine Persona ist ein JSON-File. Lookup-Reihenfolge:
  1. $COWORK_USER_DIR/personas/<name>.json   (env-override for Tests)
  2. ~/.config/claude-cowork/personas/<name>.json
  3. <plugin-bundle>/personas/<name>.json

User-Files overwrite Bundle-Files fully (kein Merge auf file-Ebene).
Im Gegensatz dazu merged `resolve(name, overrides)` Felder aus dem chat_profile
ins persona-dict — overrides gewinnen for Skalare, Listen werden zusammengeführt
(union, without Duplikate), Dicts werden flach gemerged (overrides-Keys gewinnen).

Public:
    load(name)               -> dict | None
    resolve(name, overrides) -> dict
    list_available()         -> list[dict]   # for /cowork-list
    materialize_mcp(persona) -> str | None   # writes mcp_servers nach Tempfile,
                                              # gibt path back (for --mcp-config)
    expand_dirs(persona)     -> list[str]    # add_dirs mit ~ expandiert + mkdir
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

BUNDLE_DIR = Path(__file__).resolve().parent.parent / "personas"
# REPO_ROOT = the directory holding operator/cowork/ and (optionally)
# operator/forge/, operator/voice/ alongside it. Used for {{REPO_ROOT}}
# substitution in mcp_servers.command/args/env values so personas can
# reference plugin executables without hard-coding absolute paths.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# Lazy import keeps module-load cheap and tolerant of test environments
# that pre-set COWORK_USER_DIR / COWORK_MCP_CACHE without paths.py being
# importable. paths.py sits next to resolver.py in operator/cowork/lib/.
def _cowork_default() -> Path:
    try:
        from paths import cowork_dir  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import cowork_dir  # type: ignore
    return cowork_dir()


USER_DIR = Path(
    os.environ.get("COWORK_USER_DIR")
    or str(_cowork_default())
)
USER_PERSONAS_DIR = USER_DIR / "personas"
MCP_CACHE_DIR = Path(
    os.environ.get("COWORK_MCP_CACHE")
    or str(_cowork_default() / "mcp-cache")
)

_LIST_FIELDS = ("allowed_tools", "disallowed_tools", "add_dirs",
                "needs_keys", "needs_oauth")
_DICT_FIELDS = ("mcp_servers",)


# ---------------------------------------------------------------------------
# Layer-14 LDD profile resolution
# ---------------------------------------------------------------------------
#
# A persona may carry three optional fields:
#   ldd_preset:  "default" | "strict" | "quick" | "off"  → expanded
#                via ldd.PRESETS into a layers dict
#   ldd_layers:  {layer_id: bool}                        → delta merged on top
#   ldd_enabled: bool                                    → master override
#
# The chat_profile (overrides) may carry the same three fields and ALWAYS
# wins where it is set: chat_profile beats persona, persona beats default
# (= no fields → empty section, behaves as the global cfg dictates). The
# bridge adapter passes the resulting profile dict to ldd.is_layer_active()
# unchanged, so the Cascade gate (Layer 14 v2) applies on top.
#
# Optional dep: ldd.py lives in the voice plugin's bridges/shared/ tree.
# We import it lazily so cowork keeps working when voice is uninstalled
# (mirrors the existing skill-forge / forge optional-import pattern).
# When ldd.py is absent the preset expansion is skipped and ldd_layers /
# ldd_enabled propagate verbatim.


def _ldd_module():
    """Lazy import of ldd.py. Returns None if voice plugin is missing."""
    cached = getattr(_ldd_module, "_cache", "unset")
    if cached != "unset":
        return cached
    mod = None
    candidate = REPO_ROOT / "operator" / "bridges" / "shared"
    if candidate.is_dir():
        try:
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            import ldd as _ldd  # type: ignore
            mod = _ldd
        except Exception:  # noqa: BLE001
            mod = None
    _ldd_module._cache = mod  # type: ignore[attr-defined]
    return mod


def _resolve_ldd_section(persona: dict, overrides: dict) -> dict:
    """Compute the merged ``ldd_*`` section for a chat profile.

    Two-source model — persona (preset + delta) versus chat-profile
    (explicit per-layer + master). The bridge's ``is_layer_active``
    treats every entry in ``profile.ldd_layers`` as an *explicit* per-layer
    override that beats the master kill (existing semantics). To keep
    chat-level master-off intuitive — "kill should actually kill" —
    the resolver drops persona-injected layer entries when the chat
    explicitly turns the master off, leaving only chat-explicit per-layer
    overrides in place.

    Resolution flow:
      1. Build ``persona_layers``  from ``ldd_preset`` (expanded via PRESETS)
         then shallow-merged with ``persona.ldd_layers`` (delta wins).
      2. Build ``chat_layers``     from ``overrides.ldd_layers`` only.
      3. Master flag: ``overrides.ldd_enabled`` > ``persona.ldd_enabled`` >
         preset-derived (default/strict/quick → True, off → False).
      4. If ``overrides.ldd_enabled is False`` → final layers = chat_layers
         only (persona-injected overrides are dropped — the chat-level
         kill survives).
         Else                                → final layers = persona_layers
         shallow-merged with chat_layers (chat wins per key).

    Returns a dict with at most ``ldd_layers`` (dict) and ``ldd_enabled``
    (bool) keys. An empty result means "no persona-level configuration"
    — the bridge falls back to global cfg + cascade on its own.
    """
    out: dict = {}
    ldd = _ldd_module()

    # ── Step 1: persona preset → layers dict, then persona delta. ────────
    persona_layers: dict = {}
    preset = persona.get("ldd_preset")
    persona_preset_master: bool | None = None
    if isinstance(preset, str) and ldd is not None and preset in ldd.PRESETS:
        persona_layers = dict(ldd.PRESETS[preset])
        persona_preset_master = (preset != "off")
    p_layers = persona.get("ldd_layers")
    if isinstance(p_layers, dict):
        for k, v in p_layers.items():
            if isinstance(v, bool):
                persona_layers[k] = v

    # ── Step 2: chat-profile explicit per-layer entries only. ────────────
    chat_layers: dict = {}
    o_layers = overrides.get("ldd_layers")
    if isinstance(o_layers, dict):
        for k, v in o_layers.items():
            if isinstance(v, bool):
                chat_layers[k] = v

    # ── Step 3: master flag resolution (chat > persona > preset). ────────
    p_enabled = persona.get("ldd_enabled")
    o_enabled = overrides.get("ldd_enabled")
    final_master: bool | None = None
    if isinstance(o_enabled, bool):
        final_master = o_enabled
    elif isinstance(p_enabled, bool):
        final_master = p_enabled
    elif persona_preset_master is not None:
        final_master = persona_preset_master

    if final_master is not None:
        out["ldd_enabled"] = final_master

    # ── Step 4: layer merge — chat-master-off drops persona injections. ──
    if o_enabled is False:
        merged_layers = dict(chat_layers)
    else:
        merged_layers = dict(persona_layers)
        merged_layers.update(chat_layers)
    if merged_layers:
        out["ldd_layers"] = merged_layers

    return out


# ---------------------------------------------------------------------------
# Persona aliases — historical names that resolve to a different persona
# file. Existing chat_profiles pinning the old name keep working without
# operator intervention.
#
# `skill-forge` was a phantom persona: the resolver's inject-skip check
# referenced it, but no `personas/skill-forge.json` ever existed. Layer-10's
# path-gate hook decoupled the safety story from persona-level isolation,
# so the unified generator persona is now `forge` (Tools AND Skills); the
# alias keeps `chat_profile.persona = "skill-forge"` valid.
# ---------------------------------------------------------------------------
_PERSONA_ALIASES: dict[str, str] = {
    "skill-forge": "forge",
}


# ---------------------------------------------------------------------------
# Capability briefs — appended to ``append_system`` when forge / skill-forge
# tools are injected. They explain the non-obvious mechanics (namespace
# prefix discipline, sandbox limits, linter behaviour, visibility lag) that
# Claude needs to know to use the generation tools correctly on the FIRST
# attempt instead of learning by trial-and-error from gate errors.
#
# Briefs are appended idempotently — re-resolving the same persona twice
# does not duplicate them. They only land when the corresponding capability
# is actually injected, so personas that opted out (or never had the flag)
# stay un-augmented.
# ---------------------------------------------------------------------------

_FORGE_BRIEF_TEMPLATE_NS = (
    "- Tool name MUST start with `{namespace}.` (e.g. `{namespace}.csv_diff`). "
    "Other prefixes are rejected by the namespace gate.\n"
)
_FORGE_BRIEF_TEMPLATE_WILDCARD = (
    "- Tool name is unrestricted for this persona — no namespace gate is "
    "configured. Pick a clear, dotted name (`<area>.<verb>`) anyway, e.g. "
    "`csv.diff`, `pdf.extract_text`.\n"
)
_FORGE_BRIEF_HEAD = "**Forge tool generation:**\n"
_FORGE_BRIEF_TAIL = (
    "- Output cap: 4 MiB stdout — larger results are preserved as artifacts; "
    "`meta.stdout_truncated` and `meta.stdout_full_artifact` carry the path "
    "for post-hoc retrieval.\n"
    "- Schema annotations: `x-bind: ro` for input file paths, "
    "`x-bind: rw` for output paths, `x-redact: true` for secret fields "
    "(redacted from the run manifest).\n"
    "- Discovery first: call `mcp__forge__forge_list` before creating a new "
    "tool to see what already exists in your scope — many useful artifacts "
    "may already be there.\n"
    "- A freshly forged tool becomes callable as `mcp__forge__<name>` "
    "in the **same turn** — the server emits a `tools/list_changed` "
    "notification right after registration."
)


def _bundle_policy_data() -> dict:
    """Read the bundle policy.json once. Returns {} on any read error.
    Cached on the function for the process lifetime — the bundle file
    changes rarely; tests that mutate it should reload by spawning a
    fresh subprocess (the standard pattern in this repo)."""
    cached = getattr(_bundle_policy_data, "_cache", None)
    if cached is not None:
        return cached
    bundle = REPO_ROOT / "operator" / "forge" / "forge" / "policy.json"
    try:
        data = json.loads(bundle.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    _bundle_policy_data._cache = data  # type: ignore[attr-defined]
    return data


def _persona_network_status(persona_name: str) -> str:
    """Return the user-facing description of this persona's sandbox network
    state. Reads the bundle policy.json's persona_sandbox_overrides; the
    workspace policy.json (runtime) can flip this further at run time, but
    the brief is built at resolve time so we use the bundle default."""
    overrides = _bundle_policy_data().get("persona_sandbox_overrides") or {}
    entry = overrides.get(persona_name)
    if isinstance(entry, dict) and entry.get("network") == "allow":
        return ("shares host network namespace — loopback + outbound HTTP/HTTPS "
                "with DNS + TLS available. No subprocess, fresh /tmp, ro /usr.")
    return ("no network, no subprocess, fresh /tmp, ro /usr. Prefer pure-Python "
            "implementations and stdlib modules.")


def _persona_has_namespace_gate(persona_name: str) -> bool:
    """True iff the persona has a registration prefix entry in policy."""
    ns = _bundle_policy_data().get("persona_namespaces") or {}
    return bool(ns.get(persona_name))


def _build_forge_brief(persona_name: str, namespace: str) -> str:
    """Compose the forge brief from the runtime sandbox + namespace state."""
    if _persona_has_namespace_gate(persona_name):
        head_ns = _FORGE_BRIEF_TEMPLATE_NS.format(namespace=namespace)
    else:
        head_ns = _FORGE_BRIEF_TEMPLATE_WILDCARD
    sandbox = _persona_network_status(persona_name)
    return (_FORGE_BRIEF_HEAD
            + head_ns
            + f"- Sandbox: {sandbox}\n"
            + _FORGE_BRIEF_TAIL)

_SKILL_FORGE_BRIEF = (
    "**Skill creation:**\n"
    "- Skills are markdown bodies prompt-injected into future subprocess "
    "turns. Keep them tight and instruction-shaped.\n"
    "- The linter rejects bodies containing prompt-injection patterns, "
    "embedded secrets, or persona-boundary phrases like "
    "\"ignore previous instructions\".\n"
    "- Promotion gates: task → session needs ≥ 1 positive grade; "
    "session → project needs ≥ 3 grades with mean ≥ 0.5; "
    "project → user needs explicit force.\n"
    "- Discovery first: call `mcp__skill_forge__skill_list` to see what "
    "already exists in your scope before creating a new skill.\n"
    "- A freshly created skill becomes visible on the next subprocess "
    "boot (next bridge turn). The bridge adapter then injects its body "
    "into the active prompt automatically when the skill has earned at "
    "least one positive grade."
)


def _ensure_brief(out: dict, brief: str) -> None:
    """Append *brief* to ``out['append_system']`` exactly once.

    Detection is substring-based on the brief's leading marker line
    (the bold header), so paraphrased duplicates still re-trigger but
    re-resolves of the same persona stay idempotent.
    """
    if not brief:
        return
    marker = brief.split("\n", 1)[0].strip()
    cur = (out.get("append_system") or "").strip()
    if marker and marker in cur:
        return
    parts = [cur, brief.strip()] if cur else [brief.strip()]
    out["append_system"] = "\n\n".join(parts)


def _candidate_paths(name: str) -> list[Path]:
    if not name or "/" in name or ".." in name or name.startswith("."):
        return []
    return [USER_PERSONAS_DIR / f"{name}.json", BUNDLE_DIR / f"{name}.json"]


def load(name: str) -> dict | None:
    """Lade Persona-File. None wenn nicht gefunden oder kaputtes JSON.

    Resolves persona aliases (see ``_PERSONA_ALIASES``) before lookup so
    historical names like ``skill-forge`` continue to find the unified
    ``forge`` file.
    """
    name = _PERSONA_ALIASES.get(name, name)
    for p in _candidate_paths(name):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data.setdefault("name", name)
            data["_source"] = str(p)
            return data
    return None


def _disabled_names() -> set:
    """Names deactivated via the console (per-tenant ``.disabled.json``).

    The console writes this hidden registry under the tenant cowork personas
    dir; reading it here is what makes "deactivate a persona" actually drop it
    from auto-routing / discovery at runtime, not just hide it in the UI. An
    explicit per-chat pin still resolves via :func:`load` (disabling means "don't
    offer it automatically", not "brick an active chat"). Missing/corrupt file →
    empty set (fail-open: a registry read error must never hide every persona).
    """
    try:
        raw = json.loads((USER_PERSONAS_DIR / ".disabled.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names = raw.get("disabled") if isinstance(raw, dict) else None
    return {str(n) for n in names} if isinstance(names, list) else set()


def list_available() -> list[dict]:
    """Alle aktiven Personas (Bundle + User), User shadows Bundle.

    Deactivated personas (console ``.disabled.json`` registry) are excluded so a
    persona the operator turned off is no longer offered by auto-routing or any
    "available personas" enumeration.
    """
    disabled = _disabled_names()
    seen: dict[str, dict] = {}
    for d in (BUNDLE_DIR, USER_PERSONAS_DIR):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            name = p.stem
            if name in disabled:
                continue  # deactivated → not offered at runtime
            if name in seen and d == BUNDLE_DIR:
                continue  # User wins
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            data.setdefault("name", name)
            data["_source"] = str(p)
            seen[name] = data
    return [seen[k] for k in sorted(seen)]


def resolve(name: str, overrides: dict | None = None) -> dict:
    """Persona laden + chat_profile-Overrides reinmergen.

    Skalare (permission_mode, append_system, model): overrides gewinnt.
    Listen (allowed_tools, ...): union, Reihenfolge persona zuerst.
    Dicts (mcp_servers): flach gemerged, overrides-Keys gewinnen.

    append_system speziell: persona.append_system + "\\n\\n" + overrides.append_system
    (beide mitnehmen, nicht overwrite — chat-spezifischer Hinweis complements
    die Persona-Rolle, ersetzt sie nicht).

    Alias resolution: persona name passes through ``_PERSONA_ALIASES`` before
    lookup so historical bindings (e.g. ``persona = "skill-forge"``) continue
    to resolve to the unified target.
    """
    name = _PERSONA_ALIASES.get(name, name)
    persona = load(name)
    overrides = overrides or {}
    if persona is None:
        # Persona nicht gefunden → liefer overrides unchanged back
        # (graceful — voice fällt auf legacy-Verhalten back).
        return dict(overrides)

    out: dict = {}

    # Skalare: overrides > persona, aber persona als Default.
    # inject_skills / inject_ungraded / max_injected_skills are read by
    # the bridge's skill_inject layer; persona may opt out of injection
    # (e.g. forge / skill-forge) and chat_profile may override either way.
    # forge_enabled / skill_forge_enabled / tool_namespace are layer-9
    # capability flags consumed by the bridge adapter (see
    # `bridges/shared/adapter.py::_build_claude_args`); they propagate
    # straight through so the adapter can read them off the resolved
    # profile without having to load the persona file again.
    # tts_voice / tts_voice_<lang> are read by the bridge adapter's
    # synthesize_voice_note() to pick a per-persona OpenAI TTS voice
    # (jarvis → onyx, etc.). chat_profile may override per chat — same
    # precedence as the other scalars: explicit overrides win, otherwise
    # the persona default carries through.
    # default_engine is the per-persona / per-chat engine_id picked by
    # the Phase-4 engine_registry (claude_code | codex_cli | future
    # gemini_cli / ollama / vllm). Adapter resolves it via
    # engine_registry.resolve_engine_id and hands the factory to AWP.
    for k in ("permission_mode", "model",
              "inject_skills", "inject_ungraded", "max_injected_skills",
              "forge_enabled", "skill_forge_enabled", "tool_namespace",
              "tts_voice", "tts_voice_de", "tts_voice_en",
              "default_engine", "awp_enabled",
              # Layer 29 — delegation capability flag.
              "delegate_enabled"):
        if k in overrides and overrides[k] is not None:
            out[k] = overrides[k]
        elif k in persona:
            out[k] = persona[k]

    # append_system: konkatenieren statt erset.
    parts = []
    pa = (persona.get("append_system") or "").strip()
    oa = (overrides.get("append_system") or "").strip()
    if pa:
        parts.append(pa)
    if oa:
        parts.append(oa)
    if parts:
        out["append_system"] = "\n\n".join(parts)

    # Listen: union (Reihenfolge erhalten).
    for k in _LIST_FIELDS:
        merged: list = []
        for src in (persona.get(k), overrides.get(k)):
            if isinstance(src, list):
                for item in src:
                    if item not in merged:
                        merged.append(item)
        if merged:
            out[k] = merged

    # Dicts: flach gemerged.
    for k in _DICT_FIELDS:
        merged_d: dict = {}
        for src in (persona.get(k), overrides.get(k)):
            if isinstance(src, dict):
                merged_d.update(src)
        if merged_d:
            out[k] = merged_d

    # Diagnose-Felthat throughreichen.
    out["_persona"] = persona.get("name", name)
    out["_persona_source"] = persona.get("_source")
    out = _inject_forge_capability(out, name)
    out = _inject_skill_forge_capability(out, name)
    out = _inject_delegate_capability(out, name)

    # Layer-14 LDD section: persona preset/delta + chat-profile overrides.
    # Result lives directly on the merged profile dict, where the bridge
    # adapter passes it to ldd.is_layer_active(profile=…) unchanged.
    ldd_section = _resolve_ldd_section(persona, overrides)
    if "ldd_layers" in ldd_section:
        out["ldd_layers"] = ldd_section["ldd_layers"]
    if "ldd_enabled" in ldd_section:
        out["ldd_enabled"] = ldd_section["ldd_enabled"]

    return out


def _inject_forge_capability(merged: dict, persona_name: str) -> dict:
    """Layer 9 — every persona with ``forge_enabled: true`` inherits forge
    tools (forge_tool, forge_promote). Idempotent. The forge persona itself
    is left untouched (it ships its own MCP config). Default without the
    flag: no injection — symmetrical to ``_inject_skill_forge_capability``.

    Pre-symmetry note: this gate used to also require ``zero_config: true``,
    which created a dead-flag bug — inbox.json carried ``forge_enabled: true``
    but never received forge tools because zero_config was false (Google
    OAuth requires manual setup). Striking the zero_config check fixes the
    inconsistency without changing behaviour for any persona that already
    had both flags. The path-gate hook (layer 10) keeps the sandbox boundary
    structural, so a non-zero_config persona gaining forge access is safe.

    Workspace root: forge plugin resolves it through ``forge.scope`` at
    runtime — CORVIN_CHANNEL_ID routes into per-channel session workspaces;
    CLI invocations fall back to git-detected project or user scope.
    FORGE_PERSONA remains as the persona-attribution tag for the audit log.
    """
    if not persona_name or persona_name == "forge":  return merged
    persona = load(persona_name)
    if not persona:                                  return merged
    if not persona.get("forge_enabled"):             return merged
    out = dict(merged)
    allowed = list(out.get("allowed_tools") or [])
    for t in ("mcp__forge__forge_tool", "mcp__forge__forge_promote",
              "mcp__forge__forge_list"):
        if t not in allowed:
            allowed.append(t)
    out["allowed_tools"] = allowed
    mcp = dict(out.get("mcp_servers") or {})
    if "forge" not in mcp:
        env = {
            "FORGE_PERSONA":       persona_name,
            "FORGE_ALLOWED_TOOLS": "{{ALLOWED_FORGED_TOOLS}}",
        }
        default_scope = persona.get("forge_default_scope")
        if default_scope:
            env["CORVIN_DEFAULT_SCOPE"] = default_scope
        mcp["forge"] = {
            # sys.executable, not literal "python3": Windows usually has no
            # ``python3`` on PATH, and this must be the interpreter that can
            # import forge (the venv/wheel Python running the bridge), not an
            # arbitrary bare-PATH one. Mirror of mcp_config_builder.py.
            "command": sys.executable,
            "args": ["{{REPO_ROOT}}/operator/forge/forge.py",
                     "mcp", "--permission-mode", "yes"],
            "env": env,
        }
    out["mcp_servers"] = mcp
    namespace = persona.get("tool_namespace") or persona_name
    _ensure_brief(out, _build_forge_brief(persona_name, namespace))
    return out


def _inject_skill_forge_capability(merged: dict, persona_name: str) -> dict:
    """Layer 9 — opt-in skill-forge MCP server materialization.

    Mirrors ``_inject_forge_capability`` for the skill-forge plugin. Every
    persona that explicitly declares ``skill_forge_enabled: true`` in its
    JSON gets the skill-forge MCP server wired into its mcp_servers + the
    seven skill_* tool patterns added to allowed_tools. The dedicated
    ``skill-forge`` persona is left untouched (it ships its own config).

    Backward-compat: personas without the flag (or with the flag set to
    false) remain unchanged, so today's forge-only zero_config personas
    aren't surprised with skill-forge access until the operator opts in
    via the persona JSON.
    """
    if not persona_name or persona_name == "skill-forge":
        return merged
    persona = load(persona_name)
    if not persona:
        return merged
    if not persona.get("skill_forge_enabled"):
        return merged
    out = dict(merged)
    allowed = list(out.get("allowed_tools") or [])
    for t in (
        "mcp__skill_forge__skill_create",
        "mcp__skill_forge__skill_promote",
        "mcp__skill_forge__skill_grade",
        "mcp__skill_forge__skill_list",
        "mcp__skill_forge__skill_get",
        "mcp__skill_forge__skill_purge",
        "mcp__skill_forge__skill_diff",
    ):
        if t not in allowed:
            allowed.append(t)
    out["allowed_tools"] = allowed
    mcp = dict(out.get("mcp_servers") or {})
    if "skill_forge" not in mcp:
        env: dict[str, str] = {
            "SKILL_FORGE_PERSONA": persona_name,
            # The skill-forge MCP server lives at
            # `<repo>/operator/skill-forge/skill_forge/mcp_server.py` and
            # imports `forge.policy` for the namespace gate. Both packages
            # need to be importable when claude spawns the subprocess, so
            # we inject the two plugin roots onto PYTHONPATH explicitly —
            # PATH-style paths use ":" separator on POSIX.
            "PYTHONPATH": ("{{REPO_ROOT}}/operator/skill-forge"
                           ":{{REPO_ROOT}}/operator/forge"),
        }
        mcp["skill_forge"] = {
            # sys.executable — see the forge branch above. Same interpreter
            # that can import skill_forge / forge.policy.
            "command": sys.executable,
            "args": ["-m", "skill_forge.mcp_server"],
            "env": env,
        }
    out["mcp_servers"] = mcp
    _ensure_brief(out, _SKILL_FORGE_BRIEF)
    return out


# ---------------------------------------------------------------------------
# Layer 29 — delegation capability (corvin-delegate plugin)
# ---------------------------------------------------------------------------


_DELEGATE_BRIEF = (
    "**Delegation (Layer 29):**\n"
    "- You are the OS process. Five worker engines are reachable as MCP "
    "tools: `mcp__corvin_delegate__delegate_claude_code`, "
    "`mcp__corvin_delegate__delegate_codex`, "
    "`mcp__corvin_delegate__delegate_opencode`, "
    "`mcp__corvin_delegate__delegate_hermes`, "
    "`mcp__corvin_delegate__delegate_copilot`.\n"
    "- Delegate when (a) clean-context reasoning is needed, (b) pure "
    "code-gen suited to Codex, (c) privacy-sensitive / local-first work "
    "suited to OpenCode + Ollama, (d) zero-egress / CONFIDENTIAL tasks "
    "suited to Hermes (fully local via Ollama — no cloud API needed), or "
    "(e) GitHub Copilot is available and the task is shell/git/gh command "
    "generation (zero incremental cost for Copilot Business/Enterprise).\n"
    "- Hermes model aliases: hermes-fast (7B), hermes-balanced (13B), "
    "hermes-capable (Hermes-3 8B), hermes-large (70B). Pass via 'model' "
    "field. Falls back gracefully if Ollama is not running.\n"
    "- Copilot task types via 'model' field: 'shell' (shell commands only), "
    "'git' (git commands only), 'gh' (gh CLI commands only), or omit for "
    "general chat. Requires GitHub Copilot subscription + `copilot` binary.\n"
    "- Workers have NO bridge state, NO skill-inject, NO /btw, NO audit. "
    "Pass them a SELF-CONTAINED prompt with all needed context, then "
    "wrap their final_text in your own reply.\n"
    "- Budget is wall-clock seconds (clamped 10..600, default 60). "
    "Sandbox+output-judge+prompt-safety floors set by operator are "
    "uncloseable from your tool-args."
)


def _inject_delegate_capability(merged: dict, persona_name: str) -> dict:
    """Layer 29 — inject delegate_* MCP tools when persona has
    delegate_enabled=True. Mirror of _inject_forge_capability /
    _inject_skill_forge_capability. Idempotent.

    The resolver also propagates ``delegate_output_judge_mode``,
    ``delegate_sandbox_mode``, and ``delegate_prompt_safety_mode``
    from the persona JSON into the MCP server's env so the
    operator-set security floors (29.3a, 29.5, 29.6) reach
    ``run_delegate``'s asymmetric resolution chain.
    """
    if not persona_name:
        return merged
    persona = load(persona_name)
    if not persona:
        return merged
    if not persona.get("delegate_enabled"):
        return merged
    out = dict(merged)
    allowed = list(out.get("allowed_tools") or [])
    for t in (
        "mcp__corvin_delegate__delegate_claude_code",
        "mcp__corvin_delegate__delegate_codex",
        "mcp__corvin_delegate__delegate_opencode",
        "mcp__corvin_delegate__delegate_hermes",
        "mcp__corvin_delegate__delegate_copilot",
    ):
        if t not in allowed:
            allowed.append(t)
    out["allowed_tools"] = allowed
    mcp = dict(out.get("mcp_servers") or {})
    if "corvin_delegate" not in mcp:
        env: dict[str, str] = {
            "PYTHONPATH": (
                "{{REPO_ROOT}}/core/delegate"
                ":{{REPO_ROOT}}/operator/forge"
                ":{{REPO_ROOT}}/operator/bridges/shared"
            ),
            "CORVIN_CALLER_PERSONA": persona_name,
        }
        # Layer 29.3a — output-judge env-floor (uncloseable by LLM
        # via tool-arg max-strictness).
        oj = persona.get("delegate_output_judge_mode")
        if isinstance(oj, str) and oj.strip():
            env["CORVIN_DELEGATE_OUTPUT_JUDGE_MODE"] = oj.strip()
        # Layer 29.5 — sandbox env-floor.
        sb = persona.get("delegate_sandbox_mode")
        if isinstance(sb, str) and sb.strip():
            env["CORVIN_DELEGATE_SANDBOX_FLOOR"] = sb.strip()
        # Layer 29.6 — prompt-safety env-floor.
        ps = persona.get("delegate_prompt_safety_mode")
        if isinstance(ps, str) and ps.strip():
            env["CORVIN_DELEGATE_PROMPT_SAFETY_MODE"] = ps.strip()
        # Layer 30 (ADR-0022) — engine-agnostic Forge + SkillForge
        # capability env-floor (uncloseable by LLM via tool-arg
        # max-strictness). Three independent dials so an operator
        # can grant skills-injection without unlocking forge tools,
        # or the other way around. Boolean → "1"/"0" string for env.
        di = persona.get("delegate_inject_skills")
        if isinstance(di, bool):
            env["CORVIN_DELEGATE_INJECT_SKILLS"] = "1" if di else "0"
        df = persona.get("delegate_forge_enabled")
        if isinstance(df, bool):
            env["CORVIN_DELEGATE_FORGE_ENABLED"] = "1" if df else "0"
        dsf = persona.get("delegate_skill_forge_enabled")
        if isinstance(dsf, bool):
            env["CORVIN_DELEGATE_SKILL_FORGE_ENABLED"] = "1" if dsf else "0"
        # ADR-0049 — session-pinning floor. When worker_session_pinned=true,
        # every delegation from this persona auto-sets pin_session=True.
        wsp = persona.get("worker_session_pinned")
        if isinstance(wsp, bool) and wsp:
            env["CORVIN_DELEGATE_WORKER_SESSION_PINNED"] = "1"
        mcp["corvin_delegate"] = {
            # sys.executable — see the forge branch above. Same interpreter
            # that can import corvin_delegate.mcp_server.
            "command": sys.executable,
            "args": ["-m", "corvin_delegate.mcp_server"],
            "env": env,
        }
    out["mcp_servers"] = mcp
    _ensure_brief(out, _DELEGATE_BRIEF)
    return out


def _expand_template_vars(value, *, persona=None):
    """Recursively replace template vars in mcp_servers values.

    Supported tokens:
      {{REPO_ROOT}}             absolute path to the voice-skill repo
      {{HOME}}                  $HOME of the running user
      {{ALLOWED_FORGED_TOOLS}}  comma-joined persona.allowed_forged_tools,
                                "" if absent (the forge MCP server reads
                                its FORGE_ALLOWED_TOOLS env this way)

    Walks dict/list/str. Non-string leaves pass through. Substitution
    targets are real values, so the resulting MCP config is portable
    across users without hand-edits per persona.
    """
    if isinstance(value, str):
        out = (value
               .replace("{{REPO_ROOT}}", str(REPO_ROOT))
               .replace("{{HOME}}", str(Path.home())))
        if "{{ALLOWED_FORGED_TOOLS}}" in out:
            allowed = (persona or {}).get("allowed_forged_tools") or []
            joined = ",".join(allowed) if isinstance(allowed, list) else ""
            out = out.replace("{{ALLOWED_FORGED_TOOLS}}", joined)
        return out
    if isinstance(value, dict):
        return {k: _expand_template_vars(v, persona=persona)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_template_vars(v, persona=persona) for v in value]
    return value


def materialize_mcp(profile: dict) -> str | None:
    """`mcp_servers` (dict) → JSON-File unter MCP_CACHE_DIR/<sha>.json.
    Gibt path back, der via `claude --mcp-config <path>` benutzt wed.
    None, wenn keine MCP-Server set sind.

    {{REPO_ROOT}} and {{HOME}} are expanded in command/args/env values
    before persistence — personas declaring plugin-relative paths work
    without absolute hand-edits.

    content content-addressed, so automatically idempotent + cacheable."""
    servers = profile.get("mcp_servers")
    if not isinstance(servers, dict) or not servers:
        return None
    expanded = _expand_template_vars(servers, persona=profile)
    payload = json.dumps({"mcpServers": expanded}, sort_keys=True,
                         ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    MCP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = MCP_CACHE_DIR / f"{digest}.json"
    if not out.exists():
        out.write_text(payload, encoding="utf-8")
    return str(out)


def expand_dirs(profile: dict) -> list[str]:
    """`add_dirs` mit ~/$VAR-Expansion + mkdir-on-demand."""
    raw = profile.get("add_dirs") or []
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for d in raw:
        if not isinstance(d, str) or not d.strip():
            continue
        p = Path(os.path.expandvars(os.path.expanduser(d.strip())))
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        out.append(str(p))
    return out
