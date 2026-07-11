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
    attempting (and failing) Tier 1 instead of correctly using Tier 0."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog, activate

    seed_builtin.ensure_imagegen_zero_config("_default")
    entry = catalog.get_tool("_default", "imagegen-zero-config")
    assert entry["secrets"] == []

    servers = activate.get_active_mcp_servers("_default")
    assert "imagegen-zero-config" in servers
    assert "env" not in servers["imagegen-zero-config"]


def test_seed_builtin_idempotent(monkeypatch, tmp_path):
    """Calling ensure_ twice (mirrors it running on every gateway boot)
    must not error or duplicate anything."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from mcp_manager import seed_builtin, catalog

    r1 = seed_builtin.ensure_imagegen_zero_config("_default")
    r2 = seed_builtin.ensure_imagegen_zero_config("_default")
    assert r1 == r2
    assert len(catalog.list_tools("_default")) == 1


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


def test_pollinations_500_propagates_as_generic_http_error(monkeypatch):
    import httpx
    import main as m

    fake_resp = httpx.Response(
        500, request=httpx.Request("GET", "https://image.pollinations.ai/prompt/x"),
        text="server error",
    )
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: fake_resp)

    with pytest.raises(httpx.HTTPStatusError):
        m._generate_pollinations("x")
