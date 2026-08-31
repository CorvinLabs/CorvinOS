"""The console's background refresh of the live model catalogue.

The /settings/engine/registry route kicks a refresh so a newly released Claude
model shows up in the AI-Engines pickers on its own. That refresh is real
network egress on a page-load path, so the gating matters more than the fetch:

  * a FRESH cache must not egress at all,
  * a failed attempt must not re-fire on every page load (a failed fetch never
    writes the cache, so the entry stays stale forever),
  * an L35-denied host must not be contacted,
  * nothing may raise into the route — a broken refresh must never take the
    Engines page down with it.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge",
           _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import model_catalog  # type: ignore  # noqa: E402
from corvin_console.routes import engine as ENG  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    model_catalog.load_catalog(force_reload=True)
    ENG._refresh_inflight.clear()
    ENG._refresh_last_attempt.clear()
    yield
    ENG._refresh_inflight.clear()
    ENG._refresh_last_attempt.clear()
    model_catalog.load_catalog(force_reload=True)


def _spawned(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which providers a refresh would be started for, without threads."""
    started: list[str] = []

    class _FakeThread:
        def __init__(self, target=None, args=(), **_kw):  # noqa: ANN001
            self._args = args

        def start(self) -> None:
            started.append(self._args[0])

    monkeypatch.setattr(ENG.threading, "Thread", _FakeThread)
    return started


def test_stale_cache_triggers_a_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    started = _spawned(monkeypatch)
    ENG._maybe_refresh_model_catalog("_default")
    assert started == ["anthropic"]


def test_fresh_cache_does_not_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    model_catalog.store_models("anthropic", [{"id": "claude-opus-5"}])
    started = _spawned(monkeypatch)
    ENG._maybe_refresh_model_catalog("_default")
    assert started == []


def test_failed_attempt_is_not_retried_on_every_page_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed fetch writes nothing, so the entry stays stale — without the
    retry floor the next page load would fetch again, and the next, and the
    next."""
    started = _spawned(monkeypatch)
    ENG._maybe_refresh_model_catalog("_default")
    ENG._refresh_inflight.clear()          # pretend the attempt finished, failing
    ENG._maybe_refresh_model_catalog("_default")
    assert started == ["anthropic"]

    # …and it resumes once the floor has passed.
    ENG._refresh_last_attempt["anthropic"] = time.time() - ENG._REFRESH_RETRY_FLOOR_SECONDS - 1
    ENG._maybe_refresh_model_catalog("_default")
    assert started == ["anthropic", "anthropic"]


def test_inflight_refresh_is_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    started = _spawned(monkeypatch)
    ENG._maybe_refresh_model_catalog("_default")
    ENG._refresh_last_attempt.clear()      # only the in-flight guard may stop it
    ENG._maybe_refresh_model_catalog("_default")
    assert started == ["anthropic"]


def test_egress_denied_host_is_never_contacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """L35 is enforced on the refresh, not just on the user-triggered fetch."""
    monkeypatch.setattr(ENG, "_egress_denied", lambda _url, _tenant: "blocked by policy")
    called: list[str] = []
    import engine_providers  # type: ignore
    monkeypatch.setattr(engine_providers, "fetch_models",
                        lambda *a, **k: called.append("fetched"))
    ENG._refresh_model_catalog_now("anthropic", "_default")
    assert called == []


def test_refresh_never_raises_into_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ENG, "_egress_denied",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("gate exploded")))
    ENG._refresh_model_catalog_now("anthropic", "_default")   # must not raise
    assert "anthropic" not in ENG._refresh_inflight           # and must release the guard


def test_unknown_provider_is_a_noop() -> None:
    ENG._refresh_model_catalog_now("does-not-exist", "_default")


def test_refresh_reaches_the_registry_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end across the seams that unit tests cover separately: a refresh
    must make a newly released model appear in what /settings/engine/registry
    serves — that is the whole point of the feature."""
    import engine_models  # type: ignore
    import engine_providers  # type: ignore

    monkeypatch.setattr(engine_providers._provider_keys, "resolve_by_env_var",
                        lambda _n: "sk-ant-test")
    monkeypatch.setattr(engine_providers, "_get_json", lambda *_a, **_k: {
        "data": [{"id": "claude-zenith-9", "display_name": "Claude Zenith 9"}],
        "has_more": False,
    })
    monkeypatch.setattr(ENG, "_egress_denied", lambda _url, _tenant: None)

    ENG._refresh_model_catalog_now("anthropic", "_default")

    payload = engine_models.registry_as_dict(force_reload=True)
    offered = {m["id"]: m for m in payload["claude_code"]["os_models"]}
    assert offered["claude-zenith-9"]["label"] == "Claude Zenith 9"
    assert "claude-sonnet-5" in offered, "curated entries must survive the refresh"
