/**
 * ProductionMonitoringDashboard renders the backend's honest "not measured"
 * answer (every figure null) without crashing.
 *
 * The monitoring routes stopped inventing figures and report null instead; the
 * component still called `.toFixed` on them, so the first real response would
 * have thrown during render. The component is not mounted by any route today,
 * which is why nothing noticed.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, waitFor, cleanup } from "@testing-library/react";
import ProductionMonitoringDashboard, {
  NOT_AVAILABLE, count, fmt, pct,
} from "@/components/monitoring/ProductionMonitoringDashboard";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const NULL_PAYLOADS: Record<string, unknown> = {
  "/monitoring/health": { health_score: null, status: "unknown", uptime_hours: null, last_check: null },
  "/monitoring/autonomous-stats": {
    skills_forged: null, skills_approved: null, skills_deferred: null, skills_active: null,
    success_rate: null, avg_optimization_confidence: null, avg_latency_ms: null,
    avg_error_rate: null, last_fork_timestamp: null,
  },
  "/monitoring/skill-performance": {
    skills: [{
      skill_id: "os.example", version: "1", confidence: null, latency_p50_ms: null,
      latency_p95_ms: null, latency_p99_ms: null, error_rate: null, execution_count: null,
      last_error: null, status: "unknown",
    }],
  },
  "/monitoring/alerts": {
    active_alerts: [{
      alert_id: "a1", severity: "warning", title: "t", message: "m", skill_id: null,
      metric_name: "latency", metric_value: null, threshold: null, created_at: "", resolved_at: null,
    }],
    recent_alerts: [],
  },
  "/monitoring/metrics/timeseries": { data_points: [] },
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("ProductionMonitoringDashboard with unmeasured (null) figures", () => {
  it("formatters return a placeholder instead of throwing", () => {
    for (const v of [null, undefined, Number.NaN]) {
      expect(fmt(v)).toBe(NOT_AVAILABLE);
      expect(pct(v)).toBe(NOT_AVAILABLE);
      expect(count(v)).toBe(NOT_AVAILABLE);
    }
    expect(fmt(1.25, 1, "ms")).toBe("1.3ms");
    expect(pct(0.5)).toBe("50%");
    expect(pct(0.1234, 2)).toBe("12.34%");
    expect(count(12345)).toBe("12,345");
    expect(fmt(0)).toBe("0.0");
  });

  it("mounts and renders the null payload", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const key = Object.keys(NULL_PAYLOADS).find((k) => String(url).includes(k));
      return { ok: key !== undefined, json: async () => NULL_PAYLOADS[key ?? ""] } as Response;
    }));
    const { container } = render(<ProductionMonitoringDashboard />);
    await waitFor(() => expect(container.textContent).toContain("Not measured on this build"));
    const text = container.textContent ?? "";
    expect(text).toContain("os.example");
    expect(text).toContain(`latency: ${NOT_AVAILABLE} (threshold: ${NOT_AVAILABLE})`);
    expect(text).not.toMatch(/NaN|null|undefined/);
  });

  it("takes status glyphs from the shared vocabulary, never a literal", () => {
    const src = readFileSync(
      resolve(__dirname, "../../src/components/monitoring/ProductionMonitoringDashboard.tsx"), "utf8");
    expect(src).toContain('from \'@/lib/status-markers\'');
    expect(src).not.toMatch(/[✅❌⚠ℹ\u{1F50D}⏳\u{1F4A1}\u{1F4CC}]/u);
  });
});
