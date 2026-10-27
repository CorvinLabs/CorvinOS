"""T-0302 / ADR-2287: a large object travels as a signed reference and is pulled over a signed op.

Real transports only (harness of ``test_a2a_attachment_limit_measured_e2e.py``):

* **direct** — real ``a2a_http_server`` peer on 127.0.0.1, real ``RemoteTriggerSender``, real HMAC pairing;
* **relay** — a LOCAL relay started from this repository (``a2a_relay.build_relay_router`` under uvicorn on
  127.0.0.1); the peer's direct URL is dead, so the task, the response and every pull cross the relay. No
  hosted relay and no external host is ever contacted.

The peer ("holder") is B: its worker writes the files (the model call is the only stand-in), its receiver
issues the reference, its ping path serves the pulls. The requester A pulls with
``RemoteTriggerSender.pull_object``. Audit records go to the REAL tenant chain of a sandboxed
``CORVIN_HOME`` and the chain is verified after each test.

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_large_object_reference_e2e.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import unittest
import unittest.mock as mock
import urllib.request
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_repo = _here.parents[2]
for _p in (str(_repo), str(_repo / "corvin_operator" / "forge")):
    if _p not in sys.path:
        sys.path.append(_p)

import a2a_attachments  # noqa: E402
import a2a_http_server  # noqa: E402
import a2a_objects  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402
from a2a_attachments import Attachment  # noqa: E402
from test_a2a_attachment_limit_measured_e2e import (  # noqa: E402,F401  (harness + module setup)
    IID_B as IID_B_, TransportCase, setUpModule, tearDownModule,
)

CAP = a2a_attachments.MAX_ATTACHMENTS_TOTAL_BYTES
SCHEMA = {"properties": {"ok": {"type": "boolean"}}}
# references are opt-in (review finding 5): only a requester that can pull asks for them
ASK_REFS = {**SCHEMA, "attachments_by_reference": True}
BIG = 12 * 1024 * 1024 + 12345          # above 10 MiB (ADR-2287 evidence), not a chunk multiple


def _blob(n: int, seed: int = 7) -> bytes:
    """A deterministic, incompressible-looking binary media object (PNG signature in front): it
    classifies INTERNAL, the media default. Bytes of no known format are CONFIDENTIAL (review finding 3)."""
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    block = hashlib.sha256(bytes([seed])).digest()
    while len(out) < n:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:n])


def _chain_path(home: Path) -> Path:
    return home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"


def _records(home: Path) -> list[dict]:
    p = _chain_path(home)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


class _ObjectCase(TransportCase):
    """Real chain (forge_se None) + helpers for one reference round trip."""

    forge_se = None

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(self._chain_verifies)

    # ── helpers ──────────────────────────────────────────────────────────

    def _chain_verifies(self) -> None:
        from forge import security_events as real_se

        chain = _chain_path(self.home)
        if not chain.exists():
            return
        ok, problems = real_se.verify_chain(chain)
        self.assertTrue(ok, f"tenant audit chain does not verify after the run: {problems[:3]}")

    def produce(self, files: dict[str, bytes], *, kid: "str | None" = None, schema: "dict | None" = None):
        self.engine.files = files
        return self.pair.sender.send(kid or self.pair.kid, "produce the files", result_schema=schema or ASK_REFS,
                                     ttl_s=60, timeout_s=90)

    def one_ref(self, res) -> dict:
        self.assertTrue(res.ok, res)
        self.assertEqual(len(res.attachment_refs), 1, res)
        return res.attachment_refs[0]

    def events(self, name: str) -> list[dict]:
        return [r for r in _records(self.home) if r.get("event_type") == name]

    def holder_meta(self, ref_id: str) -> dict:
        return json.loads((a2a_objects.store_dir("_default") / f"{ref_id}.json").read_text())

    def write_holder_meta(self, ref_id: str, **changes: Any) -> None:
        p = a2a_objects.store_dir("_default") / f"{ref_id}.json"
        p.write_text(json.dumps({**json.loads(p.read_text()), **changes}))

    def raw_pull(self, ref_id: str, offset: int, length: int, *, kid: "str | None" = None):
        """One signed pull through the real transport, returning the verified response dict."""
        ok, resp, _cat, _via = self.pair.sender._object_pull_request(kid or self.pair.kid, ref_id, offset, length)
        self.assertTrue(ok, f"the pull itself must be answered and verify: {_cat}")
        return resp

    def resend_raw(self, request: dict, kid: "str | None" = None) -> dict:
        """Re-send an already signed ping/pull request unchanged, over the same transport."""
        cfg = self.pair.sender._registry.load(kid or self.pair.kid)
        if self.transport == "direct":
            base = cfg["url"][: -len("/v1/a2a/receive")]
            raw = rts.RemoteTriggerSender._http_post(base + "/v1/a2a/ping", request, 10)
        else:
            # the same signed payload through the relay; the replayer brings its own reply slot (the
            # original one is pinned to the first sender's one-time credential)
            import a2a_friendship as ft

            raw = rts._relay_round_trip(
                relay_url=ft.get_my_relay_url(), hmac_key=cfg["hmac_key"], to_kid=kid or self.pair.kid,
                my_kid=f"{kid or self.pair.kid}:reply:{secrets.token_hex(8)}",
                my_relay_auth_key=secrets.token_hex(32),
                payload={**request, "_relay_sender_instance_id": "aaaaaaaa-0000-4000-8000-aaaaaaaaaaaa"},
                correlation_id=request["ping_id"], timeout_s=10,
                sender_instance_id="aaaaaaaa-0000-4000-8000-aaaaaaaaaaaa")
        resp, signed = rts.RemoteTriggerSender._verify_response(raw, cfg["recv_key"],
                                                                expected_task_id=request["ping_id"])
        self.assertTrue(signed)
        return resp


class _ObjectScenarios:
    """Every proof, run once per transport."""

    # ── the round trip ───────────────────────────────────────────────────

    def test_large_object_travels_by_reference_and_is_pulled_with_matching_digest(self):
        big, small = _blob(BIG), b"small,inline\n1,2\n"
        res = self.produce({"big.bin": big, "small.csv": small})
        ref = self.one_ref(res)
        # the reference: exactly the ADR-2287 keys, no content, signed with the response
        self.assertEqual(set(ref), {"name", "mime", "size", "sha256", "classification", "ref_id", "inline"})
        self.assertEqual((ref["name"], ref["size"], ref["inline"]), ("big.bin", BIG, False))
        self.assertEqual(ref["sha256"], hashlib.sha256(big).hexdigest())
        # the small file stays inline and byte-identical to the pre-ADR-2287 wire shape
        self.assertEqual(res.attachments, [Attachment.from_bytes(name="small.csv", mime="text/csv",
                                                                 content=small).to_dict()])
        pulled = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertTrue(pulled.ok, pulled)
        self.assertEqual(pulled.data, big)
        self.assertEqual(pulled.sha256, ref["sha256"])
        self.assertEqual(pulled.via, self.transport)
        # audit: issued once; a sequential pull writes two records (start, before any byte leaves,
        # and complete with the chunk count) instead of one per chunk (review finding 11)
        issued, chunks = self.events("A2A.object_issued"), self.events("A2A.object_pulled")
        self.assertEqual(len(issued), 1)
        n_chunks = -(-BIG // a2a_objects.CHUNK_MAX_BYTES)
        self.assertEqual([c["details"]["stage"] for c in chunks], ["start", "complete"])
        self.assertEqual(chunks[-1]["details"]["chunks"], n_chunks)
        self.assertEqual(chunks[-1]["details"]["served_bytes"], BIG)
        d = issued[0]["details"]
        self.assertEqual((d["ref_prefix"], d["size"], d["sha_prefix"], d["classification"], d["origin_id"]),
                         (ref["ref_id"][:8], BIG, ref["sha256"][:16], "INTERNAL", self.pair.kid))
        self.assertEqual(self.events("A2A.object_fetched")[-1]["details"]["status"], "ok")
        chain_text = _chain_path(self.home).read_text()
        for leak in ("big.bin", "small.csv", ref["sha256"], ref["ref_id"]):
            self.assertNotIn(leak, chain_text, "the chain carries prefixes and metadata only")
        for r in [*issued, *chunks]:
            self.assertFalse(r["details"].get("_dropped_fields"), r)

    def test_a_default_send_does_not_ask_for_references(self):
        """Review finding 5: only a caller that pulls asks for references. A plain ``send`` (console
        feed, groups, outbox, federation, MCP) keeps the byte_cap report and leaves nothing held."""
        self.engine.files = {"big.bin": _blob(CAP + 1)}
        res = self.pair.sender.send(self.pair.kid, "produce the files", result_schema=SCHEMA, ttl_s=60,
                                    timeout_s=90)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.attachment_refs, [])
        self.assertEqual(res.data["attachments_dropped"], {"count": 1, "reasons": {"byte_cap": 1}})
        self.assertEqual(self.events("A2A.object_issued"), [])
        self.assertEqual(list(a2a_objects.store_dir("_default").glob("*.bin")), [])

    def test_a_chunk_pulled_out_of_order_is_recorded_before_it_is_served(self):
        """Review finding 11: the start/complete records cover a sequential pull; a chunk that is not
        part of a recorded pull still gets its own record first (audit-first is kept)."""
        ref = self.one_ref(self.produce({"ooo.bin": _blob(3 * CAP)}))
        resp = self.raw_pull(ref["ref_id"], CAP, 100)
        self.assertIn("object_chunk", resp)
        recs = self.events("A2A.object_pulled")
        self.assertEqual([r["details"]["stage"] for r in recs], ["chunk"])
        self.assertEqual((recs[0]["details"]["offset"], recs[0]["details"]["length"]), (CAP, 100))

    def test_a_receiver_not_built_by_build_server_issues_and_serves_too(self):
        """The gateway and the console build the receiver themselves (no ``_objects_endpoints``):
        the gate must fall back to the default endpoints dir, not trip over the class's own
        ``_endpoints_dir`` helper (a TypeError there failed every worker task, found 2026-10-27)."""
        self.pair.receiver._objects_endpoints = None
        rcv = __import__("remote_trigger_receiver").RemoteTriggerReceiver(
            origins_dir=self.pair.b_origins, engine_factory=lambda: self.engine, instance_id=IID_B_)
        self.assertIsNone(rcv._objects_endpoints, "a bare receiver resolves the default dir")
        self.assertIsNotNone(rcv._objects_endpoints_dir())
        big = _blob(CAP + 3)
        ref = self.one_ref(self.produce({"gw.bin": big, "gw.txt": b"ok"}))
        self.assertEqual(self.pair.sender.pull_object(self.pair.kid, ref).data, big)
        self.assertTrue(callable(self.pair.receiver._endpoints_dir), "the class helper is untouched")

    def test_pull_to_a_file_streams_and_only_a_verified_object_lands(self):
        big = _blob(3 * CAP + 5)
        ref = self.one_ref(self.produce({"film.bin": big}))
        dest = self.root / "out" / "film.bin"
        pulled = self.pair.sender.pull_object(self.pair.kid, ref, dest=dest)
        self.assertTrue(pulled.ok, pulled)
        self.assertEqual(dest.read_bytes(), big)
        self.assertIsNone(pulled.data)

    # ── digest swap ─────────────────────────────────────────────────────

    def test_digest_swap_on_the_holder_is_refused_and_nothing_lands(self):
        big = _blob(2 * CAP + 1)
        ref = self.one_ref(self.produce({"swap.bin": big}))
        stored = a2a_objects.store_dir("_default") / f"{ref['ref_id']}.bin"
        tampered = bytearray(stored.read_bytes())
        tampered[CAP + 3] ^= 0xFF
        stored.write_bytes(bytes(tampered))
        dest = self.root / "out" / "swap.bin"
        pulled = self.pair.sender.pull_object(self.pair.kid, ref, dest=dest)
        self.assertFalse(pulled.ok)
        self.assertEqual(pulled.reason, "object_digest_mismatch")
        self.assertFalse(dest.exists(), "a mismatching object never reaches its destination")
        self.assertEqual(list(dest.parent.glob("*")) if dest.parent.exists() else [], [])
        self.assertEqual(self.events("A2A.object_fetched")[-1]["details"]["reason"], "object_digest_mismatch")

    def test_a_chunk_altered_in_flight_fails_the_response_signature(self):
        ref = self.one_ref(self.produce({"flight.bin": _blob(CAP + 10)}))
        real = a2a_http_server.process_ping_request

        def tamper(req, receiver, **kw):
            status, payload = real(req, receiver, **kw)
            ch = payload.get("object_chunk")
            if isinstance(ch, dict):
                raw = bytearray(base64.b64decode(ch["data_b64"]))
                raw[0] ^= 1
                ch["data_b64"] = base64.b64encode(bytes(raw)).decode()
            return status, payload

        with mock.patch.object(a2a_http_server, "process_ping_request", tamper):
            pulled = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertFalse(pulled.ok)
        self.assertEqual(pulled.reason, "object_auth_failed")

    # ── replay ───────────────────────────────────────────────────────────

    def test_a_replayed_pull_is_refused(self):
        ref = self.one_ref(self.produce({"replay.bin": _blob(CAP + 100)}))
        sent: list[dict] = []
        real_post, real_relay = rts.RemoteTriggerSender._http_post, rts.RemoteTriggerSender._relay_ping

        def rec_post(url, envelope, timeout_s):
            if "object_pull" in envelope:
                sent.append(dict(envelope))
            return real_post(url, envelope, timeout_s)

        def rec_relay(cfg, endpoint_id, ping_request, timeout_s):
            if "object_pull" in ping_request:
                sent.append(dict(ping_request))
            return real_relay(cfg, endpoint_id, ping_request, timeout_s)

        with mock.patch.object(rts.RemoteTriggerSender, "_http_post", staticmethod(rec_post)), \
                mock.patch.object(rts.RemoteTriggerSender, "_relay_ping", staticmethod(rec_relay)):
            first = self.raw_pull(ref["ref_id"], 0, 1000)
        self.assertIn("object_chunk", first)
        self.assertEqual(len(sent), 1)
        again = self.resend_raw(sent[0])
        self.assertNotIn("object_chunk", again)
        self.assertEqual(again["object_refusal"], "object_pull_replayed")
        self.assertEqual(self.events("A2A.object_refused")[-1]["details"]["reason"], "object_pull_replayed")

    # ── origin binding + no enumeration oracle ───────────────────────────

    def test_another_origin_unknown_and_expired_get_the_same_answer(self):
        ref = self.one_ref(self.produce({"bound.bin": _blob(CAP + 1)}))
        other = self.pair.kids[1]
        foreign = self.raw_pull(ref["ref_id"], 0, 100, kid=other)
        unknown = self.raw_pull(secrets.token_urlsafe(24), 0, 100)
        self.write_holder_meta(ref["ref_id"], expires_at=time.time() - 1)
        expired = self.raw_pull(ref["ref_id"], 0, 100)

        def shape(resp: dict) -> bytes:
            # per-request fields: the echoed ping id, the clock and the signature over them
            return json.dumps({k: v for k, v in resp.items() if k not in ("task_id", "server_time", "signature")},
                              sort_keys=True).encode()

        self.assertEqual(foreign["object_refusal"], "object_unavailable")
        self.assertEqual(shape(foreign), shape(unknown))
        self.assertEqual(shape(unknown), shape(expired))
        self.assertFalse((a2a_objects.store_dir("_default") / f"{ref['ref_id']}.bin").exists(),
                         "the expired object was swept")
        self.assertEqual(self.events("A2A.object_expired")[-1]["details"]["reason"], "ttl")

    def test_mutation_probe_origin_binding_off_is_caught(self):
        """With the origin check disabled the foreign pull is SERVED: the test above would go red."""
        ref = self.one_ref(self.produce({"probe.bin": _blob(CAP + 1)}))
        with mock.patch.object(a2a_objects, "_origin_matches", lambda meta, origin_id: True):
            leaked = self.raw_pull(ref["ref_id"], 0, 100, kid=self.pair.kids[1])
        self.assertIn("object_chunk", leaked, "mutation probe: without origin binding a foreign origin gets bytes")

    # ── size exhaustion ──────────────────────────────────────────────────

    def test_over_the_per_origin_ceiling_is_refused_at_issue(self):
        self.pair.update_origin(max_object_bytes=2 * CAP)
        res = self.produce({"huge.bin": _blob(2 * CAP + 1)})
        self.assertTrue(res.ok, res)
        self.assertEqual(res.attachment_refs, [])
        self.assertEqual(res.data["attachments_dropped"], {"count": 1, "reasons": {"too_large": 1}})
        self.assertEqual(self.events("A2A.object_refused")[-1]["details"]["reason"], "object_too_large")
        self.assertEqual(list(a2a_objects.store_dir("_default").glob("*.bin")), [])

    def test_pull_budget_is_exhausted_after_max_pulls(self):
        self.pair.update_origin(object_max_pulls=1)
        big = _blob(CAP + 7)
        ref = self.one_ref(self.produce({"once.bin": big}))
        self.assertTrue(self.pair.sender.pull_object(self.pair.kid, ref).ok)
        again = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertFalse(again.ok)
        self.assertEqual(again.reason, "object_pull_budget_exhausted")
        self.assertFalse((a2a_objects.store_dir("_default") / f"{ref['ref_id']}.bin").exists(),
                         "an exhausted object's bytes are deleted at once")
        self.assertEqual(self.events("A2A.object_expired")[-1]["details"]["reason"], "budget")

    def test_a_chunk_larger_than_the_maximum_is_refused(self):
        ref = self.one_ref(self.produce({"range.bin": _blob(3 * CAP)}))
        resp = self.raw_pull(ref["ref_id"], 0, a2a_objects.CHUNK_MAX_BYTES + 1)
        self.assertEqual(resp["object_refusal"], "object_bad_range")
        resp = self.raw_pull(ref["ref_id"], 3 * CAP - 10, 11)
        self.assertEqual(resp["object_refusal"], "object_bad_range")

    def test_a_holder_advertising_more_than_the_requesters_ceiling_is_not_pulled(self):
        ref = self.one_ref(self.produce({"big.bin": _blob(CAP + 1)}))
        with mock.patch.object(a2a_objects, "operator_max_bytes", lambda tenant_id=None: CAP):
            pulled = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertEqual((pulled.ok, pulled.reason), (False, "object_too_large"))
        self.assertEqual(self.events("A2A.object_pulled"), [], "nothing was requested")

    # ── L34 classification ───────────────────────────────────────────────

    def test_l34_confidential_object_to_an_internal_origin_is_refused_at_issue_like_inline(self):
        conf = b"[class:confidential] quarterly figures\n" + b"row,1\n" * (CAP // 6 + 10)
        res = self.produce({"conf.csv": conf, "conf_small.txt": b"[class:confidential] tiny\n"})
        self.assertTrue(res.ok, res)
        self.assertEqual((res.attachments, res.attachment_refs), ([], []))
        self.assertEqual(res.data["attachments_dropped"],
                         {"count": 2, "reasons": {"classification_exceeded": 2}},
                         "the inline file and the reference meet the same gate")
        self.assertEqual(self.events("A2A.object_refused")[-1]["details"]["classification"], "CONFIDENTIAL")

    def test_l34_applies_again_at_pull_time(self):
        self.pair.update_origin(max_data_classification="CONFIDENTIAL")
        ref = self.one_ref(self.produce({"conf.csv": b"[class:confidential] figures\n" + b"row,1\n" * (CAP // 6 + 10)}))
        self.assertEqual(ref["classification"], "CONFIDENTIAL")
        self.pair.update_origin(max_data_classification="INTERNAL")
        pulled = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertEqual((pulled.ok, pulled.reason), (False, "object_classification_exceeded"))
        self.pair.update_origin(max_data_classification="CONFIDENTIAL")
        self.assertTrue(self.pair.sender.pull_object(self.pair.kid, ref).ok, "positive control")

    # ── L35 egress ───────────────────────────────────────────────────────

    def _egress_forbid_peer(self) -> None:
        cfg = self.home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        cfg.write_text("apiVersion: corvin/v1\nkind: Tenant\nspec:\n  egress:\n    enabled: true\n"
                       "    default_action: allow\n    forbidden_hosts: ['127.0.0.1']\n")
        cfg.chmod(0o600)

    def test_l35_egress_refuses_at_issue_like_inline(self):
        self._egress_forbid_peer()
        res = self.produce({"eg.bin": _blob(CAP + 1), "eg_small.bin": b"x" * 10})
        self.assertTrue(res.ok, res)
        self.assertEqual((res.attachments, res.attachment_refs), ([], []))
        self.assertEqual(res.data["attachments_dropped"], {"count": 2, "reasons": {"egress_denied": 2}})

    def test_l35_egress_applies_again_at_pull_time(self):
        ref = self.one_ref(self.produce({"eg.bin": _blob(CAP + 1)}))
        self._egress_forbid_peer()
        pulled = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertEqual((pulled.ok, pulled.reason), (False, "object_egress_denied"))

    # ── compatibility ────────────────────────────────────────────────────

    def test_small_objects_only_response_is_unchanged(self):
        files = {"a.txt": b"alpha\n", "b.json": b'{"b": 1}'}
        res = self.produce(files)
        self.assertEqual(res.attachment_refs, [])
        self.assertEqual(res.attachments, [Attachment.from_bytes(name=n, mime=m, content=files[n]).to_dict()
                                           for n, m in (("a.txt", "text/plain"), ("b.json", "application/json"))])
        self.assertTrue(all("inline" not in a for a in res.attachments))
        self.assertEqual(self.events("A2A.object_issued"), [])

    def test_a_requester_that_does_not_ask_for_references_gets_the_old_drop_report(self):
        res = self.produce({"big.bin": _blob(CAP + 1)}, schema={**SCHEMA, "attachments_by_reference": False})
        self.assertTrue(res.ok, res)
        self.assertEqual((res.attachments, res.attachment_refs), ([], []))
        self.assertEqual(res.data["attachments_dropped"], {"count": 1, "reasons": {"byte_cap": 1}})
        self.assertEqual(list(a2a_objects.store_dir("_default").glob("*.bin")), [])

    def test_an_old_holder_without_the_op_gets_a_closed_refusal(self):
        ref = self.one_ref(self.produce({"old.bin": _blob(CAP + 1)}))
        real = a2a_http_server.process_ping_request

        def old_build(req, receiver, **kw):   # a peer that predates ADR-2287 ignores the extra keys
            return real({k: v for k, v in req.items() if k not in ("object_pull", "pull_sig")}, receiver, **kw)

        with mock.patch.object(a2a_http_server, "process_ping_request", old_build):
            pulled = self.pair.sender.pull_object(self.pair.kid, ref)
        self.assertEqual((pulled.ok, pulled.reason), (False, "object_pull_unsupported"))

    def test_an_unsigned_pull_is_refused_opaquely(self):
        ref = self.one_ref(self.produce({"sig.bin": _blob(CAP + 1)}))
        cfg = self.pair.sender._registry.load(self.pair.kid)
        req = rts.RemoteTriggerSender._ping_request(cfg, self.pair.sender._instance_id)
        req["object_pull"] = {"ref_id": ref["ref_id"], "offset": 0, "length": 100}
        req["pull_sig"] = "0" * 64
        # direct: the opaque 403 of a bad ping; relay: the listener relays that unsigned 403 body,
        # which never verifies. Either way: no byte, no signed hint.
        with self.assertRaises((rts.TransportError, rts.ResponseVerificationError)):
            self.resend_raw(req)
        self.assertEqual(self.events("A2A.object_pulled"), [])

    # ── erasure ──────────────────────────────────────────────────────────

    def test_erasure_removes_the_held_object(self):
        import erasure_handlers as eh

        ref = self.one_ref(self.produce({"erase.bin": _blob(CAP + 1)}))
        bin_path = a2a_objects.store_dir("_default") / f"{ref['ref_id']}.bin"
        self.assertTrue(bin_path.exists())
        res = eh.A2AFeedHandler(tenant_id="_default").purge(self.pair.kid, "req-erase")
        self.assertEqual(getattr(res.status, "value", res.status), "applied", res)
        self.assertFalse(bin_path.exists())
        self.assertFalse(bin_path.with_suffix(".json").exists())
        self.assertEqual(self.raw_pull(ref["ref_id"], 0, 10)["object_refusal"], "object_unavailable")


class TestDirectObjects(_ObjectScenarios, _ObjectCase):
    transport = "direct"
    pair_kwargs = {"n_kids": 2}


class TestRelayObjects(_ObjectScenarios, _ObjectCase):
    transport = "relay"
    pair_kwargs = {"n_kids": 2}


class TestCliPullsReferences(_ObjectCase):
    """The production entry point: ``corvin-a2a send --attach-out-dir`` pulls every reference."""

    transport = "direct"

    def test_cli_send_writes_the_pulled_object(self):
        sys.path.insert(0, str(_repo / "corvin_operator" / "voice" / "scripts"))
        import corvin_a2a

        big = _blob(2 * CAP + 3)
        self.engine.files = {"cli.bin": big, "note.txt": b"hi"}
        out = self.root / "cli-out"
        schema = self.root / "schema.json"
        schema.write_text(json.dumps(SCHEMA))
        with mock.patch.dict(os.environ, {"REMOTE_ENDPOINTS_DIR": str(self.pair.a_endpoints)}), \
                mock.patch.object(rts, "RemoteTriggerSender",
                                  lambda endpoints_dir=None, **k: self.pair.sender):
            rc = corvin_a2a.main(["send", self.pair.kid, "produce", "--schema", str(schema),
                                  "--attach-out-dir", str(out), "--timeout", "90"])
        self.assertEqual(rc, 0)
        self.assertEqual((out / "cli.bin").read_bytes(), big)
        self.assertEqual((out / "note.txt").read_bytes(), b"hi")

    def test_cli_refuses_a_duplicate_name_instead_of_overwriting(self):
        """Review finding 10: a (signed, but hostile) response naming one file twice — two references,
        or a reference and an inline file differing only in case — never overwrites a written file."""
        import contextlib
        import io as _io

        import remote_trigger_receiver as rtr
        sys.path.insert(0, str(_repo / "corvin_operator" / "voice" / "scripts"))
        import corvin_a2a

        first, second = _blob(CAP + 5, seed=1), _blob(CAP + 9, seed=2)
        self.engine.files = {"dup.bin": first, "other.bin": second, "Note.txt": b"inline"}
        real = rtr.RemoteTriggerReceiver._release_outputs

        def hostile(self_, *a, **k):
            released, dropped = real(self_, *a, **k)
            refs = {r["name"]: r for r in released if r.get("inline") is False}
            # the second reference is renamed onto the first one's name, a third onto the inline file
            evil = dict(refs["other.bin"], name="dup.bin")
            evil2 = dict(refs["other.bin"], name="note.txt")
            return [*released, evil, evil2], dropped

        out = self.root / "cli-dup"
        schema = self.root / "schema.json"
        schema.write_text(json.dumps(SCHEMA))
        buf = _io.StringIO()
        with mock.patch.object(rtr.RemoteTriggerReceiver, "_release_outputs", hostile), \
                mock.patch.dict(os.environ, {"REMOTE_ENDPOINTS_DIR": str(self.pair.a_endpoints)}), \
                mock.patch.object(rts, "RemoteTriggerSender", lambda endpoints_dir=None, **k: self.pair.sender), \
                contextlib.redirect_stdout(buf):
            corvin_a2a.main(["send", self.pair.kid, "produce", "--schema", str(schema),
                             "--attach-out-dir", str(out), "--timeout", "90"])
        self.assertEqual((out / "dup.bin").read_bytes(), first, "the first dup.bin was overwritten")
        self.assertEqual((out / "Note.txt").read_bytes(), b"inline")
        self.assertFalse((out / "note.txt").exists() and (out / "note.txt").read_bytes() != b"inline")
        pulled = json.loads(buf.getvalue())["attachment_refs_pulled"]
        reasons = sorted(p["reason"] for p in pulled if not p["ok"])
        self.assertEqual(reasons, ["duplicate_name", "duplicate_name"])


if __name__ == "__main__":
    unittest.main()
