"""Tests for the ADR-0191 zero-config image-generation tool: the disclosure
gate, the builtin catalog seeding, and the Tier-0 rate-limit handling.

Not a full adversarial pass (see docs/image-generation-zero-config.md +
Corvin-ADR 0191 for the design) — targeted regression coverage for the bugs
actually found while live-testing this feature in a real chat turn:
  1. seed_builtin must use an absolute, dependency-guaranteed interpreter
     path (sys.executable), not a bare "python3" resolved via PATH.
  2. seed_builtin must declare NO catalog secrets — claude does not resolve
     ${VAR} templates in MCP server env, so declaring one would inject a
     literal, garbage "${OPENAI_API_KEY}" string that looks truthy to
     provider_keys.resolve_key() and always breaks Tier-1 selection.
  3. imagegen_disclosure is one-time-per-tenant and survives a missing
     CORVIN_HOME env var via the on-disk repo-marker fallback.
  4. main.py's Tier 0 path surfaces a friendly message on 429/503, not a
     raw httpx stack trace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_MCP_MANAGER_ROOT = Path(__file__).resolve().parents[1]
if str(_MCP_MANAGER_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_MANAGER_ROOT))

_BRIDGES_SHARED = Path(__file__).resolve().parents[3] / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

_IMAGEGEN_SERVER_DIR = Path(__file__).resolve().parents[1] / "servers" / "imagegen-zero-config"
if str(_IMAGEGEN_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_IMAGEGEN_SERVER_DIR))


# ── seed_builtin ──────────────────────────────────────────────────────────

def test_seed_builtin_uses_absolute_sys_executable_not_bare_python3(monkeypatch, tmp_path):
    """Regression: a bare "python3" command resolves via the SPAWNING
    process's PATH, not this one's — confirmed live to resolve to a system
    interpreter lacking the mcp/httpx dependencies, with the MCP connection
    failing silently (status "failed", no diagnostic surfaced to chat)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog

    result = seed_builtin.ensure_imagegen_zero_config("_default")
    assert result["installed"] is True
    assert result["activated"] is True
    assert result["error"] is None

    entry = catalog.get_tool("_default", "imagegen-zero-config")
    assert entry is not None
    command = entry["runtime"]["command"]
    assert command == sys.executable
    assert Path(command).is_absolute()


def test_seed_builtin_declares_no_secrets(monkeypatch, tmp_path):
    """Regression: declaring OPENAI_API_KEY as a catalog secret makes
    get_active_mcp_servers() inject env={"OPENAI_API_KEY": "${OPENAI_API_KEY}"}
    — claude does not resolve that template, so the literal string lands in
    this server's own env and provider_keys.resolve_key() (which checks
    process env first) treats it as a genuinely-configured key, always
    attempting (and failing) Tier 1 instead of correctly using Tier 0.

    The seeded entry DOES carry a plaintext runtime.env (CORVIN_HOME /
    CORVIN_TENANT_ID passthrough) — the invariant is: no secrets, and no
    unresolved ${VAR} template values."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog, activate

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry = catalog.get_tool("_default", "imagegen-zero-config")
    assert entry["secrets"] == []

    servers = activate.get_active_mcp_servers("_default")
    assert "imagegen-zero-config" in servers
    env = servers["imagegen-zero-config"].get("env") or {}
    assert "OPENAI_API_KEY" not in env
    assert not any("${" in v for v in env.values())
    assert env.get("CORVIN_HOME") == str(tmp_path)
    assert env.get("CORVIN_TENANT_ID") == "_default"


def test_seed_builtin_idempotent(monkeypatch, tmp_path):
    """Calling ensure_ twice (mirrors it running on every boot) must not
    error, duplicate anything, or flip activation state."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog, activate

    r1 = seed_builtin.ensure_imagegen_zero_config("_default")
    assert r1["installed"] is True and r1["activated"] is True
    r2 = seed_builtin.ensure_imagegen_zero_config("_default")
    assert r2["installed"] is True and r2["error"] is None
    assert len(catalog.list_tools("_default")) == 1
    assert "imagegen-zero-config" in activate.get_active_tool_ids("_default")


