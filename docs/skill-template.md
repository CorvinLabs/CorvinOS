# Skill template

Copy the block below into a new `SKILL.md` (or the `body_md` of a `skill_create`
call) and fill it in. See [skill-format.md](skill-format.md) for the full spec.

```markdown
---
# REQUIRED. 1–128 chars, [A-Za-z0-9._] only. Dotted namespace idiomatic.
# Must start with your persona's allowed namespace (e.g. code.*).
name: namespace.short_name

# REQUIRED for forge-managed skills (omit for minimal bundle skills).
# One of: domain | persona-style | repo-context | learned-experience
type: domain

# REQUIRED. One line = the trigger surface. Say WHEN to use + trigger words
# (include DE + EN if it fires in chat). Single-quote if it has : @ # | & * ! [ ] { }
description: 'Use when <situation>. Triggers: <word1>, <word2>, <stichwort>.'

# OPTIONAL. Evidence map backing the skill. {} if none.
claim: {}

# OPTIONAL. Supporting doc/file paths or URLs. [] if none.
references: []
---

# <Skill Title>

## When to use
- Fire when: <concrete trigger>.
- Do NOT use for: <counter-case>.

## What to do
1. <imperative step — the load-bearing rule>.
2. <step>.
3. <step>.

## Constraints / invariants
- <hard limit / must-not / fail-closed rule>.

## Example (optional)
<one short illustrative example>
```

## Linter rules (fail-closed — the skill is rejected otherwise)

- body ≤ 8192 bytes UTF-8
- no `ignore previous instructions` / `you are now` / a line starting `system:` / `<|im_*|>`
- no secrets (`AKIA…`, `ghp_…`, `sk-…`, PEM private keys)
- no permission-escalation phrases (`bypass permissions`, `you may execute`, …)
- keep fenced code < 40 % of the body (else build a Forge tool, not a skill)

Lifecycle data (grades, sha256, scope, created_at) is system-managed in `meta.json` —
do **not** add it to the front-matter.
