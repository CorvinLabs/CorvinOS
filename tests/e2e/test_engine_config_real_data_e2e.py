"""E2E: the Engine Configuration console serves REAL data, over real HTTP.

This is the e2e-wiring-proof for two endpoints added by the 2026-09-15
Engine-Configuration real-data pass —
``GET /v1/engine/claude-models`` and ``GET /v1/engine/model-usage`` — plus the
regression guard for the requirement that produced them: no hardcoded model or
provider list may ship in the panel's bundle.

It also covers the fields the same pass added so the panel can be terse without
becoming vague: each model source's ``short_label``/``hint`` (one line, full
error on hover) and each tier's ``classified_count`` (why a tier with no learned
outcomes is empty). Both are display summaries of data that already existed, so
what is asserted about them is that they cannot DISAGREE with the long form.

Every request here goes over the loopback TCP socket to the RUNNING console and
carries a real session cookie from the loopback-only, credential-less
``/auth/local-login``. Nothing is imported and called directly: a unit test
proves a handler returns the right value when called, and both of these endpoints
were reachable-looking but unmounted at one point during development precisely
because the client prefixes ``/v1/console`` onto a router that also declares
``/v1/engine`` (the live path is ``/v1/console/v1/engine/...``). Only a real
request over the real URL catches that.

Assertions are internal-consistency checks against whatever this host actually
has — a share set that sums to 100%, a provenance label drawn from the declared
source list — not pinned counts. A test that asserted "47 models" would fail on
any other AWS account and prove nothing about correctness.

Skips (never fails) when the console is not listening: this suite is about the
wiring of a running install, and a missing install is not a defect in it.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import socket
import urllib.error
import urllib.request

import pytest

HOST = "127.0.0.1"
PORT = 8765
BASE = f"http://{HOST}:{PORT}"
CONSOLE = f"{BASE}/v1/console"

#: Every provenance label model_usage is allowed to emit. A value outside this
#: set means an attribution path was added without being documented in the
#: response contract or explained in the UI's PROVENANCE_NOTE map.
PROVIDER_SOURCES = {
    "live_catalog", "tenant_config", "registry", "id_prefix",
    "engine_config", "unresolved",
}

#: A label from the four-entry array that used to be hardcoded in
#: engine-config.tsx. Its presence in the served bundle means the mock list came
#: back — the one thing the operator explicitly ruled out.
REMOVED_MOCK_LABEL = "Claude Sonnet 5 (Balanced)"

#: A string only the rewritten panel contains. Asserted PRESENT, so the crawl
#: below provably reached the engine-config chunk before concluding the mock
#: label is gone from it.
PANEL_MARKER = "Reading the audit chain"


def _console_up() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _console_up(), reason=f"console not listening on {HOST}:{PORT}"
)


@pytest.fixture(scope="module")
def opener() -> urllib.request.OpenerDirector:
    """A cookie-carrying opener with a real console session.

    ``/auth/local-login`` is a GET (it is loopback-only and credential-less —
    the TCP peer IS the authorisation), which is worth stating because POSTing
    to it returns 405 and reads like a broken endpoint.
    """
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with op.open(f"{CONSOLE}/auth/local-login", timeout=15) as resp:
        assert resp.status == 200, f"local-login returned {resp.status}"
    assert len(jar) > 0, "local-login set no session cookie"
    return op


def _get_json(op: urllib.request.OpenerDirector, path: str) -> dict:
    with op.open(f"{CONSOLE}{path}", timeout=90) as resp:
        assert resp.status == 200, f"{path} returned {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Reachability: the endpoints answer at the URL the SPA actually calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/engine/claude-models", "/v1/engine/model-usage"])
def test_endpoint_requires_a_session(path: str) -> None:
    """Unauthenticated = 401, not 200 and not 404.

    404 would mean the route is not mounted; 200 would mean tenant-scoped data
    leaks to any loopback caller without a session (ADR-0007).
    """
    plain = urllib.request.build_opener()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        plain.open(f"{CONSOLE}{path}", timeout=30)
    assert excinfo.value.code == 401, f"{path} answered {excinfo.value.code}, want 401"


# ---------------------------------------------------------------------------
# GET /v1/engine/claude-models
# ---------------------------------------------------------------------------


def test_claude_models_unions_declared_sources(opener) -> None:
    data = _get_json(opener, "/v1/engine/claude-models")

    assert data["tenant_id"], "no tenant on the response"
    assert data["count"] == len(data["models"])
    assert data["models"], "not one Claude model from any source"

    source_ids = {s["id"] for s in data["sources"]}
    assert source_ids, "no sources declared"

    for src in data["sources"]:
        # A source must say what happened. 'reachable' with an error, or
        # unreachable without one, is an unreadable state for the operator.
        assert isinstance(src["reachable"], bool)
        assert isinstance(src["count"], int) and src["count"] >= 0
        if not src["reachable"]:
            assert src["error"], f"source {src['id']} is unreachable but gives no reason"


    for model in data["models"]:
        assert model["id"], "a model with no id"
        assert model["sources"], f"{model['id']} claims no source"
        # Provenance must be attributable to a source that reported itself —
        # an id tagged with a source that is not in the list is invented.
        unknown = set(model["sources"]) - source_ids
        assert not unknown, f"{model['id']} cites undeclared source(s) {unknown}"
        low = model["id"].lower()
        assert "claude" in low or "anthropic" in low, f"{model['id']} is not a Claude id"

    default_id = data["default_model_id"]
    if default_id is not None:
        # The reset target the UI uses when an external provider is removed. If
        # it is not offerable, that button writes an unselectable model.
        assert default_id in {m["id"] for m in data["models"]}, (
            f"default_model_id {default_id!r} is not in the offered list"
        )


def test_claude_model_sources_carry_a_compact_label_and_hint(opener) -> None:
    """The panel prints ONE line for all sources, so each needs a short form.

    Both fields are display-length summaries and neither may become the only copy
    of the truth: ``short_label`` must not outgrow the width it exists to save,
    and ``hint`` must stay a PREFIX of the full ``error`` the UI keeps on hover. A
    hint that says something the error does not is a second, shorter,
    unverifiable message — and a second copy of this particular message is
    exactly what went stale and got the whole block asked for removal.
    """
    data = _get_json(opener, "/v1/engine/claude-models")
    for src in data["sources"]:
        short = src.get("short_label")
        assert short, f"source {src['id']} has no short_label"
        assert len(short) <= len(src["label"])
        hint = src.get("hint")
        if src["error"]:
            assert hint, f"source {src['id']} has an error but no hint for the one-line form"
            assert len(hint) <= 48, f"hint for {src['id']} is {len(hint)} chars, too long for one line"
            stem = hint[:-1] if hint.endswith("…") else hint
            assert src["error"].startswith(stem), (
                f"hint {hint!r} is not a prefix of error {src['error']!r} — it is a "
                f"second message, not a shortening of the first"
            )
        else:
            assert not hint, f"source {src['id']} is fine but carries a failure hint"


def test_claude_models_reports_at_least_one_reachable_source(opener) -> None:
    """The curated registry is offline, so at minimum IT must answer.

    Zero reachable sources means the picker is empty and the operator cannot
    configure a model at all — the failure mode this endpoint exists to avoid.
    """
    data = _get_json(opener, "/v1/engine/claude-models")
    reachable = [s for s in data["sources"] if s["reachable"]]
    assert reachable, f"no model source is reachable: {data['sources']}"


# ---------------------------------------------------------------------------
# GET /v1/engine/config — the per-tier empty state must be explicable
# ---------------------------------------------------------------------------


def test_engine_config_tiers_account_for_every_classified_turn(opener) -> None:
    """Per-tier ``classified_count`` must sum to ``total_samples``.

    This is the denominator the card uses to explain an empty tier ("0 of 5
    classified turns landed in MEDIUM"). If the parts do not add up to the whole,
    that sentence states a false ratio about real audit-chain data — worse than
    the bare placeholder it replaced, because it looks measured.
    """
    data = _get_json(opener, "/v1/engine/config")
    tiers = data["models"]
    assert set(tiers) == {"corvinOS", "SIMPLE", "MEDIUM", "COMPLEX"}

    per_tier = 0
    for name, tier in tiers.items():
        count = tier["classified_count"]
        assert isinstance(count, int) and count >= 0, f"{name}: bad classified_count {count!r}"
        per_tier += count
        # run_count is outcome samples for (tier, currently-selected model); a
        # tier cannot have learned from more turns than were ever classified into
        # it, and an inversion means the two are being read from different keys.
        assert tier["run_count"] <= count, (
            f"{name} learned from {tier['run_count']} outcomes but only "
            f"{count} turns were ever classified into it"
        )

    assert per_tier == data["total_samples"], (
        f"tiers account for {per_tier} classified turns, total says "
        f"{data['total_samples']} — the card's ratio would be wrong"
    )


# ---------------------------------------------------------------------------
# GET /v1/engine/model-usage
# ---------------------------------------------------------------------------


def test_model_usage_shares_are_internally_consistent(opener) -> None:
    data = _get_json(opener, "/v1/engine/model-usage")
    totals = data["totals"]

    assert data["chain_path_resolved"] is True, (
        "the tenant audit chain path did not resolve — usage cannot be counted"
    )
    if not data["chain_readable"]:
        assert data["models"] == [] and data["totals"]["turns"] == 0
        pytest.skip("no audit chain on disk yet — nothing to cross-check")

    assert totals["turns"] == sum(m["turns"] for m in data["models"])
    assert totals["turns"] == totals["ok"] + totals["failed"] + totals["unfinished"]
    assert totals["total_tokens"] == sum(m["total_tokens"] for m in data["models"])

    if totals["turns"] == 0:
        pytest.skip("chain holds no engine spans yet — nothing to cross-check")

    # Shares are percentages OF THE WHOLE, not normalised to the top row.
    assert abs(sum(m["share_pct"] for m in data["models"]) - 100.0) < 0.5
    assert abs(sum(p["share_pct"] for p in data["providers"]) - 100.0) < 0.5
    if totals["total_tokens"] > 0:
        assert abs(sum(m["token_share_pct"] for m in data["models"]) - 100.0) < 0.5

    for model in data["models"]:
        assert model["provider_source"] in PROVIDER_SOURCES, (
            f"{model['model_id']} has undocumented provenance "
            f"{model['provider_source']!r}"
        )
        # An attributed row must name a provider; an unattributed one must say
        # so rather than borrowing a plausible-looking label.
        if model["provider_source"] == "unresolved":
            assert model["provider"] == "unknown"
        else:
            assert model["provider"] and model["provider"] != "unknown"
        assert model["turns"] == model["ok"] + model["failed"] + model["unfinished"]
        assert model["total_tokens"] == (
            model["input_tokens"] + model["output_tokens"]
            + model["cache_read_tokens"] + model["cache_write_tokens"]
        )

    # Per-provider rollup must reconcile with the model rows it rolls up.
    by_provider: dict[str, int] = {}
    for model in data["models"]:
        by_provider[model["provider"]] = by_provider.get(model["provider"], 0) + model["turns"]
    for prov in data["providers"]:
        assert prov["turns"] == by_provider.get(prov["provider"]), (
            f"provider {prov['provider']} rollup disagrees with its model rows"
        )


# ---------------------------------------------------------------------------
# "No hardcoded mock data" — checked against the BUNDLE THE HOST SERVES
# ---------------------------------------------------------------------------


def _crawl_served_chunks() -> dict[str, str]:
    """Every JS chunk the browser can reach, fetched over HTTP, name → body.

    A one-level scan of the SPA shell is NOT enough and silently passes: the
    engine-config panel is a lazily-imported chunk, so its filename appears only
    inside an eager bundle, never in index.html. Scanning just the shell's eight
    assets made this check vacuous — it proved the mock label was absent from
    files it was never in. Hence the transitive crawl, and hence the positive
    marker asserted below, which fails if the crawl stops short of the panel.
    """
    plain = urllib.request.build_opener()
    with plain.open(f"{BASE}/console/", timeout=30) as resp:
        shell = resp.read().decode("utf-8", "replace")

    pending = set(re.findall(r"assets/[A-Za-z0-9._-]+\.js", shell))
    assert pending, f"no JS assets referenced by the SPA shell: {shell[:200]!r}"

    bodies: dict[str, str] = {}
    while pending:
        asset = pending.pop()
        if asset in bodies:
            continue
        try:
            with plain.open(f"{BASE}/console/{asset}", timeout=60) as resp:
                bodies[asset] = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            # A chunk name assembled at runtime from string fragments can crawl
            # into a 404; a genuinely missing chunk shows up as the positive
            # marker going missing, which is asserted separately.
            continue
        pending |= {
            ref for ref in re.findall(r"assets/[A-Za-z0-9._-]+\.js", bodies[asset])
            if ref not in bodies
        }
    return bodies


def test_served_bundle_carries_no_hardcoded_model_list() -> None:
    """The removed mock label must not be in the JS the browser actually loads.

    Deliberately checked over HTTP rather than against ``dist/`` on disk: a
    correct source diff plus a stale bundle is this repo's most repeated
    frontend failure, and only the served bytes settle it.
    """
    bodies = _crawl_served_chunks()

    # Positive control FIRST: without it, "label not found" is indistinguishable
    # from "the panel's chunk was never fetched".
    panel = [name for name, body in bodies.items() if PANEL_MARKER in body]
    assert panel, (
        f"crawled {len(bodies)} served chunk(s) and none contains "
        f"{PANEL_MARKER!r} — the engine-config panel was not reached, so the "
        f"check below would pass vacuously"
    )

    offenders = [name for name, body in bodies.items() if REMOVED_MOCK_LABEL in body]
    assert not offenders, (
        f"{offenders} still ship the hardcoded model label "
        f"{REMOVED_MOCK_LABEL!r} — the mock list is back"
    )
