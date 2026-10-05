"""Adversarial review round 4 (chat + A2A, 2026-10-05) — regression tests.

Each test pins one verified finding:

* SSRF: a peer-supplied base URL with a query/fragment/userinfo/dot-segment
  swallowed the fixed suffix every caller appends;
* a signed rejection mapped a full nonce budget to "replay" and a revoked
  identity to "this instance has none";
* a slow probe finishing after a newer one rolled presence back;
* the feed's seq restarted at 0 when the seq file was empty;
* feed erasure by label wiped every peer sharing it;
* group membership edits raced the message writer (no lock);
* the chat meta writer shared one temp name across threads.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "corvin_operator" / "bridges" / "shared", _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ── SSRF URL shape ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://10.0.0.5:9000/admin/reset?x=",
    "http://10.0.0.5:9000/a#",
    "http://10.0.0.5:9000/a?",
    "http://user:pw@10.0.0.5:9000",
    "http://10.0.0.5:9000/a/../admin",
    "http://10.0.0.5:9000/a/./b",
    "http://10.0.0.5:9000/a%2f..",
    "http://10.0.0.5:9000/a;b",
    "http://10.0.0.5:9000/a\\b",
])
def test_peer_url_with_suffix_swallowing_shape_is_refused(url):
    import a2a_friendship as ft
    assert ft._url_shape_rejection(urlsplit(url), "test", url) is not None
    assert ft._ack_url_rejection_reason(url) is not None


@pytest.mark.parametrize("url", ["http://10.0.0.5:9000", "https://host.example/corvin"])
def test_plain_base_url_and_proxy_prefix_pass_the_shape_check(url):
    import a2a_friendship as ft
    assert ft._url_shape_rejection(urlsplit(url), "test", url) is None


# ── public rejection reasons ───────────────────────────────────────────────

@pytest.mark.parametrize("internal,public", [
    ("nonce_origin_quota_exceeded", "rate_limited"),
    ("nonce_store_full", "rate_limited"),
    ("replay", "replay"),
    ("network_attestation_revoked", "identity_revoked"),
    ("network_attestation_pairing_revoked", "identity_revoked"),
    ("network_attestation_instance_revoked", "identity_revoked"),
    ("network_attestation_time_window", "clock_skew"),
    ("network_attestation_required", "identity_required"),
])
def test_rejection_reason_is_mapped_to_the_true_cause(internal, public):
    import remote_trigger_receiver as rtr
    assert rtr.public_rejection_reason(internal) == public


def test_every_public_reason_has_a_sender_text_on_the_template_allowlist():
    import remote_trigger_sender as rts
    for token, text in rts._PUBLIC_REJECTION_TEXT.items():
        assert text in rts._ERROR_DETAIL_TEMPLATES, token
    assert "identity_revoked" in rts._PUBLIC_REJECTION_TEXT


# ── presence is monotonic ──────────────────────────────────────────────────

def test_a_stale_probe_result_does_not_roll_presence_back():
    import a2a_friendship as ft
    cfg: dict = {}
    ft.stamp_probe(cfg, True, 1000.0)           # fresh OK from the presence loop
    ft.stamp_probe(cfg, False, 960.0)           # a slower recheck that started earlier
    assert cfg["_last_check_at"] == 1000.0 and cfg["_last_ok_at"] == 1000.0


def test_a_corrected_clock_is_not_mistaken_for_a_stale_probe():
    import a2a_friendship as ft
    cfg: dict = {}
    ft.stamp_probe(cfg, True, 10_000.0)         # stamped while the clock ran an hour fast
    ft.stamp_probe(cfg, False, 10_000.0 - 3600)
    assert cfg["_last_check_at"] == 10_000.0 - 3600
    assert "_last_ok_at" not in cfg


# ── feed seq + erasure ─────────────────────────────────────────────────────

def test_empty_seq_file_continues_above_every_handed_out_seq(tmp_path):
    import a2a_feed
    (tmp_path / "messages.jsonl").write_text(
        "\n".join(json.dumps({"seq": n, "peer_id": "p"}) for n in (1, 2, 7)) + "\n")
    (tmp_path / "seq").write_text("")
    assert a2a_feed._next_seq(tmp_path) == 8


def test_feed_erasure_matches_the_peer_id_not_a_shared_label(tmp_path, monkeypatch):
    import a2a_feed
    monkeypatch.setattr(a2a_feed, "feed_dir", lambda tenant_id=None: tmp_path)
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    recs = [{"seq": 1, "peer_id": "p-subject", "peer_label": "Laptop", "text": "a"},
            {"seq": 2, "peer_id": "p-other", "peer_label": "Laptop", "text": "b"}]
    (root / "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    a2a_feed.erase_peer("Laptop", tenant_id="_default")
    left = [json.loads(line)["peer_id"] for line in (root / "messages.jsonl").read_text().splitlines() if line]
    assert left == ["p-subject", "p-other"]
    a2a_feed.erase_peer("p-subject", tenant_id="_default")
    left = [json.loads(line)["peer_id"] for line in (root / "messages.jsonl").read_text().splitlines() if line]
    assert left == ["p-other"]


# ── group membership edits take the group lock ─────────────────────────────

def test_participant_edit_is_refused_busy_while_another_writer_holds_the_lock(tmp_path):
    from corvin_console import chat_group_store as store
    import a2a_friendship as ft
    g = store.create_group(tmp_path, tenant_id="_default", title="G",
                           created_by_participant_id="operator")
    gid = g["group_id"]
    gdir = store._group_dir(tmp_path, gid)
    held, release = threading.Event(), threading.Event()

    def _holder():
        with ft.config_file_lock(gdir):
            held.set()
            release.wait(10)

    t = threading.Thread(target=_holder)
    t.start()
    try:
        assert held.wait(5)
        with pytest.raises(store.ChatGroupBusy):
            store.add_participant(tmp_path, gid, participant_id="x", kind="a2a_peer",
                                  display_name="X", peer_endpoint_id="x", added_by="operator")
    finally:
        release.set()
        t.join()
    store.add_participant(tmp_path, gid, participant_id="x", kind="a2a_peer",
                          display_name="X", peer_endpoint_id="x", added_by="operator")
    assert "x" in [p["participant_id"] for p in store.get_group(tmp_path, gid)["participants"]]
