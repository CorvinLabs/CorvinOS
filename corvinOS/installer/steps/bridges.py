"""Per-bridge configuration: tokens, whitelists, and settings.json management."""
from __future__ import annotations

import getpass
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass
class BridgeSetup:
    """Outcome of configuring one bridge."""
    name: str
    configured: bool = False
    settings_path: Path | None = None


# ── Public API ─────────────────────────────────────────────────────────────

def configure_bridges(
    repo_root: Path,
    selected: list[str],
    interactive: bool = True,
) -> list[BridgeSetup]:
    """Configure all selected bridges — reads existing settings, prompts only for missing values."""
    results: list[BridgeSetup] = []
    bridges_dir = repo_root / "operator" / "bridges"

    for bridge in selected:
        print(f"\n[Bridge: {bridge}]")
        if bridge == "whatsapp":
            r = _configure_whatsapp(bridges_dir, interactive)
        elif bridge == "telegram":
            r = _configure_telegram(bridges_dir, interactive)
        elif bridge == "discord":
            r = _configure_discord(bridges_dir, interactive)
        elif bridge == "slack":
            r = _configure_slack(bridges_dir, interactive)
        elif bridge == "email":
            r = _configure_email(bridges_dir, interactive)
        else:
            print(f"  ⚠ Unknown bridge '{bridge}' — skipping")
            r = BridgeSetup(name=bridge)
        results.append(r)

    return results


def whatsapp_qr_pair(repo_root: Path) -> None:
    """Run the WhatsApp QR-code pairing flow."""
    script = repo_root / "operator" / "voice" / "scripts" / "whatsapp_cli.sh"
    if not script.exists():
        print(f"⚠ QR pair script not found at {script}")
        return
    print("\n  Starting WhatsApp QR pairing...")
    result = subprocess.run(["bash", str(script), "pair"], check=False)
    if result.returncode != 0:
        print("  ⚠ Pairing exited — retry: bash operator/voice/scripts/whatsapp_cli.sh pair")


# ── Internal bridge configurators ──────────────────────────────────────────

def _configure_whatsapp(bridges_dir: Path, interactive: bool) -> BridgeSetup:
    bridge_dir = bridges_dir / "whatsapp"
    install_sh = bridge_dir / "install.sh"
    if install_sh.exists():
        result = subprocess.run(["bash", str(install_sh)], cwd=bridge_dir, check=False)
        if result.returncode != 0:
            print("  ⚠ WhatsApp install.sh reported errors — check output above")
            return BridgeSetup(name="whatsapp")
        print("  ✓ WhatsApp bridge installed")
    else:
        print("  ⚠ install.sh not found — skipping npm setup")

    # WhatsApp uses QR-code pairing, no token to prompt here
    creds = bridge_dir / "auth" / "creds.json"
    if creds.exists():
        print("  ✓ WhatsApp already paired (creds.json present)")
    else:
        if interactive:
            answer = input("  Pair via QR code now? [Y/n]: ").strip().lower() or "y"
            if not answer.startswith("n"):
                script = bridges_dir.parent.parent / "operator" / "voice" / "scripts" / "whatsapp_cli.sh"
                if script.exists():
                    subprocess.run(["bash", str(script), "pair"], check=False)
                else:
                    print("  ⚠ QR script not found — pair later with: bridge.sh pair-whatsapp")

    return BridgeSetup(name="whatsapp", configured=True, settings_path=bridge_dir / "settings.json")


def _configure_telegram(bridges_dir: Path, interactive: bool) -> BridgeSetup:
    settings_path = bridges_dir / "telegram" / "settings.json"
    bridge_dir = bridges_dir / "telegram"

    _run_bridge_install(bridge_dir)

    existing = _read_field(settings_path, "telegram_token")
    if existing and not existing.startswith("DEIN_"):
        print(f"  ✓ telegram_token already set ({existing[:8]}…) — keeping")
        return BridgeSetup(name="telegram", configured=True, settings_path=settings_path)

    if not interactive:
        print("  ⚠ telegram_token missing — set manually in settings.json")
        return BridgeSetup(name="telegram", settings_path=settings_path)

    print()
    print("  Telegram bot token — get one from @BotFather:")
    print("    1. Message @BotFather  2. /newbot  3. copy the token")
    token = input("  Paste token: ").strip()

    print()
    print("  Your Telegram numeric user-id (whitelist).")
    print("  Use @userinfobot to find it. Multiple ids: space-separated.")
    ids_str = input("  User-id(s): ").strip()

    _write_settings(settings_path, {"telegram_token": token}, ids_str.split())
    print("  ✓ Telegram settings saved")
    return BridgeSetup(name="telegram", configured=bool(token), settings_path=settings_path)


