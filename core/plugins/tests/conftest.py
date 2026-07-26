"""Audit-chain isolation for the plugin suite.

Why this file exists, and why it is the FOURTH place in this repo that needed
it: `tests/conftest.py` at the repo root redirects `VOICE_AUDIT_PATH` to tmp for
everything under `tests/`. The plugin suite does not live there — it lives next
to the package it tests — so it was never covered.

That matters here more than almost anywhere else, because this suite exercises
BOOT paths. `bootstrap_declared()` / `bootstrap_tenant()` / `bootstrap_global()`
build a real `PluginContext` whose `audit_emit` resolves the real hash-chained
writer. A test that loads a plugin therefore appends `plugin.loaded` to the live
GDPR Art. 30 chain — and because the chain is hash-linked and append-only, those
records can never be removed afterwards without corrupting it. One such run left
28 permanent test records in `.corvin/global/forge/audit.jsonl`.

They are content-free lifecycle records, so this was noise rather than a data
leak. It is still the wrong default: on a host with a pinned service directory
the same code writes into the production chain, and "unremovable" is the part
that makes it worth a structural fix instead of a per-test patch.

Individual tests that patch `audit_emit` directly still do so — this is a floor,
not a replacement for isolating a specific call path.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_audit_chain_for_plugin_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
