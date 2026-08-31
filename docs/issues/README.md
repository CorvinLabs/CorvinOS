# Beta-Tester Issue Reporting

This folder contains bug reports submitted by Corvin beta-testers.

## Who this is for

You have been invited as a **beta-tester** for Corvin. Your role is to find
and document bugs — not to fix them. The maintainer (Silvio Jurk) reviews and
fixes all issues.

## Rules

- **Do NOT push to `main`** — branch protection will reject it anyway.
- **Every PR goes to `beta-feedback` branch only**, never to `main`.
- **One bug = one PR** — one Markdown file per pull request, nothing else.
- **No code changes** — only the Markdown file in `docs/issues/` is allowed per PR.
- **No personal data** — no chat transcripts, no real user IDs, no email addresses.

## How to report a bug

```bash
# 1. Make sure you are up-to-date
git fetch origin
git checkout beta-feedback
git pull origin beta-feedback

# 2. Create a branch for your issue
git checkout -b issue/YYYY-MM-DD-short-description
# Example: git checkout -b issue/2026-05-16-voice-crash-on-reset

# 3. Copy the template and fill it out
cp docs/issues/_template.md docs/issues/YYYY-MM-DD_short-description_yourhandle.md
# Example: cp docs/issues/_template.md docs/issues/2026-05-16_voice-crash_maxmustermann.md

# 4. Fill in the template (see _template.md)

# 5. Commit
git add docs/issues/YYYY-MM-DD_...md
git commit -m "issue: short description (yourhandle)"

# 6. Push and open a PR → target: beta-feedback
git push origin issue/YYYY-MM-DD-short-description
```

Then open a Pull Request on GitHub with **base: `beta-feedback`** (not `main`).

## File naming convention

```
YYYY-MM-DD_short-description_github-handle.md
```

Example: `2026-05-16_voice-crash-on-reset_maxmustermann.md`

## Severity levels

| Level | When to use |
|---|---|
| `low` | Cosmetic issue, minor inconvenience |
| `medium` | Feature partially broken, workaround exists |
| `high` | Feature broken, no workaround |
| `critical` | Data loss, security issue, system crash |

For `critical` severity: also ping `@veegee82` directly in the PR comment.

## Questions?

Open a GitHub Discussion or contact the maintainer via the PR.
