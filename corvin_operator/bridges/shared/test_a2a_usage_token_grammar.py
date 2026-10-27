"""Token grammar of the peer-signed ``usage.model_id`` (round-two review W1+W2).

Real ids must pass (a dropped id loses the whole usage block); free text inside a listed family shape must not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_usage  # noqa: E402

_PASS = [
    # ollama tags
    "llama3.2:3b", "qwen2.5-coder:7b", "gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud",
    "kimi-k2:1t-cloud", "phi3:mini", "yi:34b", "llama3.1:8b-instruct-q4_K_M", "mixtral:8x7b", "qwen3:30b-a3b",
    # dated ids with many hyphens
    "gpt-5-codex-2025-09-15", "gpt-4o-audio-preview-2024-12-17", "gpt-5.1-codex-max-preview-2025-11-13",
    "gemini-2.5-flash-lite-preview-06-17", "mistral-large-2411", "gpt-4-0613",
    # unlisted-before families
    "ministral-8b-latest", "pixtral-large-latest", "tinyllama", "zephyr",
    # vendor prefixes
    "x-ai/grok-4", "meta-llama/llama-3.3-70b-instruct", "moonshotai/kimi-k2", "mistralai/mistral-large",
    "openai/gpt-5", "google/gemini-2.5-flash",
    # Bedrock third party
    "amazon.nova-pro-v1:0", "us.amazon.nova-lite-v1:0", "meta.llama3-70b-instruct-v1:0",
    # Claude, current and legacy
    "claude-opus-5-5", "anthropic/claude-sonnet-5-5", "eu.anthropic.claude-sonnet-5-v2:0", "claude-opus-5[1m]",
    "claude-sonnet-5@20250929", "claude-haiku-4-5-20251001", "claude-3-5-sonnet-20241022",
    "anthropic/claude-3.7-sonnet", "claude-3-opus-20240229",
    # the classics
    "gpt-4o", "gpt-5-mini", "o3-mini", "o1-preview", "gemini-2.5-pro", "gemini-1.5-pro-002", "deepseek-r1:14b",
    "llama-3.1-70b-instruct", "command-r-plus", "grok-4",
]
_FAIL = [
    "gpt-anna-schmidt-19800312", "gemini-hans.mueller-19800312", "o1-max-mustermann-hiv",
    "llama-john-smith-diabetes", "qwen-sven-meier-berlin", "claude-opus-5-19800312",
    "claude-opus-5-max-mustermann-1980", "gpt-mustermann", "llama3.2:anna", "gpt-max-1980-05",
    "gpt-4-2025-11-13-2024-12-17",  # two dates
    "gpt-4o-20250101-20250202",
    "gpt-123456789", "gpt-4-123456789012", "gpt-4-1-9-8-0-0-3-1-2",
    "gpt-4o\n", "gpt-4o\x00", "gpt-4\tx", "GPT-4O", "Max-Mustermann", "gpt-Anna",
    "unknownvendor/gpt-4", "x-ai/grok-4/extra", "o-max", "a:b:c", "gpt-" + "4-" * 40,
    "claude-sonnet-5-19800312-v1", "claude-3-5-sonnet-19800312",
]


@pytest.mark.parametrize("mid", _PASS)
def test_real_ids_pass(mid):
    assert a2a_usage.valid_model_id(mid), mid


@pytest.mark.parametrize("mid", _FAIL)
def test_free_text_fails(mid):
    assert not a2a_usage.valid_model_id(mid), mid


def test_federation_writer_agrees():
    from core.federation import audit as fa

    for mid in _PASS:
        assert fa.safe_model_id(mid) == mid
    for mid in _FAIL:
        assert fa.safe_model_id(mid) is None
