# Testing + Docs Sync (load-bearing)

## Test Suites (Before Commit)

**Run all suites before committing** changes to `adapter.py`, `daemon.js`, or `shared/js/`:

```bash
bash operator/bridges/run-all-tests.sh
```

This runs:
- **Console tests** (`core/console/tests/`) — Web UI routing, audit, permissions
- **Bridge tests** (`operator/bridges/*/test_*.py`) — Message parsing, session lifecycle, engine spawning
- **Adapter tests** (`operator/bridges/shared/test_*.py`) — Compliance gates, audit chain, data flow
- **E2E tests** (`operator/bridges/e2e/test_*.py`) — Full message → response cycles
- **Playwright tests** (`core/console/web-next/e2e/`) — Frontend browser tests

**Exclusion:** WhatsApp daemon excluded from boot test — verify manually via `bridge.sh restart`.

→ Full test reference: [tests.md](tests.md)

## Test Isolation (load-bearing)

**Tests must never touch live operator state.** This class of bug has struck three
times (bridge settings.json contamination, console env pollution, and the
2026-07-08 wipe: `tests/test_uninstall_windows_autostart.py` ran
`uninstall(purge=True)` with only `corvin_home`/`voice_config` isolated, so the
module-global `_REPO_ROOT / ".corvin"` step deleted the RUNNING bridge's session
state, budgets, and hash-chained audit log — plus the user's
`~/.config/systemd/user/corvin-*.service` units and the Claude Code plugin
cache — on every green test run).

Two structural guards exist; keep both intact:

1. **Injectable destructive roots.** `CorvinInstaller` exposes every root its
   uninstall path deletes from as instance state: `repo_root` (constructor
   param), `corvin_home`, `voice_config`, `systemd_user_dir`,
   `claude_plugins_dir`. Any test that executes installer/uninstaller code MUST
   point ALL of them into a sandbox tmpdir. Never rely on mocking a single
   path helper — audit every `rmtree`/`unlink` the code path can reach.

2. **Repo-root tripwire** (`conftest.py` at repo root). An autouse fixture
   snapshots the protected live roots before each test and FAILS the test if
   any of them disappeared. Detection-only — it never redirects paths, so it
   cannot break legitimate tests. Do not remove or weaken it; extend its
   protected-paths list when new live state locations appear.

## Docs + Diagram Sync (load-bearing)

**Every feature change** — code, config, behavior, API, protocol, CLI, error message —
**must update docs AND diagrams in the same commit.** This is not advisory; it is a hard constraint.

### What Triggers an Update

- ✅ Code change (bug fix, feature, refactor with behavior change)
- ✅ Config change (new setting, default value change)
- ✅ Protocol change (wire format, JSON schema, API endpoint)
- ✅ CLI change (new command, flag rename, help text)
- ✅ Default value change
- ✅ Error message change

### What to Update

| Changed Subsystem | Doc Targets | Diagram Targets |
|---|---|---|
| Layer N implementation | `docs/claude-ref/layer-N-*.md` + top-level doc (`docs/*.md`) that describes it | `docs/diagrams/*.svg` that reference that layer |
| Protocol / wire-format | The protocol reference doc + tutorials + JSON examples | Flow/sequence diagrams (e.g., `a2a-flow.svg`) |
| CLI command or flag | Every doc and diagram that mentions the command | Sequence / flow diagrams that show the CLI step |
| Compliance mechanism | The mechanism's layer doc + `docs/audit-and-compliance.md` + CLAUDE.md compliance table | Compliance pillar SVG |
| New module or file | `docs/claude-ref/` section covering the owning layer | Architecture / module-map SVG |

### Hard Rules (Must NOT do)

1. **Don't defer a doc or diagram update** to a follow-up commit, ticket, or "cleanup later."
   - Update must be in the SAME commit as the code change
   - No exceptions; deferred updates become tech debt

2. **Don't commit a protocol version bump** without updating the JSON examples in docs.
   - Old example + new code = docs debt
   - Test examples against code (e.g., via JSON schema validation in tests)

3. **Don't add or rename a CLI flag** without updating every doc that shows the old form.
   - Search: `grep -r "old-flag" docs/`
   - Update all matches

4. **Don't modify an SVG description text** without also updating the corresponding Markdown.
   - SVGs and docs should describe the same thing
   - If they diverge, future readers get confused

5. **Don't skip the diagram update** because "it's a minor change" — diagrams drift silently.
   - Visual architecture changes need visual updates
   - A stale diagram is worse than no diagram (it misleads)

### How to Find What Needs Updating

```bash
# Find all docs/diagrams that mention a keyword
grep -r "your-keyword" docs/ --include="*.md" --include="*.svg" -l

# Check diagrams directory for relevant files
ls docs/diagrams/

# Check top-level docs
ls docs/*.md
```

### Creating New Diagrams

If no diagram exists yet for a new feature that introduces a visual architecture change:
1. Create a **minimal placeholder SVG** with a text description
2. This signals that the architecture needs illustration
3. Can be refined later (don't let "perfect diagram" delay the commit)

## Docs Structure

| Path | Purpose |
|---|---|
| `docs/` | Top-level docs (architecture, getting started, FAQ) |
| `docs/claude-ref/` | Reference docs per layer + subsystem |
| `docs/diagrams/` | SVG architecture diagrams |
| `docs/issues/` | Beta-tester issue templates (one Markdown per issue) |
| `docs/audit-and-compliance.md` | Compliance architecture + audit events |

## Related

- [layer-summary.md](layer-summary.md) — Layer reference index
- [tests.md](tests.md) — Full test suite reference
- [CLAUDE.md](../../CLAUDE.md) — Main conventions document
