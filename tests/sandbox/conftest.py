"""Shared fixtures for the two-tenant sandbox suite (see harness.py).

Proofs live one file per plan item (``test_p0_<item>.py``) so each work package adds its own
file. Fixture order is load-bearing: ``live_tripwire`` is created before the sandbox and every
server, so its session teardown (the final check) runs after all servers have stopped.
"""
from __future__ import annotations

import json
import shutil
import time

import pytest

from ._preconditions import SKIP_REASON

if SKIP_REASON is None:  # otherwise every test module skips itself (see _preconditions.py)
    from . import harness
    from .harness import LiveStateTripwire, Sandbox, Server

MARKER_A = "SBX-A-MARKER-7f3a9c"
MEMORY_FILE_A = "user_sbx_a_fixture.md"
MEMORY_DELETE_TARGET = "user_sbx_delete_target.md"
LEGACY_EVENT_S11A = "legacy.sbx_s11a_marker"

PLANT_STORES = r"""
import json, sys, time
tenant, marker, shared = sys.argv[1], sys.argv[2], sys.argv[3]
done = []
sys.path.insert(0, shared)  # the stores import their siblings (_compat_fcntl) by bare name
import roles, quota, consent, disclosure
for name, mod in (("roles", roles), ("quota", quota), ("consent", consent), ("disclosure", disclosure)):
    try:
        path = mod._store_path("discord", "sbxachat", tenant_id=tenant)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({marker: {"role": "member", "action": "joined",
                                             "granted_at": time.time(), "note": marker}}))
        done.append(name)
    except Exception as exc:
        done.append(f"{name}:FAILED:{type(exc).__name__}")
print(json.dumps(done))
"""


@pytest.fixture(scope="session")
def live_tripwire():
    trip = LiveStateTripwire()
    trip.markers.update({MARKER_A, LEGACY_EVENT_S11A})
    yield trip
    # Final check: every server has stopped and the sandbox is gone; the markers were
    # captured while it existed (sandbox fixture teardown), so the check is not empty.
    assert trip.captured_needles and trip.captured_voice, "final check would run without markers"
    problems = trip.check()
    assert not problems, f"LIVE STATE CHANGED: {problems[:10]}"


@pytest.fixture(autouse=True)
def _live_check_after_each_test(live_tripwire):
    yield
    problems = live_tripwire.check()
    assert not problems, f"LIVE STATE CHANGED: {problems[:10]}"


@pytest.fixture(scope="session")
def sandbox(live_tripwire):
    sb = Sandbox.create()
    live_tripwire.sandboxes.append(sb)
    yield sb
    live_tripwire.capture()  # instance id + chain-key names, before the files disappear
    shutil.rmtree(sb.root, ignore_errors=True)


def stop_checked(srv: Server) -> None:
    """Origin check on the server's LAST module dump, then stop it; fail the teardown on drift."""
    try:
        problems = srv.final_origin_problems()
    finally:
        srv.stop()
    assert not problems, f"{srv.target} ran code outside REPO_ROOT: {problems[:5]}"


@pytest.fixture(scope="session")
def routes(sandbox):
    return harness.list_app_routes(sandbox)


def write_github_config(sb: Sandbox, tenant: str, *, url: str, auto_sync: bool) -> None:
    home = sb.tenant_home(tenant)
    home.mkdir(parents=True, exist_ok=True)
    (home / "github-config.json").write_text(json.dumps({"url": url, "auto_sync": auto_sync}),
                                             encoding="utf-8")


@pytest.fixture(scope="session")
def planted(sandbox):
    """Tenant-A data in the stores the plan names; computed without importing product code."""
    mem_dir = sandbox.home / ".claude" / "projects" / harness.memory_slug(harness.REPO_ROOT) / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    for name in (MEMORY_FILE_A, MEMORY_DELETE_TARGET):
        (mem_dir / name).write_text(
            f"---\nname: sbx a fixture\ndescription: {MARKER_A}\ntype: user\n---\n{MARKER_A}\n",
            encoding="utf-8")
    # S-1b: the shared KG plugin config under HOME.
    kg = sandbox.home / ".claude" / "plugins" / "corvin-knowledge.json"
    kg.parent.mkdir(parents=True, exist_ok=True)
    kg.write_text(json.dumps({"repo_path": f"/nonexistent/{MARKER_A}",
                              "remote_url": f"https://github.com/{MARKER_A}/kb"}), encoding="utf-8")
    # S-9i: tenant A's GitHub config.
    write_github_config(sandbox, harness.TENANT_A, url=f"https://github.com/{MARKER_A}/repo",
                        auto_sync=False)
    # Tenant A's own forge chain (a plain record; this chain is not verified by the proofs).
    chain = sandbox.tenant_home(harness.TENANT_A) / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True, exist_ok=True)
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "event_type": "console.sbx_a_marker",
                             "tenant_id": harness.TENANT_A, "details": {"note": MARKER_A}}) + "\n")
    # S-5f stores (roles, quota, consent, disclosure) through each store's own path function.
    shared = str(harness.REPO_ROOT / "corvin_operator" / "bridges" / "shared")
    stores = json.loads(
        sandbox.run_child(PLANT_STORES, harness.TENANT_A, MARKER_A, shared).strip().splitlines()[-1])
    return {"memory_dir": mem_dir, "stores": stores}


@pytest.fixture(scope="session")
def server(sandbox, planted, live_tripwire):
    srv = Server(sandbox, Server.CONSOLE, "console").start()
    yield srv
    stop_checked(srv)


@pytest.fixture(scope="session")
def session_for(sandbox, server):
    cache: dict[str, harness.Session] = {}

    def get(tenant: str) -> harness.Session:
        if tenant not in cache:
            cache[tenant] = harness.mint_session(sandbox, tenant)
        return cache[tenant]

    return get


@pytest.fixture(scope="session")
def sess_a(session_for):
    return session_for(harness.TENANT_A)


@pytest.fixture(scope="session")
def sess_b(session_for):
    return session_for(harness.TENANT_B)


GATEWAY_RESUME_TENANT = "sbxg"
GATEWAY_RESUME_URL = "https://github.com/x/yg"


@pytest.fixture(scope="session")
def gateway(sandbox, planted, live_tripwire):
    """The production host (``corvin_gateway.app``), started after a pre-existing
    ``auto_sync: true`` config is planted for ``sbxg`` (restart shape for S-9i)."""
    write_github_config(sandbox, GATEWAY_RESUME_TENANT, url=GATEWAY_RESUME_URL, auto_sync=True)
    srv = Server(sandbox, Server.GATEWAY, "gateway").start()
    yield srv
    stop_checked(srv)
