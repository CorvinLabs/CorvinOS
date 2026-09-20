"""E2E: the Skill Creator absorbed /app/skill-forge-generator (2026-09-20).

Two surfaces claimed to create a skill:

  /app/forge?tab=skill-forge
                           SkillForgePanel → POST /v1/console/skill-creator/generate
                           (5-phase LDD orchestration) — real. The tab's id was
                           ``creator`` until the rename later the same day;
                           forge.tsx still honours it as an alias.
  /app/skill-forge-generator
                           a form → POST /v1/skill-forge/generate, declared on a
                           FLASK blueprint (``core/console/corvin_console/routes/
                           skill_forge_api.py``) inside a FastAPI app. Nothing
                           imported it, no app mounted it, and the live host
                           answered **404** — so every click on "Generate Skill"
                           ended in ``API error: 404``. The form was real; its
                           backend was not.

The merge moved that form into the Skill Forge tab as its "From template" composer
and wired it to ``POST /v1/console/skills/manual`` — the route that writes
THROUGH the canonical SkillForge registry, which is also the registry the
Creator's library lists. This file proves the three claims that merge rests on,
each across a REAL boundary (HTTP against the running console), never by
importing the target and calling it:

  1. the dead endpoint is still dead and the SPA no longer calls it
  2. the template composer is in the JavaScript the browser actually loads
  3. a skill created the way that composer creates one really lands in the
     registry, and really shows up in the Creator's library

Run against the live console (``corvin-webui.service`` on :8765). Skipped, not
failed, when nothing is listening — a bundle proof needs a served bundle.
"""
from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request
import uuid

import pytest

from ._console_chunks import (
    BASE,
    assert_present_then_absent,
    console_up,
    crawl_served_chunks,
)

CONSOLE = f"{BASE}/v1/console"

pytestmark = pytest.mark.skipif(
    not console_up(), reason="console not listening on 127.0.0.1:8765"
)

#: A string only the merged template composer contains (its form's test id).
#: Used as the positive control: without it, an absence assertion below would
#: pass simply because the crawl never reached the Creator's chunk.
MARKER_PRESENT = "template-skill-form"

#: The dead generator's endpoint. Its absence is the claim under test — hence
#: the positive control above must be asserted first.
MARKER_ABSENT = "/v1/skill-forge/generate"


@pytest.fixture(scope="module")
def served_chunks() -> dict[str, str]:
    """Every JS chunk reachable from the SPA shell, crawled transitively."""
    return crawl_served_chunks()


@pytest.fixture(scope="module")
def opener() -> urllib.request.OpenerDirector:
    """A cookie-carrying opener holding a real console session.

    ``/auth/local-login`` is a GET: loopback-only and credential-less, the TCP
    peer IS the authorisation (POSTing to it returns 405).
    """
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with op.open(f"{CONSOLE}/auth/local-login", timeout=15) as resp:
        assert resp.status == 200, f"local-login returned {resp.status}"
    assert len(jar) > 0, "local-login set no session cookie"
    return op