def test_seed_builtin_respects_user_deactivation(monkeypatch, tmp_path):
    """Adversarial-review finding: the previous unconditional seed re-
    activated the tool on every boot, silently overriding an explicit
    user/operator deactivation of a tool that sends prompts to a third
    party — a non-respectable opt-out is also a compliance problem."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, activate

    seed_builtin.ensure_imagegen_zero_config("_default")
    assert activate.deactivate("_default", "imagegen-zero-config", scope="tenant")
    seed_builtin.ensure_imagegen_zero_config("_default")
    assert "imagegen-zero-config" not in activate.get_active_tool_ids("_default")


def test_seed_builtin_respects_operator_uninstall(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog

    seed_builtin.ensure_imagegen_zero_config("_default")
    assert catalog.remove_tool("_default", "imagegen-zero-config")
    r = seed_builtin.ensure_imagegen_zero_config("_default")
    assert r["installed"] is False
    assert catalog.get_tool("_default", "imagegen-zero-config") is None


def test_seed_builtin_preserves_operator_compliance_edit(monkeypatch, tmp_path):
    """An operator who tightened the entry (e.g. removed api.openai.com from
    hosts) must not get clobbered back to the shipped shape on next boot."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry = catalog.get_tool("_default", "imagegen-zero-config")
    entry["compliance"]["hosts"] = ["image.pollinations.ai"]
    catalog.add_tool("_default", entry)

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry2 = catalog.get_tool("_default", "imagegen-zero-config")
    assert entry2["compliance"]["hosts"] == ["image.pollinations.ai"]


def test_seed_builtin_refreshes_stale_interpreter_path(monkeypatch, tmp_path):
    """Upgrade case: the recorded venv interpreter no longer exists (new
    venv after a version upgrade) — the runtime block must be refreshed to
    the current interpreter without touching activation state."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry = catalog.get_tool("_default", "imagegen-zero-config")
    entry["runtime"]["command"] = str(tmp_path / "gone-venv" / "bin" / "python")
    catalog.add_tool("_default", entry)

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry2 = catalog.get_tool("_default", "imagegen-zero-config")
    assert entry2["runtime"]["command"] == sys.executable


def test_seed_builtin_args_path_is_absolute(monkeypatch, tmp_path):
    """Regression: mcp-tool.yaml's relative "main.py" arg only gets resolved
    via the generic local: installer's runtime.command rewrite, which does
    NOT touch entries inside runtime.args — a relative arg breaks the moment
    the spawning claude process's cwd isn't this tool's own directory."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry = catalog.get_tool("_default", "imagegen-zero-config")
    for arg in entry["runtime"]["args"]:
        assert Path(arg).is_absolute(), f"non-absolute arg would break under a foreign cwd: {arg!r}"


# ── imagegen_disclosure ───────────────────────────────────────────────────

def test_disclosure_fires_exactly_once_per_tenant(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import imagegen_disclosure as d

    assert d.has_disclosed("_default") is False
    first = d.ensure_disclosed("_default")
    assert first == d.DISCLOSURE_TEXT
    assert d.has_disclosed("_default") is True
    second = d.ensure_disclosed("_default")
    assert second is None


def test_disclosure_is_tenant_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import imagegen_disclosure as d

    d.ensure_disclosed("tenant-a")
    assert d.has_disclosed("tenant-a") is True
    assert d.has_disclosed("tenant-b") is False


def test_disclosure_falls_back_to_repo_marker_without_corvin_home(monkeypatch):
    """CORVIN_HOME is not guaranteed to reach an MCP server subprocess
    (verified live) — the on-disk .corvin_repo-marker walk-up must still
    resolve a usable, writable path."""
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    import imagegen_disclosure as d

    home = d._corvin_home()
    assert home.name == ".corvin"
    assert (home.parent / ".corvin_repo").exists() or home.parent == Path.home()


# ── main.py Tier 0 rate-limit handling ────────────────────────────────────

def test_pollinations_429_raises_friendly_message_not_raw_http_error(monkeypatch):
    import httpx
    import main as m

    fake_resp = httpx.Response(
        429, request=httpx.Request("GET", "https://image.pollinations.ai/prompt/x"),
        text="rate limited",
    )
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: fake_resp)

    with pytest.raises(m.Tier0RateLimited) as exc_info:
        m._generate_pollinations("x")
    assert "rate-limited" in str(exc_info.value)
    assert "OpenAI" in str(exc_info.value)


