"""LIC-SPACE-DOM-TOCTOU-01 (ADR-0094 class): the space_domains_max license cap is
enforced atomically inside create_domain under _FLOCK_LOCK, so concurrent creates
cannot race past the free-tier cap. Two threads creating with license_max=1 must
yield exactly one success and one license-capped rejection.
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import space_domains as SD


def test_concurrent_create_cannot_exceed_license_cap(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("CORVIN_HOME", td)
        results: list = []
        errors: list = []
        barrier = threading.Barrier(2)

        def _worker(slug: str):
            barrier.wait()  # maximise the race
            try:
                d = SD.create_domain(slug=slug, name=slug, tenant_id="_default", license_max=1)
                results.append(d.slug)
            except SD.DomainLimitError as e:
                errors.append(getattr(e, "license_capped", False))
            except Exception as e:  # noqa: BLE001
                errors.append(("other", type(e).__name__))

        t1 = threading.Thread(target=_worker, args=("alpha",))
        t2 = threading.Thread(target=_worker, args=("beta",))
        t1.start(); t2.start(); t1.join(); t2.join()

        assert len(results) == 1, f"exactly one create must succeed, got {results}"
        assert errors == [True], f"the loser must be a license-capped DomainLimitError, got {errors}"
        assert len(SD.list_domains("_default")) == 1, "free-tier cap of 1 domain must hold"


def test_unlimited_license_allows_more(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("CORVIN_HOME", td)
        # license_max=None (paid/unlimited) → only the structural DOMAIN_MAX=5 binds.
        for s in ("a", "b", "c"):
            SD.create_domain(slug=s, name=s, tenant_id="_default", license_max=None)
        assert len(SD.list_domains("_default")) == 3
