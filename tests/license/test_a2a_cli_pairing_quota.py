"""CLI pairing peer quota (``corvin_a2a.py pair`` → ``_check_local_a2a_peer_quota``).

What the tier matrix says today: ``FREE_TIER["a2a_peers_max"] == 1`` — the free
tier may hold ONE peer; ADR-0702 §2.6 keeps this counter unchanged until its
PLAN Phase 4b. (ADR-0702 §3.4's member-only pairing gate is not built.)

Pinned defect (adversarial review 2026-09-27): when the licence validator could
not be imported or resolve the limit, the check returned ``None`` — i.e. no
limit — on the theory that "the server enforces it authoritatively". The
``--offline-pair`` path never talks to that server, so the free tier could pair
without bound. It now refuses (fail-closed).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "voice" / "scripts"))


@pytest.fixture
def a2a(tmp_path, monkeypatch):
    monkeypatch.setenv("REMOTE_ORIGINS_DIR", str(tmp_path / "origins"))
    import corvin_a2a

    return corvin_a2a


def test_free_tier_first_peer_is_allowed_second_is_refused(a2a, tmp_path):
    assert a2a._check_local_a2a_peer_quota() is None
    (tmp_path / "origins").mkdir()
    (tmp_path / "origins" / "peer1.json").write_text("{}")
    err = a2a._check_local_a2a_peer_quota()
    assert err and "limit reached: 1/1" in err


def test_validator_unavailable_refuses_instead_of_unlimited(a2a):
    with mock.patch.dict(sys.modules, {"validator": None}):
        err = a2a._check_local_a2a_peer_quota()
    assert err and "fail-closed" in err


def test_validator_error_refuses(a2a):
    import validator  # the bare module the CLI imports

    with mock.patch.object(validator, "get_limit", side_effect=OSError("EIO")):
        err = a2a._check_local_a2a_peer_quota()
    assert err and "fail-closed" in err