def _configure_discord(bridges_dir: Path, interactive: bool) -> BridgeSetup:
    settings_path = bridges_dir / "discord" / "settings.json"
    bridge_dir = bridges_dir / "discord"

    _run_bridge_install(bridge_dir)

    existing = _read_field(settings_path, "discord_token")
    if existing and not existing.startswith("DEIN_"):
        print(f"  ✓ discord_token already set ({existing[:8]}…) — keeping")
        return BridgeSetup(name="discord", configured=True, settings_path=settings_path)

    if not interactive:
        print("  ⚠ discord_token missing — set manually in settings.json")
        return BridgeSetup(name="discord", settings_path=settings_path)

    print()
    print("  Discord bot token — get one at https://discord.com/developers/applications")
    print("    1. New Application → Bot → Reset Token → copy")
    print("    2. OAuth2 → URL Generator → bot + Read/Send/History perms → invite")
    print("    (Optional: enable 'MESSAGE CONTENT INTENT' to read ALL guild text;")
    print("     without it, DMs and @mentions work out of the box.)")
    token = input("  Paste token: ").strip()

    print()
    print("  Your Discord user-id (whitelist).")
    print("  Settings → Advanced → Developer Mode ON → right-click name → Copy User ID.")
    ids_str = input("  User-id(s): ").strip()

    _write_settings(settings_path, {"discord_token": token}, ids_str.split())
    print("  ✓ Discord settings saved")
    return BridgeSetup(name="discord", configured=bool(token), settings_path=settings_path)


def _configure_slack(bridges_dir: Path, interactive: bool) -> BridgeSetup:
    settings_path = bridges_dir / "slack" / "settings.json"
    bridge_dir = bridges_dir / "slack"

    _run_bridge_install(bridge_dir)

    existing_bot = _read_field(settings_path, "slack_bot_token")
    existing_app = _read_field(settings_path, "slack_app_token")

    if (existing_bot and "DEIN_SLACK" not in existing_bot
            and existing_app and "DEIN_SLACK" not in existing_app):
        print(f"  ✓ Slack tokens already configured — keeping")
        return BridgeSetup(name="slack", configured=True, settings_path=settings_path)

    if not interactive:
        print("  ⚠ Slack tokens missing — set manually in settings.json")
        return BridgeSetup(name="slack", settings_path=settings_path)

    print()
    print("  Slack requires TWO tokens — https://api.slack.com/apps")
    print("    1. New App → From scratch")
    print("    2. OAuth & Permissions → Bot Scopes: chat:write, files:read,")
    print("       files:write, reactions:write, channels/groups/im/mpim:history")
    print("    3. Socket Mode → ON → App-Level Token (connections:write) → xapp-…")
    print("    4. Event Subscriptions → ON → subscribe: message.channels/groups/im/mpim")
    print("    5. Install App → Bot User OAuth Token → xoxb-…")

    bot_token = existing_bot if (existing_bot and "DEIN_SLACK" not in existing_bot) \
        else input("  slack_bot_token (xoxb-...): ").strip()
    app_token = existing_app if (existing_app and "DEIN_SLACK" not in existing_app) \
        else input("  slack_app_token (xapp-...): ").strip()

    print()
    print("  Your Slack user-id.")
    print("  Profile picture → View profile → ⋯ → Copy member ID.")
    ids_str = input("  User-id(s): ").strip()

    _write_settings(
        settings_path,
        {"slack_bot_token": bot_token, "slack_app_token": app_token},
        ids_str.split(),
    )
    print("  ✓ Slack settings saved")
    print("  ⚠ Remember: /invite @your-bot-name in your Slack channel")
    return BridgeSetup(name="slack", configured=bool(bot_token), settings_path=settings_path)