def test_pollinations_500_degrades_to_friendly_message(monkeypatch):
    """502/504/500 are routine for a community CDN — they must surface the
    same friendly no-SLA message as 429/503, not a raw httpx stack trace."""
    import httpx
    import main as m

    fake_resp = httpx.Response(
        500, request=httpx.Request("GET", "https://image.pollinations.ai/prompt/x"),
        text="server error",
    )
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: fake_resp)

    with pytest.raises(m.Tier0RateLimited):
        m._generate_pollinations("x")


def test_pollinations_connect_error_degrades_to_friendly_message(monkeypatch):
    import httpx
    import main as m

    def _boom(self, *a, **k):
        raise httpx.ConnectError("dns down")

    monkeypatch.setattr(httpx.Client, "get", _boom)
    with pytest.raises(m.Tier0RateLimited):
        m._generate_pollinations("x")


def test_pollinations_redirect_not_followed(monkeypatch):
    """The prompt travels in the URL path — a redirect would re-send user
    content to an undeclared host (the 0.10.25 ping-redirect-leak class).
    Redirects must be refused, not followed."""
    import httpx
    import main as m

    fake_resp = httpx.Response(
        302, request=httpx.Request("GET", "https://image.pollinations.ai/prompt/x"),
        headers={"location": "https://evil.example/prompt/x"},
    )
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: fake_resp)
    with pytest.raises(m.Tier0RateLimited):
        m._generate_pollinations("x")


def test_pollinations_non_image_200_rejected(monkeypatch):
    """A 200 whose body is an HTML error page must not be relayed as a
    broken image content block."""
    import httpx
    import main as m

    fake_resp = httpx.Response(
        200, request=httpx.Request("GET", "https://image.pollinations.ai/prompt/x"),
        text="<html>maintenance</html>",
    )
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: fake_resp)
    with pytest.raises(m.Tier0RateLimited):
        m._generate_pollinations("x")


def test_pollinations_mime_matches_actual_bytes(monkeypatch):
    """Regression (live finding): Pollinations serves JPEG; the old code
    stamped format="png" on it, producing a wrong mimeType on the MCP image
    block. The declared format must be sniffed from the real bytes."""
    import httpx
    import main as m

    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    fake_resp = httpx.Response(
        200, request=httpx.Request("GET", "https://image.pollinations.ai/prompt/x"),
        content=jpeg,
    )
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: fake_resp)
    img = m._generate_pollinations("x")
    assert img._format == "jpeg" or getattr(img, "format", None) == "jpeg" or \
        img._mime_type == "image/jpeg"


def test_prompt_stays_single_path_segment():
    """quote(safe='') — a prompt containing '/' must not span URL path
    segments (path-traversal shape / silently altered request)."""
    import urllib.parse
    assert "/" not in urllib.parse.quote("a cat / a dog", safe="")


def test_generate_image_rejects_empty_and_overlong_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import main as m

    with pytest.raises(m.ImageGenRefused):
        m.generate_image("   ")
    with pytest.raises(m.ImageGenRefused):
        m.generate_image("x" * (m._MAX_PROMPT_CHARS + 1))


