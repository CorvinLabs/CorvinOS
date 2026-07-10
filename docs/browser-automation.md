# Browser Automation (ADR-0182)

CorvinOS can drive a real browser — open pages, navigate, fill fields, click
buttons, read results — while **you watch live** and can pause or take over.

## How it works

- **Perception — Set-of-Marks.** Each `observe` returns a numbered list of the
  interactive elements on the page (`[0] textbox: Email`, `[1] button: Sign in`,
  …). The agent acts by index (`click(1)`), not by pixel — robust to layout
  changes and usable by any engine (Claude or the local Hermes).
- **Action.** A Playwright-managed Chromium runs per session (isolated profile,
  sandboxed). The full tool surface is
  `browser.navigate/observe/click/fill/fill_secret/key/select_option/hover/drag/
  upload_file/read/scroll/back/tabs/switch_tab/extract_table/extract_form_schema/
  screenshot`. The autonomous agent, the REST endpoints, and the `browser.*` tool
  schema all expose the same set (kept in sync by a drift test).
- **Submit + navigate the way a person would.** The agent can press **Enter**
  (`key`) to submit a search or form — filling a field never submits it on its
  own — pick native `select` dropdown options, follow a link that opens in a
  **new tab** (it switches automatically), go **back**, and pull a table out as
  structured rows (`extract_table`). Password fields are surfaced as
  `fill_secret` targets so a real login flow works (their typed value is still
  never read back into perception).
- **Live view.** The console **Browser** page streams the driven browser as a
  live image, shows every action in real time, prompts you to approve sensitive
  actions, and has **Pause / Take over**.

## Safety (load-bearing)

- **Egress allowlist, enforced at the network layer** — every request the page
  makes (top-level navigation **and** subresource `fetch`/XHR/image/beacon/
  WebSocket) is validated against the tenant policy and aborted if disallowed,
  not just the address bar. This closes the classic indirect-prompt-injection
  exfil path (an allowlisted page `fetch()`-ing your data to an attacker host).
  Redirects and any click/Enter/select that navigates are re-checked. Fail-closed.
- **SSRF metadata guard** — cloud instance-metadata endpoints (169.254.169.254 &
  the link-local range, `metadata.google.internal`, Alibaba, the IPv6 IMDS) are
  blocked unconditionally — including obfuscated encodings (decimal, hex, octal,
  trailing-dot, IPv4-mapped IPv6) — even for a subresource request and even if a
  tenant allowlist names one.
- **Metadata-only audit** — every action logs host + action + element role; the
  audit trail and action log never contain typed values, passwords, or page text
  (a cross-host confirm shows only the host, never a URL that could carry a token).
- **Never echoes field values** — perception uses element *labels*, never a
  field's current value, so a typed secret/PII can't leak back into the model.
  Accessible names are length-capped and the planner's untrusted-content fence
  uses a per-request nonce, so page text can't break out and inject instructions.
- **Secret vault** — `fill_secret(index, vault_key)` types a secret from the
  vault; the value never enters the model context or any log.
- **Human-in-the-loop** — buy / send / delete / login clicks **and a committing
  Enter/Space or a `select`/`drag` on a payment/credential form** require your
  explicit confirmation in the live view. No confirm channel → the action is
  blocked (fail-closed).
- **Sandbox** — the Chromium renderer sandbox is ON by default (it loads
  untrusted pages). Only disable it on a sandbox-incapable host via
  `CORVIN_BROWSER_NO_SANDBOX=1`.
- **Bounds** — max 8 concurrent browser sessions per tenant; the profile
  (cookies/localStorage) is wiped when the session closes.

## Enable it

```bash
pip install "corvinos[browser]"   # or: uv pip install "corvinos[browser]"
playwright install chromium        # one-time browser download
```

Playwright is imported lazily, so the console runs fine without it — the feature
simply activates once the package + browser are present. Open the console →
**Browser** to use it.

## Egress config (optional)

Restrict which hosts the agent may reach in `tenant.corvin.yaml`:

```yaml
spec:
  browser:
    allowed_hosts: ["example.com", "internal.corp"]   # only these (deny-by-default)
    forbidden_hosts: ["ads.example.net"]              # always blocked
```

No `allowed_hosts` → all hosts allowed (still audited). `forbidden_hosts` always
wins.

## Configuration reference

| Setting | Effect |
|---|---|
| `spec.browser.allowed_hosts` | Egress allowlist (deny-by-default when set) |
| `spec.browser.forbidden_hosts` | Always-blocked hosts |
| env `CORVIN_BROWSER_NO_SANDBOX=1` | Disable the renderer sandbox (constrained hosts only) |

## Known limitations

- A confirm can be approved from the live-view **or** from the main chat
  (`/browser confirm <sid> yes|no`); both resolve the same pending request.
- Sensitivity detection is heuristic (element name + URL path + form context); an
  icon-only commit button on a plain-looking page may not be auto-flagged (the
  network-layer egress guard, the audit trail, and the live view are the
  backstops). A committing Enter/Space and a `select`/`drag` on a
  password/card-bearing form *are* now gated.
- DNS rebinding (an allowlisted hostname whose DNS later resolves to a
  metadata/loopback IP) is not caught by the lexical host check; the network
  route validates the request URL, not the post-resolution IP. Pin hosts you
  care about by IP where this matters.
- The screencast live view renders the real screen, so a secret typed into a
  *non-password* field (e.g. an API-key box) is visible to the operator watching
  it — the live view is owner-only and the value still never reaches the model
  context or any log.
- The browser-extension mode (drive your *own* logged-in browser, ADR-0182 M5)
  is not yet built; today CorvinOS drives its own managed browser.
