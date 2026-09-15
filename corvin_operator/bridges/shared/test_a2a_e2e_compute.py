"""LIVE end-to-end for Layer 38 v3 — bidirectional A2A with real compute.

Scenario reproduced here:

  1. Two Corvin instances ("cloud-sim" and "local-prod") run on
     127.0.0.1 ephemeral ports, paired with real HMAC keys.
  2. Cloud-sim sends a TaskEnvelope to local-prod, attaching a real
     CSV slice from the Spotify charts dataset under
     <path-to-datasets>/archive/.
  3. Local-prod's RemoteTriggerReceiver validates, audits, drops the
     CSV into a private worker scratch dir, invokes the
     DeterministicComputeEngine (real summary + matplotlib histogram).
  4. The receiver harvests ``summary.json`` + ``histogram.png`` from
     the scratch dir, signs them into the ResponseEnvelope as
     attachments, returns to cloud-sim.
  5. Cloud-sim verifies the response signature, verifies the
     instance_id pin, verifies each attachment digest, writes the
     histogram to disk for visual proof.

This test is the live demonstration of the user's request:
"Cloud → Local: berechne, Local → Cloud: result + image".
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

# NOTE: the license compute-quota module is poisoned in setUpModule()/
# tearDownModule() below (test-execution time), not here at collection/
# import time. remote_trigger_receiver/sender and a2a_http_server only
# import license.validator/license.limits lazily inside functions (not at
# their own module level), so poisoning at collection time was never
# actually required for the imports right below — and doing it here left
# "license.compute_quota"/"license.limits" permanently set to None in
# sys.modules for the rest of the process. In a combined session (`pytest
# tests/ operator/... core/...`, as CI's coverage job runs), every
# later-collected file doing a real `import license.validator` /
# `from license.limits import ...` then hit `ModuleNotFoundError: import of
# license.limits halted; None in sys.modules` instead of importing the real
# module.
import remote_trigger_receiver as rtr  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402
import a2a_http_server  # noqa: E402
import a2a_attachments as a2a_att  # noqa: E402
from a2a_compute_engine import DeterministicComputeEngine, _MPL_OK  # noqa: E402


_SAVED_LICENSE_MODULES: dict[str, object | None] = {}


def setUpModule() -> None:
    """Poison the license compute-quota module so spawn_a2a_worker treats it
    as absent (ImportError → fail-open) — see the note near the imports
    above for why this must not happen at module-import/collection time."""
    for name in ("license.compute_quota", "license.limits"):
        _SAVED_LICENSE_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]


def tearDownModule() -> None:
    for name, mod in _SAVED_LICENSE_MODULES.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


# Mock forge audit writer so the test never touches the real chain.
_emitted: list[dict] = []


def _capture(audit_path_arg, event_type, **kwargs):
    _emitted.append({"event_type": event_type, **kwargs})
    return {"hash": "abc"}


_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(side_effect=_capture)


# ── Helpers ───────────────────────────────────────────────────────────────

CSV_SAMPLE = Path(os.environ.get("CORVIN_TEST_CSV", "")) if os.environ.get("CORVIN_TEST_CSV") else None


def _write_origin(d: Path, *, origin_id: str, hmac_key: str, recv_key: str):
    cfg = {
        "origin_id": origin_id, "hmac_key": hmac_key, "recv_key": recv_key,
        "enabled": True, "max_ttl_s": 300,
        "allowed_personas": ["compute-csv"],
        "spawn_worker": True,
    }
    p = d / f"{origin_id}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)


def _write_endpoint(d: Path, *, endpoint_id: str, url: str,
                    hmac_key: str, recv_key: str, instance_id: str,
                    our_origin_id: str):
    cfg = {
        "endpoint_id": endpoint_id, "url": url,
        "hmac_key": hmac_key, "recv_key": recv_key,
        "instance_id": instance_id, "enabled": True,
        "default_ttl_s": 120,
        "our_origin_id": our_origin_id,
    }
    p = d / f"{endpoint_id}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)


def _build_csv_sample(target_bytes: int = 800_000) -> bytes:
    """Read enough rows from the real dataset to fit under ``target_bytes``."""
    if CSV_SAMPLE is None or not CSV_SAMPLE.exists():
        raise unittest.SkipTest(
            f"dataset not present: {CSV_SAMPLE!r} — set CORVIN_TEST_CSV to enable"
        )
    out = bytearray()
    with CSV_SAMPLE.open("rb") as fh:
        for line in fh:
            if out and len(out) + len(line) > target_bytes:
                break
            out += line
    return bytes(out)


# ── The live E2E test ────────────────────────────────────────────────────

class TestLiveE2ECompute(unittest.TestCase):
    """Two real HTTP servers, two real instance_ids, real CSV attachment,
    real compute pipeline (matplotlib), real PNG returned."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmpdir = Path(cls._tmp.name)

        # Pre-built CSV sample to ship as attachment — may raise SkipTest if
        # pandas/matplotlib is absent; must run BEFORE env-var setup so that a
        # SkipTest doesn't leave CORVIN_A2A_ATTESTATION_DISABLED=1 in the
        # environment when tearDownClass is skipped (Python < 3.12 behaviour).
        cls.csv_bytes = _build_csv_sample()

        # Disable network membership attestation: the test license.key on the
        # developer machine causes the sender to include a network_attestation
        # block whose RS256 sig cannot be verified in the test environment.
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"

        # Per-instance dirs
        cls.local_origins_dir = cls.tmpdir / "local" / "origins"
        cls.local_endpoints_dir = cls.tmpdir / "local" / "endpoints"
        cls.cloud_origins_dir = cls.tmpdir / "cloud" / "origins"
        cls.cloud_endpoints_dir = cls.tmpdir / "cloud" / "endpoints"
        for d in (cls.local_origins_dir, cls.local_endpoints_dir,
                  cls.cloud_origins_dir, cls.cloud_endpoints_dir):
            d.mkdir(parents=True)

        cls.local_iid = "iid-local-prod-laptop"
        cls.cloud_iid = "iid-cloud-fsn1-hetzner"

        # Spawn local-prod receiver with the compute engine
        cls.local_server = a2a_http_server.build_server(
            host="127.0.0.1", port=0,
            origins_dir=cls.local_origins_dir,
            engine_factory=lambda: DeterministicComputeEngine(),
            instance_id=cls.local_iid,
            forge_se=_mock_se,
        )
        a2a_http_server.serve_in_thread(cls.local_server)
        h, p = cls.local_server.server_address[:2]
        cls.local_url = f"http://{h}:{p}/v1/a2a/receive"

        # Cloud-sim doesn't need a receiver for *this* test (cloud → local
        # only), but we still build one for the audit chain capture.
        cls.cloud_server = a2a_http_server.build_server(
            host="127.0.0.1", port=0,
            origins_dir=cls.cloud_origins_dir,
            engine_factory=lambda: DeterministicComputeEngine(),
            instance_id=cls.cloud_iid,
            forge_se=_mock_se,
        )
        a2a_http_server.serve_in_thread(cls.cloud_server)
        h2, p2 = cls.cloud_server.server_address[:2]
        cls.cloud_url = f"http://{h2}:{p2}/v1/a2a/receive"

        # Pair: cloud-sim signs envelopes to local-prod
        import secrets
        cls.k_cl_hmac = secrets.token_hex(32)
        cls.k_cl_recv = secrets.token_hex(32)
        _write_origin(
            cls.local_origins_dir, origin_id="cloud-sim",
            hmac_key=cls.k_cl_hmac, recv_key=cls.k_cl_recv,
        )
        _write_endpoint(
            cls.cloud_endpoints_dir, endpoint_id="corvin-local",
            url=cls.local_url, hmac_key=cls.k_cl_hmac, recv_key=cls.k_cl_recv,
            instance_id=cls.local_iid, our_origin_id="cloud-sim",
        )

        # Reverse pair (local → cloud); used for the reverse-direction test
        cls.k_lc_hmac = secrets.token_hex(32)
        cls.k_lc_recv = secrets.token_hex(32)
        _write_origin(
            cls.cloud_origins_dir, origin_id="local-prod",
            hmac_key=cls.k_lc_hmac, recv_key=cls.k_lc_recv,
        )
        _write_endpoint(
            cls.local_endpoints_dir, endpoint_id="corvin-cloud",
            url=cls.cloud_url, hmac_key=cls.k_lc_hmac, recv_key=cls.k_lc_recv,
            instance_id=cls.cloud_iid, our_origin_id="local-prod",
        )

        cls.cloud_sender = rts.RemoteTriggerSender(
            endpoints_dir=cls.cloud_endpoints_dir, instance_id=cls.cloud_iid,
            forge_se=_mock_se,
        )
        cls.local_sender = rts.RemoteTriggerSender(
            endpoints_dir=cls.local_endpoints_dir, instance_id=cls.local_iid,
            forge_se=_mock_se,
        )

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
        cls.local_server.shutdown()
        cls.local_server.server_close()
        cls.cloud_server.shutdown()
        cls.cloud_server.server_close()
        cls._tmp.cleanup()

    def setUp(self):
        _emitted.clear()

    # ── The main scenario ────────────────────────────────────────────

    @unittest.skipUnless(_MPL_OK, "matplotlib not installed — install via requirements.txt")
    def test_cloud_sends_csv_local_computes_returns_png(self):
        """The full user-requested flow.

        Cloud → Local: instruction + spotify_sample.csv as attachment
        Local computes summary + histogram, returns summary.json + histogram.png
        Cloud verifies and decodes both attachments.
        """
        csv_att = a2a_att.Attachment.from_bytes(
            name="data.csv", mime="text/csv", content=self.csv_bytes,
        )
        res = self.cloud_sender.send(
            "corvin-local",
            instruction=(
                "Please compute a numeric summary of data.csv and render a "
                "histogram of the first numeric column. Return summary.json "
                "and histogram.png."
            ),
            result_schema={
                "properties": {
                    "ok":               {"type": "boolean"},
                    "rows_total":       {"type": "integer"},
                    "numeric_columns":  {"type": "array"},
                    "histogram_column": {"type": "string"},
                    "engine":           {"type": "string"},
                },
                "attachments_out_allowed": ["summary.json", "histogram.png"],
            },
            attachments=[csv_att],
            ttl_s=60,
            timeout_s=60,
        )

        # ── Top-level assertions ────────────────────────────────
        self.assertTrue(res.ok, msg=f"status={res.status}, data={res.data}")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.instance_id, self.local_iid)
        self.assertTrue(res.instance_id_match)

        # ── Filtered response data ──────────────────────────────
        self.assertTrue(res.data.get("ok"))
        self.assertGreater(res.data.get("rows_total", 0), 100)
        self.assertEqual(res.data.get("engine"), "compute-csv")
        self.assertIsInstance(res.data.get("numeric_columns"), list)
        self.assertIn("rank", res.data["numeric_columns"])
        self.assertIn("streams", res.data["numeric_columns"])

        # ── Attachments returned + digest-verified ──────────────
        names = sorted(a["name"] for a in res.attachments)
        self.assertEqual(names, ["histogram.png", "summary.json"])

        # summary.json: parse and check shape
        summary_att = next(a for a in res.attachments if a["name"] == "summary.json")
        summary_bytes = a2a_att.Attachment.from_dict(summary_att).decode()
        summary = json.loads(summary_bytes)
        self.assertIn("columns", summary)
        self.assertGreater(len(summary["columns"]), 0)
        # Pick the rank column — known numeric
        self.assertIn("rank", summary["columns"])
        rank_stats = summary["columns"]["rank"]
        self.assertGreater(rank_stats["count"], 100)
        self.assertGreater(rank_stats["max"], rank_stats["min"])

        # histogram.png: must be a real PNG
        png_att = next(a for a in res.attachments if a["name"] == "histogram.png")
        png_bytes = a2a_att.Attachment.from_dict(png_att).decode()
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
                        msg=f"not a PNG (starts with {png_bytes[:8]!r})")
        self.assertGreater(len(png_bytes), 1000,
                           msg="PNG suspiciously small")

        # ── Visual proof: write PNG to repo's /tmp for human inspection
        proof_dir = Path("/tmp/corvin_a2a_e2e_proof")
        proof_dir.mkdir(exist_ok=True)
        (proof_dir / "histogram.png").write_bytes(png_bytes)
        (proof_dir / "summary.json").write_bytes(summary_bytes)
        (proof_dir / "manifest.json").write_text(json.dumps({
            "test_run": "test_cloud_sends_csv_local_computes_returns_png",
            "sender_instance_id": self.cloud_iid,
            "receiver_instance_id": res.instance_id,
            "csv_bytes_sent": len(self.csv_bytes),
            "rows_summarised": res.data["rows_total"],
            "numeric_columns": res.data["numeric_columns"],
            "png_bytes": len(png_bytes),
            "summary_keys": list(summary.keys()),
        }, sort_keys=True, indent=2))

        # ── Audit chain shape ───────────────────────────────────
        types = {e["event_type"] for e in _emitted}
        for required in ("A2A.envelope_sent", "A2A.envelope_received",
                         "A2A.engine_spawned", "A2A.result_filtered",
                         "A2A.response_signed", "A2A.response_received"):
            self.assertIn(required, types,
                          msg=f"missing audit: {required}")

        # ── Audit details must not leak the CSV body ────────────
        leak_canary = self.csv_bytes[:80].decode("utf-8", errors="replace")
        for ev in _emitted:
            details_str = json.dumps(ev.get("details", {}))
            self.assertNotIn(leak_canary, details_str)

    # ── Reverse direction (Local → Cloud) ────────────────────────

    def test_reverse_direction_local_to_cloud(self):
        """Same flow in the opposite direction.

        Local-prod sends a (smaller) CSV to cloud-sim; cloud-sim runs
        the same compute pipeline and returns PNG + summary.
        """
        # Smaller CSV — quick reverse path
        small_csv = b"a,b,c\n1,2,3\n4,5,6\n7,8,9\n10,11,12\n13,14,15\n"
        att = a2a_att.Attachment.from_bytes(
            name="tiny.csv", mime="text/csv", content=small_csv,
        )
        res = self.local_sender.send(
            "corvin-cloud",
            instruction="Compute summary for tiny.csv.",
            result_schema={
                "properties": {
                    "ok": {"type": "boolean"},
                    "rows_total": {"type": "integer"},
                    "numeric_columns": {"type": "array"},
                    "engine": {"type": "string"},
                },
                "attachments_out_allowed": ["summary.json", "histogram.png"],
            },
            attachments=[att],
        )
        self.assertTrue(res.ok, msg=f"status={res.status}")
        self.assertEqual(res.instance_id, self.cloud_iid)
        self.assertEqual(res.data["rows_total"], 5)
        self.assertEqual(
            sorted(res.data["numeric_columns"]), ["a", "b", "c"],
        )
        # Attachments came back
        att_names = sorted(a["name"] for a in res.attachments)
        self.assertIn("summary.json", att_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
