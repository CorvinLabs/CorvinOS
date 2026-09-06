"""Live model catalogue — cache, registry merge, and the Anthropic fetch.

Covers the mechanism that keeps the console's AI-Engines model pickers current
when Anthropic ships a new model (Opus 5, …) without a CorvinOS release:

  engine_providers.fetch_models("anthropic")   → GET /v1/models
      → model_catalog.store_models()           → ~/.corvin/global/model_catalog.json
          → engine_models.load_registry()      → merged into the curated lists

The invariants under test are the ones that decide whether a bad refresh can
DEGRADE the picker: an empty or failed fetch must never wipe the last-good
cache, and the curated list (the only list an install without an API key has)
must survive every merge with its order and its `default` flag intact.
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import engine_models  # type: ignore  # noqa: E402
import engine_providers  # type: ignore  # noqa: E402
import model_catalog  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point CORVIN_HOME at a tmp dir and reset every module-level cache, so a
    test can never read — or write — the developer's real catalog file."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    model_catalog.load_catalog(force_reload=True)
    engine_models.load_registry(force_reload=True)
    yield
    model_catalog.load_catalog(force_reload=True)
    engine_models.load_registry(force_reload=True)


# ---------------------------------------------------------------------------
# model_catalog — cache semantics
# ---------------------------------------------------------------------------

class TestCatalogCache:
    def test_missing_file_is_empty_not_an_error(self) -> None:
        assert model_catalog.load_catalog() == {}
        assert model_catalog.catalog_models("anthropic") == []
        assert model_catalog.fetched_at("anthropic") is None
        assert model_catalog.is_stale("anthropic") is True

    def test_store_then_read_roundtrip(self) -> None:
        assert model_catalog.store_models(
            "anthropic", [{"id": "claude-opus-5", "label": "Claude Opus 5"}]) is True
        assert model_catalog.catalog_models("anthropic") == [
            {"id": "claude-opus-5", "label": "Claude Opus 5"}]
        assert model_catalog.is_stale("anthropic") is False
        assert model_catalog.catalog_path().stat().st_mode & 0o777 == 0o600

    def test_empty_fetch_does_not_wipe_last_good(self) -> None:
        """A provider answering 200-with-no-models must not leave the picker
        with only the curated list — the old entry stays."""
        model_catalog.store_models("anthropic", [{"id": "claude-opus-5", "label": "Opus 5"}])
        assert model_catalog.store_models("anthropic", []) is False
        assert len(model_catalog.catalog_models("anthropic")) == 1

    def test_entries_are_deduped_and_labels_default_to_the_id(self) -> None:
        model_catalog.store_models("anthropic", [
            {"id": "a", "label": "A"}, {"id": "a", "label": "A again"}, {"id": "b"},
            {"no-id": True}, "not-a-dict",
        ])
        assert model_catalog.catalog_models("anthropic") == [
            {"id": "a", "label": "A"}, {"id": "b", "label": "b"}]

    def test_store_is_per_provider(self) -> None:
        model_catalog.store_models("anthropic", [{"id": "claude-opus-5"}])
        model_catalog.store_models("openrouter", [{"id": "some/model"}])
        assert [m["id"] for m in model_catalog.catalog_models("anthropic")] == ["claude-opus-5"]
        assert [m["id"] for m in model_catalog.catalog_models("openrouter")] == ["some/model"]

    def test_corrupt_cache_degrades_to_empty(self) -> None:
        p = model_catalog.catalog_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", "utf-8")
        assert model_catalog.load_catalog(force_reload=True) == {}
        assert model_catalog.catalog_models("anthropic") == []

    def test_aged_out_entry_is_stale(self) -> None:
        model_catalog.store_models("anthropic", [{"id": "claude-opus-5"}])
        p = model_catalog.catalog_path()
        raw = json.loads(p.read_text("utf-8"))
        raw["providers"]["anthropic"]["fetched_at"] = time.time() - 2 * model_catalog.DEFAULT_TTL_SECONDS
        p.write_text(json.dumps(raw), "utf-8")
        model_catalog.load_catalog(force_reload=True)
        assert model_catalog.is_stale("anthropic") is True

    def test_future_timestamp_counts_as_stale(self) -> None:
        """A backwards clock jump must not pin an entry as fresh forever."""
        model_catalog.store_models("anthropic", [{"id": "claude-opus-5"}])
        p = model_catalog.catalog_path()
        raw = json.loads(p.read_text("utf-8"))
        raw["providers"]["anthropic"]["fetched_at"] = time.time() + 10_000
        p.write_text(json.dumps(raw), "utf-8")
        model_catalog.load_catalog(force_reload=True)
        assert model_catalog.is_stale("anthropic") is True


# ---------------------------------------------------------------------------
# engine_models — merge into the curated registry
# ---------------------------------------------------------------------------

class TestRegistryMerge:
    def test_registry_declares_a_live_source_for_claude_code(self) -> None:
        reg = engine_models.load_registry(force_reload=True)
        assert reg["claude_code"].live_models is not None
        assert reg["claude_code"].live_models.provider == "anthropic"
        assert reg["claude_code"].live_models.prefix == ""
        assert "hermes" not in reg  # Hermes removed in v2.0 (Claude Code only)

    def test_no_catalog_leaves_the_curated_list_untouched(self) -> None:
        reg = engine_models.load_registry(force_reload=True)
        ids = [m.id for m in reg["claude_code"].os_models]
        assert "claude-sonnet-5" in ids
        assert reg["claude_code"].os_models[0].default is True

    def test_new_model_appears_without_a_release(self) -> None:
        model_catalog.store_models("anthropic", [
            {"id": "claude-zenith-9", "label": "Claude Zenith 9"}])
        reg = engine_models.load_registry(force_reload=True)
        entry = next(m for m in reg["claude_code"].os_models if m.id == "claude-zenith-9")
        assert entry.label == "Claude Zenith 9"
        assert entry.default is False
        assert "claude-zenith-9" in [m.id for m in reg["claude_code"].worker_models]

    def test_curated_entries_are_not_duplicated_and_keep_their_default(self) -> None:
        model_catalog.store_models("anthropic", [
            {"id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
            {"id": "claude-zenith-9", "label": "Claude Zenith 9"},
        ])
        reg = engine_models.load_registry(force_reload=True)
        ids = [m.id for m in reg["claude_code"].worker_models]
        assert ids.count("claude-sonnet-5") == 1
        # The curated default must still be the default — the live list only adds.
        assert reg["claude_code"].default_worker_model() == "claude-opus-5"
        assert reg["claude_code"].default_os_model() is None  # "" = adaptive

    def test_prefix_is_applied_per_engine(self) -> None:
        """OpenCode addresses models as '<provider>/<model>'; Claude Code doesn't."""
        model_catalog.store_models("anthropic", [{"id": "claude-zenith-9"}])
        reg = engine_models.load_registry(force_reload=True)
        assert "anthropic/claude-zenith-9" in [m.id for m in reg["opencode"].os_models]
        assert "claude-zenith-9" not in [m.id for m in reg["opencode"].os_models]

    def test_role_without_a_curated_list_stays_empty(self) -> None:
        """os_models: [] means 'no model choice for this role' — merging live
        models into it would invent a picker the engine cannot honour."""
        model_catalog.store_models("anthropic", [{"id": "claude-zenith-9"}])
        reg = engine_models.load_registry(force_reload=True)
        assert reg["codex_cli"].os_models == []

    def test_refresh_is_visible_without_force_reload(self) -> None:
        """The console writes the cache in a background thread; the next read
        must see it, or the new model only appears after a restart."""
        engine_models.load_registry(force_reload=True)
        model_catalog.store_models("anthropic", [{"id": "claude-zenith-9"}])
        reg = engine_models.load_registry()  # no force_reload
        assert "claude-zenith-9" in [m.id for m in reg["claude_code"].os_models]

    def test_registry_as_dict_exposes_the_live_source(self) -> None:
        d = engine_models.registry_as_dict(force_reload=True)
        assert d["claude_code"]["live_models"] == {"provider": "anthropic", "prefix": ""}
        assert d["hermes"]["live_models"] is None

    def test_merged_models_pass_tier_validation(self) -> None:
        """resolve_model_for_workload validates against load_registry() — a
        live-only model must not be rejected as unknown."""
        model_catalog.store_models("anthropic", [{"id": "claude-zenith-9"}])
        engine_models.load_registry(force_reload=True)
        assert engine_models.resolve_model_for_workload(
            "claude_code", "code", user_chosen_model="claude-zenith-9",
        ) == "claude-zenith-9"


