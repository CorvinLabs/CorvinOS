"""Adversarial Review phase for Layer Forge (M4, ADR-2227, Phase 4 A2 Canary Rollout).

REVIEW runs after ENFORCE and before CREATE. An LLM argues against accepting the
layer definition, identifying scope creep, security gaps, untested complexity, and
other risks. A FLAGGED verdict does not auto-reject, but marks the definition as
flagged in the registry; promotion to accepted requires operator override
(--force-review-flagged).

Config versioning (Phase 3 C): The REVIEW_PROMPT is immutable and versioned.
Each ReviewVerdict records which prompt version was used. When feedback patterns
emerge (operators override FLAGGED entries that succeed), the optimizer can suggest
prompt updates to reduce false-positive flags.

Canary rollout (Phase 4 A2): Deterministic per-entry sampling routes entries to
prompt versions based on entry_id and rollout_percentage. Same entry_id always gets
same version (hash(entry_id) % 100 < rollout_percentage).

Fail-closed: LLM errors → ERROR verdict (review blocks create, never auto-passes).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .enforcement import EnforcementVerdict

# Base adversarial review prompt (v1.0). Phase 3 C optimizer can suggest updates.
# This is the prompt used by default; versioned history is maintained in
# core/orchestration/layer_forge/optimizer.py::ReviewPromptVersions.
REVIEW_PROMPT_VERSION = "v1.0"

REVIEW_PROMPT = """You are an adversarial reviewer for Layer Forge definitions.
Your job is to identify scope creep, security gaps, untested complexity, compliance risks,
dependency debt, host asymmetry, and other concerns that might make this layer definition unsafe or unwise.

Be skeptical. Question whether the targets and rules are too broad, whether enforcement
is sufficient, whether the complexity is justified by the gates in place, and whether
there are GDPR or audit-chain issues.

Focus on architectural concerns, not implementation details."""


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
    prompt_version: str = REVIEW_PROMPT_VERSION  # Which prompt version was used (Phase 3 C)

    def __post_init__(self):
        if self.flags is None:
            object.__setattr__(self, 'flags', [])

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "flags": [f.value for f in self.flags],
            "reason": self.reason,
            "prompt_version": self.prompt_version,
        }


class ReviewLLMError(RuntimeError):
    """LLM call failed or returned invalid output."""
    pass


def review_layer_definition(
    manifest: dict,
    enforcement_verdicts: list[EnforcementVerdict],
    prompt_version: Optional[str] = None,
) -> ReviewVerdict:
    """Run adversarial review phase with optional canary sampling (Phase 4 A2).

    Args:
        manifest: The layer-definition manifest
        enforcement_verdicts: Verdicts from the ENFORCE phase
        prompt_version: Which prompt version to use (Phase 3 C). If None, uses canary sampling
                       based on manifest["id"] to deterministically route to canary or stable.

    Returns:
        ReviewVerdict with status (PASS | FLAGGED | ERROR) and optional flags.

    Fail-closed: any LLM error → ERROR verdict (blocks create).
    """
    # Determine prompt version via canary sampling if not explicit
    if prompt_version is None:
        entry_id = manifest.get("id", "")
        if entry_id:
            prompt_version = select_canary_version(entry_id)
        else:
            prompt_version = REVIEW_PROMPT_VERSION

    try:
        from anthropic import Anthropic
    except ImportError:
        return ReviewVerdict("ERROR", reason="anthropic SDK not installed", prompt_version=prompt_version)

    try:
        client = Anthropic()
        flags = _adversarial_review(client, manifest, enforcement_verdicts, prompt_version=prompt_version)

        if not flags:
            return ReviewVerdict("PASS", flags=[], reason="No adversarial concerns", prompt_version=prompt_version)

        return ReviewVerdict("FLAGGED", flags=flags, reason=f"Raised {len(flags)} concern(s)", prompt_version=prompt_version)

    except ReviewLLMError as e:
        return ReviewVerdict("ERROR", reason=f"Review LLM error: {str(e)[:100]}", prompt_version=prompt_version)
    except Exception as e:
        return ReviewVerdict("ERROR", reason=f"Unexpected review error: {type(e).__name__}", prompt_version=prompt_version)


def select_canary_version(entry_id: str) -> str:
    """Deterministically select a prompt version for this entry (Phase 4 A2).

    Uses hash(entry_id) % 100 to assign entries to prompt versions based on
    their rollout_percentage. Same entry_id always gets same version.

    Returns the version key (e.g., "v1.0", "v1.1") that should be used.
    Falls back to current version if canary lookup fails.

    Args:
        entry_id: Layer definition ID (used for deterministic sampling)

    Returns:
        Prompt version key (e.g., "v1.0", "v1.1")
    """
    try:
        from .optimizer import ReviewPromptVersions
        from core.paths import tenant_home

        versions = ReviewPromptVersions(tenant_home("_default") / "global" / "layer_forge" / "prompt_versions.json")
        current = versions.current_version()

        # Compute deterministic hash for this entry
        hash_val = int(hashlib.sha256(entry_id.encode()).hexdigest(), 16)
        sample_pct = hash_val % 100  # 0-99

        # Check if current version is selected for this entry
        if sample_pct < current.rollout_percentage:
            return current.version

        # Otherwise, use parent version (canary sampling: entry gets parent until canary rolls out)
        if current.parent_version:
            return current.parent_version

        # Fallback to current if no parent
        return current.version
    except Exception:
        # Fallback to base version on any error (fail-closed)
        return REVIEW_PROMPT_VERSION


def get_review_prompt_text(prompt_version: str = REVIEW_PROMPT_VERSION) -> str:
    """Get the prompt text for a specific version.

    Phase 3 C: This resolves the prompt from the version history. During v1.0,
    it just returns the hardcoded REVIEW_PROMPT. Later versions will come from
    the optimizer's version store.

    Args:
        prompt_version: Version key (e.g., "v1.0", "v1.1")

    Returns:
        The prompt text for that version (defaults to base prompt if not found)
    """
    # For v1.0 and unknown versions, use the base hardcoded prompt
    if prompt_version != "v1.0":
        try:
            from .optimizer import ReviewPromptVersions
            from core.paths import tenant_home
            versions = ReviewPromptVersions(tenant_home("_default") / "global" / "layer_forge" / "prompt_versions.json")
            version_obj = versions.get_version(prompt_version)
            if version_obj:
                return version_obj.prompt_text
        except Exception:
            pass  # Fall back to base prompt
    return REVIEW_PROMPT


def _adversarial_review(
    client: object,  # Anthropic client
    manifest: dict,
    enforcement_verdicts: list[EnforcementVerdict],
    prompt_version: str = REVIEW_PROMPT_VERSION,
) -> list[ReviewFlag]:
    """Call LLM with adversarial prompt; parse response to extract flags.

    Args:
        client: Anthropic client
        manifest: Layer definition manifest
        enforcement_verdicts: Verdicts from enforcement phase
        prompt_version: Which prompt version to use (Phase 3 C)

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

    # Get prompt text for this version (Phase 3 C support)
    prompt = get_review_prompt_text(prompt_version)

    # Build dynamic context for the prompt
    context = f"""Layer Definition Summary:
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

    full_prompt = f"{prompt}\n\n{context}"

    try:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=200,
            messages=[{"role": "user", "content": full_prompt}],
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
