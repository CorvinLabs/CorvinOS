"""End-to-end tests for the three report generators.

Each test seeds the audit chain with realistic events, runs the
generator, and verifies:
  - PDF file is produced
  - PDF is non-empty and starts with %PDF
  - The returned metadata dict is structurally valid
  - The hash anchor matches the chain's last-event hash
"""
from __future__ import annotations

import time

import pytest

from corvin_compliance_reports import (
    ai_act_evidence, audit_attestation, audit_query, gdpr_ropa,
)


def _pdf_starts_well(path) -> bool:
    return path.exists() and path.read_bytes().startswith(b"%PDF")


# ── AI Act Evidence ───────────────────────────────────────────────────

def test_ai_act_generates_pdf_with_empty_chain(sandbox_home, tmp_path):
    out = tmp_path / "ai_act.pdf"
    md = ai_act_evidence.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["report_type"] == "ai_act_art_50"
    assert md["disclosure_events"] == 0
    assert md["chain_intact"] is True


def test_ai_act_includes_disclosure_and_consent(
    sandbox_home, seed_chain, tmp_path,
):
    seed_chain("disclosure.shown", channel="discord",
               chat_key="123", uid="user1")
    seed_chain("disclosure.action", channel="discord",
               chat_key="123", uid="user1", action="joined")
    seed_chain("consent.granted", channel="discord",
               chat_key="123", uid="user1", mode="durable")
    seed_chain("consent.observer_dropped", channel="discord",
               chat_key="123", uid="user2", reason="no-consent")
    seed_chain("bridge.read_only_drop", channel="discord",
               chat_key="123", uid="user3", first_drop=True)

    out = tmp_path / "ai_act.pdf"
    md = ai_act_evidence.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["disclosure_events"] == 2  # shown + action
    assert md["consent_events"] == 2  # granted + observer_dropped
    assert md["read_only_drops"] == 1
    assert md["anchor_hash"] is not None
    assert len(md["anchor_hash"]) >= 16


def test_ai_act_anchor_matches_chain_last(
    sandbox_home, seed_chain, chain_path, tmp_path,
):
    seed_chain("disclosure.shown", uid="x")
    seed_chain("consent.granted", uid="x", mode="durable")
    out = tmp_path / "ai_act.pdf"
    md = ai_act_evidence.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    stats = audit_query.compute_stats(tenant_id="_default")
    assert md["anchor_hash"] == stats.last_event_hash


# ── GDPR RoPA ─────────────────────────────────────────────────────────

def test_gdpr_ropa_generates_pdf_with_empty_chain(sandbox_home, tmp_path):
    out = tmp_path / "ropa.pdf"
    md = gdpr_ropa.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["report_type"] == "gdpr_art_30_ropa"


def test_gdpr_ropa_includes_engine_and_data_events(
    sandbox_home, seed_chain, tmp_path,
):
    seed_chain("gateway.run_created", engine="claude_code",
               compliance_zone="eu-central", run_id="r1")
    seed_chain("gateway.run_created", engine="codex_cli",
               compliance_zone="eu-central", run_id="r2")
    seed_chain("data.registered", data_handle="data_abc123",
               format="csv", size_b=1000)
    seed_chain("data.pii_detected", data_handle="data_abc123",
               classes={"email": 1, "phone": 2})
    seed_chain("voice.transcribed", provider="openai",
               audio_s=30, chars=200)
    seed_chain("voice.transcribed", provider="openai",
               audio_s=15, chars=100)
    seed_chain("memory.turn_indexed", channel="discord",
               chat_key="123")

    out = tmp_path / "ropa.pdf"
    md = gdpr_ropa.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["engine_run_events"] == 2
    assert md["data_handle_events"] == 2
    assert md["voice_events"] == 2
    assert md["memory_events"] == 1


def test_gdpr_ropa_pdf_does_not_leak_pii_into_audit(
    sandbox_home, seed_chain, chain_path, tmp_path,
):
    """The generator MAY write a compliance.report_generated audit event,
    but that event MUST carry metadata only — not the raw transcript /
    customer data / etc. that the PDF includes."""
    seed_chain("data.registered", data_handle="data_abc")
    out = tmp_path / "ropa.pdf"
    gdpr_ropa.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    # The generator itself does NOT auto-write audit events; that's
    # the route-layer's responsibility. So chain should be unchanged.
    chain_text = chain_path.read_text() if chain_path.exists() else ""
    assert "compliance.report_generated" not in chain_text


# ── Audit Attestation ────────────────────────────────────────────────

def test_attestation_empty_chain(sandbox_home, tmp_path):
    out = tmp_path / "attestation.pdf"
    md = audit_attestation.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["report_type"] == "audit_chain_attestation"
    assert md["chain_intact"] is True
    assert md["total_events"] == 0


def test_attestation_with_intact_chain(
    sandbox_home, seed_chain, tmp_path,
):
    for i in range(20):
        seed_chain(f"test.event_{i % 3}", index=i)
    out = tmp_path / "attestation.pdf"
    md = audit_attestation.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["chain_intact"] is True
    assert md["total_events"] == 20
    assert md["chain_problems_count"] == 0


def test_attestation_detects_tampered_chain(
    sandbox_home, seed_chain, chain_path, tmp_path,
):
    """If the chain is tampered, the attestation MUST report chain_intact=False."""
    seed_chain("event.a", x=1)
    seed_chain("event.b", x=2)
    seed_chain("event.c", x=3)
    # Tamper: rewrite the middle line's event_type — definitely changes
    # the canonical-JSON over which the stored hash was computed.
    lines = chain_path.read_text().splitlines()
    assert len(lines) == 3
    assert '"event.b"' in lines[1], f"unexpected line: {lines[1]}"
    lines[1] = lines[1].replace('"event.b"', '"event.TAMPERED"')
    chain_path.write_text("\n".join(lines) + "\n")

    out = tmp_path / "attestation.pdf"
    md = audit_attestation.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    assert _pdf_starts_well(out)
    assert md["chain_intact"] is False
    assert md["chain_problems_count"] >= 1


# ── PDF structural sanity ────────────────────────────────────────────

@pytest.mark.parametrize("generator,name", [
    (ai_act_evidence, "ai_act_art_50"),
    (gdpr_ropa, "gdpr_art_30_ropa"),
    (audit_attestation, "audit_chain_attestation"),
])
def test_generator_metadata_shape(sandbox_home, seed_chain, tmp_path, generator, name):
    seed_chain("disclosure.shown", uid="x")
    seed_chain("consent.granted", uid="x", mode="durable")
    out = tmp_path / f"{name}.pdf"
    md = generator.generate(
        tenant_id="_default",
        start_ts=0, end_ts=int(time.time()) + 1000,
        output_path=out,
    )
    # Required metadata fields every report carries
    for key in ("report_type", "tenant_id", "period_start_ts",
                "period_end_ts", "generated_at_ts", "generator_version",
                "anchor_hash", "chain_intact"):
        assert key in md, f"{name}: missing key {key}"
    assert md["report_type"] == name
    # PDF is non-trivial in size
    assert out.stat().st_size > 1024
