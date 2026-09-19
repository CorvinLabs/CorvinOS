/**
 * Telemetry page against MSW: the marker renders; the four tiles count the
 * channels by status; a channel that only collects shows its note and its
 * outbox count; "Show what is sent" reveals the payload the sender builds;
 * tokens are never rendered; a 404 build says so instead of zeros.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

import { MARKER_TELEMETRY, OTELTelemetryPage } from "@/pages/otel-telemetry";

const BODY = {
  tenant_id: "_default", corvin_home: "/srv/corvin", took_ms: 12, timestamp: "2026-09-20T10:00:00Z", errors: {},
  never_transmitted: ["prompts, transcripts or chat text", "raw IP addresses"],
  channels: [
    { id: "ping", title: "Daily instance ping", status: "sent", enabled: true, endpoint_host: "corvin-labs.com", transport: "HTTPS POST", cadence: "once per 24 h",
      last_sent: "2026-09-20T09:00:00Z", next_due: "2026-09-21T09:00:00Z", opt_out: "spec.telemetry.ping_enabled: false",
      identity: { instance_id: "0885f8d6-uuid", instance_token_present: true, telemetry_token_present: true },
      headers: ["Authorization: Bearer <telemetry token>"], payload: { corvin_version: "2.0.0", platform: "linux", python_minor: "3.11", active_engine: "claude_code" },
      payload_fields: ["active_engine", "corvin_version", "platform", "python_minor"] },
    { id: "heartbeat", title: "Presence heartbeat", status: "unknown", enabled: true, endpoint_host: "corvin-labs.com", cadence: "every 5 min", attempts: 0, successes: 0,
      consecutive_failures: 0, thread_running_in_this_process: false, payload: { features: { ldd_enabled: true } }, note: "No outcome recorded yet — the state file is written from the first attempt after this build." },
    { id: "healing_traces", title: "Healing traces", status: "sent", enabled: true, endpoint_host: "corvin-features-production.up.railway.app", pending_records: 13, pending: [{ file: "2026-09-20.jsonl", records: 13, bytes: 5900, compressed: false }],
      sent_bundles: 13, sent: [{ file: "2026-09-19.jsonl.gz", bytes: 376, sent_at: "2026-09-20T00:03:45Z" }], last_upload: { bundle_day: "2026-09-19", bundles: 1 }, last_sent: "2026-09-20T00:03:45Z", retention_days: 14, local_dir: "healing-traces",
      payload_fields: ["heal_action", "heal_outcome"], sample_record: { heal_action: "stale_lock", heal_outcome: "success" } },
    { id: "error_reports", title: "Error signatures", status: "collected_never_sent", enabled: true, intake_configured: false, outbox: { reports: 10164, bytes: 6301268, oldest: "2026-07-26T21:30:49Z", newest: "2026-09-17T01:15:18Z", dir: "aco/telemetry/outbox" },
      sent_reports: 0, top_signatures: [{ exc_type: "RuntimeError", top_repo_file: "core/gateway/corvin_gateway/app.py", func: "_lifespan", count: 500 }], signatures_sampled_from_newest: 500,
      note: "Reports accumulate in the outbox but nothing submits them: no intake URL is configured (CORVIN_TELEMETRY_URL) and no scheduler on this build calls submit()." },
    { id: "geo", title: "Geography", status: "sent", enabled: true, configured_tier: 3, effective_tier: 3, effective_tier_name: "city (10 km grid)", what_leaves: "The header X-HTrace-Geo-Tier: 3 on ping and heartbeat", carried_by: ["ping", "heartbeat"] },
    { id: "otlp_export", title: "OTLP export", status: "not_wired", enabled: false, sdk_installed: false, note: "OTELExporter has no production caller on this build; nothing is exported over OTLP." },
    { id: "stability", title: "Feature-stability digest", status: "not_wired", enabled: false, note: "The stability daemon is never initialised by the console host; nothing is sent." },
  ],
};
const LOCAL = { metrics: [{ name: "skill_executions", value: 42, unit: "events", status: "ok", source: "learning.event_store" }], alerts: [], available: true, sources: ["learning.event_store"], detail: "", range: "1h", timestamp: "2026-09-20T10:00:00Z" };

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><OTELTelemetryPage /></QueryClientProvider>);
}
afterEach(() => cleanup());

describe("Telemetry page", () => {
  it("renders every channel from its own state and counts them by status", async () => {
    server.use(
      http.get("/v1/console/telemetry/channels", () => HttpResponse.json(BODY)),
      http.get("/v1/console/v1/monitoring/metrics", () => HttpResponse.json(LOCAL)),
    );
    renderIt();
    await screen.findByTestId("telemetry-page");
    expect(screen.getByText(MARKER_TELEMETRY)).toBeInTheDocument();
    expect(screen.getAllByTestId("status-sent")).toHaveLength(3);
    expect(screen.getAllByTestId("status-not_wired")).toHaveLength(2);
    expect(screen.getByTestId("status-collected_never_sent").textContent).toBe("collected, never sent");
    // the tiles
    const tiles = screen.getByTestId("telemetry-page").textContent ?? "";
    expect(tiles).toMatch(/3\s*Sending/);
    expect(tiles).toMatch(/1\s*Collected, never sent/);
    expect(tiles).toMatch(/2\s*Not wired/);
    // the outbox that nobody submits — opened by default because it needs attention
    const er = screen.getByTestId("channel-error_reports");
    expect(er.textContent).toMatch(/10,164 reports · 6\.0 MB/);
    expect(er.textContent).toMatch(/no intake URL is configured/);
    expect(er.textContent).toMatch(/core\/gateway\/corvin_gateway\/app\.py::_lifespan/);
    // healing traces: waiting vs sent, from the files
    expect(screen.getByTestId("channel-healing_traces").textContent).toMatch(/13 records waiting in 1 file/);
    expect(screen.getByTestId("channel-healing_traces").textContent).toMatch(/13 bundles · last bundle for 2026-09-19/);
    // the ping payload appears only after opening the card, and no token ever does
    expect(screen.queryByTestId("detail-ping")).toBeNull();
    fireEvent.click(screen.getByTestId("toggle-ping"));
    expect(screen.getByTestId("detail-ping").textContent).toMatch(/"active_engine": "claude_code"/);
    expect(screen.getByTestId("detail-ping").textContent).toMatch(/instance token present/);
    expect(document.body.textContent).not.toMatch(/Bearer [A-Za-z0-9]{20,}/);
    // heartbeat without a recorded outcome is "no outcome recorded", not "0 sent"
    expect(screen.getByTestId("status-unknown").textContent).toBe("no outcome recorded");
    expect(screen.getByTestId("channel-heartbeat").textContent).toMatch(/sender thread not running in the console process/);
    // local metrics are named as local
    expect(await screen.findByText(/Measured on this install by learning.event_store — never transmitted/)).toBeInTheDocument();
  });

  it("a 404 build says so instead of showing zeros", async () => {
    server.use(http.get("/v1/console/telemetry/channels", () => HttpResponse.json({ detail: "nope" }, { status: 404 })));
    renderIt();
    await screen.findByText("Telemetry overview is not available on this build.");
  });
});
