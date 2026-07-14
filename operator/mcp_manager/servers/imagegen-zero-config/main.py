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
# unbounded all the way up to the user's screen.
#
# _SAVE_TIMEOUT_S / _TOTAL_TIMEOUT_S bound it, on a plain daemon=True
# threading.Thread — deliberately NOT concurrent.futures.ThreadPoolExecutor.
# operator/voice/scripts/stt/local_whisper.py::_load_model() already hit
# this exact same "bound a blocking call, cross-platform, no signal.alarm on
# Windows" problem and its docstring documents why ThreadPoolExecutor was
# rejected there after a review found two real races in it: (a) releasing a
# lock from a Future done-callback can race the calling thread publishing a
# result, since Future.set_result() notifies waiters BEFORE invoking
# callbacks; (b) ThreadPoolExecutor's process-wide atexit hook joins every
# worker thread ever created, so one stalled call can hang the whole
# long-lived server at shutdown. Both were reason enough to use the same
# plain-Thread pattern here rather than reach for the "obvious" stdlib tool.
# A genuinely cross-package shared helper (operator/bridges/shared/ would be
# the natural home) is a reasonable follow-up, out of scope for this fix.
#
# _run_bounded() below is the ONE local implementation both call sites use
# (they used to be two hand-duplicated copies) — result/exception are always
# published by the WORKER thread itself in `outcome`, in that order, same
# reasoning as local_whisper.py's ordering guarantee above. A still-stuck
# call is ABANDONED (not awaited) past the timeout: daemon=True means it can
# never block process exit, and it holds no lock/resource the rest of the
# server needs, so it's harmless whether it eventually finishes or stays
# stuck forever.
#
# Known limitation (adversarial review, 2026-07-14): FastMCP dispatches a
# sync @mcp.tool() function directly on its own asyncio event loop thread,
# not via an executor — so the outer generate_image() call's t.join() below
# blocks THIS SERVER PROCESS from handling any other concurrent tool call
# for up to _TOTAL_TIMEOUT_S. This is not a new regression: ANY blocking
# call in a sync FastMCP tool already had this property (unbounded, before
# this fix). Bounding it converts "blocks forever" into "blocks at most
# _TOTAL_TIMEOUT_S", which is strictly better, but per-call concurrency
# isolation would need an async tool implementation or a separate worker
# process — tracked as a follow-up, out of scope here.
#
# Second known limitation (adversarial review, 2026-07-14): a still-stuck
# worker thread is ABANDONED, not killed — Python cannot force-terminate a
# thread blocked inside a syscall. That is harmless for any ONE stuck call,
# but if the SAME permanently-broken resource (a stalled network/synced
# drive, a provider that never returns) is retried repeatedly, abandoned
# threads accumulate for the life of this long-running server process. A
# proper fix needs a killable child PROCESS instead of a thread for the
# stuck step — a larger change tracked as a follow-up, not done here.
# _run_bounded (below) at least converts the worst case — the OS refusing
# to create a new thread once the process's thread budget is exhausted —
# into the same clean timeout error the caller already handles, instead of
# an unhandled RuntimeError that would crash this MCP server process.
_SAVE_TIMEOUT_S = 30.0
# Per-PROVIDER hard bound. The reported 240s "hang" (fresh install, 2026-07-14)
# was the Pollinations HTTP call stuck past its own httpx timeout (the socket-
# level edge this module already documents). httpx.timeout can't be trusted to
# interrupt it, so the provider call runs inside its OWN _run_bounded thread with
# this deadline — a stuck free-tier request now degrades to the friendly
# "service unavailable, add an OpenAI key" message in ~75s instead of a generic
# 4-minute timeout the model then blindly retries. Pollinations legitimately
# takes ~15-40s, so 75s still clears a slow-but-working generation.
_PROVIDER_TIMEOUT_S = 75.0
_TOTAL_TIMEOUT_S = 180.0  # ULTIMATE backstop for TRUE infinite hangs, above the
# legitimate worst case: L44 gate ~35s (house_rules qwen3 classifier now runs
# think=False so a cold classify doesn't burn its retry budget) + provider
# _PROVIDER_TIMEOUT_S + save _SAVE_TIMEOUT_S. Down from 240s; the provider bound
# above is what actually makes a stuck generation fail fast.