def _configure_email(bridges_dir: Path, interactive: bool) -> BridgeSetup:
    settings_path = bridges_dir / "email" / "settings.json"
    bridge_dir = bridges_dir / "email"

    _run_bridge_install(bridge_dir)

    existing_user = _read_field(settings_path, "imap_user")
    if existing_user and "YOUR_" not in existing_user:
        print(f"  ✓ Email already configured ({existing_user}) — keeping")
        return BridgeSetup(name="email", configured=True, settings_path=settings_path)

    if not interactive:
        print("  ⚠ Email not configured — set manually in settings.json")
        return BridgeSetup(name="email", settings_path=settings_path)

    print()
    print("  Email bridge — IMAP inbound + SMTP outbound.")
    print("  Use an APP-SPECIFIC PASSWORD, never your account password:")
    print("    Gmail:   myaccount.google.com → Security → App passwords")
    print("    iCloud:  appleid.apple.com → App-Specific Passwords")
    print("    Outlook: account.microsoft.com → Security → App passwords")

    email = input("  Your email address: ").strip().lower()
    password = getpass.getpass("  App-specific password: ").strip()

    print()
    print("  Whitelist — email addresses allowed to send tasks (space-separated).")
    whitelist_str = input("  Whitelist: ").strip()

    imap_host, smtp_host = _detect_mail_hosts(email)
    if not imap_host:
        imap_host = input("  IMAP host (e.g. imap.example.com): ").strip()
        smtp_host = input("  SMTP host (e.g. smtp.example.com): ").strip()

    print(f"  IMAP: {imap_host}   SMTP: {smtp_host}")

    fields: dict[str, str] = {
        "imap_host":     imap_host,
        "smtp_host":     smtp_host,
        "imap_user":     email,
        "imap_password": password,
        "smtp_user":     email,
        "smtp_password": password,
        "from_address":  email,
    }
    whitelist = [a.strip().lower() for a in whitelist_str.split() if a.strip()]
    _write_settings(settings_path, fields, whitelist)
    print("  ✓ Email settings saved")
    return BridgeSetup(name="email", configured=bool(email), settings_path=settings_path)


# ── Helpers ────────────────────────────────────────────────────────────────

def _run_bridge_install(bridge_dir: Path) -> bool:
    install_sh = bridge_dir / "install.sh"
    if not install_sh.exists():
        return True  # no install script is fine
    result = subprocess.run(["bash", str(install_sh)], cwd=bridge_dir, check=False)
    if result.returncode != 0:
        print(f"  ⚠ {bridge_dir.name}/install.sh reported errors")
        return False
    return True


def _read_field(settings_path: Path, field: str) -> str:
    """Read a single field from a bridge's settings.json."""
    try:
        data: dict[str, Any] = json.loads(settings_path.read_text())
        return str(data.get(field, ""))
    except Exception:
        return ""


def _write_settings(
    settings_path: Path,
    fields: dict[str, str],
    whitelist: list[str],
) -> None:
    """Merge fields and whitelist entries into settings.json (idempotent)."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data: dict[str, Any] = json.loads(settings_path.read_text())
    except Exception:
        data = {}

    for k, v in fields.items():
        if v:
            data[k] = v

    if whitelist:
        current: list[str] = data.setdefault("whitelist", [])
        for entry in whitelist:
            if entry and entry not in current:
                current.append(entry)

    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


_MAIL_HOSTS: dict[str, tuple[str, str]] = {
    "@gmail.com":      ("imap.gmail.com",           "smtp.gmail.com"),
    "@googlemail.com": ("imap.gmail.com",           "smtp.gmail.com"),
    "@icloud.com":     ("imap.mail.me.com",          "smtp.mail.me.com"),
    "@me.com":         ("imap.mail.me.com",          "smtp.mail.me.com"),
    "@mac.com":        ("imap.mail.me.com",          "smtp.mail.me.com"),
    "@outlook.com":    ("outlook.office365.com",     "smtp.office365.com"),
    "@hotmail.com":    ("outlook.office365.com",     "smtp.office365.com"),
    "@live.com":       ("outlook.office365.com",     "smtp.office365.com"),
    "@msn.com":        ("outlook.office365.com",     "smtp.office365.com"),
}


def _detect_mail_hosts(email: str) -> tuple[str, str]:
    for suffix, hosts in _MAIL_HOSTS.items():
        if email.endswith(suffix):
            return hosts
    return ("", "")
