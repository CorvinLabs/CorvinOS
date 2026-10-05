"""Adversarial Review phase for Layer Forge (M4, ADR-2227).

REVIEW runs after ENFORCE and before CREATE. An LLM argues against accepting the
layer definition, identifying scope creep, security gaps, untested complexity, and
other risks. A FLAGGED verdict does not auto-reject, but marks the definition as
flagged in the registry; promotion to accepted requires operator override
(--force-review-flagged).

Fail-closed: LLM errors → ERROR verdict (review blocks create, never auto-passes).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .enforcement import EnforcementVerdict


class ReviewFlag(Enum):
    """Flags that an adversarial reviewer may raise."""
    SCOPE_CREEP = "scope_creep"  # Targets too many layers
    SECURITY_GAP = "security_gap"  # Missing enforcement for sensitive operations
    UNTESTED_COMPLEXITY = "untested_complexity"  # Complex logic without adequate gates
    COMPLIANCE_RISK = "compliance_risk"  # GDPR/audit-chain concerns
    DEPENDENCY_DEBT = "dependency_debt"  # Heavy dependency chain
    HOST_ASYMMETRY = "host_asymmetry"  # Source/runtime mismatch risk
    UNKNOWN_RISK = "unknown_risk"  # Unspecified concerns


@dataclass(frozen=True)
class ReviewVerdict:
    """Result of adversarial review phase."""
    status: str  # PASS | FLAGGED | ERROR
    flags: list[ReviewFlag] = None  # List of raised flags (FLAGGED only)
    reason: str = ""  # Explanation (metadata only, never in audit payload)

    def __post_init__(self):
        if self.flags is None:
            object.__setattr__(self, 'flags', [])

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "flags": [f.value for f in self.flags],
            "reason": self.reason,
        }


class ReviewLLMError(RuntimeError):
    """LLM call failed or returned invalid output."""
    pass


def review_layer_definition(
    manifest: dict,
    enforcement_verdicts: list[EnforcementVerdict],
) -> ReviewVerdict:
    """Run adversarial review phase.

    Args:
        manifest: The layer-definition manifest
        enforcement_verdicts: Verdicts from the ENFORCE phase

    Returns:
        ReviewVerdict with status (PASS | FLAGGED | ERROR) and optional flags.

    Fail-closed: any LLM error → ERROR verdict (blocks create).
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return ReviewVerdict("ERROR", reason="anthropic SDK not installed")

    try:
        client = Anthropic()
        flags = _adversarial_review(client, manifest, enforcement_verdicts)

        if not flags:
            return ReviewVerdict("PASS", flags=[], reason="No adversarial concerns")

        return ReviewVerdict("FLAGGED", flags=flags, reason=f"Raised {len(flags)} concern(s)")

    except ReviewLLMError as e:
        return ReviewVerdict("ERROR", reason=f"Review LLM error: {str(e)[:100]}")
    except Exception as e:
        return ReviewVerdict("ERROR", reason=f"Unexpected review error: {type(e).__name__}")


def _adversarial_review(
    client: object,  # Anthropic client
    manifest: dict,
    enforcement_verdicts: list[EnforcementVerdict],
) -> list[ReviewFlag]:
    """Call LLM with adversarial prompt; parse response to extract flags.

    Returns list of ReviewFlags (empty = PASS).
    Raises ReviewLLMError on LLM failures.
    """
    targets = manifest.get("targets", [])
    target_layer_ids = [t.get("layer_id") for t in targets]
    gates = manifest.get("quality_gates", [])
    rules = manifest.get("enforcement_rules", [])
    dependencies = manifest.get("dependencies", [])

    # Build enforcement summary (verdicts only, not details)
    enforcement_summary = "\n".join([
        f"- {v.rule_id}: {v.status}"
        for v in enforcement_verdicts
    ])

    prompt = f"""You are an adversarial reviewer of a software layer definition. Your job is to argue AGAINST accepting this definition—to find risks, scope creep, security gaps, and untested complexity.

Layer Definition Summary:
- Targets: {', '.join(target_layer_ids)} ({len(targets)} layers)
- Quality Gates: {len(gates)} gates
- Enforcement Rules: {len(rules)} rules
- Dependencies: {len(dependencies)} dependencies
- Enforcement Check Results:
{enforcement_summary}

Answer ONLY with a JSON list of concerns. Each concern MUST be one of these flags:
- "scope_creep" — targets too many layers or has unclear boundaries
- "security_gap" — missing enforcement for sensitive operations
- "untested_complexity" — complex logic without adequate quality gates
- "compliance_risk" — GDPR/audit-chain concerns
- "dependency_debt" — heavy or circular dependency chain
- "host_asymmetry" — source/runtime mismatch risk
- "unknown_risk" — unspecified concerns

If you find no concerns, return: []

Example response: ["scope_creep", "untested_complexity"]

Your response:"""

    try:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise ReviewLLMError(f"LLM call failed: {e}") from e

    try:
        import json
        text = response.content[0].text if response.content else ""
        text = text.strip()
        if not text:
            raise ReviewLLMError("LLM returned empty response")

        # Try to extract JSON from the response (may be wrapped in markdown)
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        flag_strs = json.loads(text)
        if not isinstance(flag_strs, list):
            raise ReviewLLMError(f"Expected list, got {type(flag_strs).__name__}")

        flags = []
        for flag_str in flag_strs:
            try:
                flags.append(ReviewFlag(flag_str))
            except ValueError:
                # Invalid flag string — skip it (fail-closed: unknown flag is not raised)
                pass

        return flags

    except json.JSONDecodeError as e:
        raise ReviewLLMError(f"LLM response is not valid JSON: {e}") from e
    except Exception as e:
        raise ReviewLLMError(f"Failed to parse LLM response: {e}") from e
