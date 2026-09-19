/**
 * Telemetry — what this install collects and what it sends.
 *
 * Every card is one outbound channel, rendered from that channel's OWN state
 * (stamp files, outbox, sent bundles, opt-out flags) via
 * GET /v1/console/telemetry/channels. Nothing on this page is estimated: a
 * channel that leaves no trace says "unknown", a channel nothing calls says
 * "not wired", and a channel that only collects says so — the error-signature
 * outbox on this host held ten thousand reports nobody ever submitted.
 *
 * The local metrics block at the bottom (learning events, plugin health) is
 * what /v1/monitoring/metrics measures on this build; it never leaves the
 * machine and is kept here because the page used to be only that.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ChevronDown, ChevronRight, Gauge, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api/client";

// ── Types (routes/telemetry_overview.py) ─────────────────────────────────────

export type ChannelStatus = "sent" | "never" | "disabled" | "failing" | "unknown" | "collected_never_sent" | "not_wired" | "unavailable";

export interface Channel {
  id: string;
  title: string;
  purpose?: string;
  enabled: boolean | null;
  status: ChannelStatus;
  opt_out?: string;
  legal_basis?: string;
  cadence?: string;
  endpoint?: string | null;
  endpoint_host?: string | null;
  transport?: string;
  headers?: string[];
  payload?: Record<string, unknown>;
  payload_fields?: string[];
  feature_fields?: string[];
  identity?: { instance_id: string; instance_token_present: boolean; telemetry_token_present: boolean };
  last_sent?: string | null;
  next_due?: string | null;
  last_attempt?: string | null;
  last_success?: string | null;
  last_detail?: string | null;
  attempts?: number;
  successes?: number;
  consecutive_failures?: number;
  thread_running_in_this_process?: boolean;
  sample_record?: Record<string, unknown> | null;
  local_dir?: string;
  retention_days?: number;
  pending?: Array<{ file: string; records: number | null; bytes: number; compressed: boolean }>;
  pending_records?: number;
  sent_bundles?: number;
  sent?: Array<{ file: string; bytes: number; sent_at: string }>;
  last_upload?: { bundle_day: string; bundles: number } | null;
  intake_configured?: boolean;
  outbox?: { reports: number; bytes: number; oldest: string | null; newest: string | null; dir: string };
  top_signatures?: Array<{ exc_type: string; top_repo_file: string; func: string; count: number }>;
  signatures_sampled_from_newest?: number;
  sent_reports?: number;
  configured_tier?: number;
  consent_flag?: boolean;
  effective_tier?: number;
  effective_tier_name?: string;
  what_leaves?: string;
  carried_by?: string[];
  sdk_installed?: boolean;
  sdk_version?: string | null;
  wired?: boolean;
  fallback_records?: number;
  fallback_dir?: string;
  last_batch?: { reports_merged: number; signatures: number } | null;
  sent_batches?: Array<{ file: string; sent_at: string | null; reports_merged: number | null; signatures: number }>;
  started_at?: string | null;
  state_file?: string | null;
  note?: string | null;
}

export interface ChannelsResponse {
  tenant_id: string;
  corvin_home: string;
  channels: Channel[];
  never_transmitted: string[];
  errors: Record<string, string>;
  took_ms: number;
  timestamp: string;
}

interface LocalMetric { name: string; value: number; unit: string; status?: string; source?: string; total?: number; timestamp?: string }
interface LocalMetrics { metrics: LocalMetric[]; available: boolean; sources: string[]; detail: string; timestamp: string }

/** Rendered caption — also the deploy marker. */
export const MARKER_TELEMETRY = "Every outbound channel of this install, read from the channel's own state: what it collects, where it goes, when it last went.";

const KEY = ["telemetry", "channels"] as const;

// ── Formatting ───────────────────────────────────────────────────────────────

