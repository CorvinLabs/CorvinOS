# Signal Bridge

## Overview

The Signal bridge is the most privacy-focused bridge in Corvin. Signal's
end-to-end encryption means that message content is never exposed to any server
outside the participants' devices. The bridge uses
[signal-cli](https://github.com/AsamK/signal-cli) — a JVM-based command-line
client for Signal — paired with a dedicated phone number that acts as the
bot identity.

This bridge is well suited for high-trust, low-volume deployments (security
researchers, small teams, compliance-conscious enterprises) where message
privacy is a hard requirement. For high-volume public bots, prefer Telegram
or Discord.

---

## Prerequisites

- **Java 17 or later** — `java -version` must report `17.x` or higher.
  Install via your distro's package manager or from
  [Adoptium](https://adoptium.net).
- **signal-cli ≥ 0.13** — see installation section below.
- **[signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) ≥ commit
  [`#740`](https://github.com/bbernhard/signal-cli-rest-api/pull/740) (merged
  2025-09-10)** — the bridge talks to signal-cli through this REST wrapper
  (`SIGNAL_API_URL`, default `http://localhost:8080`), not to signal-cli
  directly. Sticky-progress message editing/deletion (`edit_timestamp` on
  `POST /v2/send`, `DELETE /v1/remote-delete/{number}`) requires a build at
  or after that PR. An older build silently ignores the unknown
  `edit_timestamp` field instead of erroring — every "edit" then posts a
  brand-new message and the sticky progress display never collapses, with
  no crash or log line to point at the real cause. If progress messages look
  like they're spamming instead of updating in place, this is the first
  thing to check.
- **A dedicated phone number** — the number used as the bot identity must
  **not** already be an active Signal account on another device. A virtual
  SIM (e.g. from a VoIP provider) or a dedicated physical SIM works best.
  Reusing a personal Signal account will unlink it from your phone.

---

## Installing signal-cli

1. Download the latest release archive from
   https://github.com/AsamK/signal-cli/releases

   ```bash
   VERSION=0.13.7   # replace with the latest release
   wget "https://github.com/AsamK/signal-cli/releases/download/v${VERSION}/signal-cli-${VERSION}-Linux.tar.gz"
   tar -xzf "signal-cli-${VERSION}-Linux.tar.gz"
   sudo mv "signal-cli-${VERSION}" /opt/signal-cli
   sudo ln -sf /opt/signal-cli/bin/signal-cli /usr/local/bin/signal-cli
   ```

2. Verify the installation:

   ```bash
   signal-cli --version
   # Expected output: signal-cli 0.13.7
   ```

---

## Registering a Phone Number

Use this path when the phone number has never been registered on Signal before.

1. **Request registration** — Signal will send a verification code via SMS:

   ```bash
   signal-cli -u +49123456789 register
   ```

   Replace `+49123456789` with your E.164-formatted phone number.

2. **Confirm the verification code** received by SMS:

   ```bash
   signal-cli -u +49123456789 verify 123456
   ```

   If Signal offers a voice call instead of SMS, append `--voice` to the
   `register` command.

3. **Verify the account is functional**:

   ```bash
   signal-cli -u +49123456789 receive
   # Should return without error (no messages is fine)
   ```

---

## Linking an Existing Account (Alternative)

If you have an existing Signal account and want to add Corvin as a linked
device (without giving it a dedicated number), use the link flow instead.

1. **Start the link process**:

   ```bash
   signal-cli link -n "Corvin Bot"
   ```

   signal-cli will print a `tsdevice:/…` URI and optionally render a QR code
   in the terminal.

2. **Scan the QR code** with the Signal app on your phone:
   - Open Signal → Settings → Linked Devices → Link New Device.
   - Point the camera at the QR code printed in the terminal.

3. **Confirm** — once linked, signal-cli will confirm with a message like
   `Associated with: +49123456789`. All messages sent to that number via
   Signal will now also arrive on this device.

Note: a linked device cannot initiate contacts independently — the primary
account must be the one inviting others to a conversation first.

---

## Configuration

Create or edit `operator/bridges/signal/settings.json`:

```json
{
  "signal_number": "+49123456789",
  "whitelist": [
    "+49987654321",
    "+491701234567"
  ],
  "signal_cli_path": "/usr/local/bin/signal-cli"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `signal_number` | Yes | E.164-formatted number registered with signal-cli. |
| `whitelist` | Yes | Array of E.164 numbers allowed to interact with the bot. Messages from numbers not in the whitelist are silently dropped. |
| `signal_cli_path` | No | Absolute path to the `signal-cli` binary. Defaults to `signal-cli` on `PATH`. |

All other standard Corvin bridge settings (`pin`, `rate_limit_per_hour`,
`chat_profiles`, `local_announce_inbound`, etc.) are supported and hot-reload
on mtime change.

---

## Starting

The recommended way is via `bridge.sh`, which manages all bridges uniformly:

```bash
bash operator/bridges/bridge.sh up
```

To start the Signal daemon directly (useful for debugging):

```bash
node operator/bridges/signal/daemon.js
```

Logs are written to the standard Corvin journal. Pipe through `bridge.sh logs signal`
to follow them.

---

## Limitations

- **Rate limits are strict.** Signal aggressively rate-limits accounts that send
  many messages in a short period. Accounts that trip rate limits may be
  temporarily or permanently banned. Do not use this bridge for high-volume,
  broadcast, or spammy patterns.
- **Groups are supported** but the bot must be explicitly added by an existing
  group member. The bot cannot join a group on its own.
- **No inline media** — the bridge currently delivers text replies only; rich
  media (images, files) produced by Forge tools is not forwarded over Signal.
- **signal-cli updates** — Signal occasionally changes its protocol in ways that
  break older signal-cli versions. Keep signal-cli up to date; the bridge will
  log a `signal_cli.version_mismatch` warning when a newer version is detected.
- **Single device only** — do not run signal-cli on two machines with the same
  number simultaneously; Signal will revoke one of the sessions.
