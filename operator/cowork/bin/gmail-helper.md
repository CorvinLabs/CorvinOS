# gmail-helper

Local helper that lets any cowork persona compose Gmail messages **with real
MIME attachments**. The claude.ai Gmail connector can create drafts but not
attach files — `gmail-helper` fills that gap.

## TL;DR

```bash
gmail-helper status            # what is configured?
gmail-helper wizard            # interactive setup (recommended)
gmail-helper draft  --to a@b.de --subject Hi --body Yo --attach file.pdf
gmail-helper send   --to a@b.de --subject Hi --body Yo --attach file.pdf
```

Default verb is **`draft`**: a Gmail Draft is created via the Gmail API, you
review and edit it in Gmail, then hit Send yourself. Use **`send`** only when
you want to skip the review step (it goes out via SMTP immediately).

## Two modes — pick whichever you like

The helper supports two independent backends. Each unlocks one verb:

| Verb     | Backend          | Auth needed              | Review step? |
|----------|------------------|--------------------------|--------------|
| `draft`  | Gmail API (HTTPS) | OAuth (Desktop client)   | yes — in Gmail |
| `send`   | SMTP (smtp.gmail.com:465) | App password    | no |

Both modes accept `--attach FILE` (repeatable) for real attachments, plus
`--to`, `--cc`, `--bcc`, `--subject`, `--body`, `--body-file`, `--html`,
`--sender`.

## Setup — interactive

```bash
gmail-helper wizard
```

The wizard:

1. shows the current status,
2. asks which path you want (OAuth Drafts / SMTP Send / Both),
3. walks you through the Google account/console steps,
4. installs Python libs (for OAuth) on demand,
5. saves credentials to the right files,
6. runs a self-test (test mail or test draft).

Run it from a real terminal — it needs interactive input.
For non-interactive environments, see the next section.

## Setup — non-interactive

### (A) SMTP send — fastest

1. Enable 2-Step Verification on your Google account
   — <https://myaccount.google.com/security>
2. Create an App Password (name it `gmail-helper`)
   — <https://myaccount.google.com/apppasswords>
   You will get 16 lowercase characters in 4 groups of 4.
3. Append to `~/.config/corvin-voice/service.env` (mode 600):
   ```
   GMAIL_USER=you@gmail.com
   GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
   ```
4. Test:
   ```bash
   gmail-helper send --to you@gmail.com --subject Test --body Hi --attach /etc/hostname
   ```

### (B) OAuth draft — recommended

Drafts let you review the mail in Gmail before sending. Set this up once and
the helper handles token refresh automatically.

1. Install the Google API client libraries:
   ```bash
   gmail-helper install-libs
   ```
   (this runs `pip install --user --break-system-packages google-auth
   google-auth-oauthlib google-api-python-client`).
2. Open <https://console.cloud.google.com/>
3. Create or pick a project.
4. *APIs & Services* → *Library* → enable **Gmail API**.
5. *APIs & Services* → *OAuth consent screen* → external, add your address as
   a test user.
6. *APIs & Services* → *Credentials* → *Create credentials* → *OAuth client
   ID* → application type **Desktop app** → download the JSON.
7. Save the JSON as:
   ```
   ~/.config/corvin-voice/google/credentials.json
   ```
8. Run the OAuth flow once:
   ```bash
   gmail-helper auth
   ```
   A browser window opens. Approve the `gmail.compose` scope. The helper
   writes a refresh token to `~/.config/corvin-voice/google/token.json`
   (mode 600).
9. Test:
   ```bash
   gmail-helper draft --to you@gmail.com --subject Test --body Hi --attach /etc/hostname
   ```
   Open Gmail → *Drafts* → review → *Send*.

## Files & paths

| Path | Mode | What it is |
|------|------|------------|
| `~/.config/corvin-voice/service.env` | 600 | `GMAIL_USER`, `GMAIL_APP_PASSWORD` (SMTP) |
| `~/.config/corvin-voice/google/credentials.json` | 600 | OAuth client JSON from Google Cloud Console |
| `~/.config/corvin-voice/google/token.json` | 600 | Refresh token, written by `auth` |

The OAuth scope is `gmail.compose` — drafts and sends only, no read access.

## Use from a cowork persona

`gmail-helper` is in `$PATH` (symlinked into `~/.local/bin/` by the cowork
plugin). Every persona that has `Bash(gmail-helper:*)` in its allow-list can
shell out to it. After this update that includes
`assistant`, `coder`, `browser`, `research`, `inbox`, `homeassistant` —
i.e. every persona in the bundle.

Typical persona prompt fragment:
> Compose without attachments → `mcp__claude_ai_Gmail__create_draft`.
> Compose **with** attachments → `gmail-helper draft …`.
> Send immediately (skip review) → `gmail-helper send …`.
> If `gmail-helper draft` errors with "credentials.json missing" or "libs
> missing", tell the user to run `gmail-helper wizard` once.

## Troubleshooting

* **`gmail-helper: SMTP send needs GMAIL_USER + GMAIL_APP_PASSWORD`**
  → run `gmail-helper wizard` and pick option 2, or write the keys yourself.
* **`gmail-helper: Gmail API libs missing`**
  → `gmail-helper install-libs`.
* **`gmail-helper: ~/.config/corvin-voice/google/credentials.json missing`**
  → follow OAuth setup steps above (the JSON has to come from your own Google
  Cloud project; there is no way to skip this).
* **`smtplib.SMTPAuthenticationError`**
  → you typed your account password, not an App Password. Generate a fresh
  App Password and replace `GMAIL_APP_PASSWORD`.
* **OAuth flow opens but fails with `access_denied`**
  → the OAuth consent screen is in test mode and your address is not on the
  test-user list. Add it under *OAuth consent screen* → *Test users*.

## Why two modes?

* **Drafts (recommended)** are reviewable. The helper hands you a Draft in
  Gmail; nothing leaves your machine until you press Send. Token-based, no
  password lying around.
* **SMTP send** is the no-fuss path: stdlib only, one-line setup with an App
  Password, fires immediately. Use it for log shipping or when you really
  trust the body the agent wrote.

If both are configured, persona prompts default to `draft`. Personas only
fall back to `send` when the user says "send now" / "schick raus" /
"nicht als Entwurf".
