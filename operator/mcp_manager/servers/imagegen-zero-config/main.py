"""Zero-config image generation MCP server (ADR-0191).

Tier 0 (default, no configuration): Pollinations.ai — free, no signup, no
key. Best-effort / rate-limited community service, not a contracted API.

Tier 1 (automatic upgrade): OpenAI's Images API, engaged automatically the
moment an OpenAI key is available via the canonical provider_keys resolver
(the SAME key already used for Whisper/TTS) — no separate configuration.

Every prompt is checked against the L44 house-rules acceptable-use gate
BEFORE it reaches either provider. The very first time a prompt actually
goes to Tier 0 for a tenant, a one-time disclosure names the third-party
endpoint involved (see imagegen_disclosure.py) — Tier 1 does not
re-disclose, since BYOK is itself an explicit opt-in.

Registered as an mcp_manager catalog entry (compliance.hosts declared in
mcp-tool.yaml) rather than hardcoded persona JSON — see ADR-0191 and
docs/image-generation-zero-config.md for why this distinction matters.
"""
from __future__ import annotations

import base64
import os
import sys
import urllib.parse
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

_HERE = Path(__file__).resolve().parent
_BRIDGES_SHARED = _HERE.parents[2] / "bridges" / "shared"
if _BRIDGES_SHARED.is_dir() and str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

from spawn_gates import check_l44  # type: ignore  # noqa: E402
from provider_keys import resolve_key  # type: ignore  # noqa: E402
from imagegen_disclosure import ensure_disclosed  # type: ignore  # noqa: E402

POLLINATIONS_HOST = "image.pollinations.ai"
_HTTP_TIMEOUT = 60.0

mcp = FastMCP("corvin-imagegen")


def _tenant_id() -> str:
    """CORVIN_TENANT_ID is not guaranteed to reach an MCP server subprocess
    (see imagegen_disclosure._corvin_home's docstring) — unlike CORVIN_HOME,
    a tenant id can't be recovered by walking the filesystem, so this falls
    back to "_default". Fine for the current single-tenant deployments;
    genuine multi-tenant use needs the env var to actually be threaded
    through the real spawn path, tracked as a follow-up."""
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


class ImageGenRefused(ValueError):
    """Raised when the L44 house-rules gate refuses the prompt. Message text
    is the user-facing refusal string check_l44() already produced — never
    a raw exception/stack trace."""


class Tier0RateLimited(RuntimeError):
    """Raised on a Pollinations 429/503 — never surfaced as a raw HTTP
    stack trace; the message is the ADR-0191-promised friendly explanation
    (per docs/image-generation-zero-config.md §6: Tier 0 has no uptime
    SLA, and that trade-off must be disclosed, not hidden behind a
    generic client-error string)."""


def _generate_pollinations(prompt: str) -> Image:
    url = f"https://{POLLINATIONS_HOST}/prompt/{urllib.parse.quote(prompt)}"
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        try:
            resp = client.get(url, params={"nologo": "true"})
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 503):
                raise Tier0RateLimited(
                    "The free image service (Pollinations) is rate-limited right "
                    "now — it's a best-effort community service with no uptime "
                    "guarantee. Try again in a bit, or add your own OpenAI API "
                    "key for unlimited, higher-quality generation."
                ) from e
            raise
        return Image(data=resp.content, format="png")


def _generate_openai(prompt: str, api_key: str) -> Image:
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        resp = client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
            },
        )
        resp.raise_for_status()
        b64 = resp.json()["data"][0]["b64_json"]
        return Image(data=base64.b64decode(b64), format="png")


@mcp.tool()
def generate_image(prompt: str) -> list:
    """Generate an image from a text prompt.

    Zero-config by default: works immediately with no setup, via a free
    community image service. If you've configured an OpenAI API key (the
    same one used for voice), that's used automatically instead for
    higher quality — no extra configuration needed either way.
    """
    tid = _tenant_id()

    refusal = check_l44(prompt, tid, persona="assistant", engine_id="imagegen_mcp")
    if refusal:
        raise ImageGenRefused(refusal)

    openai_key = resolve_key("openai_api_key")
    if openai_key:
        return [_generate_openai(prompt, openai_key)]

    # Tier 0 (Pollinations): a text content block placed BEFORE the image
    # block is what actually makes the one-time disclosure visible to the
    # end user — FastMCP converts a returned list into multiple content
    # blocks in order, and the calling model relays text content in its
    # own reply, so this is a real disclosure, not just a server log line.
    image = _generate_pollinations(prompt)
    disclosure = ensure_disclosed(tid)
    if disclosure:
        return [disclosure, image]
    return [image]


if __name__ == "__main__":
    mcp.run()
