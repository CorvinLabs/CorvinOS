#!/usr/bin/env python3
"""Compliance CI reviewer — called by .github/workflows/compliance-check.yml.

Loads the compliance manifest, determines which rules are relevant to the
PR diff, calls Haiku to review, and outputs structured findings as JSON.

Exit codes:
  0 — all findings are warnings or lower
  1 — at least one critical finding
  2 — setup / manifest error

Environment variables required:
  ANTHROPIC_API_KEY   — Anthropic API key (from GitHub secret)
  PR_DIFF             — the full unified diff of the PR (from git diff)
  CHANGED_FILES       — space-separated list of changed file paths

This script MAY import anthropic — it is a CI tool, not an Corvin module.
The "no import anthropic" AST lint applies only to operator/bridges/shared/*.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure shared module is importable
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from compliance_manifest import run_compliance_check, resolve_manifest_dir  # noqa: E402

# ── Layer → file path pattern mapping ────────────────────────────────────────

_LAYER_PATTERNS: dict[str, list[str]] = {
    "L10":                ["operator/voice/hooks/path_gate"],
    "L16":                ["operator/bridges/shared/audit",
                           "operator/bridges/shared/consent",
                           "operator/bridges/shared/vault"],
    "L19":                ["operator/bridges/shared/disclosure"],
    "L22":                ["operator/bridges/shared/adapter",
                           "operator/bridges/shared/engine"],
    "L23":                ["operator/voice/scripts/stt"],
    "L32":                ["operator/bridges/shared/data_classification"],
    "L34":                ["operator/bridges/shared/data_classification"],
    "L35":                ["operator/bridges/shared/egress_gate"],
    "L36":                ["operator/bridges/shared/erasure"],
    "L37":                ["operator/bridges/shared/audit_sealer"],
    "L38":                ["operator/bridges/shared/remote_trigger",
                           "operator/bridges/shared/a2a_"],
    "L39":                ["operator/bridges/shared/incident_tracker"],
    "ADR-0007":           ["operator/bridges/shared/compliance_zone",
                           "operator/bridges/shared/engine_policy"],
    "compliance-reports": ["core/compliance"],
    "CLAUDE.md":          ["CLAUDE.md"],
    "docs/decisions":     ["docs/decisions/"],
    "compliance":         ["compliance/"],
}


def _affected_layers(changed_files: list[str]) -> set[str]:
    affected: set[str] = set()
    for layer, patterns in _LAYER_PATTERNS.items():
        for pat in patterns:
            if any(pat in f for f in changed_files):
                affected.add(layer)
    return affected


def _rules_for_layers(layers: set[str]) -> list[dict]:
    manifest_dir = resolve_manifest_dir()
    result = run_compliance_check(manifest_dir, verify_sig=False)
    if result.load_error:
        return []

    try:
        import yaml  # type: ignore

        all_rules: list[dict] = []
        for fname in ("eu-ai-act.yaml", "gdpr.yaml"):
            fpath = manifest_dir / fname
            if not fpath.exists():
                continue
            data = yaml.safe_load(fpath.read_text())
            if isinstance(data, dict):
                all_rules.extend(data.get("rules", []))
    except Exception:
        return []

    # Include a rule if any of its implemented_by layers match the affected set
    matched: list[dict] = []
    for rule in all_rules:
        rule_layers = {
            str(e.get("layer", "")) for e in rule.get("implemented_by", [])
        }
        if rule_layers & layers:
            matched.append(rule)

    # Always include rules for docs/decisions and compliance/ changes
    if "docs/decisions" in layers or "compliance" in layers:
        for rule in all_rules:
            if rule not in matched:
                matched.append(rule)

    return matched


def _build_prompt(rules: list[dict], diff: str) -> str:
    rules_block = json.dumps(
        [
            {
                "id": r.get("id"),
                "article": r.get("article"),
                "severity": r.get("severity"),
                "invariants": r.get("invariants", []),
                "forbidden_patterns": r.get("forbidden_patterns", []),
            }
            for r in rules
        ],
        indent=2,
    )

    return f"""You are a compliance reviewer for Corvin, an AI operating system
designed to comply with EU AI Act 2026 and GDPR.

Below are the compliance rules that apply to this pull request, followed by
the PR diff.  Review the diff against each rule's invariants and
forbidden_patterns.  Report ONLY concrete, specific violations.

RULES:
{rules_block}

PR DIFF (unified format):
{diff[:12000]}

Respond with JSON only — no prose outside the JSON block.  Schema:

{{
  "findings": [
    {{
      "rule_id": "<rule id from the rules list>",
      "severity": "critical" | "warning",
      "location": "<file:line if identifiable, else empty string>",
      "rationale": "<one sentence: which invariant or forbidden_pattern is violated and how>",
      "suggestion": "<one sentence: what to change>"
    }}
  ]
}}

If there are no violations, return {{"findings": []}}.
Do not invent violations.  Only flag what is concretely present in the diff."""


def _call_haiku(prompt: str) -> dict:
    import anthropic  # type: ignore

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # Strip markdown code fence if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            l for l in lines if not l.startswith("```")
        ).strip()

    return json.loads(raw)


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # CI-5: also emit the error on STDOUT as structured JSON (not only
        # stderr) so the workflow's Parse step sees an "error" key and fails
        # CLOSED. A silent stderr-only message left the gate green.
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        print(json.dumps({"error": "ANTHROPIC_API_KEY not set", "findings": []}))
        return 2

    diff = os.environ.get("PR_DIFF", "")
    if not diff:
        print("ERROR: PR_DIFF not set", file=sys.stderr)
        print(json.dumps({"error": "PR_DIFF not set", "findings": []}))
        return 2

    changed_raw = os.environ.get("CHANGED_FILES", "")
    changed_files = [f.strip() for f in changed_raw.split() if f.strip()]

    # Check manifest loads cleanly
    manifest_dir = resolve_manifest_dir()
    if not manifest_dir.exists():
        print(
            json.dumps({
                "error": f"compliance/ directory not found at {manifest_dir}",
                "findings": [],
            })
        )
        return 2

    layers = _affected_layers(changed_files)
    if not layers:
        # No compliance-relevant files changed → no findings
        print(json.dumps({"layers_checked": [], "findings": []}))
        return 0

    rules = _rules_for_layers(layers)
    if not rules:
        print(json.dumps({"layers_checked": sorted(layers), "findings": []}))
        return 0

    prompt = _build_prompt(rules, diff)

    try:
        response = _call_haiku(prompt)
    except Exception as exc:
        print(
            json.dumps({
                "error": f"Haiku call failed: {type(exc).__name__}: {exc}",
                "findings": [],
            })
        )
        return 2

    findings = response.get("findings", [])
    output = {
        "layers_checked": sorted(layers),
        "rules_evaluated": len(rules),
        "findings": findings,
    }
    print(json.dumps(output, indent=2))

    critical = [f for f in findings if f.get("severity") == "critical"]
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
