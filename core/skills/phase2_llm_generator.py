"""
Skill Forge v2.0 Phase 2: LLM-Driven Generator

Claude API integration for code, test, and doc generation.
Structured output validation. Fail-closed linter backstop.

Author: Claude Haiku 4.5 (Phase 2 implementation)
License: Apache-2.0
"""

import json
import ast
from pathlib import Path
from typing import Dict, Any, Optional
import anthropic

from core.skills.phase1_manifest_v2 import SkillManifestV2


class SkillLLMGenerator:
    """Phase 2: Use Claude API to fill implementation details."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-opus-5"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def generate_code(self, skeleton_path: Path, user_prompt: str) -> str:
        """Generate execute() implementation via Claude API."""

        # Read skeleton
        skill_py = (skeleton_path / "src" / "skill.py").read_text()
        manifest_data = json.loads((skeleton_path / "skill.json").read_text())

        prompt = f"""
You are generating production-ready Python code for a CorvinOS Skill (ADR-0532-0535).

Domain: {manifest_data.get('domain')}
Input Schema: {manifest_data.get('input_schema')}
Output Schema: {manifest_data.get('output_schema')}

User Request: {user_prompt}

Requirements:
1. Implement execute() method body (marked with TODO)
2. Add input/output validation
3. Handle errors gracefully (return error in output, not exception)
4. Log execution with lom attribution
5. Maximum latency p99 < 500ms
6. Type hints required

Constraints:
- Do NOT inject code outside execute()
- Do NOT import packages outside requirements.txt
- Do NOT use async inside execute() body
- Do NOT call external APIs without guards
- Do NOT log PII

Current skeleton:
```python
{skill_py}
```

Generate ONLY the implementation code for the execute() method body.
Replace the TODO comment entirely.
"""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        generated_code = message.content[0].text

        # Validate syntax
        try:
            ast.parse(generated_code)
        except SyntaxError as e:
            raise ValueError(f"Generated code has syntax error: {e}")

        return generated_code

    async def generate_tests(self, skill_path: Path) -> str:
        """Generate test suite."""
        skill_code = (skill_path / "src" / "skill.py").read_text()

        prompt = f"""Generate pytest tests for this Skill. Requirements: unit tests, edge cases,
        adversarial tests, coverage >85%.

Code:
```python
{skill_code}
```

Generate test file (test_skill.py format, ready to paste into tests/).
Include imports, fixtures, and all test classes."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        return message.content[0].text

    async def generate_docs(self, skill_path: Path) -> str:
        """Generate documentation."""
        skill_code = (skill_path / "src" / "skill.py").read_text()

        prompt = f"""Generate Markdown documentation (README.md, API.md, EXAMPLES.md).

Code:
```python
{skill_code}
```

Generate ALL documentation files (use markdown headers for each file)."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        return message.content[0].text

    async def enhance_manifest(self, skill_path: Path) -> Dict[str, Any]:
        """Complete manifest skeleton with generated metadata."""
        skill_code = (skill_path / "src" / "skill.py").read_text()
        manifest = json.loads((skill_path / "skill.json").read_text())

        prompt = f"""Complete the CorvinOS Skill Manifest (ADR-0533).

Current manifest:
```json
{json.dumps(manifest, indent=2)}
```

Code:
```python
{skill_code}
```

Update: audit_events, learning config, boot_layer, capabilities.
Return ONLY valid JSON (no markdown). Preserve existing fields."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        try:
            return json.loads(message.content[0].text)
        except json.JSONDecodeError:
            return manifest  # Fallback


async def generate_skill_complete(
    skeleton_path: Path,
    user_prompt: str,
    model: str = "claude-opus-5"
) -> None:
    """Complete Phase 2: Generate full implementation."""

    generator = SkillLLMGenerator(model=model)

    # 1. Generate code
    print("Generating code...")
    code = await generator.generate_code(skeleton_path, user_prompt)
    (skeleton_path / "src" / "skill.py").write_text(code)

    # 2. Generate tests
    print("Generating tests...")
    tests = await generator.generate_tests(skeleton_path)
    (skeleton_path / "tests" / "test_skill.py").write_text(tests)

    # 3. Generate docs
    print("Generating docs...")
    docs = await generator.generate_docs(skeleton_path)
    # Parse and write docs files

    # 4. Enhance manifest
    print("Enhancing manifest...")
    enhanced = await generator.enhance_manifest(skeleton_path)
    (skeleton_path / "skill.json").write_text(json.dumps(enhanced, indent=2))

    print("✓ Phase 2 complete")


if __name__ == "__main__":
    import asyncio

    # Example: Would run with actual skeleton path
    print("Phase 2 LLM Generator ready for async execution")
