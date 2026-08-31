# CLA Signatories

This file is the canonical record of all contributors who have accepted
[`CLA.md`](CLA.md). It is maintained by the Maintainer (Silvio Jurk).

The Maintainer verifies this list before any commercial relicense decision
that would invoke CLA §3. No PR or push may be merged if the author is
not present here (or if their entry is `pending` and the change is
security-sensitive or load-bearing).

---

## Format

| GitHub handle | Legal name | Date accepted | Method | Notes |
|---|---|---|---|---|
| `@handle` | Real Name | YYYY-MM-DD | explicit / implicit-push / implicit-pr | — |

**Method values:**
- `explicit` — PR/issue comment, email, or `Signed-off-by` trailer
- `implicit-push` — direct push while CLA.md was in default branch
- `implicit-pr` — PR opened while CLA.md was in default branch

---

## Maintainer

The Maintainer (Silvio Jurk) is the copyright owner and CLA licensor.
No separate CLA acceptance is required from the Maintainer for their
own contributions; they are the counterparty, not a signatory.

| GitHub handle | Legal name | Role |
|---|---|---|
| `@veegee82` / `@shumway` | Silvio Jurk | Maintainer / Copyright Owner |

---

## Contributors

| GitHub handle | Legal name | Date accepted | Method | Notes |
|---|---|---|---|---|
| `@MathewRGB` | Mathias Kuhlmey | 2026-06-02 | explicit | Email acceptance received 2026-06-02. ⚠️ All commits use corporate email m.kuhlmey@fm-maschinenbau.de — CCLA from FM Maschinenbau required before commercial relicense decision (§3). See CCLA.md. |

---

---

## CCLA Records

Corporate Contributor License Agreement tracking (CCLA.md §8). Required for any
company whose employees contribute. PRs from employees of `ccla-pending` companies
must not be merged until the countersigned copy is returned.

| Company | Date Received | Date Countersigned | Scope | Status |
|---|---|---|---|---|
| FM Maschinenbau GmbH | — | — | @MathewRGB | `ccla-pending` — CCLA not yet signed; individual CLA accepted 2026-06-02 |

---

## Process for future contributors

1. Every PR and direct-push triggers a check: is the author in this file?
2. If not → Maintainer requests explicit acceptance before merging.
3. Once accepted → add entry here in the same commit that merges the contribution.
4. `Signed-off-by:` trailers are also accepted; add entry as `explicit`.
