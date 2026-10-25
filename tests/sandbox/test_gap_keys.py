"""API Keys (gap 7): a stored provider key can be deleted, with re-auth and audit.

Red on origin/main (925406772): ``routes/byok.py`` has no ``DELETE /byok/secrets/{key}`` (405), so a
stored key could only be rotated, never removed. Green: the route removes the key from the vault and
from service.env, refuses an empty or wrong re-auth token (nothing removed), answers 404 for a key
that is not stored, and records ``byok.secret_deleted`` (key name only) in the tenant chain.
"""
from __future__ import annotations

import json

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .helpers_gap import action_records, verify_tenant_chain  # noqa: E402

KEY = "custom_sbxdemo"
ENV_LINE = "CORVIN_CUSTOM_SBXDEMO=sbx-demo-value-1234"
SERVICE_ENV_PROBE = r"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1])))
import provider_keys
print(str(provider_keys.service_env_path()))
"""


@pytest.fixture
def env_file(sandbox):
    path = __import__("pathlib").Path(
        sandbox.run_child(SERVICE_ENV_PROBE, str(harness.REPO_ROOT / "corvin_operator" / "bridges" / "shared")
                          ).strip().splitlines()[-1])
    assert str(path).startswith(str(sandbox.root)), f"service.env would leave the sandbox: {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"OTHER_KEY=keep-me\n{ENV_LINE}\n", encoding="utf-8")
    return path


def _fp(server, sess) -> str:
    return harness.fetch(server, sess, "/auth/whoami").json()["fingerprint"]


def _delete(server, sess, token):
    body = {} if token is None else {"re_auth_token": token}
    return harness.fetch(server, sess, f"/byok/secrets/{KEY}", method="DELETE", json_body=body)


def test_G_keys_delete_refuses_a_missing_or_wrong_token_and_removes_nothing(sandbox, server, session_for, env_file):
    sess = session_for("_default")
    for token in (None, "", "wrong-token"):
        r = _delete(server, sess, token)
        assert r.status_code == 401, (token, r.status_code, r.text[:200])
    assert ENV_LINE in env_file.read_text(), "a refused delete removed the key"


def test_G_keys_delete_removes_the_key_and_keeps_other_keys(sandbox, server, session_for, env_file):
    sess = session_for("_default")
    r = _delete(server, sess, _fp(server, sess))
    assert r.status_code == 200, r.text[:300]
    text = env_file.read_text()
    assert "CORVIN_CUSTOM_SBXDEMO" not in text, "the key is still in service.env"
    assert "OTHER_KEY=keep-me" in text, "an unrelated key was removed"
    recs = action_records(sandbox, "_default", "byok.secret_deleted")
    assert recs and recs[-1]["event_type"] == "console.action_performed"
    assert KEY in json.dumps(recs[-1]) and "sbx-demo-value" not in json.dumps(recs[-1]), \
        "the audit record must name the key, never its value"
    assert verify_tenant_chain(sandbox, "_default")["ok"]


def test_G_keys_delete_of_an_absent_key_is_404_and_audited_as_failure(sandbox, server, session_for, env_file):
    sess = session_for("_default")
    env_file.write_text("OTHER_KEY=keep-me\n", encoding="utf-8")  # the key is not stored any more
    r = _delete(server, sess, _fp(server, sess))
    assert r.status_code == 404, r.text[:200]
    fails = [x for x in action_records(sandbox, "_default", "byok.secret_deleted")
             if x["event_type"] == "console.action_failed"]
    assert fails, "the failed delete is not in the chain"
    assert verify_tenant_chain(sandbox, "_default")["ok"]
