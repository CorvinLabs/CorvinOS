"""The two console pages must agree — same source, same window, same numbers.

`/console/app/engine-config` and `/console/app/model-cost-optimizer` show the
same traffic from two angles. An operator reads one, then the other, and any
disagreement between them is read as one of the pages being wrong — which is the
right reading, because one of them is.

They drifted twice, and both times the split was invisible from either page
alone:

1. **Different windows.** `_real_stats` (classified turns) read the whole chain
   while `model_usage` honoured the ADR-0760 counting window, so the card said
   "0 of 288 classified turns" directly above "18.2% of all turns" — an all-time
   total stacked on a windowed one.
2. **Different denominators, same wording.** "classified turns" counts OS turns
   the shadow classifier bucketed; "% of all turns" is measured against every
   engine span, OS and worker. Both are correct and they are not comparable, so
   the UI has to say which is which.

What this file pins is the first kind — numbers that MUST be equal — plus the
guard that the second kind stays explicitly labelled.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "forge",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.console.corvin_console import auth as session_auth  # noqa: E402
from core.console.corvin_console import deps as console_deps  # noqa: E402


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


@pytest.fixture
def client():
    """Both routers on one app — the two pages as one surface, which is how an
    operator experiences them."""
    from core.console.corvin_console.routes import engine_api
    from core.console.corvin_console.routes import model_cost_optimizer_api

    app = FastAPI()
    app.include_router(engine_api.router, prefix="/v1/console")
    app.include_router(model_cost_optimizer_api.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = (
        lambda: _fake_session_record("_default")
    )
    app.dependency_overrides[console_deps.require_csrf] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def pages(client):
    usage = client.get("/v1/console/v1/engine/model-usage")
    cost = client.get("/v1/console/learning/model-cost-optimizer/status")
    assert usage.status_code == 200, usage.text
    assert cost.status_code == 200, cost.text
    return usage.json(), cost.json()


class TestSameWindow:
    def test_both_pages_report_the_same_counting_window(self, pages):
        usage, cost = pages
        assert usage["window"] == cost["window"], (
            "the two pages are counting over different periods; every total "
            "below is then incomparable"
        )

    def test_classified_turns_are_windowed_like_everything_else(self, client, monkeypatch):
        """The specific drift: `_real_stats` read the whole chain.

        Asserted by MOVING the window and watching the count change — a test
        that only read the current value would pass against the all-time
        implementation too.
        """
        import time

        from core.console.corvin_console import usage_epoch
        from core.console.corvin_console.routes import engine_api

        before = client.get("/v1/console/v1/engine/config").json()["total_samples"]

        # A window starting in the future can contain nothing.
        original = usage_epoch.epoch_ts
        monkeypatch.setattr(engine_api.usage_epoch if hasattr(engine_api, "usage_epoch")
                            else usage_epoch, "epoch_ts", lambda tid: time.time() + 86_400)
        monkeypatch.setattr(usage_epoch, "epoch_ts", lambda tid: time.time() + 86_400)
        after = client.get("/v1/console/v1/engine/config").json()["total_samples"]
        monkeypatch.setattr(usage_epoch, "epoch_ts", original)

        assert after == 0, (
            f"classified turns ignored the counting window ({before} -> {after}); "
            f"the Engine Configuration card would quote an all-time total beside "
            f"windowed usage figures"
        )


class TestSameNumbers:
    def test_os_and_worker_turn_counts_match(self, pages):
        usage, cost = pages
        roles = {r["role"]: r for r in usage["roles"]}
        assert roles.get("os", {}).get("turns", 0) == cost["cost_total_turns"]
        assert roles.get("worker", {}).get("turns", 0) == cost["acs_total_turns"]

    def test_per_model_turn_counts_match(self, pages):
        usage, cost = pages
        from_usage = {m["model_id"]: m["turns"] for m in usage["models"]}
        from_cost: dict[str, int] = {}
        for mix in (cost.get("cost_model_mix") or {}, cost.get("acs_model_mix") or {}):
            for model, n in mix.items():
                from_cost[model] = from_cost.get(model, 0) + n
        # Only models the cost view could price appear there; every one of them
        # must agree with the usage view. A model in usage but not in cost is a
        # turn whose tokens were never recorded, which is a different fact.
        for model, n in from_cost.items():
            assert from_usage.get(model) == n, (
                f"{model}: usage says {from_usage.get(model)} turns, cost says {n}"
            )

    def test_role_totals_reconcile_within_the_usage_page(self, pages):
        usage, _ = pages
        assert sum(r["turns"] for r in usage["roles"]) == usage["totals"]["turns"]
        assert sum(m["turns"] for m in usage["models"]) == usage["totals"]["turns"]

    def test_combined_cost_is_the_sum_of_the_two_sources(self, pages):
        _, cost = pages
        if not cost.get("combined_data_available"):
            # Not a coverage hole: the combination arithmetic is pinned
            # deterministically against injected data in
            # test_usage_epoch_adr0760_e2e.py::TestWorkerAndCombinedArithmetic.
            # This check is the live-install cross-read of the same identity.
            pytest.skip("this install has no data on both sides in the current window")
        assert cost["combined_actual_usd"] == pytest.approx(
            cost["cost_current_usd"] + cost["acs_cost_actual_usd"], abs=1e-3)
        assert cost["combined_baseline_usd"] == pytest.approx(
            cost["cost_baseline_usd"] + cost["acs_cost_baseline_usd"], abs=1e-3)


class TestOneChain:
    def test_every_reader_resolves_the_same_audit_chain(self):
        """Three modules read 'the chain'. A fourth path would be a fourth truth.

        This is the ADR-0650 split in miniature: the numbers can only agree if
        they are counted from the same file.
        """
        from core.paths import tenant_audit_chain
        from core.skills.skill_audit import audit_chain_path
        from core.console.corvin_console import model_usage as mu

        canonical = tenant_audit_chain("_default")
        assert Path(audit_chain_path("_default")) == Path(canonical)
        assert Path(mu._chain_path("_default") or "") == Path(canonical)


class TestLabelledDenominators:
    """The numbers that are NOT equal must be visibly not-the-same-thing."""

    # ADR-0885 (2026-09-18): pages/engine-config.tsx was folded into the Models
    # console; the task-type card and the Model Usage panel that carry these
    # labels live in pages/models/components/engine-parts.tsx now.
    _MODELS = _REPO / "core/console/corvin_console/web-next/src/pages/models"

    def _panel(self) -> str:
        return (self._MODELS / "components/engine-parts.tsx").read_text(encoding="utf-8")

    def _models_tree(self) -> str:
        return "\n".join(
            f.read_text(encoding="utf-8") for f in sorted(self._MODELS.rglob("*.ts*"))
        )

    def test_the_lifetime_sample_count_says_it_is_lifetime(self):
        panel = self._panel()
        assert "lifetime" in panel, (
            "the optimizer's sample count is not narrowed by the window; if the "
            "UI does not say so it reads as a windowed figure like its neighbours"
        )

    def test_the_two_denominators_are_named(self):
        panel = self._panel()
        assert "not the same denominator" in panel, (
            "classified turns and engine spans sit next to each other and count "
            "different populations; the card has to say which is which"
        )

    def test_numbers_are_formatted_for_the_shipped_language(self):
        """`toLocaleString()` with no locale follows the BROWSER: on a German
        host 159562 renders as "159.562", which an English reader parses as a
        decimal — the same glyphs, a 1000x different value."""
        import re

        panel = self._models_tree()  # every file of the Models console
        assert "toLocaleString('en-US')" in panel or 'toLocaleString("en-US")' in panel
        # Match the CALL (leading dot, empty parens), not the phrase — the first
        # revision of this assertion matched the word inside the doc comment
        # explaining the rule and failed on a file that already followed it.
        unpinned = re.findall(r"\.toLocale(?:String|DateString|TimeString)\(\s*\)", panel)
        assert not unpinned, (
            f"{len(unpinned)} unpinned locale call(s) render console numbers in "
            f"whatever locale the operator's browser happens to use"
        )
