# Operator Bundle

Complete operator stack for Corvin — personas, skills, forge tools,
and bridge configuration templates. See ADR-0034 for the full decision.

## Install

```bash
# From the repo root:
corvin pkg install ./operator/bundle/

# Or from a built archive:
corvin pkg install com.shumway.operator-1.0.0.awpkg
```

## After install

Fill in secrets for each bridge you use:

```bash
# Discord
$EDITOR ~/.corvin/bridges/discord/settings.json
# Set: discord_token, whitelist, chat_profiles

# Telegram
$EDITOR ~/.corvin/bridges/telegram/settings.json
# Set: telegram_token, whitelist

# Email
$EDITOR ~/.corvin/bridges/email/settings.json
# Set: imap_user, imap_password, smtp_password, ...
```

Then restart bridges:

```bash
bridge.sh restart
```

## Contents

| Directory | What | Count |
|---|---|---|
| `personas/` | Cowork persona definitions | 8 |
| `skills/ldd/` | Loss-Driven Development skill suite | 12 |
| `tools/` | Forge tool definitions (trading backtest) | 3 |
| `bridge-config/` | Settings templates (no secrets) | 5 |

## Upgrading

```bash
# Edit manifest.yaml version, then:
corvin pkg upgrade com.shumway.operator
```

## Building a distributable archive

```bash
corvin pkg build ./operator/bundle/
# → com.shumway.operator-1.0.0.awpkg
```
