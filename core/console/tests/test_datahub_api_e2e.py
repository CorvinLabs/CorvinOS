"""/v1/console/datahub/* through the real console router.

Measured 2026-10-01: the DataHub panel posted to /v1/datahub/ingest and
/v1/datahub/create, which do not exist (404 live); GET /datahub/list was shadowed by
GET /datahub/{artifact_id}; CSV ingestion was a stub returning no rows, SQL/API/Parquet
returned [] and still produced a "completed" artifact; the generated body was thrown
away; test_count counted test names nothing ever wrote; the ADR-0661 security scan
never ran; nothing was audited; data_path could read another tenant's files.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_BASE = "/v1/console/datahub"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _actions(home: Path) -> list[str]:
    return [(e.get("details") or {}).get("action", "") for e in _audit_events(home)
            if e.get("event_type") == "console.action_performed"]


class DataHubApiTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.json_src = _write(self.data / "rows.json", json.dumps(
            [{"name": "a", "score": 1}, {"name": "b", "score": None}]))
        self.csv_src = _write(self.data / "rows.csv", "name,score\na,1\nb,2\nc,3\n")

    def _create(self, client, csrf, **over):
        body = {"name": "orders", "description": "Order rows", "creation_type": "skill",
                "data_source": "json", "data_path": str(self.json_src)}
        body.update(over)
        return client.post(f"{_BASE}/create", headers={"X-CSRF-Token": csrf}, json=body)

    def test_analyze_json_reports_rows_completeness_and_scan(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            r = client.post(f"{_BASE}/analyze", headers={"X-CSRF-Token": csrf},
                            json={"data_source": "json", "data_path": str(self.json_src)})
            self.assertEqual(r.status_code, 200, r.text)
            a = r.json()["analysis"]
            self.assertEqual(a["row_count"], 2)
            self.assertAlmostEqual(a["completeness"], 0.75)
            self.assertEqual(a["schema"], {"name": "string", "score": "number"})
            self.assertEqual(a["security"], {"secret": 0, "pii": 0, "injection": 0})

    def test_csv_is_really_parsed(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            r = client.post(f"{_BASE}/analyze", headers={"X-CSRF-Token": csrf},
                            json={"data_source": "csv", "data_path": str(self.csv_src)})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["analysis"]["row_count"], 3)

    def test_create_persists_body_lists_and_audits(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 201, r.text)
            meta = r.json()["metadata"]
            self.assertEqual(meta["status"], "completed")
            self.assertEqual(meta["row_count"], 2)
            self.assertNotIn("test_count", meta)
            aid = meta["artifact_id"]

            got = client.get(f"{_BASE}/{aid}").json()
            self.assertIn("Based on analysis of 2 rows", got["body"])

            listed = client.get(f"{_BASE}/list")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual([i["artifact_id"] for i in listed.json()["items"]], [aid])

            self.assertEqual(self._create(client, csrf).status_code, 409)

            d = client.delete(f"{_BASE}/{aid}", headers={"X-CSRF-Token": csrf})
            self.assertEqual(d.status_code, 200, d.text)
            self.assertEqual(client.get(f"{_BASE}/list").json()["items"], [])
            self.assertEqual(_actions(home).count("datahub.create"), 1)
            self.assertEqual(_actions(home).count("datahub.delete"), 1)

    def test_unsupported_sources_are_refused_not_faked(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            for src in ("sql", "api", "parquet"):
                r = self._create(client, csrf, name=f"n-{src}", data_source=src)
                self.assertEqual(r.status_code, 400, (src, r.text))
                self.assertIn("not supported", r.json()["detail"])
            self.assertEqual(client.get(f"{_BASE}/list").json()["items"], [])

    def test_source_with_a_secret_is_refused(self):
        leaky = _write(self.data / "leaky.json",
                       json.dumps([{"note": "ghp_" + "a" * 36}]))
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            r = self._create(client, csrf, data_path=str(leaky))
            self.assertEqual(r.status_code, 422, r.text)
            self.assertNotIn("ghp_", r.text)
            self.assertEqual(client.get(f"{_BASE}/list").json()["items"], [])

    def test_empty_source_is_refused(self):
        empty = _write(self.data / "empty.json", "[]")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self.assertEqual(self._create(client, csrf, data_path=str(empty)).status_code, 422)

    def test_another_tenants_files_are_not_readable(self):
        with _sandbox(self.tmp, tenants=("_default", "other")) as (client, csrf, home, _):
            foreign = _write(home / "tenants" / "other" / "global" / "x.json", "[{}]")
            r = client.post(f"{_BASE}/analyze", headers={"X-CSRF-Token": csrf},
                            json={"data_source": "json", "data_path": str(foreign)})
            self.assertEqual(r.status_code, 400, r.text)

    def test_wrong_extension_and_missing_file(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            r = client.post(f"{_BASE}/analyze", headers={"X-CSRF-Token": csrf},
                            json={"data_source": "json", "data_path": str(self.csv_src)})
            self.assertEqual(r.status_code, 400, r.text)
            r = client.post(f"{_BASE}/analyze", headers={"X-CSRF-Token": csrf},
                            json={"data_source": "json", "data_path": str(self.data / "nope.json")})
            self.assertEqual(r.status_code, 400, r.text)

    def test_bad_artifact_id_and_auth(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self.assertEqual(client.get(f"{_BASE}/..").status_code, 404)
            self.assertEqual(client.get(f"{_BASE}/not-an-id").status_code, 404)
            self.assertEqual(client.post(f"{_BASE}/analyze", json={}).status_code, 403)
            client.cookies.clear()
            self.assertEqual(client.get(f"{_BASE}/list").status_code, 401)


if __name__ == "__main__":
    unittest.main()
