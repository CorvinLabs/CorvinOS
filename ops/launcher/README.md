# corvin-launcher

Thin CLI launcher for [CorvinOS](https://github.com/CorvinLabs/CorvinOS) — standalone
setup via `pip install corvinos`.

## Install

**Linux / macOS — one-liner:**
```bash
curl -fsSL https://corvin-labs.com/install.sh | bash
```

**Windows — PowerShell:**
```powershell
irm https://corvin-labs.com/install.ps1 | iex
```

**All platforms — pip:**
```bash
pip install corvinos
# or
uv pip install corvinos
```

## Usage

```bash
# One-shot: setup if needed, start gateway, open browser
corvin start

# Or step by step:
corvin setup                         # interactive wizard
corvin setup --yes                   # non-interactive
corvin gateway start                 # start the gateway (foreground)
corvin gateway setup                 # connect Discord / Telegram / Slack / …
corvin open                          # open the web console in your browser
corvin status                        # show running state
```

**Typical first-run flow:**
```bash
pip install corvinos && corvin start
```

## Requirements

- Python 3.10+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS) or Docker Engine (Linux)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — the AI engine, configured in the console setup wizard
