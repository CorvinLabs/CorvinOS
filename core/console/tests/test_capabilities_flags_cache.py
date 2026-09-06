"""The manifest's gated-flag read is cached ≤5 s per tenant and invalidated by a toggle.

F31 (2026-09-06): every ``/capabilities/manifest`` request executed
``os.capabilities`` (an audited Skill execution) — hundreds per ten minutes of
ordinary SPA polling. The cache bounds that without letting an operator's
toggle go stale: ``routes/features.py`` invalidates explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO / "core" / "console"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from corvin_console.routes import capabilities as caps  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    caps.invalidate_flags_cache()
    yield
    caps.invalidate_flags_cache()


def test_second_read_within_ttl_does_not_execute_the_skill(monkeypatch):
    calls: list[str] = []

    def fake(tenant_id: str) -> dict[str, bool]:
        calls.append(tenant_id)
        return {"vibe_engineering": True}

    monkeypatch.setattr(caps, "_read_flags_uncached", fake)
    assert caps._read_flags("_default") == {"vibe_engineering": True}
    assert caps._read_flags("_default") == {"vibe_engineering": True}
    assert calls == ["_default"]


def test_cache_is_per_tenant(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(caps, "_read_flags_uncached", lambda t: (calls.append(t) or {t: True}))
    assert caps._read_flags("_default") == {"_default": True}
    assert caps._read_flags("acme") == {"acme": True}
    assert calls == ["_default", "acme"]


def test_invalidate_forces_a_fresh_read(monkeypatch):
    values = iter([{"f": False}, {"f": True}])
    monkeypatch.setattr(caps, "_read_flags_uncached", lambda t: dict(next(values)))
    assert caps._read_flags("_default") == {"f": False}
    caps.invalidate_flags_cache("_default")
    assert caps._read_flags("_default") == {"f": True}


def test_ttl_expiry(monkeypatch):
    values = iter([{"f": False}, {"f": True}])
    monkeypatch.setattr(caps, "_read_flags_uncached", lambda t: dict(next(values)))
    clock = [1000.0]
    monkeypatch.setattr(caps.time, "monotonic", lambda: clock[0])
    assert caps._read_flags("_default") == {"f": False}
    clock[0] += caps._FLAGS_TTL_S + 0.1
    assert caps._read_flags("_default") == {"f": True}


def test_returned_dict_is_a_copy(monkeypatch):
    monkeypatch.setattr(caps, "_read_flags_uncached", lambda t: {"f": True})
    first = caps._read_flags("_default")
    first["f"] = False
    assert caps._read_flags("_default") == {"f": True}