def _run_bounded(fn, timeout_s: float, thread_name: str):
    """Run ``fn()`` on a background daemon thread; return ``(outcome, timed_out)``.

    ``outcome`` is ``{"ok": value}`` on success or ``{"error": exc}`` if
    ``fn`` raised — never both, and always published by the worker thread
    itself before it returns, so a caller that finds ``timed_out is False``
    can trust ``outcome`` is fully populated (no partial-publish race). See
    the module-level comment above for why this is a plain Thread, not a
    ThreadPoolExecutor.
    """
    outcome: dict = {}

    def _worker() -> None:
        try:
            outcome["ok"] = fn()
        except Exception as exc:  # noqa: BLE001 — captured for the caller to re-raise
            outcome["error"] = exc

    t = threading.Thread(target=_worker, daemon=True, name=thread_name)
    try:
        t.start()
    except RuntimeError:
        # The OS refused to create a new thread — the accumulated-leak worst
        # case documented above (see the module comment). Report it exactly
        # like a hang instead of letting a raw RuntimeError crash the whole
        # MCP server process over one call.
        return {}, True
    t.join(timeout=timeout_s)
    return outcome, t.is_alive()


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

    The ENTIRE body (path/env construction included, not just the mkdir/write)
    is covered by the outer try/except — a prior version moved that setup
    outside the guard, so a malformed CORVIN_IMAGE_OUTDIR value (or any other
    path-construction error) propagated uncaught and turned a cosmetic
    display-persistence failure into a hard failure of the whole
    generate_image() call (adversarial review, 2026-07-14). Persistence is
    best-effort; it must never be able to break the tool, full stop.
    """
    try:
        if not data:
            return None
        import time  # noqa: PLC0415
        import secrets  # noqa: PLC0415
        ext = (fmt or "png").lower().lstrip(".") or "png"
        outdir = os.environ.get("CORVIN_IMAGE_OUTDIR", "").strip()
        base = Path(outdir) if outdir else (Path.cwd() / "outputs")
        fpath = base / f"corvin-image-{int(time.time())}-{secrets.token_hex(3)}.{ext}"

        def _write() -> None:
            base.mkdir(parents=True, exist_ok=True)
            fpath.write_bytes(data)

        outcome, timed_out = _run_bounded(_write, _SAVE_TIMEOUT_S, "imagegen-save")
        if timed_out or "error" in outcome:
            return None
        return str(fpath)
    except Exception:  # noqa: BLE001 — display persistence is best-effort, never fatal
        return None


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
    a raw exception/stack trace. Also (pre-existing) reused for the
    empty/overlong-prompt input-validation refusals below — NOT for a
    timeout; see ImageGenTimeout for that distinct failure mode."""


class ImageGenTimeout(RuntimeError):
    """Raised when generate_image() hits its overall _TOTAL_TIMEOUT_S —
    an infrastructural hang (stalled network drive, provider gone silent),
    never a content-policy decision. Deliberately NOT an ImageGenRefused
    subclass: any current/future code that classifies by exception type
    (e.g. EU AI Act Art. 50 refusal-rate accounting) must not be able to
    mistake "the tool was too slow" for "the prompt was refused."""


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


@mcp.tool()
def generate_image(prompt: str) -> list:
    """Generate an image from a text prompt.

    Zero-config by default: works immediately with no setup, via a free
    community image service. If you've configured an OpenAI API key (the
    same one used for voice), that's used automatically instead for
    higher quality — no extra configuration needed either way.

    Bug report 2026-07-13 (Windows 11): a call hung forever with no result,
    no error, no timeout anywhere in the whole chain (confirmed: neither
    this server's own code nor the console/bridge callers ever bound a
    single turn). _save_image_bytes's write got a targeted fix; this is the
    holistic safety net — if literally anything else in the call graph (a
    provider HTTP client that ignores its own timeout under some edge case,
    a future code change, an unknown OS quirk) ever hangs, the tool still
    returns within _TOTAL_TIMEOUT_S instead of leaving the user staring at
    a spinner indefinitely. See _run_bounded's docstring for why this is a
    plain daemon Thread, not ThreadPoolExecutor.
    """
    outcome, timed_out = _run_bounded(
        lambda: _generate_image_impl(prompt), _TOTAL_TIMEOUT_S, "imagegen-generate",
    )
    if timed_out:
        # A DISTINCT exception type from ImageGenRefused (the L44
        # content-policy refusal) — adversarial review, 2026-07-14: reusing
        # ImageGenRefused for an infrastructural timeout would let any
        # current/future internal code that classifies by exception type
        # (e.g. EU AI Act Art. 50 refusal-rate accounting, which this
        # repo's compliance baseline treats as load-bearing) misclassify a
        # stalled network drive as a content-policy block.
        raise ImageGenTimeout(
            f"Image generation timed out after {int(_TOTAL_TIMEOUT_S)}s without "
            "responding — this can happen if a background component hangs "
            "(e.g. a stalled network drive or synced folder backing your "
            "session directory). Please try again."
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["ok"]


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
            # Same per-provider hard bound as Pollinations below — a stuck
            # OpenAI request degrades (via the except) to the free tier rather
            # than hanging the whole call.
            _ai, _ai_timed = _run_bounded(
                lambda: _generate_openai(prompt, openai_key),
                _PROVIDER_TIMEOUT_S, "imagegen-openai")
            if _ai_timed:
                raise RuntimeError("OpenAI image request timed out")
            if "error" in _ai:
                raise _ai["error"]
            img = _ai["ok"]
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
    # Hard per-provider bound: the free community endpoint can get stuck past its
    # own httpx timeout (documented socket edge), so run it on its own bounded
    # thread and degrade a stuck request to the friendly unavailable message in
    # ~_PROVIDER_TIMEOUT_S instead of waiting out the whole-call backstop.
    _pv, _pv_timed = _run_bounded(
        lambda: _generate_pollinations(prompt), _PROVIDER_TIMEOUT_S, "imagegen-pollinations")
    if _pv_timed:
        raise Tier0RateLimited(_TIER0_UNAVAILABLE_MSG)
    if "error" in _pv:
        raise _pv["error"]
    image = _pv["ok"]
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