def test_broken_tier1_key_falls_back_to_tier0(monkeypatch, tmp_path):
    """A configured-but-broken OpenAI key must not leave the user worse off
    than having no key: degrade to Tier 0 with an explanatory note."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import main as m

    monkeypatch.setattr(m, "check_l44", lambda *a, **k: None)
    monkeypatch.setattr(m, "resolve_key", lambda name: "sk-broken")

    def _openai_boom(prompt, key):
        raise RuntimeError("401 invalid key")

    sentinel = object()
    monkeypatch.setattr(m, "_generate_openai", _openai_boom)
    monkeypatch.setattr(m, "_generate_pollinations", lambda prompt: sentinel)
    monkeypatch.setattr(m, "ensure_disclosed", lambda tid: None)

    blocks = m.generate_image("a nice tree")
    assert sentinel in blocks
    assert any(isinstance(b, str) and "fell back" in b for b in blocks)


# ── _save_image_bytes + CORVIN_IMAGE_OUTDIR ────────────────────────────────
# Bug report 2026-07-12: generated images not showing inline in chat. This
# function's own docstring already documents that it relies on implicit
# Path.cwd() inheritance through the claude CLI subprocess when no explicit
# outdir is given -- exactly the class of cross-process assumption this
# server's env vars (CORVIN_HOME/CORVIN_TENANT_ID) already needed an
# explicit workaround for. get_active_mcp_servers() now sets
# CORVIN_IMAGE_OUTDIR explicitly (see operator/mcp_manager/tests/
# test_mcp_m4.py::TestImageOutdirInjection) -- these tests prove the WRITE
# side actually honours it, closing the round trip end to end.

def test_save_image_bytes_honours_corvin_image_outdir(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    explicit_outdir = tmp_path / "session-workdir" / "outputs"
    monkeypatch.setenv("CORVIN_IMAGE_OUTDIR", str(explicit_outdir))
    import main as m

    saved = m._save_image_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes", "jpeg")
    assert saved is not None
    saved_path = Path(saved)
    assert saved_path.parent == explicit_outdir
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def test_save_image_bytes_falls_back_to_cwd_outputs_without_env(monkeypatch, tmp_path):
    """Regression guard: the pre-existing cwd-relative behavior must survive
    unchanged for callers that don't set the new env var (e.g. the messenger
    bridges before this fix's rollout reaches them, or a future MCP host
    that doesn't wire it)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_IMAGE_OUTDIR", raising=False)
    monkeypatch.chdir(tmp_path)
    import main as m

    saved = m._save_image_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes", "jpeg")
    assert saved is not None
    assert Path(saved).parent == tmp_path / "outputs"


def test_save_image_bytes_produces_a_file_chat_runtime_classifies_as_image(monkeypatch, tmp_path):
    """The other half of the round trip: chat_runtime.py's post-turn scan
    calls _artifact_mime() on every new file under the session workdir. This
    proves a file _save_image_bytes actually writes (under CORVIN_IMAGE_OUTDIR
    pointed at a session workdir's outputs/, exactly what the new
    get_active_mcp_servers(image_outdir=...) wiring provides) is classified
    as image/jpeg by that exact function -- not a synthetic path, the real
    saved file."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    workdir = tmp_path / "session-workdir"
    monkeypatch.setenv("CORVIN_IMAGE_OUTDIR", str(workdir / "outputs"))
    import main as m

    saved = m._save_image_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes", "jpeg")
    assert saved is not None

    _console_root = Path(__file__).resolve().parents[3] / "core" / "console"
    if str(_console_root) not in sys.path:
        sys.path.insert(0, str(_console_root))
    from corvin_console import chat_runtime

    mime = chat_runtime._artifact_mime(Path(saved))
    assert mime == "image/jpeg"


def test_disclosure_survives_readonly_store(monkeypatch, tmp_path):
    """ensure_disclosed's 'never raises' contract: a storage failure must
    degrade to 'shown again next time', not fail the tool call after the
    image was already generated."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import imagegen_disclosure as d

    target = tmp_path / "tenants" / "_default" / "global"
    target.mkdir(parents=True)
    target.chmod(0o500)
    try:
        text = d.ensure_disclosed("_default")
        assert text == d.DISCLOSURE_TEXT
    finally:
        target.chmod(0o700)


