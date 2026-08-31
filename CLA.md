# Corvin — Contributor License Agreement (CLA)

**Version 3.1 — 2026-05-27**

Thank you for contributing to Corvin. This document is the
Contributor License Agreement ("Agreement") between you ("Contributor")
and Silvio Jurk ("Maintainer", `silvio.jurk@gmail.com`).

It is intentionally short. The two clauses that matter are §2 (Outbound
License) and §3 (Relicense Right). Everything else mirrors industry-
standard CLAs (Apache Software Foundation ICLA, Google CLA).

By opening a pull request against this repository, by adding a
`Signed-off-by:` trailer to your commits, or by otherwise intentionally
submitting work for inclusion in the Project, you accept this Agreement.

---

## 1. Definitions

**"Contribution"** — any original work of authorship intentionally
submitted for inclusion in the Project, in any form (code, docs,
configuration, tests, etc.).

**"Project"** — the CorvinOS source repository at
`github.com/CorvinLabs/CorvinOS` (formerly `github.com/veegee82/Corvin`
and `github.com/veegee82/corvinOS`; any future successor location)
plus all software it produces.

---

## 2. Outbound License (Apache-2.0)

You license each Contribution to the Maintainer and to every recipient
of the Project under the **Apache License, Version 2.0** (see the
[`LICENSE`](LICENSE) file). The copyright and patent grants of Apache
§§ 2 and 3 apply.

This mirrors Apache § 5 ("inbound = outbound") and would apply even
without this Agreement. It is restated here for clarity.

---

## 3. Relicense Right (the load-bearing clause)

You additionally grant the Maintainer a **perpetual, worldwide,
non-exclusive, royalty-free, irrevocable right** to relicense your
Contribution, in whole or in part, under any of the following:

(a) any license approved by the Open Source Initiative;

(b) any source-available license (Business Source License, Functional
    Source License, Fair Source License, Elastic License v2, or any
    license of comparable structure);

(c) a commercial license offered to third parties who cannot or do not
    wish to comply with the open-source terms.

This right exists so the Maintainer can adapt the Project's license
to future market conditions (e.g. hyperscaler-clone defence,
enterprise procurement requirements) without needing to coordinate
with every past Contributor. **You retain the right to use your own
Contribution under Apache-2.0 indefinitely.** The relicense right is
non-exclusive — it grants additional licensing options to the
Maintainer; it does not strip any rights from you.

A relicense decision does not affect any version of the Project
already published under Apache-2.0; those releases remain Apache-2.0
forever. Only future releases would carry the new license, at the
Maintainer's discretion.

---

## 4. Representations

You represent that:

1. **You are legally entitled to grant the above licenses.** If your
   employer has rights to intellectual property that includes your
   Contributions, you have received permission, your employer has
   waived such rights, or your employer has executed a separate
   Corporate CLA with the Maintainer.

2. **Each Contribution is your original creation** (or, if not, you
   submit it separately marked as third-party work with full source
   and license details).

3. **Your Contribution does not knowingly include patents, trademarks,
   or other restrictions** that would prevent the licenses in §§ 2–3
   from being effective.

---

## 5. No Warranty

You provide Contributions on an "AS IS" basis, without warranties of
any kind, express or implied. The Maintainer is under no obligation
to integrate, distribute, or otherwise use any Contribution.

---

## 6. How to sign

You sign by **any one** of the following acts. Each constitutes an
offer and acceptance (Angebot und Annahme) under § 145 ff. BGB and the
governing law of § 7. One acceptance per contributor covers all current
and future Contributions to this Project.

**Explicit methods (preferred — creates a clear audit trail):**

- **A PR comment or issue comment** containing the exact sentence:
  `I have read CLA.md and accept its terms.`
- **An email** to `silvio.jurk@gmail.com` with subject
  `CLA acceptance — <Your GitHub handle>` and the same sentence in
  the body.
- **Adding `Signed-off-by: Your Name <email>` to your commits.**
  This DCO trailer additionally constitutes CLA acceptance for this
  Project. `git commit -s` adds it automatically.

**Implicit methods (also binding, subject to the conditions below):**

- **Opening a pull request** against this repository, provided that
  `CLA.md` was present in the default branch at the time the PR was
  opened and the PR body does not contain an explicit opt-out.
- **Pushing commits directly to any branch in this repository**
  (write-access contributors), provided that `CLA.md` was present in
  the default branch at the time of the push.

For implicit-method contributions, the Maintainer will request
explicit confirmation (a PR/issue comment or email) before the first
commercial relicense decision that affects the Contributor's code. The
Contributor's continued non-objection after receiving that request
constitutes acceptance of §3 for their prior Contributions.

**Tracking:** The Maintainer records the §6 pre-relicense confirmation
request date and acknowledgment status in the `Notes` column of
`CLA-SIGNATORIES.md` (format: `§6-confirmation-sent: YYYY-MM-DD` and
`§6-confirmed: YYYY-MM-DD` or `pending`). Implicit-method contributors
who have not received a §6 confirmation request must be contacted
before any commercial relicense decision.

All accepted contributors are listed in
[`CLA-SIGNATORIES.md`](CLA-SIGNATORIES.md). If your name is not listed
and you have contributed, please send the acceptance sentence to the
Maintainer's email.

If you wish to revoke a future acceptance, contact the Maintainer at
`silvio.jurk@gmail.com`. Revocation does not retroact on Contributions
already merged — the licenses granted on those Contributions are
irrevocable as stated in §§ 2–3.

---

## 7. Governing law

This Agreement is governed by the laws of the Federal Republic of
Germany, without regard to its conflict-of-laws provisions. The
exclusive place of jurisdiction for any disputes arising from or in
connection with this Agreement is the seat of the Maintainer, to the
extent legally permissible.

---

## Why this CLA exists (plain-language explanation)

Corvin is currently Apache-2.0 for maximal adoption. If in the
future a major cloud provider clones the Project and offers it as a
managed service in competition with the Maintainer's own hosted
offering, the Maintainer may switch the license of future releases
to a source-available form (e.g. BSL with a 2-year Apache-2.0 change
date — the same path Sentry, HashiCorp, and MariaDB MaxScale took).

That move requires the relicense right granted in §3. Without it,
each past Contributor would need to individually agree, which is
not feasible at scale. The §3 grant is the optionality you give the
Maintainer in exchange for the broader stewardship of the Project.

Existing Apache-2.0 releases stay Apache-2.0 forever — your
Contribution under Apache-2.0 cannot be retracted from versions
already published. The relicense right only affects future versions.

---

## Historical versions

- **v3.1 — 2026-05-27** (this file) — Expanded §6: direct-push and
  email acceptance added as explicit triggers; SIGNATORIES tracking
  added; explicit-vs-implicit distinction for German-law clarity.
- **v3.0 — 2026-05-11** — Apache-2.0 + relicense right (§3 added).
- **v2.0 — 2026-05-11** — Apache-2.0 era, no CLA (Apache § 5 only).
- **v1.0 — 2026-05-09** — AGPL-3.0 + Commercial dual-license CLA.
  No contributor signatures were collected under v1.0.
