"""Regression (2026-09-27 adversarial review, round 2).

``_dialectic_session_reset`` located the session workspace with a
``tenant_id`` it never received. The NameError was swallowed by the probe's
``except``, so every reset reported 0 skills / 0 tools and the heat score
could never cross the dialectic threshold.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import session_reset  # noqa: E402


def test_dialectic_probe_counts_the_tenant_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(session_reset, "_sessions_root", lambda tid="_default": tmp_path / tid)
    chan = "discord:42"
    skills = tmp_path / "acme" / chan / "skill-forge" / "skills"
    for name in ("a", "b", "c"):
        (skills / name).mkdir(parents=True)

    seen: dict = {}
    fake = types.ModuleType("dialectic")
    fake.decide = lambda **kw: seen.update(kw)
    monkeypatch.setitem(sys.modules, "dialectic", fake)

    session_reset._dialectic_session_reset(
        channel="discord", chat_id="42", forge_chan_id=chan, reason="manual",
        tenant_id="acme",
    )
    assert seen["thesis"]["n_skills"] == 3
    assert seen["consequence"] > 0.5
