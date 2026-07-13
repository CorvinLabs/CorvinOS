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
import threading
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
# Hard byte cap on any provider response relayed as an MCP image block —
# the block is base64-embedded into the tool result, so an unbounded body
# would balloon the calling model's context.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Pollinations carries the prompt in the URL path; beyond a few KB servers
# reply 414/handshake errors instead of a useful message.
_MAX_PROMPT_CHARS = 4000

# Bug report 2026-07-13 (Windows 11): generate_image() hung forever with no
# result. Root cause (confirmed): _save_image_bytes's mkdir/write_bytes had
# no timeout — a try/except cannot interrupt a syscall stuck INSIDE the
# kernel (a stalled OneDrive-synced or network-mapped folder backing the
# session workdir, both common on Windows), and nothing downstream (the
# console's stdout-reading loop, the bridge's subprocess.communicate()) ever
# times out a hanging turn either — so a stuck write here was invisible and
# unbounded all the way up to the user's screen. _SAVE_TIMEOUT_S bounds it.
_SAVE_TIMEOUT_S = 15.0


def _save_image_bytes(data: bytes, fmt: str) -> "str | None":
    """ALSO persist the generated image to a file so the user actually SEES it.

    The MCP ``Image`` block alone only reaches the calling MODEL's context — it
    is never surfaced to the user's chat view. The web console DISPLAYS any new
    image file that appears in the session workdir (it scans ``workdir.rglob('*')``
    after each turn, cwd == workdir), and the messenger bridges attach
    ``./outputs/``. Writing the bytes to ``./outputs/`` (relative to the engine's
    cwd) makes the image show up on BOTH surfaces — closing the ADR-0191 display
    gap where a generation reported "done" but nothing was shown. Best-effort:
    never raises AND never blocks longer than _SAVE_TIMEOUT_S, so a write
    failure OR a stuck filesystem can't break/hang the tool.

    Directory: ``CORVIN_IMAGE_OUTDIR`` if set, else ``./outputs`` under cwd.

    Runs the actual mkdir+write on a background daemon thread with a bounded
    join(): threading.Thread has no cross-platform way to force-kill a stuck
    thread (signal.alarm doesn't exist on Windows), so a still-stuck write is
    ABANDONED rather than awaited — daemon=True guarantees it can never block
    process exit either. That abandoned thread either eventually finishes
    (harmless, its result is just never used) or stays stuck forever on a
    genuinely broken filesystem (also harmless — it holds no lock the rest of
    the server needs). Either way, generate_image() itself is never held
    hostage by it.
    """
    if not data:
        return None
    import time  # noqa: PLC0415
    import secrets  # noqa: PLC0415
    ext = (fmt or "png").lower().lstrip(".") or "png"
    outdir = os.environ.get("CORVIN_IMAGE_OUTDIR", "").strip()
    base = Path(outdir) if outdir else (Path.cwd() / "outputs")
    fpath = base / f"corvin-image-{int(time.time())}-{secrets.token_hex(3)}.{ext}"

    outcome: dict = {}

    def _write() -> None:
        try:
            base.mkdir(parents=True, exist_ok=True)
            fpath.write_bytes(data)
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 — display persistence is best-effort
            outcome["error"] = exc

    t = threading.Thread(target=_write, daemon=True, name="imagegen-save")
    t.start()
    t.join(timeout=_SAVE_TIMEOUT_S)
    if not outcome.get("ok"):
        return None
    return str(fpath)


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


def _detected_image_format(content: bytes) -> str | None:
    """Sniff the real image format from magic bytes. Pollinations serves
    JPEG despite the .png-ish URL scheme (verified live) — declaring the
    wrong format puts a wrong mimeType on the MCP image content block,
    which strict clients refuse to render."""
    if content.startswith(b"\x89PNG"):
        return "png"
    if content.startswith(b"\xff\xd8"):
        return "jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return None


_TIER0_UNAVAILABLE_MSG = (
    "The free image service (Pollinations) is unavailable right now — it's a "
    "best-effort community service with no uptime guarantee. Try again in a "
    "bit, or add your own OpenAI API key for reliable, higher-quality "
    "generation."
)


def _generate_pollinations(prompt: str) -> Image:
    # quote(safe="") — the prompt must stay ONE path segment; the default
    # safe='/' would let a prompt containing slashes span segments.
    url = f"https://{POLLINATIONS_HOST}/prompt/{urllib.parse.quote(prompt, safe='')}"
    # follow_redirects stays False: the prompt travels in the URL path, so a
    # provider redirect would re-send user content to a host L35 never saw
    # declared (the 0.10.25 ping-redirect-leak class). A redirect is treated
    # as "service unavailable" rather than followed.
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
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
            if e.response.status_code >= 300:
                # 3xx (redirect refused, see above) and the remaining 4xx/5xx
                # family degrade to the same friendly message instead of a raw
                # stack trace — the ADR-0191-promised behavior for a community
                # service without an SLA.
                raise Tier0RateLimited(_TIER0_UNAVAILABLE_MSG) from e
            raise
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise Tier0RateLimited(_TIER0_UNAVAILABLE_MSG) from e
        if len(resp.content) > _MAX_IMAGE_BYTES:
            raise Tier0RateLimited(_TIER0_UNAVAILABLE_MSG)
        fmt = _detected_image_format(resp.content)
        if fmt is None:
            # A 200 that isn't an image (e.g. an HTML error page from the
            # community service) must not be relayed as a broken image block.
            raise Tier0RateLimited(
                "The free image service (Pollinations) returned an unexpected "
                "non-image response — it's a best-effort community service with "
                "no uptime guarantee. Try again in a bit, or add your own "
                "OpenAI API key for reliable, higher-quality generation."
            )
        return Image(data=resp.content, format=fmt)


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


