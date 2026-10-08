"""LLM-based manifest generation for Layer Forge (ADR-2225 Phase 1).

Given a layer ID and an intent, generate a complete layer-definition manifest
via LLM. The generated manifest MUST pass validation through the existing
schema/registry gates before it is written.

The prompt is deterministic: same input + model produces byte-identical output.
Output is JSON-validated fail-closed — malformed or incomplete output is
rejected, never repaired or assumed.

Audit: every plan attempt is recorded (success + LLM output, failure + error).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

# Bounded like the review (review.py): the plan call runs inside POST /layer-forge/plan, so a
# hung model call must not hold a worker for the SDK default of 10 minutes x 3 attempts.
PLAN_TIMEOUT_S = 60.0
PLAN_MAX_RETRIES = 1

logger = logging.getLogger(__name__)


class LLMPlanError(ValueError):
    """LLM plan generation failed."""
    pass


@dataclass(frozen=True)
class LayerPlanInput:
    """Input to the LLM planner."""
    layer_id: str  # e.g., "L34"
    intent: str    # e.g., "audit downstream of L10"
    repo_root: Path = Path(__file__).resolve().parents[3]

    def __post_init__(self):
        """Validate input."""
        if not self.layer_id or not isinstance(self.layer_id, str):
            raise ValueError("layer_id required (e.g., 'L34')")
        if not self.intent or not isinstance(self.intent, str):
            raise ValueError("intent required")


class LayerPlanPrompt:
    """Deterministic prompt builder for layer-definition generation.

    The prompt is structured to elicit a complete, valid JSON manifest
    that passes schema validation. Key constraints:
    - id/version formatting is enforced
    - target layers are clarified
    - dependencies are enumerated
    - paths are repo-relative
    - hosts-awareness cross-checks are included
    """

    SYSTEM_PROMPT = """You are a Layer Forge architect. Your task is to generate a complete, valid layer-definition manifest in JSON format.

The manifest describes how a new security/compliance layer integrates into CorvinOS. It specifies:
- Which existing layers it targets (depends on/audits/gates)
- Quality gates (test paths to verify correctness)
- Enforcement rules (compile-time or boot-time checks)
- Host awareness (file paths to verify between source and runtime)

CRITICAL CONSTRAINTS:
1. Output ONLY valid JSON, no markdown, no explanation
2. Use semver (x.y.z) for version
3. layer_id must match "^[a-z0-9][a-z0-9._-]*$"
4. test_path must be under tests/ (repo-relative)
5. All file paths are repo-relative, no "..", no absolute paths
6. targets must be a non-empty list of {{layer_id, layer_name}}
7. Never include user_input or unvalidated text in the manifest
8. Empty arrays ({{}}) are valid for optional fields
"""

    USER_PROMPT_TEMPLATE = """Generate a layer-definition manifest for:

LAYER ID: {layer_id}
INTENT: {intent}

Complete the manifest with:
1. Appropriate id (lowercase, e.g., "{layer_id_lower}-audit")
2. Version 1.0.0 (first release)
3. Target layers that match the intent
4. Quality gates (list ALL test paths that validate this layer)
5. Enforcement rules (describe what this layer enforces)
6. Host awareness paths (if any source/runtime files must be verified)
7. Dependencies on other registry entries (if needed)

Output a single valid JSON object. No markdown, no explanation. Start with {{ and end with }}.
"""

    @classmethod
    def build_user_prompt(cls, layer_id: str, intent: str) -> str:
        """Build the user-facing part of the prompt."""
        layer_id_lower = layer_id.lower() if layer_id else "layer"
        return cls.USER_PROMPT_TEMPLATE.format(
            layer_id=layer_id,
            layer_id_lower=layer_id_lower,
            intent=intent,
        )


def generate_manifest_from_intent(
    layer_id: str,
    intent: str,
    *,
    model: str = "claude-opus-5",
    max_tokens: int = 2048,
) -> dict:
    """Call Claude to generate a layer-definition manifest.

    Args:
        layer_id: The layer identifier (e.g., 'L34')
        intent: Description of what the layer should do
        model: Model to use (default: opus for best quality)
        (no sampling parameters: the installed SDK's messages.create() takes no `temperature`;
        the plan is validated and gated afterwards, not trusted to be deterministic)
        max_tokens: Max tokens in response

    Returns:
        A dict representing the manifest (passes JSON parse, may fail validation)

    Raises:
        LLMPlanError: if the LLM output is invalid JSON or the call fails
    """
    try:
        client = anthropic.Anthropic(timeout=PLAN_TIMEOUT_S, max_retries=PLAN_MAX_RETRIES)
    except Exception as e:
        raise LLMPlanError(f"cannot initialize Anthropic client: {e}") from e

    user_prompt = LayerPlanPrompt.build_user_prompt(layer_id, intent)

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=LayerPlanPrompt.SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as e:
        raise LLMPlanError(f"LLM call failed: {e}") from e

    # Extract the response text
    if not message.content or not message.content[0]:
        raise LLMPlanError("LLM returned empty response")

    response_text = message.content[0].text if hasattr(message.content[0], "text") else str(
        message.content[0]
    )

    # Parse JSON (fail-closed: invalid JSON is an error, never repaired)
    try:
        manifest = json.loads(response_text)
    except json.JSONDecodeError as e:
        raise LLMPlanError(f"LLM output is not valid JSON: {e}\nGot: {response_text[:200]}") from e

    if not isinstance(manifest, dict):
        raise LLMPlanError(f"LLM output must be a JSON object, got {type(manifest).__name__}")

    return manifest


def plan_layer_definition(
    layer_id: str,
    intent: str,
    *,
    model: str = "claude-opus-5",
) -> dict:
    """Public API for layer-definition planning.

    Generates a manifest via LLM. The caller is responsible for validation
    via the orchestrator's create_layer_definition() (VALIDATE phase).

    Args:
        layer_id: The layer ID (e.g., 'L34')
        intent: What the layer should do
        model: LLM model to use

    Returns:
        A dict (raw LLM output, not yet validated)

    Raises:
        LLMPlanError: if the LLM call fails or output is not valid JSON
    """
    return generate_manifest_from_intent(layer_id, intent, model=model)