def test_disclosure_rejects_path_injection_tenant(monkeypatch, tmp_path):
    """Tenant ids are path components — a crafted value must fall back to
    _default instead of escaping the tenants dir (or crashing on ':' under
    Windows)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import imagegen_disclosure as d

    p = d._store_path("../../../etc")
    assert "tenants/_default" in str(p).replace("\\", "/")


# ── Bounded timeouts (bug report 2026-07-13: hangs forever on Windows) ─────
# Root cause confirmed by investigation: _save_image_bytes's mkdir/write_bytes
# had no timeout (a try/except cannot interrupt a syscall stuck inside the
# kernel -- e.g. a stalled OneDrive-synced or network-mapped folder backing
# the session workdir, both common on Windows), and NOTHING downstream (the
# console's stdout-reading loop, the bridge's subprocess.communicate()) ever
# times out a hanging turn either. These tests simulate a genuinely stuck
# write / a stuck implementation and prove the tool now returns within a
# bounded time instead of hanging -- using real threading.Thread.join()
# timeouts, not just asserting the constant exists.

def test_save_image_bytes_abandons_a_stuck_write_instead_of_hanging(monkeypatch, tmp_path):
    import time as _time
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_IMAGE_OUTDIR", str(tmp_path / "outputs"))
    import main as m

    monkeypatch.setattr(m, "_SAVE_TIMEOUT_S", 0.3)

    real_mkdir = Path.mkdir

    def _stuck_mkdir(self, *a, **k):
        _time.sleep(5)  # simulate a hung/offline synced or mapped drive
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", _stuck_mkdir)

    t0 = _time.monotonic()
    result = m._save_image_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes", "jpeg")
    elapsed = _time.monotonic() - t0

    assert result is None, "a stuck write must degrade to 'not saved', not hang"
    assert elapsed < 2.0, (
        f"_save_image_bytes must return promptly after _SAVE_TIMEOUT_S "
        f"(0.3s) even though the write itself takes 5s -- took {elapsed:.2f}s"
    )


def test_generate_image_returns_timeout_error_instead_of_hanging_forever(monkeypatch, tmp_path):
    """The holistic safety net: even if something OTHER than the save step
    hangs (a future bug, an unknown OS quirk), generate_image() itself must
    still return -- with a clear, catchable error -- instead of leaving the
    caller waiting forever with zero feedback (the exact reported symptom)."""
    import time as _time
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import main as m

    monkeypatch.setattr(m, "_TOTAL_TIMEOUT_S", 0.3)

    def _stuck_impl(prompt):
        _time.sleep(5)
        return ["never gets here"]

    monkeypatch.setattr(m, "_generate_image_impl", _stuck_impl)

    t0 = _time.monotonic()
    with pytest.raises(m.ImageGenTimeout) as exc_info:
        m.generate_image("a nice tree")
    elapsed = _time.monotonic() - t0

    assert "timed out" in str(exc_info.value).lower()
    assert elapsed < 2.0, (
        f"generate_image() must return within _TOTAL_TIMEOUT_S (0.3s) even "
        f"though the implementation hangs for 5s -- took {elapsed:.2f}s"
    )


def test_generate_image_timeout_is_not_an_image_gen_refused(monkeypatch, tmp_path):
    """Adversarial review (2026-07-14): a timeout must be a DISTINCT
    exception type from ImageGenRefused (the L44 content-policy refusal) --
    reusing ImageGenRefused for an infrastructural hang would let any
    exception-type-based refusal-rate accounting misclassify a stalled
    network drive as a content-policy block."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import main as m

    assert not issubclass(m.ImageGenTimeout, m.ImageGenRefused)
    assert not issubclass(m.ImageGenRefused, m.ImageGenTimeout)


def test_save_image_bytes_never_raises_even_if_path_construction_fails(monkeypatch, tmp_path):
    """Regression (adversarial review, 2026-07-14): a prior version moved
    the path/env construction (os.environ.get, Path(), the f-string) OUTSIDE
    the try/except into the calling thread, so an error in THAT setup code
    (not just the background write) propagated uncaught -- turning a
    cosmetic display-persistence failure into a hard crash of the whole
    generate_image() call. The whole function body must be covered."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_IMAGE_OUTDIR", str(tmp_path / "outputs"))
    import main as m

    def _boom(*a, **k):
        raise ValueError("simulated path-construction failure")

    monkeypatch.setattr(m, "Path", _boom)
    result = m._save_image_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes", "jpeg")
    assert result is None, "a path-construction failure must degrade to 'not saved', never raise"


def test_generate_image_still_works_normally_through_the_timeout_wrapper(monkeypatch, tmp_path):
    """Regression guard: wrapping generate_image() in a timeout must not
    change its behavior on the normal (fast, successful) path."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import main as m

    monkeypatch.setattr(m, "check_l44", lambda *a, **k: None)
    monkeypatch.setattr(m, "resolve_key", lambda name: None)
    monkeypatch.setattr(m, "ensure_disclosed", lambda tid: None)
    sentinel = object()
    monkeypatch.setattr(m, "_generate_pollinations", lambda prompt: sentinel)
    monkeypatch.setattr(m, "_save_image_bytes", lambda data, fmt: None)

    blocks = m.generate_image("a nice tree")
    assert sentinel in blocks
