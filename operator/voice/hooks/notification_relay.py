#!/usr/bin/env python3
"""notification_relay.py — Brücke Desktop-Claude → Messenger.

Wird von Claude Code als Notification- und SessionStart-Hook aufgerufen.
Liest die Hook-Payload von stdin, sucht in
``~/.config/corvin-voice/relay.json`` nach einem Relay-Profil, und schreibt
einen Outbox-Envelope nach ``bridges/shared/outbox/``. Der entsprechende
Bridge-Daemon (telegram/discord/whatsapp/slack) picks the file up und
sendet die Nachricht an das konfigurierte Konto.

Use-Case:
- Du arbeitest am Desktop, lässt Claude Code etwas Längeres tun.
- Claude meldet sich via Notification-Hook ("Bash command needs approval")
  während du gerade nicht am Schreibtisch bist.
- Dieses Skript leitet die Notification an dein Telegram weiter.
- Du siehst sie auf dem Phone, antwortest dort weiter.

Konfig-Schema (~/.config/corvin-voice/relay.json):
    {
      "enabled": true,
      "channel": "telegram",       // telegram | discord | whatsapp | slack
      "to": "123456789",           // chat_id (telegram) oder JID (whatsapp)
      "events": ["Notification"],  // welche hook_event_name weiterleiten
      "prefix": "🔔 Desktop-Claude:" // optional, wird vor message gesetzt
    }

Wenn die Datei fehlt oder enabled=false, exitet das Skript schweigend mit
Code 0 — Claude Code läuft normal weiter.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _shared_outbox() -> Path:
    """Resolve the outbox directory the messenger daemons actually poll.

    THE bug this fixes: the daemons poll ``operator/bridges/shared/outbox``
    (``discord/daemon.js`` etc.: ``SHARED = resolve(__dirname,'..','shared')``).
    This hook lives in ``operator/voice/hooks/``, so the polled outbox is TWO
    levels up + ``bridges/shared/outbox`` — ``ROOT.parent.parent``. The previous
    ``ROOT.parent`` pointed at ``operator/voice/bridges/shared/outbox``, a real
    but orphan directory no daemon reads, so every relayed notification was
    silently dropped (fire-and-forget with no acknowledgement).

    ``ADAPTER_OUTBOX`` env override wins (tests / channel-agnostic single-dir
    deployments), mirroring scheduler.py / adapter.py.
    """
    env = os.environ.get("ADAPTER_OUTBOX")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    # operator/voice/hooks → operator/voice → operator → operator/bridges/shared/outbox
    return ROOT.parent.parent / "bridges" / "shared" / "outbox"


SHARED_OUTBOX = _shared_outbox()


def _voice_dir() -> Path:
    """Inline Corvin path resolver — kept self-contained so this hook
    keeps running standalone when Claude Code calls it."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env))) / "voice"
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin" / "voice"
    return Path.home() / ".corvin" / "voice"


