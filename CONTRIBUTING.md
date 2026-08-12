# Contributing to Corvin

Thank you for considering a contribution! Corvin is an opinionated
agent runtime, and outside contributions are very welcome. This
document explains the process.

## TL;DR

1. Fork [`github.com/CorvinLabs/CorvinOS`](https://github.com/CorvinLabs/CorvinOS), branch off `main`.
2. Make your change. Run the test suite (`bash operator/bridges/run-all-tests.sh`).
3. Open a pull request. **Add a comment:** `I have read CLA.md and accept its terms.`
4. A maintainer checks [`CLA-SIGNATORIES.md`](CLA-SIGNATORIES.md) and merges.

No bot, no signing ceremony — opening the PR is the acceptance. Adding
`Signed-off-by:` to your commits (`git commit -s`) is the cleanest
audit trail and is encouraged for security-sensitive code.

## License + CLA

Corvin is licensed under the [Apache License, Version 2.0](LICENSE).

[`CLA.md`](CLA.md) is a short Contributor License Agreement that
mirrors the Apache Software Foundation ICLA. Two load-bearing clauses:

- **§2 (Outbound License):** your contribution is licensed under
  Apache-2.0 — the same terms as the project itself.
- **§3 (Relicense Right):** you additionally grant the Maintainer the
  non-exclusive right to relicense your contribution under any future
  OSI-approved or source-available license for *future* releases.
  Apache-2.0 versions stay Apache-2.0 forever.

§3 is the optionality clause that lets Corvin adapt to future
market conditions without coordinating with every past contributor.

### How to accept

**Explicit methods (preferred):**

- Leave a comment on your PR or any issue containing:
  ```
  I have read CLA.md and accept its terms.
  ```
- Add `Signed-off-by: Your Name <email>` to your commits (`git commit -s`).
- Send the acceptance sentence to `silvio.jurk@gmail.com`.

**Implicit methods (also binding):**

- **Opening a PR** against [`github.com/CorvinLabs/CorvinOS`](https://github.com/CorvinLabs/CorvinOS) — provided `CLA.md` was present at the time the PR was opened and the PR body does not contain an explicit opt-out.
- **Pushing commits directly** to any branch (write-access contributors) — provided `CLA.md` was present in the default branch at the time of the push.

See §6 of [`CLA.md`](CLA.md) for the full text and conditions for both methods.

**All contributors must appear in [`CLA-SIGNATORIES.md`](CLA-SIGNATORIES.md)**
before their code is merged. The Maintainer adds the entry at merge time.

For company contributors (employee work, contractor work): a
[Corporate CLA (`CCLA.md`)](CCLA.md) **must be on file before the
first merge of any contribution from that company**. Individual
contributors from that company must not be added to SIGNATORIES — and
their PRs must not be merged — until the CCLA from the employer is
received and countersigned. This is a hard pre-merge block, not a
post-merge follow-up. Absent CCLA = employer's IP waiver missing for §3.

See [`CLA.md`](CLA.md) for the full text and plain-language explanation.

## Trademark

"Corvin" is the project identifier of the copyright holder. The
Apache License explicitly does not grant trademark rights (§ 6). Forks
under a different name are permitted and welcome; forks marketed under
the "Corvin" name are not.

## Code style

- **Python**: follow the existing style of the file you're editing.
  No new formatter or linter wars — match the surroundings.
- **JavaScript** (bridge daemons): same — match `bridges/<channel>/daemon.js`
  conventions.
- **Comments**: only when the *why* is non-obvious. The codebase
  prefers no comments over wrong/stale comments. See `CLAUDE.md`
  for the full doc-as-DoD discipline.

## Tests

The repo follows **LDD (Loss-Driven Development)** with a hard
per-subtask E2E rule. Every feature ships with one E2E that exercises
the new behaviour end-to-end (real subprocess for MCP, real filesystem
for run-workspaces, real `bwrap` for sandbox isolation).

Run the full suite:

```bash
bash operator/bridges/run-all-tests.sh
```

Mocks are accepted only where a network resource is the sole external
dependency. Security-touching changes (forge, policy, audit, path-gate)
require a real-E2E.

## Commit messages

Follow the existing repo style — terse, factual, lowercase verb prefix
(`feat`, `fix`, `chore`, `docs`, `refactor`):

```
feat(layer16): consent gate phase 4 + voice-summary truthfulness check
fix(adapter): test-isolation + periodic cleanup_terminated
docs(readme): clarify license terms
```

For provenance on security-sensitive contributions, `git commit -s`
adds a `Signed-off-by:` trailer per the [Developer Certificate of
Origin](https://developercertificate.org/). Not enforced, recommended
for forge / skill-forge / audit-chain / sandbox / policy changes.

## ADR (Architectural Decision Record) Requirements

Every non-trivial code change to `core/` **must be documented in an ADR**.

This is **enforced by:**
- ✓ Pre-commit hook (blocks local commits)
- ✓ CI/CD gate (blocks PR merge)
- ✓ Code review checklist (blocks approval)

### When you need an ADR

**✅ YES — Create an ADR if:**
- New module or package
- New public API, endpoint, CLI command, or plugin type
- Change to compliance/security/audit machinery
- New layer-level contract or cross-module coupling
- Feature that gates major behavior

**❌ NO — Skip ADR if:**
- Bug fix with no behavior change outside the bug
- Pure refactor (same behavior, different code)
- Test or fixture changes
- Documentation-only changes
- Config parameter tuning (thresholds, timeouts)

**⚠️ MAYBE — Ask if:**
- New feature flag (if it gates complex behavior: YES; if cosmetic: NO)
- Optimization (ask: does it change the contract? if yes: ADR needed)

### How to write an ADR

1. Create file `Corvin-ADR/decisions/ADR-XXXX-short-title.md`
2. Start with ADR-0264 frontmatter:
   ```yaml
   ---
   id: ADR-XXXX
   status: PROPOSED
   depends_on: [ADR-YYYY]  # if any
   relates_to: []
   paths:
     - core/module/file.py
   docs:
     - docs/claude-ref/layer-NN-*.md
   ---
   ```
3. Follow the ADR structure (Rationale, Alternatives, Decision, Consequences)
4. Commit code + ADR together

**Example:**
```bash
git add core/newfeature/ Corvin-ADR/decisions/ADR-0320-newfeature.md
git commit -m "feat(core): add new feature (ADR-0320)

Implements the design from ADR-0320.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

### Exception workflow

If you're certain no ADR is needed, add a skip flag to the commit message:

```bash
git commit -m "fix(core): urgent hotfix [skip-adr-check: one-line security patch, no structural change]"
```

Valid skip reasons (document in commit message):
- `[test-only]` — test/fixture changes only
- `[docs-only]` — documentation changes only
- `[skip-adr-check: reason]` — rare cases (be explicit)

The CI/CD gate will still validate this; if you mark it wrong, you'll be caught in PR review.

## Pull request checklist

- [ ] Tests pass locally (`run-all-tests.sh`).
- [ ] If behaviour, public API, CLI flags, config shape, defaults, or
  error messages changed: documentation in `CLAUDE.md` and/or `docs/`
  updated in the SAME commit (`docs-as-definition-of-done` discipline).
- [ ] **ADR status:** If this PR changes `core/` code, verify:
  - [ ] An ADR exists (or skip reason documented)
  - [ ] ADR.paths matches the changed files
  - [ ] ADR.commits field lists this PR's commits
  - [ ] ADR title accurately describes this change
- [ ] If structural daemon code changed: PR description includes the
  line *"Requires `bash operator/bridges/bridge.sh restart` to
  take effect."*

## Reporting security issues

Do **not** open a public issue for security vulnerabilities. See
[`SECURITY.md`](SECURITY.md) for the full disclosure policy and
contact address. You will get a response within 72 hours.
