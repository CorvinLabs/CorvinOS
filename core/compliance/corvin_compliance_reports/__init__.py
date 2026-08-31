"""corvin-compliance-reports — ADR-0017 Phase II PDF reports.

Apache-2.0 plugin that produces regulator-defensible PDF reports from
the Corvin audit chain. Three baseline reports ship in this plugin
and are FREE for every operator:

  - EU AI Act Art. 50 — Active-Disclosure Evidence Report
  - GDPR Art. 30 — Records of Processing Activities (RoPA)
  - Audit-Chain Integrity Attestation

The Enterprise plugin (Phase V, separate repo) layers premium variants
on top (scheduled generation, custom templates, WORM archival), but
the baseline transparency artefacts are never gated.

ADR-0017 baseline rule (`CLAUDE.md` Licensing baseline § "must NOT do"):
> Don't gate compliance-report generation on the license.
> Transparency is a structural feature.
"""
__version__ = "0.1.0"