const fmtWhen = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US") : "—");
const fmtAgo = (iso?: string | null): string => {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const fmtInt = (n: number) => n.toLocaleString("en-US");

const STATUS: Record<ChannelStatus, { label: string; variant: "ok" | "warn" | "danger" | "outline" | "secondary" }> = {
  sent: { label: "sending", variant: "ok" },
  never: { label: "nothing sent yet", variant: "outline" },
  disabled: { label: "opted out", variant: "secondary" },
  failing: { label: "failing", variant: "danger" },
  unknown: { label: "no outcome recorded", variant: "outline" },
  collected_never_sent: { label: "collected, never sent", variant: "warn" },
  not_wired: { label: "not wired", variant: "secondary" },
  unavailable: { label: "unavailable", variant: "danger" },
};

function StatusBadge({ s }: { s: ChannelStatus }) {
  const m = STATUS[s] ?? STATUS.unavailable;
  return <Badge variant={m.variant} data-testid={`status-${s}`}>{m.label}</Badge>;
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-2 text-sm py-1 border-b border-border/60 last:border-b-0">
      <div className="text-muted-foreground">{k}</div>
      <div className="min-w-0 break-words">{children}</div>
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return <pre className="text-xs bg-muted/40 rounded-md p-2 overflow-x-auto font-mono">{JSON.stringify(value, null, 2)}</pre>;
}

function Fields({ fields }: { fields?: string[] }) {
  if (!fields || fields.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {fields.map((f) => <span key={f} className="font-mono text-xs px-1.5 py-0.5 rounded bg-muted/50 border border-border">{f}</span>)}
    </div>
  );
}

// ── Channel card ─────────────────────────────────────────────────────────────

function ChannelCard({ c }: { c: Channel }) {
  const [open, setOpen] = useState(c.status === "collected_never_sent" || c.status === "failing");
  const lastSent = c.last_sent ?? c.last_success ?? null;
  return (
    <Card data-testid={`channel-${c.id}`}>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">{c.title}</CardTitle>
          <StatusBadge s={c.status} />
          {c.enabled === false && c.status !== "disabled" && c.status !== "not_wired" && <Badge variant="secondary">off</Badge>}
          <span className="ml-auto text-xs text-muted-foreground" data-testid={`last-${c.id}`}>
            {c.status === "not_wired" || c.status === "disabled" ? "" : c.carried_by ? `rides on ${c.carried_by.join(" and ")}` : `last sent ${fmtAgo(lastSent)}`}
          </span>
        </div>
        {c.purpose && <CardDescription>{c.purpose}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-2">
        {c.note && (
          <div className="flex gap-2 text-sm rounded-md border border-amber-500/30 bg-amber-500/10 p-2" data-testid={`note-${c.id}`}>
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" /> <span>{c.note}</span>
          </div>
        )}
        {/* the essentials, always visible */}
        <div>
          {c.endpoint_host && <Row k="Goes to"><span className="font-mono text-xs">{c.endpoint_host}</span>{c.transport ? <span className="text-muted-foreground"> · {c.transport}</span> : null}</Row>}
          {c.id === "error_reports" && !c.intake_configured && <Row k="Goes to"><span className="text-muted-foreground">no intake URL configured</span></Row>}
          {c.cadence && <Row k="When">{c.cadence}</Row>}
          {c.id === "ping" && <Row k="Last · next">{fmtWhen(c.last_sent)} · next due {fmtWhen(c.next_due)}</Row>}
          {c.id === "heartbeat" && (
            <Row k="Outcomes">
              {c.attempts ? (
                <>{fmtInt(c.successes ?? 0)} of {fmtInt(c.attempts)} attempts succeeded · last {c.last_detail ?? "—"} at {fmtWhen(c.last_attempt)}
                  {c.consecutive_failures ? <span className="text-destructive"> · {c.consecutive_failures} consecutive failures</span> : null}</>
              ) : <span className="text-muted-foreground">none recorded</span>}
              {c.thread_running_in_this_process === false && <span className="text-muted-foreground"> · sender thread not running in the console process</span>}
            </Row>
          )}
          {c.id === "healing_traces" && (
            <>
              <Row k="Collected">{fmtInt(c.pending_records ?? 0)} records waiting in {c.pending?.length ?? 0} file{(c.pending?.length ?? 0) === 1 ? "" : "s"} · retained {c.retention_days} days · <span className="font-mono text-xs">{c.local_dir}</span></Row>
              <Row k="Sent">{fmtInt(c.sent_bundles ?? 0)} bundles{c.last_upload ? ` · last bundle for ${c.last_upload.bundle_day} (${c.last_upload.bundles} upload${c.last_upload.bundles === 1 ? "" : "s"})` : ""}</Row>
            </>
          )}
          {c.id === "error_reports" && c.outbox && (
            <>
              <Row k="Waiting">{fmtInt(c.outbox.reports)} reports · {fmtBytes(c.outbox.bytes)} · {c.outbox.oldest ? `${fmtWhen(c.outbox.oldest)} → ${fmtWhen(c.outbox.newest)}` : "empty"} · <span className="font-mono text-xs">{c.outbox.dir}</span></Row>
              <Row k="Sent">{fmtInt(c.sent_reports ?? 0)} reports in {fmtInt(c.successes ?? 0)} batch{(c.successes ?? 0) === 1 ? "" : "es"}{c.last_batch ? ` · last batch merged ${fmtInt(c.last_batch.reports_merged)} reports into ${c.last_batch.signatures} signature${c.last_batch.signatures === 1 ? "" : "s"}` : ""}{c.last_detail ? ` · last ${c.last_detail} at ${fmtWhen(c.last_attempt)}` : ""}</Row>
            </>
          )}
          {(c.id === "otlp_export" || c.id === "stability") && (
            <Row k="Outcomes">
              {c.attempts ? (
                <>{fmtInt(c.successes ?? 0)} of {fmtInt(c.attempts)} pushes succeeded · last {c.last_detail ?? "—"} at {fmtWhen(c.last_attempt)}
                  {c.consecutive_failures ? <span className="text-destructive"> · {c.consecutive_failures} consecutive failures</span> : null}</>
              ) : <span className="text-muted-foreground">none recorded</span>}
              {c.id === "otlp_export" && (c.fallback_records ?? 0) > 0 && <span> · {fmtInt(c.fallback_records ?? 0)} records kept locally in <span className="font-mono text-xs">{c.fallback_dir}</span> after failed pushes</span>}
              {c.id === "stability" && c.thread_running_in_this_process === false && <span className="text-muted-foreground"> · daemon not running in the console process</span>}
            </Row>
          )}
          {c.id === "geo" && (
            <>
              <Row k="Tier">configured {c.configured_tier} · effective {c.effective_tier} ({c.effective_tier_name})</Row>
              <Row k="What leaves">{c.what_leaves}</Row>
            </>
          )}
          {c.opt_out && <Row k="Opt out"><span className="font-mono text-xs">{c.opt_out}</span></Row>}
        </div>

        <button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" data-testid={`toggle-${c.id}`}>
          {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />} {open ? "Hide" : "Show"} what is sent
        </button>
        {open && (
          <div className="space-y-2" data-testid={`detail-${c.id}`}>
            {c.identity && <Row k="Identity">instance id <span className="font-mono text-xs">{c.identity.instance_id}</span> (random uuid4) · instance token {c.identity.instance_token_present ? "present" : "absent"} · telemetry token {c.identity.telemetry_token_present ? "present" : "absent"}</Row>}
            {c.headers && c.headers.length > 0 && <Row k="Headers"><Fields fields={c.headers} /></Row>}
            {c.payload_fields && c.payload_fields.length > 0 && <Row k="Fields"><Fields fields={c.payload_fields} /></Row>}
            {c.feature_fields && c.feature_fields.length > 0 && <Row k="Feature keys"><Fields fields={c.feature_fields} /></Row>}
            {c.payload && Object.keys(c.payload).length > 0 && <Row k="Body now"><Json value={c.payload} /></Row>}
            {c.payload && Object.keys(c.payload).length === 0 && <Row k="Body now"><span className="text-muted-foreground">empty body — the headers carry the identity</span></Row>}
            {c.sample_record && <Row k="Latest record"><Json value={c.sample_record} /></Row>}
            {c.top_signatures && c.top_signatures.length > 0 && (
              <Row k={`Top signatures`}>
                <div className="text-xs text-muted-foreground mb-1">from the newest {fmtInt(c.signatures_sampled_from_newest ?? 0)} reports</div>
                <table className="text-xs w-full"><tbody>
                  {c.top_signatures.map((s) => (
                    <tr key={`${s.exc_type}${s.top_repo_file}${s.func}`} className="border-b border-border/60 last:border-b-0">
                      <td className="py-0.5 pr-2 font-mono">{s.exc_type}</td><td className="py-0.5 pr-2 font-mono">{s.top_repo_file}::{s.func}</td><td className="py-0.5 text-right tabular-nums">{fmtInt(s.count)}</td>
                    </tr>
                  ))}
                </tbody></table>
              </Row>
            )}
            {c.sent && c.sent.length > 0 && (
              <Row k="Sent bundles">
                <table className="text-xs w-full"><tbody>
                  {c.sent.map((b) => <tr key={b.file} className="border-b border-border/60 last:border-b-0"><td className="py-0.5 pr-2 font-mono">{b.file}</td><td className="py-0.5 pr-2 tabular-nums">{fmtBytes(b.bytes)}</td><td className="py-0.5 text-right">{fmtWhen(b.sent_at)}</td></tr>)}
                </tbody></table>
              </Row>
            )}
            {c.pending && c.pending.length > 0 && (
              <Row k="Waiting">
                {c.pending.map((p) => <div key={p.file} className="font-mono text-xs">{p.file} · {p.records === null ? "compressed" : `${p.records} records`} · {fmtBytes(p.bytes)}</div>)}
              </Row>
            )}
            {c.carried_by && <Row k="Carried by">{c.carried_by.join(", ")}</Row>}
            {c.legal_basis && <Row k="Legal basis">{c.legal_basis}</Row>}
            {c.state_file && <Row k="State file"><span className="font-mono text-xs">{c.state_file}</span></Row>}
            {c.sdk_installed !== undefined && <Row k="SDK">{c.sdk_installed ? `opentelemetry-sdk ${c.sdk_version ?? ""} with the OTLP/HTTP exporter` : "opentelemetry SDK or OTLP/HTTP exporter not installed"}</Row>}
            {c.sent_batches && c.sent_batches.length > 0 && (
              <Row k="Sent batches">
                <table className="text-xs w-full"><tbody>
                  {c.sent_batches.map((b) => <tr key={b.file} className="border-b border-border/60 last:border-b-0"><td className="py-0.5 pr-2 font-mono">{b.file}</td><td className="py-0.5 pr-2 tabular-nums">{fmtInt(b.reports_merged ?? 0)} reports → {b.signatures} signature{b.signatures === 1 ? "" : "s"}</td><td className="py-0.5 text-right">{fmtWhen(b.sent_at)}</td></tr>)}
                </tbody></table>
              </Row>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function OTELTelemetryPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [...KEY], queryFn: ({ signal }) => api<ChannelsResponse>("/telemetry/channels", { signal }), refetchInterval: 30_000, retry: false });
  const local = useQuery({ queryKey: ["telemetry", "local-metrics"], queryFn: ({ signal }) => api<LocalMetrics>("/v1/monitoring/metrics?range=1h", { signal }), refetchInterval: 30_000, retry: false });

  if (q.isLoading) return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  if (q.isError || !q.data) {
    const off = q.error instanceof ApiError && q.error.status === 404;
    return (
      <div className="max-w-6xl mx-auto p-6">
        <Card className="border-destructive/30 bg-destructive/10"><CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} /> {off ? "Telemetry overview is not available on this build." : "The telemetry overview could not be loaded."}
        </CardContent></Card>
      </div>
    );
  }

  const d = q.data;
  const sending = d.channels.filter((c) => c.status === "sent").length;
  const collectedOnly = d.channels.filter((c) => c.status === "collected_never_sent").length;
  const notWired = d.channels.filter((c) => c.status === "not_wired").length;
  const optedOut = d.channels.filter((c) => c.status === "disabled").length;

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6" data-testid="telemetry-page">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2"><Gauge className="w-7 h-7" /> Telemetry</h1>
          <p className="text-muted-foreground max-w-3xl">{MARKER_TELEMETRY}</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void qc.invalidateQueries({ queryKey: ["telemetry"] })}><RefreshCw className="w-4 h-4" /> Refresh</Button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          ["Sending", sending], ["Collected, never sent", collectedOnly], ["Opted out", optedOut], ["Not wired", notWired],
        ].map(([label, n]) => (
          <Card key={String(label)}><CardContent className="pt-6 text-center">
            <div className="text-3xl font-bold tabular-nums">{n}</div>
            <p className="text-xs text-muted-foreground mt-1">{label}</p>
          </CardContent></Card>
        ))}
      </div>

      <Card>
        <CardContent className="py-3 text-sm flex flex-wrap items-center gap-x-6 gap-y-1">
          <span><span className="text-muted-foreground">Install:</span> <span className="font-mono text-xs">{d.corvin_home}</span></span>
          <span className="flex items-center gap-1 text-muted-foreground"><ShieldCheck className="w-4 h-4" /> Never transmitted:</span>
          <span className="text-muted-foreground">{d.never_transmitted.join(" · ")}</span>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {d.channels.map((c) => <ChannelCard key={c.id} c={c} />)}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Local metrics</CardTitle>
          <CardDescription data-testid="local-caption">
            {local.isError ? "could not be loaded" : local.data?.available ? `Measured on this install by ${local.data.sources.join(", ")} — never transmitted.` : local.data?.detail ?? "loading…"}
          </CardDescription>
        </CardHeader>
        {local.data?.available && (
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {local.data.metrics.map((m) => (
                <div key={m.name} className="rounded-lg border border-border p-3">
                  <div className="text-2xl font-bold tabular-nums">{Number.isInteger(m.value) ? fmtInt(m.value) : m.value.toFixed(2)}{m.total !== undefined ? <span className="text-sm font-normal text-muted-foreground"> / {m.total}</span> : null}</div>
                  <div className="text-xs text-muted-foreground">{m.name.replace(/_/g, " ")} · {m.unit}</div>
                </div>
              ))}
            </div>
          </CardContent>
        )}
      </Card>

      <div className="text-xs text-muted-foreground">Read in {d.took_ms} ms · {fmtWhen(d.timestamp)}</div>
    </div>
  );
}
