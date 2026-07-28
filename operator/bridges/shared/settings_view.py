"""settings_view.py — single-message renderer for `/settings`.

Aggregates the chat-scoped *and* system-scoped configuration state of
Corvin into one chat-friendly reply, split into three blocks:

  ━━ WORKING / PFADE ━━   (where state lives on disk for this chat)
  ━━ SESSION ━━           (chat-specific: persona, LDD, voice, profile, ...)
  ━━ SYSTEM ━━            (global: tenant, bridges, engine, forge, audit, ...)

Every section is fail-open: a missing dependency, an unreadable file or
a stale env var degrades to ``—`` instead of crashing the dispatcher.
The reply is the user-facing surface; an exception here would mean the
operator typing ``/settings`` sees nothing.

CLI
---
``python settings_view.py render <channel> <chat_key> [--uid <uid>]
[--lang de|en] [--tenant <tid>]`` → prints the full block to stdout
and exits 0.

Public API
----------
``render_settings(channel, chat_key, *, uid=None, lang="de",
tenant_id=None) -> str`` — used by the JS dispatcher via spawnSync,
and by tests directly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import paths  # noqa: E402  — local module, same dir


# ── i18n labels (German is the operator's default per CLAUDE.md) ───────────

_L = {
    "de": {
        "title":         "🔧 Corvin — Settings",
        "h_paths":       "━━ WORKING / PFADE ━━",
        "h_session":     "━━ SESSION ━━",
        "h_system":      "━━ SYSTEM ━━",
        "corvin_home":  "Corvin home",
        "tenant":        "Tenant",
        "session_dir":   "Session dir",
        "voice_state":   "Voice state",
        "persona_dirs":  "Persona dirs",
        "cwd":           "cwd",
        "persona":       "Persona",
        "permission":    "Permission",
        "ldd":           "LDD",
        "ldd_active":    "aktiv",
        "ldd_off":       "aus",
        "dialectic":     "Dialectic",
        "voice":         "Voice",
        "audience":      "Audience",
        "profile":       "Profile",
        "role":          "Role",
        "quota":         "Quota",
        "consent":       "Consent",
        "obs_off":       "Observer-Modus: off",
        "obs_on":        "Observer-Modus: transcript",
        "bridges":       "Bridges",
        "engine":        "Engine",
        "stt":           "STT",
        "ldd_def":       "LDD defaults",
        "forge":         "Forge",
        "skills":        "Skills",
        "audit_chain":   "Audit chain",
        "autoupdate":    "Auto-update",
        "gateway":       "Gateway",
        "tools":         "tools",
        "user":          "user",
        "session":       "session",
        "verified":      "verified",
        "unknown":       "unbekannt",
        "off":           "off",
        "on":            "on",
        "none":          "—",
    },
    "en": {
        "title":         "🔧 Corvin — Settings",
        "h_paths":       "━━ WORKING / PATHS ━━",
        "h_session":     "━━ SESSION ━━",
        "h_system":      "━━ SYSTEM ━━",
        "corvin_home":  "Corvin home",
        "tenant":        "Tenant",
        "session_dir":   "Session dir",
        "voice_state":   "Voice state",
        "persona_dirs":  "Persona dirs",
        "cwd":           "cwd",
        "persona":       "Persona",
        "permission":    "Permission",
        "ldd":           "LDD",
        "ldd_active":    "active",
        "ldd_off":       "off",
        "dialectic":     "Dialectic",
        "voice":         "Voice",
        "audience":      "Audience",
        "profile":       "Profile",
        "role":          "Role",
        "quota":         "Quota",
        "consent":       "Consent",
        "obs_off":       "Observer mode: off",
        "obs_on":        "Observer mode: transcript",
        "bridges":       "Bridges",
        "engine":        "Engine",
        "stt":           "STT",
        "ldd_def":       "LDD defaults",
        "forge":         "Forge",
        "skills":        "Skills",
        "audit_chain":   "Audit chain",
        "autoupdate":    "Auto-update",
        "gateway":       "Gateway",
        "tools":         "tools",
        "user":          "user",
        "session":       "session",
        "verified":      "verified",
        "unknown":       "unknown",
        "off":           "off",
        "on":            "on",
        "none":          "—",
    },
}

# Channels the dispatcher checks for bridge-readiness. Read from channels.py so
# this stays every shipped channel — the hand-written copy that used to sit here
# listed five, so a Signal or Teams operator's `/settings` silently omitted the
# only row about their own bridge (and its comment claimed "four-channel set").
from channels import BRIDGE_CHANNELS as _BRIDGE_CHANNELS  # noqa: E402,I001
from channels import CHANNEL_LABELS as _CHANNEL_LABELS  # noqa: E402,I001


# ── safe import shims ──────────────────────────────────────────────────────
# Every helper module is wrapped so a missing or broken module degrades
# gracefully into the "—" path instead of taking the whole reply down.

def _try_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


# ── tiny utilities ─────────────────────────────────────────────────────────

def _short_path(p: Path | str | None) -> str:
    if p is None:
        return "—"
    try:
        s = str(Path(p))
    except Exception:
        return "—"
    home = str(Path.home())
    if s.startswith(home):
        return "~" + s[len(home):]
    return s


def _read_json_safe(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _truncate(s: str, n: int = 40) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ── settings.json reading (per-channel) ────────────────────────────────────

def _read_channel_settings(channel: str) -> dict[str, Any]:
    """Return the per-channel settings.json or empty dict.

    Looks at the canonical ADR-0008 location first; falls back to the
    legacy in-repo location if the runtime root is empty.
    """
    try:
        p = paths.bridge_settings_path(channel)
        if p.is_file():
            d = _read_json_safe(p)
            if d:
                return d
    except Exception:
        pass
    # Legacy fallback (pre-ADR-0008).
    try:
        legacy = paths.legacy_bridge_runtime_dir(channel, "root")
        if legacy is not None:
            p2 = legacy / "settings.json"
            if p2.is_file():
                d2 = _read_json_safe(p2)
                if d2:
                    return d2
    except Exception:
        pass
    return {}


def _chat_profile(channel: str, chat_key: str) -> dict[str, Any]:
    s = _read_channel_settings(channel)
    profiles = s.get("chat_profiles") or {}
    if not isinstance(profiles, dict):
        return {}
    if chat_key in profiles:
        return profiles[chat_key] or {}
    # JID-normalised fallback (mirrors the adapter resolver).
    norm = str(chat_key).split("@")[0]
    if norm in profiles:
        return profiles[norm] or {}
    return profiles.get("default") or {}


# ── persona resolution ─────────────────────────────────────────────────────

def _persona_files() -> list[Path]:
    """Bundle + user persona dirs that ship with cowork."""
    candidates: list[Path] = []
    # Repo-relative resolution: settings_view.py → operator/bridges/shared
    # → repo root is three parents up.
    repo_root = HERE.parent.parent.parent
    bundle = repo_root / "operator" / "cowork" / "personas"
    if bundle.is_dir():
        candidates.append(bundle)
    user = paths.tenant_cowork_dir() / "personas"
    if user.is_dir():
        candidates.append(user)
    return candidates


def _load_persona(name: str) -> dict[str, Any] | None:
    if not name:
        return None
    for d in _persona_files():
        p = d / f"{name}.json"
        if p.is_file():
            data = _read_json_safe(p)
            if data:
                return data
    return None


def _active_persona(channel: str, chat_key: str) -> tuple[str, dict[str, Any]]:
    """(persona_name, persona_dict) for this chat. Empty defaults if none."""
    profile = _chat_profile(channel, chat_key)
    name = profile.get("persona") or ""
    persona = _load_persona(name) if name else None
    if persona is None:
        # Fall through to coder as the documented default.
        name = name or "coder"
        persona = _load_persona(name) or {}
    return name, persona


# ── PATHS block ────────────────────────────────────────────────────────────

def render_paths_block(channel: str, chat_key: str, *,
                       lang: str = "de",
                       tenant_id: str | None = None) -> list[str]:
    L = _L.get(lang, _L["de"])
    out: list[str] = [L["h_paths"]]

    try:
        ah = paths.corvin_home()
    except Exception:
        ah = None
    out.append(f"• {L['corvin_home']}: {_short_path(ah)}")

    tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    out.append(f"• {L['tenant']}: {tid}")

    bridge_chat = f"{channel}:{chat_key}"
    try:
        sess = paths.tenant_sessions_dir(tenant_id) / bridge_chat
    except Exception:
        sess = None
    out.append(f"• {L['session_dir']}: {_short_path(sess)}")

    try:
        # voice/sessions/<channel>/<chat_key>/  — matches adapter convention.
        safe_chat = str(chat_key).replace("/", "_")
        voice_state = (paths.tenant_voice_dir(tenant_id)
                       / "sessions" / channel / safe_chat)
    except Exception:
        voice_state = None
    out.append(f"• {L['voice_state']}: {_short_path(voice_state)}")

    _, persona = _active_persona(channel, chat_key)
    dirs = persona.get("add_dirs") or []
    wd = persona.get("working_dir")
    extras: list[str] = []
    if wd:
        extras.append(f"working_dir={_short_path(wd)}")
    if dirs:
        # add_dirs entries are usually absolute and short — show up to 3.
        short = [_short_path(d) for d in dirs[:3]]
        suffix = f" (+{len(dirs)-3})" if len(dirs) > 3 else ""
        extras.append("add_dirs=" + ", ".join(short) + suffix)
    out.append("• " + L["persona_dirs"] + ": " +
               (", ".join(extras) if extras else L["none"]))
    return out


# ── SESSION block ──────────────────────────────────────────────────────────

def _ldd_summary(profile: dict[str, Any], lang: str) -> str:
    L = _L[lang]
    ldd = _try_import("ldd")
    if ldd is None:
        return L["none"]
    try:
        on_layers: list[str] = []
        off_layers: list[str] = []
        for layer in ldd.LAYERS:
            active, _reason = ldd.effective_state(layer, profile=profile)
            (on_layers if active else off_layers).append(layer)
        cfg = ldd.load_config()
        master = bool(cfg.get("enabled", True))
        master_str = L["on"] if master else L["off"]
        head = f"{master_str} · {len(on_layers)}/{len(ldd.LAYERS)} layers"
        # Compact list: first 4 active, rest collapsed.
        if on_layers:
            head += "\n  ├ " + L["ldd_active"] + ": " + ", ".join(on_layers[:4])
            if len(on_layers) > 4:
                head += f", +{len(on_layers)-4}"
        if off_layers:
            head += "\n  └ " + L["ldd_off"] + ": " + ", ".join(off_layers[:4])
            if len(off_layers) > 4:
                head += f", +{len(off_layers)-4}"
        return head
    except Exception:
        return L["none"]


def _dialectic_summary(lang: str) -> str:
    L = _L[lang]
    d = _try_import("dialectic")
    if d is None:
        return L["none"]
    try:
        cfg = d.load_config()
        if not cfg.get("enabled", True):
            return L["off"]
        sites_cfg = cfg.get("sites") or {}
        items = []
        for site, default in d.SITES.items():
            mode = (sites_cfg.get(site) or {}).get("mode") or default["mode"]
            # Compact "skill_promotion=skill"; only the last segment of long
            # site names, for phone-friendliness.
            short = site.split("_")[0]
            items.append(f"{short}={mode}")
        return ", ".join(items)
    except Exception:
        return L["none"]


def _voice_summary(lang: str) -> str:
    L = _L[lang]
    # ~/.config/corvin-voice/config.json
    home = Path.home() / ".config" / "corvin-voice" / "config.json"
    cfg = _read_json_safe(home) if home.is_file() else {}
    parts: list[str] = []
    cfg = cfg or {}
    v_lang = cfg.get("voice_lang") or cfg.get("lang") or "auto"
    v_mode = cfg.get("voice_mode") or "auto"
    v_engine = cfg.get("voice_engine") or os.environ.get("VOICE_ENGINE") or "auto"
    parts.append(str(v_lang))
    parts.append(str(v_mode))
    parts.append(str(v_engine))
    return " · ".join(parts) if parts else L["none"]


def _audience_summary(lang: str) -> str:
    L = _L[lang]
    pf = _try_import("profile")
    if pf is None:
        return L["none"]
    try:
        data = pf.load() or {}
        keys = [
            ("voice_audience_level",      "level"),
            ("voice_audience_jargon",     "jargon"),
            ("voice_audience_style",      "style"),
            ("voice_audience_metaphors",  "metaph"),
            ("voice_audience_learning",   "learn"),
        ]
        parts = []
        for k, label in keys:
            if k in data and data[k] not in (None, ""):
                parts.append(f"{label}={data[k]}")
        return " · ".join(parts) if parts else L["none"]
    except Exception:
        return L["none"]


def _profile_line(lang: str) -> str:
    L = _L[lang]
    pf = _try_import("profile")
    if pf is None:
        return L["none"]
    try:
        d = pf.load() or {}
        bits = []
        if d.get("name"):     bits.append(str(d["name"]))
        if d.get("tone"):     bits.append(str(d["tone"]))
        if d.get("timezone"): bits.append(str(d["timezone"]))
        if d.get("display_language"): bits.append(str(d["display_language"]))
        return " · ".join(bits) if bits else L["none"]
    except Exception:
        return L["none"]


def _role_quota_summary(channel: str, chat_key: str, uid: str | None,
                       lang: str) -> tuple[str, str]:
    L = _L[lang]
    role_str = L["none"]
    quota_str = L["none"]
    if not uid:
        return role_str, quota_str
    r = _try_import("roles")
    if r is not None:
        try:
            st = r.status(channel, chat_key, uid)
            role_str = st.get("bundle") or st.get("role") or L["none"]
        except Exception:
            pass
    q = _try_import("quota")
    if q is not None:
        try:
            usage = q.get_usage(channel, chat_key, uid)
            if usage:
                msgs = usage.get("messages", 0)
                tokens = usage.get("tokens", 0)
                lim_m = usage.get("limit_messages") or "∞"
                lim_t = usage.get("limit_tokens") or "∞"
                quota_str = f"{msgs}/{lim_m} msgs · {tokens}/{lim_t} tokens"
        except Exception:
            pass
    return role_str, quota_str


def _consent_summary(channel: str, chat_key: str, lang: str) -> str:
    L = _L[lang]
    profile = _chat_profile(channel, chat_key)
    vis = profile.get("observer_visibility") or "off"
    flag = L["obs_on"] if vis == "transcript" else L["obs_off"]
    c = _try_import("consent")
    n_grants = 0
    if c is not None:
        try:
            grants = c.list_grants(channel, chat_key) if hasattr(c, "list_grants") else {}
            if isinstance(grants, dict):
                # Schema: {<uid>: {...grant entry...}}  — count active ones.
                n_grants = sum(1 for v in grants.values() if v)
        except Exception:
            pass
    return f"{L['none']}  ({flag}, {n_grants} grants)" if n_grants == 0 else f"{n_grants} active  ({flag})"


def render_session_block(channel: str, chat_key: str, *,
                         uid: str | None = None,
                         lang: str = "de") -> list[str]:
    L = _L.get(lang, _L["de"])
    out: list[str] = [L["h_session"]]

    persona_name, persona = _active_persona(channel, chat_key)
    profile = _chat_profile(channel, chat_key)
    perm = (profile.get("permission_mode")
            or persona.get("permission_mode")
            or "bypassPermissions")
    out.append(f"• {L['persona']}:     {persona_name}")
    out.append(f"• {L['permission']}:  {perm}")

    out.append(f"• {L['ldd']}:         {_ldd_summary(profile, lang)}")
    out.append(f"• {L['dialectic']}:   {_dialectic_summary(lang)}")
    out.append(f"• {L['voice']}:       {_voice_summary(lang)}")
    out.append(f"• {L['audience']}:    {_audience_summary(lang)}")
    out.append(f"• {L['profile']}:     {_profile_line(lang)}")

    role_str, quota_str = _role_quota_summary(channel, chat_key, uid, lang)
    out.append(f"• {L['role']}:        {role_str}")
    out.append(f"• {L['quota']}:       {quota_str}")
    out.append(f"• {L['consent']}:     {_consent_summary(channel, chat_key, lang)}")
    return out


# ── SYSTEM block ───────────────────────────────────────────────────────────

def _tenant_config_summary(tenant_id: str | None, lang: str) -> str:
    L = _L[lang]
    try:
        cfg_path = paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
    except Exception:
        return L["none"]
    if not cfg_path.is_file():
        return f"(zone: {L['none']}, engines: alle)"
    # Lightweight parser: avoid pulling pydantic / pyyaml in the
    # bridge process. Only emit a single-line summary.
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except Exception:
        return L["none"]
    zone = L["none"]
    engines = "alle"
    for line in text.splitlines():
        ls = line.strip()
        if ls.startswith("zone:"):
            zone = ls.split(":", 1)[1].strip() or zone
        elif ls.startswith("allowed_engines:"):
            tail = ls.split(":", 1)[1].strip()
            if tail and tail != "[]":
                engines = tail
    return f"(zone: {zone}, engines: {engines})"


def _bridges_summary(lang: str) -> str:
    """Compact bridge-state line: WA ✓ TG ✗ Discord ✓ Slack ✗ Mail ✓."""
    L = _L[lang]
    label_map = _CHANNEL_LABELS
    bits = []
    for ch in _BRIDGE_CHANNELS:
        # Heuristic: settings.json present at EITHER the canonical
        # (ADR-0008) or legacy (in-repo) location. Mtime / pid probes
        # would be more truthful but cost a fork; the present-file
        # signal is enough for "operator configured this channel".
        ok = bool(_read_channel_settings(ch))
        bits.append(f"{label_map.get(ch, ch)} {'✓' if ok else '✗'}")
    return "  ".join(bits)


def _engine_summary(lang: str) -> str:
    L = _L[lang]
    flag = os.environ.get("CORVIN_USE_ENGINE_LAYER", "1")
    if flag == "0":
        return "claude_code (legacy direct-spawn)"
    return "claude_code (Layer 22)"


def _stt_summary(lang: str) -> str:
    L = _L[lang]
    pin = os.environ.get("CORVIN_STT_PROVIDER")
    chain = os.environ.get("CORVIN_STT_CHAIN")
    if pin:
        return f"pinned={pin}"
    if chain:
        return chain
    # Default chain documented in Layer 23.
    return "openai → local"


def _ldd_defaults(lang: str) -> str:
    L = _L[lang]
    ldd = _try_import("ldd")
    if ldd is None:
        return L["none"]
    try:
        cfg = ldd.load_config()
        master = L["on"] if cfg.get("enabled", True) else L["off"]
        return f"master={master}"
    except Exception:
        return L["none"]


def _forge_summary(tenant_id: str | None, lang: str) -> str:
    L = _L[lang]
    try:
        tools_dir = paths.tenant_forge_dir(tenant_id) / "tools"
    except Exception:
        return L["none"]
    count = 0
    try:
        if tools_dir.is_dir():
            count = sum(1 for p in tools_dir.iterdir() if p.is_dir())
    except Exception:
        pass
    # Policy max_budget — read the bundled default; per-scope overrides
    # are too noisy for a one-line summary.
    bundled = HERE.parent.parent.parent / "operator" / "forge" / "forge" / "policy.json"
    budget = "—"
    pol = _read_json_safe(bundled) if bundled.is_file() else {}
    if isinstance(pol, dict):
        b = pol.get("max_budget") or pol.get("default_budget")
        if isinstance(b, (int, float)):
            budget = f"{b}s"
    return f"max_budget={budget} · {count} {L['tools']}"


def _skills_summary(tenant_id: str | None, lang: str) -> str:
    L = _L[lang]
    try:
        sf_root = paths.tenant_skill_forge_dir(tenant_id)
    except Exception:
        return L["none"]
    user_count = 0
    session_count = 0
    try:
        ud = sf_root / "skills"
        if ud.is_dir():
            user_count = sum(1 for p in ud.iterdir() if p.is_dir())
    except Exception:
        pass
    # Session-scope skills live under <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/skill-forge/skills/
    try:
        sess_root = paths.tenant_sessions_dir(tenant_id)
        if sess_root.is_dir():
            for sess in sess_root.iterdir():
                sk_dir = sess / "skill-forge" / "skills"
                if sk_dir.is_dir():
                    session_count += sum(1 for p in sk_dir.iterdir() if p.is_dir())
    except Exception:
        pass
    return f"{user_count} {L['user']} · {session_count} {L['session']}"


def _audit_chain_summary(tenant_id: str | None, lang: str) -> str:
    L = _L[lang]
    try:
        chain = (paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl")
    except Exception:
        return L["none"]
    if not chain.is_file():
        return L["none"]
    try:
        size = chain.stat().st_size
        return f"✓ present ({size//1024} KiB)"
    except Exception:
        return "✓ present"


def _autoupdate_summary(lang: str) -> str:
    L = _L[lang]
    # Walk up from the bridges module to find the repo root (mirrors
    # paths._repo_root logic without re-importing the private helper).
    repo = Path(__file__).resolve()
    for parent in [repo, *repo.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir() and (parent / ".git").is_dir():
            repo = parent
            break
    else:
        return L["none"]
    marker = repo / ".corvin" / "no-auto-update"
    marker_note = " · marker gesetzt" if marker.is_file() else ""
    head_label = L["unknown"]
    try:
        head_file = repo / ".git" / "HEAD"
        head = head_file.read_text(encoding="utf-8").strip() if head_file.is_file() else ""
        if head.startswith("ref: refs/heads/"):
            head_label = "branch=" + head[len("ref: refs/heads/"):]
        elif head:
            # Detached HEAD on a commit / tag.
            head_label = "HEAD=" + head[:12]
    except Exception:
        pass
    return f"{head_label}{marker_note}"


def _gateway_summary(lang: str) -> str:
    L = _L[lang]
    # The gateway is opt-in; "off" unless a uvicorn entrypoint is running.
    # No reliable cheap probe → present the capability statically.
    return f"{L['off']}  (Phase 2 verfügbar)"


def render_system_block(*, lang: str = "de",
                        tenant_id: str | None = None) -> list[str]:
    L = _L.get(lang, _L["de"])
    out: list[str] = [L["h_system"]]
    tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    out.append(f"• {L['tenant']}:       {tid}  {_tenant_config_summary(tenant_id, lang)}")
    out.append(f"• {L['bridges']}:      {_bridges_summary(lang)}")
    out.append(f"• {L['engine']}:       {_engine_summary(lang)}")
    out.append(f"• {L['stt']}:          {_stt_summary(lang)}")
    out.append(f"• {L['ldd_def']}: {_ldd_defaults(lang)}")
    out.append(f"• {L['forge']}:        {_forge_summary(tenant_id, lang)}")
    out.append(f"• {L['skills']}:       {_skills_summary(tenant_id, lang)}")
    out.append(f"• {L['audit_chain']}:  {_audit_chain_summary(tenant_id, lang)}")
    out.append(f"• {L['autoupdate']}:   {_autoupdate_summary(lang)}")
    out.append(f"• {L['gateway']}:      {_gateway_summary(lang)}")
    return out


# ── public renderer ────────────────────────────────────────────────────────

def render_settings(channel: str, chat_key: str, *,
                    uid: str | None = None,
                    lang: str = "de",
                    tenant_id: str | None = None) -> str:
    if lang not in _L:
        lang = "de"
    L = _L[lang]
    lines: list[str] = [L["title"], ""]
    lines.extend(render_paths_block(channel, chat_key,
                                    lang=lang, tenant_id=tenant_id))
    lines.append("")
    lines.extend(render_session_block(channel, chat_key,
                                      uid=uid, lang=lang))
    lines.append("")
    lines.extend(render_system_block(lang=lang, tenant_id=tenant_id))
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────

def _usage() -> str:
    return (
        "usage:\n"
        "  settings_view.py render <channel> <chat_key> "
        "[--uid <uid>] [--lang de|en] [--tenant <tid>]"
    )


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    sub = argv[0]
    rest = argv[1:]
    if sub != "render":
        print(_usage(), file=sys.stderr)
        return 2
    if len(rest) < 2:
        print(_usage(), file=sys.stderr)
        return 2
    channel, chat_key = rest[0], rest[1]
    flags = rest[2:]
    uid = None
    lang = "de"
    tenant = None
    i = 0
    while i < len(flags):
        f = flags[i]
        if f in ("--uid",):
            uid = flags[i + 1] if i + 1 < len(flags) else None
            i += 2
            continue
        if f in ("--lang",):
            v = flags[i + 1] if i + 1 < len(flags) else "de"
            lang = v if v in _L else "de"
            i += 2
            continue
        if f in ("--tenant", "--tenant-id"):
            tenant = flags[i + 1] if i + 1 < len(flags) else None
            i += 2
            continue
        # Unknown flag — keep robust, skip.
        i += 1
    out = render_settings(channel, chat_key,
                          uid=uid, lang=lang, tenant_id=tenant)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
