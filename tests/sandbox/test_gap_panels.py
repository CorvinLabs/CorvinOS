""""Your panels" (gap 7): the chat install path is gated and audited like POST /panels.

Red on origin/main (925406772): ``chat_runtime._install_generated_panels`` stored every
``corvin-panel*.html`` a chat turn wrote, with no ``forge.create`` capability check and no
``console.panel_created`` record. The sandbox has no licence key, so the tenant is on the free
tier, where POST /panels answers 402. Green: the chat path refuses the same way, audits the
denial, stores nothing, and still consumes the generated file.

Driven in a child process of the sandbox (the real function, the real gate, the real audit writer)
because a chat turn needs a live engine; the real-LLM turn is NOT part of this proof.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .helpers_gap import action_records, verify_tenant_chain  # noqa: E402

TENANT = "_default"  # the audit writer is one tenant per process (AuditTenantMismatch)

INSTALL = r"""
import json, sys
from pathlib import Path
from corvin_console import chat_runtime
workdir = Path(sys.argv[1])
out = chat_runtime._install_generated_panels(sys.argv[2], workdir)
print(json.dumps({"installed": [m.get("id") for m in out],
                  "left": sorted(p.name for p in workdir.glob("corvin-panel*.html"))}))
"""


@pytest.fixture(scope="module")
def install_result(sandbox):
    wd = sandbox.root / "chat-workdir"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "corvin-panel-sbx.html").write_text(
        "<html><head><title>Sandbox Panel</title></head><body>hello</body></html>", encoding="utf-8")
    raw = sandbox.run_child(INSTALL, str(wd), TENANT)
    return json.loads(raw.strip().splitlines()[-1]), sandbox.tenant_home(TENANT) / "global" / "console_panels"


def test_G_panels_chat_install_is_refused_on_the_free_tier(install_result, server, session_for):
    result, store = install_result
    assert result["installed"] == [], f"a free-tier chat turn installed a panel: {result}"
    assert not (store / "sandbox-panel" / "index.html").exists()
    assert "corvin-panel-sbx.html" not in result["left"], "the refused file was not consumed"


def test_G_panels_chat_install_denial_is_audited(install_result, sandbox):
    recs = action_records(sandbox, TENANT, "panel.install")
    assert recs, "the refused panel install left no audit record"
    assert recs[-1]["event_type"] == "console.action_denied"
    assert verify_tenant_chain(sandbox, TENANT)["ok"]


def test_G_panels_post_route_still_refuses_the_same_tenant(server, session_for):
    sess = session_for(TENANT)
    r = harness.fetch(server, sess, "/panels", method="POST", json_body={
        "id": "sandbox-panel", "title": "Sandbox Panel", "html": "<p>x</p>"})
    assert r.status_code == 402, r.text[:200]