# ---------------------------------------------------------------------------
# engine_providers — the Anthropic /v1/models fetch
# ---------------------------------------------------------------------------

def _page(models: list[dict], has_more: bool = False, last_id: str = "") -> dict:
    return {"data": models, "has_more": has_more, "last_id": last_id}


class TestAnthropicFetch:
    def test_maps_display_name_and_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                            lambda _n: "sk-ant-test")
        seen: dict[str, Any] = {}

        def fake_get(url: str, **kw: Any) -> dict:
            seen["url"] = url
            seen["headers"] = kw.get("headers")
            return _page([{"id": "claude-opus-5", "display_name": "Claude Opus 5"},
                          {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"}])

        monkeypatch.setattr(engine_providers, "_get_json", fake_get)
        res = engine_providers.fetch_models(
            "anthropic", base_url="https://api.anthropic.com",
            model_source="anthropic", credential_env="ANTHROPIC_API_KEY")

        assert res["reachable"] is True
        assert res["models"][0] == {"id": "claude-opus-5", "label": "Claude Opus 5"}
        assert seen["url"].startswith("https://api.anthropic.com/v1/models")
        assert seen["headers"]["x-api-key"] == "sk-ant-test"
        assert seen["headers"]["anthropic-version"] == "2023-06-01"
        # The point of the fetch: it lands in the cache for the registry merge.
        assert res["cached"] is True
        assert [m["id"] for m in model_catalog.catalog_models("anthropic")] == [
            "claude-opus-5", "claude-haiku-4-5"]

    def test_follows_pagination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                            lambda _n: "sk-ant-test")
        pages = [
            _page([{"id": "m1"}], has_more=True, last_id="m1"),
            _page([{"id": "m2"}], has_more=False),
        ]
        urls: list[str] = []

        def fake_get(url: str, **_kw: Any) -> dict:
            urls.append(url)
            return pages[len(urls) - 1]

        monkeypatch.setattr(engine_providers, "_get_json", fake_get)
        res = engine_providers.fetch_models(
            "anthropic", base_url="https://api.anthropic.com",
            model_source="anthropic", credential_env="ANTHROPIC_API_KEY")
        assert [m["id"] for m in res["models"]] == ["m1", "m2"]
        assert "after_id=m1" in urls[1]

    def test_has_more_without_last_id_terminates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A provider bug must not spin the page walk."""
        monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                            lambda _n: "sk-ant-test")
        calls = {"n": 0}

        def fake_get(_url: str, **_kw: Any) -> dict:
            calls["n"] += 1
            return _page([{"id": f"m{calls['n']}"}], has_more=True, last_id="")

        monkeypatch.setattr(engine_providers, "_get_json", fake_get)
        engine_providers.fetch_models(
            "anthropic", base_url="https://api.anthropic.com",
            model_source="anthropic", credential_env="ANTHROPIC_API_KEY")
        assert calls["n"] == 1

    def test_missing_key_is_explained_not_a_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Claude Code subscription login exposes no API key — the common
        case must read as an explanation, and must not hit the network."""
        monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                            lambda _n: None)
        monkeypatch.setattr(engine_providers, "_get_json",
                            lambda *_a, **_k: pytest.fail("must not egress without a key"))
        res = engine_providers.fetch_models(
            "anthropic", base_url="https://api.anthropic.com",
            model_source="anthropic", credential_env="ANTHROPIC_API_KEY")
        assert res["reachable"] is False
        assert "ANTHROPIC_API_KEY" in res["error"]
        assert "curated model list" in res["error"]

    def test_http_error_keeps_the_last_good_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        model_catalog.store_models("anthropic", [{"id": "claude-opus-5", "label": "Opus 5"}])
        monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                            lambda _n: "sk-ant-bad")

        def boom(*_a: Any, **_k: Any) -> dict:
            raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))  # type: ignore[arg-type]

        monkeypatch.setattr(engine_providers, "_get_json", boom)
        res = engine_providers.fetch_models(
            "anthropic", base_url="https://api.anthropic.com",
            model_source="anthropic", credential_env="ANTHROPIC_API_KEY")
        assert res["reachable"] is False
        assert "401" in res["error"]
        assert [m["id"] for m in model_catalog.catalog_models("anthropic")] == ["claude-opus-5"]

    def test_anthropic_provider_is_live_not_static(self) -> None:
        """The provider entry is what enables the whole path — a regression to
        model_source: static silently freezes the model list again."""
        providers = engine_models.load_providers(force_reload=True)
        assert providers["anthropic"].model_source == "anthropic"
        assert providers["anthropic"].credential_env == "ANTHROPIC_API_KEY"

    def test_cache_write_failure_does_not_break_the_response(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                            lambda _n: "sk-ant-test")
        monkeypatch.setattr(engine_providers, "_get_json",
                            lambda *_a, **_k: _page([{"id": "claude-opus-5"}]))
        with mock.patch.object(engine_providers._model_catalog, "store_models",
                               side_effect=OSError("read-only fs")):
            res = engine_providers.fetch_models(
                "anthropic", base_url="https://api.anthropic.com",
                model_source="anthropic", credential_env="ANTHROPIC_API_KEY")
        assert res["reachable"] is True
        assert res["cached"] is False
        assert [m["id"] for m in res["models"]] == ["claude-opus-5"]