@pytest.fixture(scope="module")
def csrf(opener: urllib.request.OpenerDirector) -> str:
    with opener.open(f"{CONSOLE}/auth/whoami", timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = payload.get("csrf_token")
    assert token, f"no csrf_token in whoami payload: {sorted(payload)}"
    return token


def _request(
    op: urllib.request.OpenerDirector,
    method: str,
    path: str,
    *,
    csrf_token: str | None = None,
    body: dict | None = None,
    timeout: int = 60,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{CONSOLE}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if csrf_token:
        req.add_header("X-CSRF-Token", csrf_token)
    with op.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        return resp.status, json.loads(raw)


# ---------------------------------------------------------------------------
# 1. The removed backend stays removed, and nothing calls it any more
# ---------------------------------------------------------------------------


def test_the_generator_endpoint_is_not_mounted() -> None:
    """404, not 401.

    The distinction is the whole point: 401 would mean the route exists behind
    a session gate (and deleting the page would have removed a capability),
    404 means it was never mounted — which is what the page was posting into.
    """
    plain = urllib.request.build_opener()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        plain.open(
            urllib.request.Request(
                f"{BASE}/v1/skill-forge/generate",
                data=b'{"name":"x","description":"y"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=30,
        )
    assert excinfo.value.code == 404, (
        f"expected 404 (route not mounted), got {excinfo.value.code} — if this "
        f"is now 401 the route exists and this merge's premise is wrong"
    )


def test_served_bundle_has_the_composer_and_not_the_dead_endpoint(
    served_chunks: dict[str, str],
) -> None:
    """Positive control BEFORE the absence check.

    A one-level scan of the SPA shell would pass vacuously here: the Creator is
    a lazily imported chunk, so its contents appear in none of the eager
    bundles. ``assert_present_then_absent`` fails loudly when the crawl never
    reached the chunk that the removed string used to live in.
    """
    assert len(served_chunks) > 1, "crawl found a single chunk — it stopped short"
    assert_present_then_absent(served_chunks, MARKER_PRESENT, MARKER_ABSENT)


def test_the_old_route_redirects_rather_than_404ing(
    served_chunks: dict[str, str],
) -> None:
    """Existing bookmarks keep working: the address survives, the panel does not.

    The redirect is a client-side ``<Navigate>``, so the proof is that the SPA
    ships the pair (old path → Creator tab) rather than an HTTP status.
    """
    routed = [
        name
        for name, body in served_chunks.items()
        if "skill-forge-generator" in body and "/app/forge?tab=skill-forge" in body
    ]
    assert routed, (
        "no served chunk pairs the retired path with its redirect target — "
        "an existing /app/skill-forge-generator bookmark would hit the 404 page"
    )


def test_the_retired_tab_id_is_still_honoured(served_chunks: dict[str, str]) -> None:
    """``?tab=creator`` must still resolve, not fall through to the default.

    The tab was renamed from ``creator`` to ``skill-forge`` hours after the
    merge, and the merge's own redirect pointed at the old id. A link minted in
    between would otherwise open the default tab with no error — the worst kind
    of breakage, because it looks like it worked.
    """
    aliased = [
        name
        for name, body in served_chunks.items()
        if "TAB_ALIASES" in body or ('creator:' in body and "skill-forge" in body)
    ]
    assert aliased, (
        "no served chunk carries the creator → skill-forge alias; "
        "?tab=creator would silently open the default tab"
    )


def test_skill_forge_is_the_first_tab_and_the_default(
    served_chunks: dict[str, str],
) -> None:
    """Leftmost tab and opening tab are the same one.

    A tab bar whose first entry is not the one that opens reads as a bug, and
    the two facts live in different constants — the ordered id list and the
    default — so nothing but a check keeps them together.
    """
    hits = [
        body
        for body in served_chunks.values()
        if '"skill-forge","tools","skills"' in body.replace(" ", "")
        or "'skill-forge','tools','skills'" in body.replace(" ", "")
    ]
    assert hits, (
        "the served bundle does not order Forge's tabs skill-forge → tools → "
        "skills; either the order changed or the crawl missed the page chunk"
    )


# ---------------------------------------------------------------------------
# 2. The route the composer now posts to really creates a skill
# ---------------------------------------------------------------------------


def test_template_composer_route_is_mounted_and_gated_not_missing(
    opener: urllib.request.OpenerDirector, csrf: str
) -> None:
    """The composer's route EXISTS — which is the whole difference to the one
    it replaced.

    ``/v1/skill-forge/generate`` answers 404: not mounted, nothing behind it.
    ``/skills/manual`` answers 200 (created) or 400 ``license_required`` (the
    registry's `forge.create` capability gate refuses a free tier BEFORE
    writing anything). Either is a live route; neither is a 404. A test that
    only asserted "200" would go red on an unlicensed install and say nothing
    about the merge.
    """
    name = f"assistant.e2e_merge_probe_{uuid.uuid4().hex[:8]}"
    try:
        status, _ = _request(
            opener, "POST", "/skills/manual", csrf_token=csrf,
            body={"name": name, "body": "# probe\n\nreachability probe\n"},
        )
        assert status == 200
        _request(opener, "DELETE", f"/skills/manual/{name}", csrf_token=csrf)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        assert exc.code != 404, (
            "/skills/manual is not mounted — the template composer posts into "
            "the void, exactly like the page it replaced"
        )
        assert exc.code == 400 and "license_required" in detail, (
            f"unexpected refusal {exc.code}: {detail[:300]}"
        )


def _license_allows_creation(op: urllib.request.OpenerDirector, token: str) -> bool:
    """True when this install's licence tier permits `forge.create`."""
    probe = f"assistant.e2e_license_probe_{uuid.uuid4().hex[:8]}"
    try:
        _request(op, "POST", "/skills/manual", csrf_token=token,
                 body={"name": probe, "body": "# probe\n\nlicence probe\n"})
    except urllib.error.HTTPError:
        return False
    _request(op, "DELETE", f"/skills/manual/{probe}", csrf_token=token)
    return True


def test_template_composer_route_creates_a_skill_visible_in_the_creator_library(
    opener: urllib.request.OpenerDirector, csrf: str
) -> None:
    """The merge's load-bearing claim, end to end over HTTP.

    The template composer POSTs exactly this shape. What makes the merge
    coherent — rather than two lists that disagree — is that the skill then
    appears in ``/skill-creator/skills``, the list the Creator tab renders
    under BOTH composers: ``skills_manual`` writes at scope ``user`` under
    ``<tenant_home>/skill-forge``, which is the same registry root
    ``skill_creator_api._registry_root`` reads.

    Creates a uniquely named skill and deletes it again, so a repeat run on a
    live install leaves nothing behind.

    Skipped — explicitly, never silently — when the licence tier refuses
    `forge.create`: the registry then writes nothing at all, so there is no
    skill whose registry membership could be observed. The reachability of the
    route itself is asserted unconditionally by the test above; this one needs
    a write to actually happen.
    """
    if not _license_allows_creation(opener, csrf):
        pytest.skip(
            "licence tier refuses forge.create — no write happens, so the "
            "registry-membership claim cannot be observed on this install"
        )

    name = f"assistant.e2e_merge_probe_{uuid.uuid4().hex[:8]}"
    body = (
        f"# E2E Merge Probe\n\n"
        f"**Type:** Learned Experience\n\n"
        f"## Context\n\nCreated by test_skill_creator_merge_e2e; safe to delete.\n\n"
        f"## The Pattern\n\nProves the template composer's route writes to the "
        f"registry the Creator tab lists.\n"
    )

    status, created = _request(
        opener, "POST", "/skills/manual", csrf_token=csrf, body={"name": name, "body": body}
    )
    assert status == 200, f"create returned {status}: {created}"
    assert created.get("ok") is True, created
    assert created.get("scope") == "user", (
        f"manual skills are written at scope 'user' — got {created.get('scope')!r}. "
        f"The composer deliberately renders no scope control; if this changes, "
        f"that decision has to be revisited."
    )
    assert created.get("sha256"), "no content hash — the registry did not bind the body"

    try:
        status, library = _request(opener, "GET", "/skill-creator/skills")
        assert status == 200, f"library returned {status}"
        names = {s["name"] for s in library.get("skills", [])}
        assert name in names, (
            f"{name!r} was created but is absent from the Creator's library "
            f"({len(names)} skills listed) — the two composers would be writing "
            f"into different registries, which is exactly what the merge claims "
            f"they do not"
        )

        entry = next(s for s in library["skills"] if s["name"] == name)
        assert entry["injectable"] is False, (
            "a freshly created, ungraded skill must report itself as inert — it "
            "sits below skill_inject's eligibility gate and is never injected, "
            "and the UI must not imply otherwise"
        )
    finally:
        _request(opener, "DELETE", f"/skills/manual/{name}", csrf_token=csrf)

    status, library = _request(opener, "GET", "/skill-creator/skills")
    assert name not in {s["name"] for s in library.get("skills", [])}, (
        "cleanup failed — the probe skill is still registered"
    )