# Bug report 2026-07-13 (Windows 11): a generate_image() call hung forever
# with no result, no error, no timeout anywhere in the whole chain
# (confirmed: neither this server's own code nor the console/bridge callers
# ever bound a single turn). _save_image_bytes's write got the targeted fix
# above; this is the holistic safety net -- if literally anything else in
# this function's call graph (a provider HTTP client that ignores its own
# timeout under some edge case, a future code change, an unknown OS quirk)
# ever hangs, the tool still returns within _TOTAL_TIMEOUT_S instead of
# leaving the user staring at a spinner indefinitely. Same daemon-thread +
# bounded-join pattern as _save_image_bytes, for the same reason
# (signal.alarm-based timeouts don't exist on Windows, so this must be
# thread-based to actually be cross-platform).
_TOTAL_TIMEOUT_S = 150.0


@mcp.tool()
def generate_image(prompt: str) -> list:
    """Generate an image from a text prompt.

    Zero-config by default: works immediately with no setup, via a free
    community image service. If you've configured an OpenAI API key (the
    same one used for voice), that's used automatically instead for
    higher quality — no extra configuration needed either way.
    """
    outcome: dict = {}

    def _run() -> None:
        try:
            outcome["value"] = _generate_image_impl(prompt)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's
            # thread below so ImageGenRefused/Tier0RateLimited etc. keep their
            # real type instead of being swallowed here.
            outcome["error"] = exc

    t = threading.Thread(target=_run, daemon=True, name="imagegen-generate")
    t.start()
    t.join(timeout=_TOTAL_TIMEOUT_S)
    if t.is_alive():
        raise ImageGenRefused(
            f"Image generation timed out after {int(_TOTAL_TIMEOUT_S)}s without "
            "responding — this can happen if a background component hangs "
            "(e.g. a stalled network drive or synced folder backing your "
            "session directory). Please try again."
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _generate_image_impl(prompt: str) -> list:
    tid = _tenant_id()

    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageGenRefused("Empty prompt — describe the image you want.")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise ImageGenRefused(
            f"Prompt too long ({len(prompt)} chars, max {_MAX_PROMPT_CHARS}) — "
            "please shorten the description."
        )

    refusal = check_l44(prompt, tid, persona="assistant", engine_id="imagegen_mcp")
    if refusal:
        raise ImageGenRefused(refusal)

    tier1_note: str | None = None
    openai_key = resolve_key("openai_api_key")
    if openai_key:
        try:
            img = _generate_openai(prompt, openai_key)
            blocks: list = []
            saved = _save_image_bytes(getattr(img, "data", None),
                                      getattr(img, "_format", None) or "png")
            if saved:
                blocks.append(
                    f"Generated the image and saved it to `{saved}` — it is shown "
                    "inline in the chat above (no separate window needed)."
                )
            blocks.append(img)
            return blocks
        except Exception:  # noqa: BLE001 — a broken/expired BYOK key must not
            # leave the user WORSE off than having no key at all: degrade to
            # Tier 0 with an explicit note instead of a raw provider error.
            tier1_note = (
                "Note: your configured OpenAI API key failed (expired/invalid/"
                "over quota?) — fell back to the free community image service."
            )

    # Tier 0 (Pollinations): text content blocks placed BEFORE the image
    # block are what actually makes the one-time disclosure visible to the
    # end user — FastMCP converts a returned list into multiple content
    # blocks in order, and the calling model relays text content in its
    # own reply, so this is a real disclosure, not just a server log line.
    # The disclosure store is marked BEFORE the prompt leaves the machine
    # (ADR-0191 Decision 3 — "before a prompt first leaves"), not after the
    # provider call happens to succeed.
    disclosure = ensure_disclosed(tid)
    image = _generate_pollinations(prompt)
    blocks = []
    if disclosure:
        blocks.append(disclosure)
    if tier1_note:
        blocks.append(tier1_note)
    saved = _save_image_bytes(getattr(image, "data", None),
                              getattr(image, "_format", None) or "png")
    if saved:
        blocks.append(
            f"Generated the image and saved it to `{saved}` — it is shown inline "
            "in the chat above (no separate window needed)."
        )
    blocks.append(image)
    return blocks


if __name__ == "__main__":
    mcp.run()
