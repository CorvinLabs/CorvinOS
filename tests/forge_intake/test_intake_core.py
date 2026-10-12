"""The intake stages (ADR-2254 D2; T-0154): every refusal path, the redaction, the bounded pack, and fail-closed errors.

Real files in a tmp upload area; Layer 24, the secrets scanner and the PII detector are the real ones. Only the *gate* (the
caller's pre-spawn gate) is a stand-in, because its real counterpart is the console's - exercised in the route tests."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from core.forge_intake import intake
from core.forge_intake.intake import ItemReport, analyze_item, build_pack

CLEAN_CSV = "id,product,amount\n" + "\n".join(f"{i},widget{i % 5},{i * 3}" for i in range(1, 41)) + "\n"
EMAIL_CSV = "id,email,amount\n" + "\n".join(f"{i},person{i}@example.org,{i * 3}" for i in range(1, 41)) + "\n"


@pytest.fixture
def area(tmp_path):
    up = tmp_path / "uploads"
    up.mkdir()
    return up


def put(area: Path, name: str, data: bytes | str) -> Path:
    p = area / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


def run(area, p, gate=lambda text: None, item="it1"):
    return analyze_item(p, item_id=item, upload_root=area, tenant_id="_default", gate=gate)


def test_a_clean_csv_is_admitted_with_a_schema_a_sample_and_no_file_name(area):
    r = run(area, put(area, "sales-secret-customer-list.csv", CLEAN_CSV))
    assert r.report.verdict == "admitted" and r.report.reason_code == ""
    assert r.report.fmt == "csv" and r.report.columns == 3 and r.report.size_bytes == len(CLEAN_CSV)
    assert [c["name"] for c in r.projection["columns"]] == ["id", "product", "amount"]
    assert 0 < len(r.projection["sample"]) <= 20
    assert "secret-customer" not in json.dumps(r.report.to_dict()), "a report never carries the file name"


def test_a_header_like_city_is_treated_as_an_address_conservatively(area):
    r = run(area, put(area, "places.csv", "id,city\n" + "\n".join(f"{i},Berlin" for i in range(30)) + "\n"))
    assert r.report.verdict == "admitted_redacted" and r.report.pii_kinds == {"address": 1}
    assert "<no_pii>" not in r.report.pii_kinds, "the clean-column bucket is not a PII kind"
    assert "Berlin" not in json.dumps(r.projection["sample"])


def test_a_json_file_named_json_is_admitted_and_a_json_named_csv_is_not(area):
    ok = run(area, put(area, "rows.json", json.dumps([{"a": i, "b": i * 2} for i in range(30)])))
    assert ok.report.verdict == "admitted" and ok.report.fmt == "json"
    assert run(area, put(area, "rows-as.csv", json.dumps([{"a": 1}]))).report.reason_code == intake.R_MISMATCH
    assert run(area, put(area, "not-json.json", "id,a\n1,2\n")).report.reason_code == intake.R_MISMATCH


def test_pii_is_detected_counted_and_the_raw_value_never_reaches_the_projection(area):
    r = run(area, put(area, "people.csv", EMAIL_CSV))
    assert r.report.verdict == "admitted_redacted"
    assert r.report.pii_kinds.get("email", 0) >= 1 and r.report.redactions
    raw = json.dumps(r.projection)
    assert "person1@example.org" not in raw and "example.org" not in raw, "the raw e-mail never leaves the projection stage"
    assert "person1@example.org" not in json.dumps(r.report.to_dict())
    assert next(c for c in r.projection["columns"] if c["name"] == "email")["strategy"], "the strategy applied is named"


@pytest.mark.parametrize("secret", [
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a" * 36,
])
def test_a_file_containing_a_secret_is_refused_wherever_the_secret_sits(area, secret):
    body = CLEAN_CSV + ("x" * 3_000_000) + "\n" + f"99,{secret},1\n"   # past the first window: every byte is scanned
    r = run(area, put(area, "has-secret.csv", body))
    assert r.report.verdict == "refused" and r.report.reason_code == intake.R_SECRET and r.projection is None


def test_a_path_outside_the_upload_area_is_refused(tmp_path, area):
    other = tmp_path / "another_tenant" / "uploads"
    other.mkdir(parents=True)
    foreign = put(other, "theirs.csv", CLEAN_CSV)
    assert run(area, foreign).report.reason_code == intake.R_OUTSIDE
    sneaky = area / ".." / "another_tenant" / "uploads" / "theirs.csv"
    assert run(area, sneaky).report.reason_code == intake.R_OUTSIDE


def test_a_symlink_is_refused_even_when_it_points_inside_the_area(area):
    real = put(area, "real.csv", CLEAN_CSV)
    link = area / "link.csv"
    link.symlink_to(real)
    assert run(area, link).report.reason_code == intake.R_SYMLINK


def test_an_oversized_file_is_refused(area, monkeypatch):
    monkeypatch.setattr(intake, "MAX_ITEM_BYTES", 100)
    assert run(area, put(area, "big.csv", CLEAN_CSV)).report.reason_code == intake.R_TOO_BIG


def test_a_mislabelled_file_is_refused(area):
    r = run(area, put(area, "pretends.csv", json.dumps([{"a": 1}, {"a": 2}])))
    assert r.report.verdict == "refused" and r.report.reason_code == intake.R_MISMATCH


@pytest.mark.parametrize("name,data", [
    ("looks-like.csv", gzip.compress(CLEAN_CSV.encode())),     # a gzip renamed .csv is still a gzip
    ("bundle.zip", b"PK\x03\x04" + b"\x00" * 40),
    ("bundle.csv", b"PK\x03\x04" + b"\x00" * 40),              # a zip renamed .csv
    ("data.tar", b"x" * 600),
])
def test_an_archive_is_refused_by_content_and_by_name(area, name, data):
    assert run(area, put(area, name, data)).report.reason_code == intake.R_ARCHIVE


@pytest.mark.parametrize("name", ["tool.exe", "image.png", "noextension"])
def test_an_unsupported_type_is_refused(area, name):
    assert run(area, put(area, name, "hello")).report.reason_code == intake.R_TYPE


def test_a_gate_refusal_refuses_the_item_without_quoting_the_gate(area):
    r = run(area, put(area, "ok.csv", CLEAN_CSV), gate=lambda text: "Generation refused: this looks like a SECRET-CLASS payload")
    assert r.report.reason_code == intake.R_CLASSIFIED and r.projection is None
    assert "SECRET-CLASS" not in json.dumps(r.report.to_dict())


def test_the_gate_sees_the_projection_not_the_raw_file(area):
    seen: list[str] = []
    run(area, put(area, "people.csv", EMAIL_CSV), gate=lambda text: seen.append(text))
    assert seen and "person1@example.org" not in seen[0], "the gate classifies what the engine would read"


def test_an_error_in_the_gate_refuses_the_item_it_never_lets_it_through(area):
    def boom(text):
        raise RuntimeError("classifier down")
    r = run(area, put(area, "ok.csv", CLEAN_CSV), gate=boom)
    assert r.report.verdict == "refused" and r.report.reason_code == intake.R_ERROR


@pytest.mark.parametrize("target", ["_secret_hits", "_projection"])
def test_a_crash_in_any_stage_is_a_refusal_not_a_pass(area, monkeypatch, target):
    monkeypatch.setattr(intake, target, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stage crashed")))
    r = run(area, put(area, "ok.csv", CLEAN_CSV))
    assert r.report.verdict == "refused" and r.report.reason_code == intake.R_ERROR and r.projection is None


def test_an_unreadable_file_is_refused(area):
    r = run(area, area / "does-not-exist.csv")
    assert r.report.verdict == "refused"


def test_the_pack_holds_only_admitted_items_and_nothing_of_the_refused(area):
    ok = run(area, put(area, "a.csv", CLEAN_CSV), item="a")
    bad = run(area, put(area, "b.csv", gzip.compress(b"x")), item="b")
    pack = build_pack([ok, bad])
    assert [i["item_id"] for i in pack["items"]] == ["a"] and pack["degraded"] == []


def test_a_pack_over_the_cap_degrades_in_a_fixed_order_and_says_so(area, monkeypatch):
    wide = "id," + ",".join(f"c{i}" for i in range(40)) + "\n" + "\n".join(
        str(r) + "," + ",".join(str(r * i) for i in range(40)) for r in range(60)) + "\n"
    it = run(area, put(area, "wide.csv", wide))
    monkeypatch.setattr(intake, "PACK_MAX_BYTES", 4000)
    pack = build_pack([it])
    assert pack["degraded"][:1] == ["sample"], pack["degraded"]
    assert pack["items"][0]["sample"] == [] and pack["items"][0]["columns"], "the schema is never dropped"
    assert len(json.dumps(pack)) < 60_000


def test_report_to_dict_has_a_closed_set_of_fields(area):
    keys = set(run(area, put(area, "a.csv", CLEAN_CSV)).report.to_dict())
    assert keys == {"item_id", "verdict", "reason_code", "fmt", "ext", "size_bytes", "sha256_prefix", "data_class",
                    "pii_kinds", "redactions", "columns", "rows"}
