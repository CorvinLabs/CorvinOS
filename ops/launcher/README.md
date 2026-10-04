# corvin-launcher

Thin CLI launcher for [CorvinOS](https://github.com/CorvinLabs/CorvinOS). It ships as part of
CorvinOS and is installed together with it.

## Install

CorvinOS is installed only from a local clone of the repository (no `curl … | sh` /
`irm … | iex` one-liner and no PyPI install — `pip install corvinos` is no longer supported):

```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
cd CorvinOS
./install.sh                                          # Linux / macOS / WSL
.\install.ps1  # Windows, from the checkout
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

**Typical first-run flow** (after `./install.sh` from the checkout):
```bash
corvin start
```

## Requirements

- Python 3.10+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS) or Docker Engine (Linux)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — the AI engine, configured in the console setup wizard
