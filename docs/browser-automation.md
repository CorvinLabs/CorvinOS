# Browser Automation (ADR-0182)

CorvinOS can drive a real browser — open pages, navigate, fill fields, click
buttons, read results — while **you watch live** and can pause or take over.

## How it works

- **Perception — Set-of-Marks.** Each `observe` returns a numbered list of the
  interactive elements on the page (`[0] textbox: Email`, `[1] button: Sign in`,
  …). The agent acts by index (`click(1)`), not by pixel — robust to layout
  changes and usable by any engine (Claude or the local Hermes).
- **Action.** A Playwright-managed Chromium runs per session (isolated profile,
  sandboxed). The tool surface is `browser.navigate/observe/click/fill/
  fill_secret/read/scroll/back/screenshot`.
- **Live view.** The console **Browser** page streams the driven browser as a
  live image, shows every action in real time, prompts you to approve sensitive
  actions, and has **Pause / Take over**.

## Safety (load-bearing)

- **Egress allowlist** — navigation (and any click that navigates) is validated
  against the tenant policy; redirects are re-checked. Fail-closed.
- **Metadata-only audit** — every action logs host + action + element role; the
  audit trail and action log never contain typed values, passwords, or page text.
- **Never echoes field values** — perception uses element *labels*, never a
  field's current value, so a typed secret/PII can't leak back into the model.
- **Secret vault** — `fill_secret(index, vault_key)` types a secret from the
  vault; the value never enters the model context or any log.
- **Human-in-the-loop** — buy / send / delete / login clicks require your
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

- The human-in-the-loop confirm currently shares the console session with the
  tool driver — the person at the live view is the approver; a separate approver
  channel is future work.
- Sensitivity detection is name-based; an icon-only commit button may not be
  auto-flagged (egress + audit + the live view remain the backstops).
- The browser-extension mode (drive your *own* logged-in browser, ADR-0182 M5)
  is not yet built; today CorvinOS drives its own managed browser.
