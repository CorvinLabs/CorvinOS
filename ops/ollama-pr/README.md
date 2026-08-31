# Ollama PR — CorvinOS Integration

Files to be submitted as a Pull Request to `github.com/ollama/ollama`.

## Prerequisites — check before submitting

| # | Prerequisite | Status |
|---|---|---|
| 1 | GitHub issue approved by Ollama maintainer (ADR-0068 M1) | ⏳ not opened yet |
| 2 | `pip install corvinos` works (PyPI publication) | ⏳ not published yet |
| 3 | `https://github.com/CorvinLabs/CorvinOS` is publicly accessible | ⏳ verify |

> **Invoke the skill `corvinos.release.ollama` for exact commands.**

## Files in this directory

```
cmd/launch/corvinos.go                              →  ollama/ollama: cmd/launch/corvinos.go
cmd/launch/corvinos_test.go                         →  ollama/ollama: cmd/launch/corvinos_test.go
docs/integrations/corvinos.mdx                      →  ollama/ollama: docs/integrations/corvinos.mdx
app/ui/app/public/launch-icons/corvinos.svg         →  ollama/ollama: app/ui/app/public/launch-icons/corvinos.svg
github_issue_body.md                                →  post to github.com/ollama/ollama/issues/new
```

## Additional manual changes needed in `ollama/ollama`

**`docs/integrations/index.mdx`** — add under `## Assistants`:
```markdown
- [CorvinOS](/integrations/corvinos)
```

**`docs/docs.json`** — add to Assistants group:
```json
{
  "group": "Assistants",
  "expanded": true,
  "pages": [
    "/integrations/openclaw",
    "/integrations/hermes",
    "/integrations/corvinos"
  ]
}
```

**`cmd/launch/launch.go`** — register in lookup table:
```go
"corvinos": {Name: "corvinos", Runner: &CorvinOS{}},
```

**`cmd/launch/integrations_test.go`** — add to expected integrations list:
```go
"corvinos",
```

## PR commit message

```
integrations: add CorvinOS as an Ollama Assistant

CorvinOS is a privacy-first AI assistant gateway that bridges messaging
platforms (Discord, Telegram, Slack, WhatsApp, Email) to local and cloud
AI models through a GDPR/EU AI Act-compliant layer.

Adds:
- cmd/launch/corvinos.go: pip-based auto-install + gateway start/stop
- cmd/launch/corvinos_test.go: unit tests (binary detection, config paths)
- docs/integrations/corvinos.mdx: integration documentation
- docs/integrations/index.mdx: listing under Assistants
- docs/docs.json: sidebar navigation entry
- app/ui/app/public/launch-icons/corvinos.svg: app icon
```

## Canonical reference

- ADR: `Corvin-ADR/decisions/0068-ollama-official-integration-listing.md`
- Skill: `corvinos.release.ollama` (project scope)