def _voice_config_dir() -> Path:
    """Canonical voice-config root: $XDG_CONFIG_HOME/corvin-voice (default
    ~/.config/corvin-voice). This is where the voice subsystem WRITES relay.json
    (profile/memory/vault all live here too). Previously this hook read it from
    <corvin_home>/voice → reader≠writer for the relay (path-audit #MED10)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"


def _config_dir() -> Path:
    """Resolve the relay config root.

    ``VOICE_CONFIG_DIR`` env override stays highest priority (tests/users).
    Fallback is the canonical ~/.config/corvin-voice (XDG), matching where the
    voice subsystem writes relay.json — NOT <corvin_home>/voice.
    """
    env = os.environ.get("VOICE_CONFIG_DIR")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return _voice_config_dir()


CONFIG_DIR = _config_dir()
RELAY_CONFIG = CONFIG_DIR / "relay.json"


def log(*args) -> None:
    """Best-effort Logging in voice.log; nie blockierend."""
    try:
        log_file = CONFIG_DIR / "voice.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a") as fh:
            fh.write(
                f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] "
                f"[notification_relay] " + " ".join(str(a) for a in args) + "\n"
            )
    except OSError:
        pass


def load_relay_config() -> dict | None:
    """Lädt relay.json. Return None wenn Datei fehlt, leer ist, ungültig
    ist oder enabled=false. Damit ist das Skript ein No-Op solange der User
    die Brücke nicht aktiv eingerichtet hat."""
    if not RELAY_CONFIG.exists():
        return None
    try:
        cfg = json.loads(RELAY_CONFIG.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log(f"relay.json unreadable: {e}")
        return None
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return None
    if not cfg.get("channel") or not cfg.get("to"):
        log(f"relay.json incomplete (channel/to missing): {cfg}")
        return None
    return cfg


def build_message(payload: dict, cfg: dict) -> str | None:
    """Aus Hook-Payload + Konfig den Nachricht-Text bauen.

    Hook-Events:
      Notification → message-Feld direkt nutzen
      SessionStart → kurzes Briefing ("Session gestartet (source=…)")
      Stop         → kein Auto-Forward (würde jeden Reply doppelt schicken)
      Andere       → message wenn vorhanden, sonst Skip
    """
    event = payload.get("hook_event_name", "")
    message = payload.get("message", "")
    prefix = cfg.get("prefix") or ""

    if event == "Notification":
        if not message:
            return None
        return f"{prefix} {message}".strip() if prefix else str(message)

    if event == "SessionStart":
        source = payload.get("source", "startup")
        # Auf "startup" reagieren, "resume"/"compact" sind weniger interessant
        # für eine Push-Benachrichtigung.
        if source != "startup":
            return None
        cwd = payload.get("cwd") or os.getcwd()
        cwd_short = Path(cwd).name or cwd
        return (
            f"{prefix} Claude Code Session gestartet in `{cwd_short}`.".strip()
            if prefix else
            f"Claude Code Session gestartet in `{cwd_short}`."
        )

    # Generischer Fallback: wenn ein message-Feld da ist, weiterleiten.
    if message:
        return f"{prefix} {message}".strip() if prefix else str(message)
    return None


def write_outbox(channel: str, to: str, text: str,
                 chat_id: "str | int | None" = None) -> bool:
    """Schreibt Outbox-Envelope für den Daemon. Liefert True bei Erfolg.

    Routing keys per channel (see the daemons): discord/telegram/slack/signal/
    email route on ``chat_id``; whatsapp routes on ``to`` (JID). We therefore
    ALWAYS stamp both — ``to`` (for whatsapp) and a resolved ``chat_id`` (for the
    rest). ``chat_id`` comes from the explicit config value when set, else falls
    back to ``to``. Previously chat_id was derived only for telegram/discord and
    only from ``to``, so slack/signal relays were dropped for a missing key and
    an explicit chat_id in relay.json was ignored.
    """
    # Ensure the outbox exists rather than dropping the notification when the
    # bridge simply hasn't created it yet (fresh install / first relay).
    try:
        SHARED_OUTBOX.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log(f"outbox dir uncreatable: {SHARED_OUTBOX}: {e}")
        return False
    # Random suffix: two hook events in the same millisecond would otherwise
    # write the same relay_<ms>.json and one notification would be lost.
    msg_id = f"relay_{int(time.time()*1000)}_{os.urandom(3).hex()}"
    resolved = chat_id if chat_id not in (None, "") else to
    # Keep chat_id a STRING — never int-coerce. Discord channel snowflakes are
    # 19 digits (> 2^53); emitting one as a JSON number loses precision when the
    # daemon re-parses with JSON.parse (float64) and the message is misrouted.
    # discord.js/telegram/slack/signal daemons all accept string ids.
    envelope: dict = {
        "channel": channel,
        "to": to,
        "chat_id": str(resolved),
        "text": text,
        "_relay": True,
    }
    out_file = SHARED_OUTBOX / f"{msg_id}.json"
    try:
        out_file.write_text(json.dumps(envelope, ensure_ascii=False))
        log(f"forwarded → {channel}:{to} ({len(text)} chars)")
        return True
    except OSError as e:
        log(f"outbox write failed: {e}")
        return False


def main() -> int:
    cfg = load_relay_config()
    if cfg is None:
        return 0  # Relay nicht aktiviert — still aussteigen.

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        log(f"stdin not JSON: {e}")
        return 0

    event = payload.get("hook_event_name", "")
    allowed_events = cfg.get("events") or ["Notification"]
    if event and event not in allowed_events:
        return 0  # Event nicht im Filter.

    text = build_message(payload, cfg)
    if not text:
        return 0

    write_outbox(cfg["channel"], str(cfg["to"]), text, chat_id=cfg.get("chat_id"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
