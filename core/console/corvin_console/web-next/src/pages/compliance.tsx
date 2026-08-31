import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Hash,
  Link2,
  Layers,
  Plus,
  RefreshCw,
  ScrollText,
  Shield,
  ShieldCheck,
  Trash2,
  UserCheck,
  Webhook,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  auditTail,
  dashboard,
  listMembers,
  listAuditLayers,
  registerAuditLayer,
  removeAuditLayer,
  emitCustomAuditEvent,
  listWebhookChannels,
  registerWebhookChannel,
  removeWebhookChannel,
  type AuditEvent,
  type AuditLayer,
  type WebhookChannel,
} from "@/lib/api";
import { cn, formatBytes, formatDate } from "@/lib/utils";
import { useAuth } from "@/lib/auth";

export function CompliancePage() {
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="font-serif text-3xl font-light tracking-tight">Audit & Compliance</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your audit trail, user roles, and privacy settings — all in one place.
        </p>
      </header>
      <GuaranteesCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <AuditChainCard />
        <RolesCard />
      </div>
      <AuditTailCard />
      <CustomAuditLayersSection />
      <WebhookChannelsSection />
    </div>
  );
}

function GuaranteesCard() {
  // These are structural — never derived from runtime state, so they're
  // safe to render statically. The "Verify Now" button in the chain
  // card is the dynamic check.
  const guarantees = [
    "Hash-chained audit log (`audit.jsonl` + daily verify)",
    "Per-user consent gate (deny-by-default, TTL-capped)",
    "Bot-disclosure card (one-time per uid)",
    "Secret vault → bwrap env (never in LLM context)",
    "Path-gate hook (fail-closed on FS writes)",
    "Voice-transcribe audit emits METADATA ONLY",
  ];
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-accent" />
          <CardTitle className="text-base">Structural guarantees</CardTitle>
        </div>
        <CardDescription>Locked in CLAUDE.md; CI lints these are not weakened.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-1.5 sm:grid-cols-2">
        {guarantees.map((g) => (
          <div key={g} className="flex items-start gap-2 text-sm">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
            <span>{g}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function AuditChainCard() {
  const q = useQuery({
    queryKey: ["dashboard"],
    queryFn: ({ signal }) => dashboard(signal),
    refetchInterval: 30_000,
  });
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Hash className="h-4 w-4 text-accent" />
          <CardTitle className="text-base">Hash chain</CardTitle>
        </div>
        <CardDescription>
          `voice-audit verify` is the canonical integrity check; this card surfaces the tail
          summary.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {q.isLoading && <Skeleton className="h-24 w-full" />}
        {q.data && (
          <>
            <Row
              label="Present"
              value={
                q.data.audit_chain.present ? (
                  <Badge variant="ok">yes</Badge>
                ) : (
                  <Badge variant="outline">no</Badge>
                )
              }
            />
            <Row
              label="Size"
              value={
                <span className="font-mono">
                  {formatBytes(q.data.audit_chain.size_bytes)}
                </span>
              }
            />
            <Row
              label="Last event"
              value={
                <span className="font-mono">
                  {q.data.audit_chain.last_event_type ?? "—"}
                </span>
              }
            />
            <Row
              label="Last ts"
              value={
                <span className="font-mono text-xs">
                  {formatDate(q.data.audit_chain.last_event_ts ?? null)}
                </span>
              }
            />
            <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              For a full integrity verification:{" "}
              <span className="font-mono">voice-audit verify</span> on the host.
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RolesCard() {
  const q = useQuery({
    queryKey: ["members"],
    queryFn: ({ signal }) => listMembers(signal),
    retry: 0,
  });
  const chats = q.data?.chats ?? [];
  const totalMembers = chats.reduce((s, c) => s + (c.members ?? 0), 0);
  const totalConsent = chats.reduce((s, c) => s + (c.consent_entries ?? 0), 0);
  const totalDisclosure = chats.reduce((s, c) => s + (c.disclosure_entries ?? 0), 0);
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <UserCheck className="h-4 w-4 text-accent" />
          <CardTitle className="text-base">Roles, consent & disclosure</CardTitle>
        </div>
        <CardDescription>
          One row per chat across all bridges. Per-uid drill-down lands in a future iteration.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {q.isLoading && <Skeleton className="h-20 w-full" />}
        {q.isError && (
          <p className="text-xs text-muted-foreground">
            Members endpoint unavailable — {(q.error as Error | undefined)?.message ?? "unknown"}
          </p>
        )}
        {q.data && chats.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No chat-bound role / consent / disclosure state yet.
          </p>
        )}
        {q.data && chats.length > 0 && (
          <>
            <div className="grid grid-cols-3 gap-2 rounded-md bg-muted/30 px-3 py-2 text-xs">
              <Row label="Total members" value={<span className="font-mono">{totalMembers}</span>} />
              <Row label="Consent grants" value={<span className="font-mono">{totalConsent}</span>} />
              <Row label="Disclosure" value={<span className="font-mono">{totalDisclosure}</span>} />
            </div>
            <div className="space-y-1">
              {chats.slice(0, 10).map((c) => (
                <div
                  key={c.chat_key}
                  className="flex items-center justify-between rounded-md bg-muted/40 px-3 py-1.5 text-xs"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <Badge variant="outline" className="font-mono">
                      {c.channel || "unknown"}
                    </Badge>
                    <span className="truncate font-mono text-muted-foreground">{c.chat || c.chat_key}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant="secondary" className="text-[10px]">
                      {c.members} member{c.members === 1 ? "" : "s"}
                    </Badge>
                    {c.consent_entries > 0 && (
                      <Badge variant="accent" className="text-[10px]">
                        {c.consent_entries} consent
                      </Badge>
                    )}
                  </div>
                </div>
              ))}
              {chats.length > 10 && (
                <p className="px-3 pt-1 text-[10px] text-muted-foreground">
                  … and {chats.length - 10} more
                </p>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Event family config ────────────────────────────────────────────────────

interface FamilyStyle {
  label: string;
  dot:   string;
  line:  string;
  badge: string;
  text:  string;
  bg:    string;
  border: string;
}

const FAMILY_STYLES: Record<string, FamilyStyle> = {
  chain:     { label: "Chain",
    dot:    "border-amber-500 bg-amber-200 dark:bg-amber-950",
    line:   "bg-amber-200/70 dark:bg-amber-900/40",
    badge:  "border-amber-400 text-amber-600 dark:border-amber-700 dark:text-amber-400",
    text:   "text-amber-700 dark:text-amber-300",
    bg:     "bg-amber-50/80 dark:bg-amber-950/30",
    border: "border-amber-300/60 dark:border-amber-700/40",
  },
  chain_dna: { label: "Chain DNA",
    dot:    "border-amber-500 bg-amber-200 dark:bg-amber-950",
    line:   "bg-amber-200/70 dark:bg-amber-900/40",
    badge:  "border-amber-400 text-amber-600 dark:border-amber-700 dark:text-amber-400",
    text:   "text-amber-700 dark:text-amber-300",
    bg:     "bg-amber-50/80 dark:bg-amber-950/30",
    border: "border-amber-300/60 dark:border-amber-700/40",
  },
  console: { label: "Console",
    dot:    "border-indigo-500 bg-indigo-200 dark:bg-indigo-950",
    line:   "bg-indigo-200/70 dark:bg-indigo-900/40",
    badge:  "border-indigo-400 text-indigo-600 dark:border-indigo-700 dark:text-indigo-400",
    text:   "text-indigo-700 dark:text-indigo-300",
    bg:     "bg-indigo-50/80 dark:bg-indigo-950/30",
    border: "border-indigo-300/60 dark:border-indigo-700/40",
  },
  os_turn: { label: "OS Turn",
    dot:    "border-sky-500 bg-sky-200 dark:bg-sky-950",
    line:   "bg-sky-200/70 dark:bg-sky-900/40",
    badge:  "border-sky-400 text-sky-600 dark:border-sky-700 dark:text-sky-400",
    text:   "text-sky-700 dark:text-sky-300",
    bg:     "bg-sky-50/80 dark:bg-sky-950/30",
    border: "border-sky-300/60 dark:border-sky-700/40",
  },
  delegation: { label: "Delegation",
    dot:    "border-orange-500 bg-orange-200 dark:bg-orange-950",
    line:   "bg-orange-200/70 dark:bg-orange-900/40",
    badge:  "border-orange-400 text-orange-600 dark:border-orange-700 dark:text-orange-400",
    text:   "text-orange-700 dark:text-orange-300",
    bg:     "bg-orange-50/80 dark:bg-orange-950/30",
    border: "border-orange-300/60 dark:border-orange-700/40",
  },
  A2A: { label: "A2A",
    dot:    "border-orange-500 bg-orange-200 dark:bg-orange-950",
    line:   "bg-orange-200/70 dark:bg-orange-900/40",
    badge:  "border-orange-400 text-orange-600 dark:border-orange-700 dark:text-orange-400",
    text:   "text-orange-700 dark:text-orange-300",
    bg:     "bg-orange-50/80 dark:bg-orange-950/30",
    border: "border-orange-300/60 dark:border-orange-700/40",
  },
  acs: { label: "ACS",
    dot:    "border-emerald-500 bg-emerald-200 dark:bg-emerald-950",
    line:   "bg-emerald-200/70 dark:bg-emerald-900/40",
    badge:  "border-emerald-400 text-emerald-600 dark:border-emerald-700 dark:text-emerald-400",
    text:   "text-emerald-700 dark:text-emerald-300",
    bg:     "bg-emerald-50/80 dark:bg-emerald-950/30",
    border: "border-emerald-300/60 dark:border-emerald-700/40",
  },
  forge: { label: "Forge",
    dot:    "border-cyan-500 bg-cyan-200 dark:bg-cyan-950",
    line:   "bg-cyan-200/70 dark:bg-cyan-900/40",
    badge:  "border-cyan-400 text-cyan-600 dark:border-cyan-700 dark:text-cyan-400",
    text:   "text-cyan-700 dark:text-cyan-300",
    bg:     "bg-cyan-50/80 dark:bg-cyan-950/30",
    border: "border-cyan-300/60 dark:border-cyan-700/40",
  },
  voice: { label: "Voice",
    dot:    "border-violet-500 bg-violet-200 dark:bg-violet-950",
    line:   "bg-violet-200/70 dark:bg-violet-900/40",
    badge:  "border-violet-400 text-violet-600 dark:border-violet-700 dark:text-violet-400",
    text:   "text-violet-700 dark:text-violet-300",
    bg:     "bg-violet-50/80 dark:bg-violet-950/30",
    border: "border-violet-300/60 dark:border-violet-700/40",
  },
  audit: { label: "Audit",
    dot:    "border-amber-400 bg-amber-100 dark:bg-amber-950",
    line:   "bg-amber-100/70 dark:bg-amber-900/40",
    badge:  "border-amber-400 text-amber-700 dark:border-amber-600 dark:text-amber-300",
    text:   "text-amber-800 dark:text-amber-200",
    bg:     "bg-amber-50 dark:bg-amber-950/40",
    border: "border-amber-300/60 dark:border-amber-600/40",
  },
  session: { label: "Session",
    dot:    "border-slate-500 bg-slate-200 dark:bg-slate-900",
    line:   "bg-slate-200/70 dark:bg-slate-800",
    badge:  "border-slate-400 text-slate-600 dark:border-slate-600 dark:text-slate-400",
    text:   "text-slate-700 dark:text-slate-300",
    bg:     "bg-slate-50/80 dark:bg-slate-900/60",
    border: "border-slate-300/50 dark:border-slate-700/40",
  },
  default: { label: "Other",
    dot:    "border-slate-400 bg-slate-100 dark:bg-slate-900",
    line:   "bg-slate-200/50 dark:bg-slate-800/60",
    badge:  "border-slate-400 text-slate-600 dark:border-slate-700 dark:text-slate-500",
    text:   "text-slate-600 dark:text-slate-400",
    bg:     "bg-slate-50/60 dark:bg-slate-900/40",
    border: "border-slate-300/40 dark:border-slate-700/30",
  },
};

// Filter tabs: label → event_type prefix or special key
const FILTER_TABS = [
  { key: "",           label: "All" },
  { key: "SECURITY",   label: "Security" },
  { key: "chain",      label: "Chain" },
  { key: "console",    label: "Console" },
  { key: "os_turn",    label: "OS Turn" },
  { key: "delegation", label: "Delegation" },
  { key: "A2A",        label: "A2A" },
  { key: "acs",        label: "ACS" },
  { key: "forge",      label: "Forge" },
] as const;

function getFamily(eventType: string): string {
  const prefix = eventType.split(".")[0];
  if (prefix === "chain" && eventType.includes("dna")) return "chain_dna";
  return prefix in FAMILY_STYLES ? prefix : "default";
}

function familyStyle(eventType: string): FamilyStyle {
  return FAMILY_STYLES[getFamily(eventType)] ?? FAMILY_STYLES.default;
}

// ── Inline detail helper ────────────────────────────────────────────────────

function inlineDetail(ev: AuditEvent): string | null {
  const d = ev.details as Record<string, unknown>;
  const et = ev.event_type;
  const s = (v: unknown, max = 12) => String(v ?? "").slice(0, max);

  if (et === "chain.genesis")
    return `net:${s(d.network_id, 20)} · inst:${s(d.instance_id)}…`;
  if (et === "chain.rotation_link")
    return `prev:${s(d.prev_hash_prefix)}…`;
  if (et === "console.action_performed")
    return `${s(d.action, 24)} · ${s(d.target_kind, 20)}`;
  if (et === "console.action_failed" || et === "console.action_denied")
    return `${s(d.action, 20)} · reason: ${s(d.reason, 30)}`;
  if (et === "console.session_started" || et === "console.session_denied")
    return d.channel ? `${s(d.channel, 16)} · ${s(d.chat_key, 20)}` : null;
  if (et === "os_turn.started")
    return `${s(d.persona, 16)} · ${s(d.model, 24)} · ${s(d.chat_key, 20)}`;
  if (et === "os_turn.completed")
    return `${d.tools_called ?? 0} tools · ${((Number(d.duration_ms) || 0) / 1000).toFixed(1)}s · exit:${d.exit_code ?? 0}`;
  if (et === "os_turn.tool_called")
    return `${s(d.tool_name, 20)} · seq:${d.seq ?? "?"}`;
  if (et === "delegation.started")
    return `→ ${s(d.target_engine, 20)} · id:${s(d.delegation_id)}…`;
  if (et === "delegation.ended")
    return `${((Number(d.duration_ms) || 0) / 1000).toFixed(1)}s · ${s(d.status, 10)}`;
  if (et === "A2A.engine_spawned")
    return `engine:${s(d.engine_id, 16)} · origin:${s(d.origin_id)}…`;
  if (et === "A2A.chain_dna_verified")
    return `DNA ✓ · match:${d.instance_id_match}`;
  if (et === "A2A.chain_dna_mismatch")
    return `DNA ✗ · ${s(d.reason, 30)}`;
  if (et === "A2A.result_filtered")
    return `pass:${d.filter_pass_count ?? 0} · reject:${d.filter_reject_count ?? 0}`;
  if (et === "A2A.envelope_sent" || et === "A2A.envelope_received")
    return `task:${s(d.task_id)}… · ttl:${d.ttl_s ?? "?"}s`;
  if (et === "A2A.response_signed" || et === "A2A.response_received")
    return `${((Number(d.duration_ms) || 0) / 1000).toFixed(1)}s · http:${d.http_status ?? "?"}`;
  if (et === "acs.manager_decided")
    return `iter:${d.iteration} · ${s(d.decision_type, 20)} · ${d.n_subtasks ?? 0} tasks`;
  if (et === "acs.worker_spawned")
    return `worker:${s(d.worker_id, 16)} · depth:${d.depth ?? 0}`;
  if (et === "acs.worker_traced")
    return `worker:${s(d.worker_id, 16)} · ${s(d.status, 10)} · conf:${((Number(d.confidence) || 0) * 100).toFixed(0)}%`;
  if (et === "acs.engine_started" || et === "acs.engine_completed")
    return `worker:${s(d.worker_id, 12)} · ${s(d.engine_id, 12)} · ${s(d.locality, 10)}`;
  if (et === "acs.workflow_complete")
    return `${d.workers_spawned ?? 0} workers · ${d.iterations ?? 0} iters · ${s(d.status, 10)}`;
  if (et === "forge.tool_executed")
    return `${s(d.tool_name, 20)} · ${s(d.decision, 8)}`;
  return d.chat_key ? `${s(d.chat_key, 28)}` : null;
}

// ── Distribution bar ────────────────────────────────────────────────────────

const FAMILY_BAR_COLOR: Record<string, string> = {
  chain:      "bg-amber-600",
  chain_dna:  "bg-amber-500",
  console:    "bg-indigo-500",
  os_turn:    "bg-sky-500",
  delegation: "bg-orange-500",
  A2A:        "bg-orange-400",
  acs:        "bg-emerald-500",
  forge:      "bg-cyan-500",
  voice:      "bg-violet-500",
  audit:      "bg-amber-400",
  session:    "bg-slate-500",
  default:    "bg-slate-600",
};

function ChainDistributionBar({ events }: { events: AuditEvent[] }) {
  const counts = React.useMemo(() => {
    const m: Record<string, number> = {};
    for (const ev of events) {
      const f = getFamily(ev.event_type);
      m[f] = (m[f] || 0) + 1;
    }
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  }, [events]);

  if (counts.length === 0) return null;
  const total = counts.reduce((s, [, n]) => s + n, 0);

  return (
    <div className="space-y-1">
      <div className="flex h-2 w-full overflow-hidden rounded-full gap-px">
        {counts.map(([fam, n]) => (
          <div
            key={fam}
            className={cn("transition-all", FAMILY_BAR_COLOR[fam] ?? "bg-slate-600")}
            style={{ flex: n }}
            title={`${FAMILY_STYLES[fam]?.label ?? fam}: ${n}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
        {counts.slice(0, 8).map(([fam, n]) => (
          <span key={fam} className="flex items-center gap-1 text-[9px] text-muted-foreground">
            <span className={cn("inline-block h-1.5 w-1.5 rounded-full", FAMILY_BAR_COLOR[fam] ?? "bg-slate-600")} />
            {FAMILY_STYLES[fam]?.label ?? fam}
            <span className="text-muted-foreground/60">
              {n} ({((n / total) * 100).toFixed(0)}%)
            </span>
          </span>
        ))}
        {counts.length > 8 && (
          <span className="text-[9px] text-muted-foreground/50">+{counts.length - 8} more</span>
        )}
      </div>
    </div>
  );
}

// ── Chain event row ────────────────────────────────────────────────────────

function ChainEventRow({ ev, last }: { ev: AuditEvent; last?: boolean }) {
  const [open, setOpen] = React.useState(false);
  const fs = familyStyle(ev.event_type);
  const detail = inlineDetail(ev);
  const hasDetails = Object.keys(ev.details).length > 0;
  const isCritical = ev.severity === "CRITICAL" || ev.severity === "ERROR";
  const isWarning  = ev.severity === "WARNING"  || ev.severity === "WARN";

  return (
    <div className="flex gap-1.5 min-h-0 px-3">
      {/* chain link column */}
      <div className="flex flex-col items-center w-3 flex-shrink-0 pt-2.5">
        <div className={cn("w-2.5 h-2.5 rounded-full border-2 flex-shrink-0", fs.dot)} />
        {!last && <div className={cn("w-px flex-1 mt-0.5 min-h-[12px]", fs.line)} />}
      </div>

      {/* event card */}
      <div
        className={cn(
          "flex-1 rounded border my-0.5 min-w-0",
          fs.bg, fs.border,
          hasDetails && "cursor-pointer select-none",
          isCritical && "border-red-400/60 dark:border-red-500/60 bg-red-50/80 dark:bg-red-950/30",
          isWarning  && "border-amber-400/50 dark:border-amber-500/50 bg-amber-50/80 dark:bg-amber-950/20",
        )}
        onClick={() => hasDetails && setOpen(v => !v)}
      >
        {/* main row */}
        <div className="flex items-center gap-2 px-2 py-1.5 min-w-0">
          {/* expand toggle */}
          {hasDetails ? (
            open
              ? <ChevronDown className="h-2.5 w-2.5 shrink-0 text-muted-foreground/60" />
              : <ChevronRight className="h-2.5 w-2.5 shrink-0 text-muted-foreground/40" />
          ) : (
            <span className="h-2.5 w-2.5 shrink-0" />
          )}

          {/* family badge */}
          <Badge
            variant="outline"
            className={cn("text-[8px] px-1 py-0 shrink-0 font-mono", fs.badge)}
          >
            {FAMILY_STYLES[getFamily(ev.event_type)]?.label ?? getFamily(ev.event_type)}
          </Badge>

          {/* event type */}
          <span className={cn("font-mono text-[10px] font-semibold shrink-0", fs.text)}>
            {ev.event_type}
          </span>

          {/* inline detail */}
          {detail && (
            <span className="font-mono text-[9px] text-muted-foreground/70 truncate min-w-0 flex-1">
              {detail}
            </span>
          )}

          {/* severity badge */}
          {(isCritical || isWarning) && (
            <Badge
              variant="outline"
              className={cn(
                "text-[8px] px-1 py-0 shrink-0 ml-auto",
                isCritical ? "border-red-500 text-red-400" : "border-amber-500 text-amber-400",
              )}
            >
              {ev.severity}
            </Badge>
          )}

          {/* hash prefix */}
          {ev.hash_prefix && (
            <span className="font-mono text-[8px] text-cyan-600 dark:text-cyan-800 shrink-0 ml-auto flex items-center gap-0.5">
              <Link2 className="h-2 w-2" />
              {ev.hash_prefix}
            </span>
          )}

          {/* timestamp */}
          <span className="font-mono text-[8px] text-muted-foreground/60 shrink-0 whitespace-nowrap">
            {formatDate(ev.ts)}
          </span>
        </div>

        {/* expanded details — structured key-value grid */}
        {open && hasDetails && (
          <div className="border-t border-border/60 px-2 py-1.5 mx-0">
            <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 sm:grid-cols-3">
              {Object.entries(ev.details).map(([k, v]) => (
                <div key={k} className="flex items-baseline gap-1.5 min-w-0">
                  <span className="font-mono text-[9px] text-muted-foreground shrink-0">{k}</span>
                  <span
                    className={cn(
                      "font-mono text-[9px] truncate",
                      typeof v === "boolean"
                        ? v ? "text-emerald-600 dark:text-emerald-400" : "text-red-500 dark:text-red-400"
                        : "text-foreground/80",
                    )}
                  >
                    {v === null || v === undefined
                      ? <span className="text-muted-foreground/40">null</span>
                      : typeof v === "object"
                      ? JSON.stringify(v)
                      : String(v)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── ChainAuditViewer (replaces old AuditTailCard) ──────────────────────────

function AuditTailCard() {
  const [limit, setLimit]         = React.useState(200);
  const [activeTab, setActiveTab] = React.useState<string>("");

  // Compute event_prefix and severity from activeTab
  const isSecurityTab = activeTab === "SECURITY";
  const queryPrefix   = (!activeTab || isSecurityTab) ? undefined : activeTab;
  const querySeverity = undefined; // severity filter handled by activeTab

  const q = useQuery({
    queryKey: ["audit", "tail", limit, querySeverity, queryPrefix],
    queryFn:  ({ signal }) => auditTail(
      { limit, severity: querySeverity, eventPrefix: queryPrefix },
      signal,
    ),
    refetchInterval: 20_000,
  });

  // For the SECURITY pseudo-tab, filter client-side
  const events = React.useMemo(() => {
    const evs = q.data?.events ?? [];
    if (isSecurityTab) return evs.filter(e => e.severity === "WARNING" || e.severity === "CRITICAL" || e.severity === "ERROR");
    return evs;
  }, [q.data, isSecurityTab]);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">
              Audit chain{q.data && ` · ${q.data.count} events`}
            </CardTitle>
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={String(limit)}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="h-7 text-xs w-28"
            >
              {[50, 100, 200, 500, 1000].map((n) => (
                <option key={n} value={n}>Limit · {n}</option>
              ))}
            </Select>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => q.refetch()}
              disabled={q.isFetching}
              className="h-7 px-2"
            >
              <RefreshCw className={cn("h-3 w-3", q.isFetching && "animate-spin")} />
            </Button>
          </div>
        </div>
        <CardDescription>
          Both audit chains merged · newest-first · chain size:{" "}
          <span className="font-mono">{formatBytes(q.data?.chain_size_b)}</span>.
          GDPR Art. 30/32 — metadata only, no prompts or outputs.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {/* filter tabs */}
        <div className="flex flex-wrap gap-1">
          {FILTER_TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                "rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
                activeTab === tab.key
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
              )}
            >
              {tab.label}
              {q.data && tab.key === "" && (
                <span className="ml-1 text-muted-foreground/60">{q.data.count}</span>
              )}
            </button>
          ))}
        </div>

        {q.isLoading && (
          <div className="space-y-1.5">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        )}

        {q.data && events.length > 0 && (
          <>
            {/* distribution bar */}
            <ChainDistributionBar events={events} />

            {/* chain event list */}
            <div className="rounded-md border border-border dark:border-slate-800 bg-card dark:bg-slate-950 py-2 overflow-hidden">
              {events.map((ev, i) => (
                <ChainEventRow
                  key={`${ev.ts}-${i}`}
                  ev={ev}
                  last={i === events.length - 1}
                />
              ))}
            </div>
          </>
        )}

        {q.data && events.length === 0 && (
          <div className="rounded-md border border-dashed border-border dark:border-slate-800 bg-muted/20 dark:bg-slate-950/40 px-4 py-10 text-center">
            <Shield className="mx-auto mb-2 h-6 w-6 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">No events match this filter.</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      {value}
    </div>
  );
}

// ── Custom Audit Layers ────────────────────────────────────────────────────

function CustomAuditLayersSection() {
  const queryClient = useQueryClient();
  const { session } = useAuth();

  const [showRegisterDialog, setShowRegisterDialog] = React.useState(false);
  const [layerId, setLayerId]         = React.useState("");
  const [layerName, setLayerName]     = React.useState("");
  const [eventTypes, setEventTypes]   = React.useState("");
  const [allowedFields, setAllowedFields] = React.useState("");
  const [registerError, setRegisterError] = React.useState<string | null>(null);

  // Per-layer "Test Emit" state
  const [emitLayerId, setEmitLayerId]         = React.useState<string | null>(null);
  const [emitEventType, setEmitEventType]     = React.useState("");
  const [emitDetailsJson, setEmitDetailsJson] = React.useState("{}");
  const [emitError, setEmitError]             = React.useState<string | null>(null);
  const [emitSuccess, setEmitSuccess]         = React.useState(false);

  const q = useQuery({
    queryKey: ["audit-layers"],
    queryFn: ({ signal }) => listAuditLayers(signal),
  });

  const registerMutation = useMutation({
    mutationFn: (vars: { layer_id: string; display_name: string; event_types: string[]; allowed_fields: string[] }) =>
      registerAuditLayer(vars.layer_id, { display_name: vars.display_name, event_types: vars.event_types, allowed_fields: vars.allowed_fields }, session!.csrf_token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["audit-layers"] });
      setShowRegisterDialog(false);
      setLayerId("");
      setLayerName("");
      setEventTypes("");
      setAllowedFields("");
      setRegisterError(null);
    },
    onError: (err: Error) => {
      setRegisterError(err.message ?? "Registration failed");
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => removeAuditLayer(id, session!.csrf_token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["audit-layers"] });
    },
  });

  const emitMutation = useMutation({
    mutationFn: (vars: { layer_id: string; event_type: string; details: Record<string, unknown> }) =>
      emitCustomAuditEvent(vars.layer_id, vars.event_type, vars.details, session!.csrf_token),
    onSuccess: () => {
      setEmitSuccess(true);
      setEmitError(null);
      setTimeout(() => setEmitSuccess(false), 2000);
    },
    onError: (err: Error) => {
      setEmitError(err.message ?? "Emit failed");
    },
  });

  function handleRegisterSubmit(e: React.FormEvent) {
    e.preventDefault();
    setRegisterError(null);
    const parsedEventTypes   = eventTypes.split("\n").map(s => s.trim()).filter(Boolean);
    const parsedAllowedFields = allowedFields.split("\n").map(s => s.trim()).filter(Boolean);
    if (!layerId.trim()) { setRegisterError("Layer ID is required"); return; }
    if (!/^[a-z][a-z0-9_-]*$/.test(layerId.trim())) {
      setRegisterError("Layer ID must be lowercase letters, digits, _ or - (start with a letter)");
      return;
    }
    if (!layerName.trim()) { setRegisterError("Display name is required"); return; }
    registerMutation.mutate({
      layer_id: layerId.trim(),
      display_name: layerName.trim(),
      event_types: parsedEventTypes,
      allowed_fields: parsedAllowedFields,
    });
  }

  function openEmitDialog(layer: AuditLayer) {
    setEmitLayerId(layer.layer_id);
    setEmitEventType(layer.event_types[0] ?? "");
    setEmitDetailsJson("{}");
    setEmitError(null);
    setEmitSuccess(false);
  }

  function closeEmitDialog() {
    setEmitLayerId(null);
    setEmitError(null);
    setEmitSuccess(false);
  }

  function handleEmitSubmit(e: React.FormEvent) {
    e.preventDefault();
    setEmitError(null);
    let parsedDetails: Record<string, unknown>;
    try {
      parsedDetails = JSON.parse(emitDetailsJson);
    } catch {
      setEmitError("Details must be valid JSON");
      return;
    }
    emitMutation.mutate({
      layer_id: emitLayerId!,
      event_type: emitEventType,
      details: parsedDetails,
    });
  }

  const layers = q.data?.layers ?? [];
  const emitLayer = layers.find(l => l.layer_id === emitLayerId) ?? null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Custom Audit Layers</CardTitle>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 text-xs"
            data-testid="register-layer-btn"
            onClick={() => {
              setShowRegisterDialog(true);
              setRegisterError(null);
            }}
          >
            <Plus className="h-3 w-3" />
            Register Layer
          </Button>
        </div>
        <CardDescription>
          Register application-specific audit layers to extend the hash chain with your own event
          families and allowed metadata fields.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {q.isLoading && <Skeleton className="h-16 w-full" />}
        {q.isError && (
          <p className="text-xs text-muted-foreground">
            Could not load audit layers — {(q.error as Error | undefined)?.message ?? "unknown"}
          </p>
        )}

        {/* Layer list */}
        {!q.isLoading && layers.length === 0 && (
          <div className="rounded-md border border-dashed border-border bg-muted/20 px-4 py-8 text-center">
            <Layers className="mx-auto mb-2 h-5 w-5 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">No custom audit layers registered yet.</p>
          </div>
        )}

        {layers.length > 0 && (
          <div className="space-y-2">
            {layers.map((layer) => (
              <div
                key={layer.layer_id}
                className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2.5 text-sm"
              >
                <div className="flex min-w-0 flex-col gap-0.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-foreground">
                      {layer.layer_id}
                    </span>
                    <span className="text-xs text-muted-foreground">{layer.display_name}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="text-[10px]">
                      {layer.event_types.length} event type{layer.event_types.length === 1 ? "" : "s"}
                    </Badge>
                    <Badge variant="outline" className="text-[10px]">
                      {layer.allowed_fields.length} allowed field{layer.allowed_fields.length === 1 ? "" : "s"}
                    </Badge>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 gap-1 text-xs"
                    data-testid={`test-emit-btn-${layer.layer_id}`}
                    onClick={() => openEmitDialog(layer)}
                  >
                    <Zap className="h-3 w-3" />
                    Test Emit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 gap-1 text-xs text-destructive hover:text-destructive"
                    data-testid={`remove-layer-btn-${layer.layer_id}`}
                    onClick={() => removeMutation.mutate(layer.layer_id)}
                    disabled={removeMutation.isPending}
                  >
                    <Trash2 className="h-3 w-3" />
                    Remove
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Register Layer dialog */}
        {showRegisterDialog && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
            <div className="w-full max-w-md rounded-lg border border-border bg-background p-6 shadow-xl">
              <h2 className="mb-4 text-base font-semibold">Register Audit Layer</h2>
              <form onSubmit={handleRegisterSubmit} className="space-y-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="layer-id">
                    Layer ID
                  </label>
                  <input
                    id="layer-id"
                    data-testid="layer-id-input"
                    type="text"
                    placeholder="e.g. my_app"
                    value={layerId}
                    onChange={e => setLayerId(e.target.value)}
                    pattern="[a-z][a-z0-9_-]*"
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    autoComplete="off"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Lowercase letters, digits, _ or - only. Must start with a letter.
                  </p>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="layer-name">
                    Display Name
                  </label>
                  <input
                    id="layer-name"
                    data-testid="layer-name-input"
                    type="text"
                    placeholder="My Application"
                    value={layerName}
                    onChange={e => setLayerName(e.target.value)}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    autoComplete="off"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="layer-event-types">
                    Event Types
                  </label>
                  <textarea
                    id="layer-event-types"
                    data-testid="layer-event-types-input"
                    placeholder={"my-app.user_action\nmy-app.data_processed"}
                    value={eventTypes}
                    onChange={e => setEventTypes(e.target.value)}
                    rows={4}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-[10px] text-muted-foreground">One event type per line.</p>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="layer-fields">
                    Allowed Fields
                  </label>
                  <textarea
                    id="layer-fields"
                    data-testid="layer-fields-input"
                    placeholder={"action\ntarget_kind\ntarget_id"}
                    value={allowedFields}
                    onChange={e => setAllowedFields(e.target.value)}
                    rows={4}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    One field name per line. Only these fields will be stored in the audit chain
                    (metadata-only invariant).
                  </p>
                </div>
                {registerError && (
                  <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {registerError}
                  </p>
                )}
                <div className="flex justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setShowRegisterDialog(false);
                      setRegisterError(null);
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    size="sm"
                    data-testid="register-layer-submit"
                    disabled={registerMutation.isPending}
                  >
                    {registerMutation.isPending ? "Registering…" : "Register"}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Test Emit dialog */}
        {emitLayerId && emitLayer && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
            <div className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-xl">
              <h2 className="mb-1 text-base font-semibold">Test Emit</h2>
              <p className="mb-4 text-xs text-muted-foreground">
                Emit a test event into the audit chain for layer{" "}
                <span className="font-mono font-semibold">{emitLayerId}</span>.
              </p>
              <form onSubmit={handleEmitSubmit} className="space-y-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="emit-event-type">
                    Event Type
                  </label>
                  {emitLayer.event_types.length > 0 ? (
                    <select
                      id="emit-event-type"
                      data-testid={`emit-event-type-select-${emitLayerId}`}
                      value={emitEventType}
                      onChange={e => setEmitEventType(e.target.value)}
                      className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    >
                      {emitLayer.event_types.map((et: string) => (
                        <option key={et} value={et}>{et}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id="emit-event-type"
                      data-testid={`emit-event-type-select-${emitLayerId}`}
                      type="text"
                      placeholder="my-app.event_name"
                      value={emitEventType}
                      onChange={e => setEmitEventType(e.target.value)}
                      className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                  )}
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="emit-details">
                    Details JSON
                  </label>
                  <textarea
                    id="emit-details"
                    data-testid={`emit-details-input-${emitLayerId}`}
                    value={emitDetailsJson}
                    onChange={e => setEmitDetailsJson(e.target.value)}
                    rows={5}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Only fields declared in <span className="font-mono">allowed_fields</span> will
                    be stored.
                  </p>
                </div>
                {emitError && (
                  <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {emitError}
                  </p>
                )}
                {emitSuccess && (
                  <p className="rounded-md bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400">
                    Event emitted successfully.
                  </p>
                )}
                <div className="flex justify-end gap-2 pt-1">
                  <Button type="button" variant="ghost" size="sm" onClick={closeEmitDialog}>
                    Close
                  </Button>
                  <Button
                    type="submit"
                    size="sm"
                    data-testid={`emit-submit-${emitLayerId}`}
                    disabled={emitMutation.isPending}
                  >
                    {emitMutation.isPending ? "Emitting…" : "Emit"}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Webhook Channels ───────────────────────────────────────────────────────

function WebhookChannelsSection() {
  const queryClient = useQueryClient();
  const { session } = useAuth();

  const [showAddDialog, setShowAddDialog] = React.useState(false);
  const [channelId, setChannelId]         = React.useState("");
  const [displayName, setDisplayName]     = React.useState("");
  const [hmacEnvVar, setHmacEnvVar]       = React.useState("");
  const [persona, setPersona]             = React.useState("assistant");
  const [addError, setAddError]           = React.useState<string | null>(null);

  const [copiedId, setCopiedId] = React.useState<string | null>(null);

  const q = useQuery({
    queryKey: ["webhook-channels"],
    queryFn: ({ signal }) => listWebhookChannels(signal),
  });

  const addMutation = useMutation({
    mutationFn: (vars: { channel_id: string; display_name: string; hmac_secret_env?: string; persona?: string }) =>
      registerWebhookChannel(vars.channel_id, { display_name: vars.display_name, hmac_secret_env: vars.hmac_secret_env, persona: vars.persona }, session!.csrf_token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webhook-channels"] });
      setShowAddDialog(false);
      setChannelId("");
      setDisplayName("");
      setHmacEnvVar("");
      setPersona("assistant");
      setAddError(null);
    },
    onError: (err: Error) => {
      setAddError(err.message ?? "Registration failed");
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => removeWebhookChannel(id, session!.csrf_token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webhook-channels"] });
    },
  });

  function handleAddSubmit(e: React.FormEvent) {
    e.preventDefault();
    setAddError(null);
    if (!channelId.trim()) { setAddError("Channel ID is required"); return; }
    if (!displayName.trim()) { setAddError("Display name is required"); return; }
    addMutation.mutate({
      channel_id: channelId.trim(),
      display_name: displayName.trim(),
      hmac_secret_env: hmacEnvVar.trim() || undefined,
      persona: persona.trim() || "assistant",
    });
  }

  function handleCopyUrl(channel: WebhookChannel) {
    navigator.clipboard.writeText(channel.inbound_url).then(() => {
      setCopiedId(channel.channel_id);
      setTimeout(() => setCopiedId(null), 1500);
    });
  }

  const channels = q.data?.channels ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Webhook className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Webhook Channels</CardTitle>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 text-xs"
            data-testid="add-webhook-channel-btn"
            onClick={() => {
              setShowAddDialog(true);
              setAddError(null);
            }}
          >
            <Plus className="h-3 w-3" />
            Add Channel
          </Button>
        </div>
        <CardDescription>
          Inbound webhook channels route external HTTP POST requests into the CorvinOS voice
          adapter as chat messages. Each channel gets a unique signed URL.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {q.isLoading && <Skeleton className="h-16 w-full" />}
        {q.isError && (
          <p className="text-xs text-muted-foreground">
            Could not load webhook channels — {(q.error as Error | undefined)?.message ?? "unknown"}
          </p>
        )}

        {/* Channel list */}
        {!q.isLoading && channels.length === 0 && (
          <div className="rounded-md border border-dashed border-border bg-muted/20 px-4 py-8 text-center">
            <Webhook className="mx-auto mb-2 h-5 w-5 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">No webhook channels configured yet.</p>
          </div>
        )}

        {channels.length > 0 && (
          <div className="space-y-2">
            {channels.map((channel) => (
              <div
                key={channel.channel_id}
                className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/30 px-3 py-2.5 text-sm"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-foreground">
                      {channel.channel_id}
                    </span>
                    <span className="text-xs text-muted-foreground">{channel.display_name}</span>
                    {channel.hmac_secret_env && (
                      <Badge variant="secondary" className="text-[10px]">HMAC</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-mono text-[10px] text-muted-foreground max-w-xs">
                      {channel.inbound_url}
                    </span>
                    <button
                      type="button"
                      data-testid={`copy-url-btn-${channel.channel_id}`}
                      onClick={() => handleCopyUrl(channel)}
                      className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                      title="Copy inbound URL"
                    >
                      <Copy className="h-3 w-3" />
                    </button>
                    {copiedId === channel.channel_id && (
                      <span className="text-[10px] text-emerald-600 dark:text-emerald-400">
                        Copied!
                      </span>
                    )}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 shrink-0 gap-1 text-xs text-destructive hover:text-destructive"
                  data-testid={`remove-webhook-btn-${channel.channel_id}`}
                  onClick={() => removeMutation.mutate(channel.channel_id)}
                  disabled={removeMutation.isPending}
                >
                  <Trash2 className="h-3 w-3" />
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* Add Channel dialog */}
        {showAddDialog && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
            <div className="w-full max-w-md rounded-lg border border-border bg-background p-6 shadow-xl">
              <h2 className="mb-4 text-base font-semibold">Add Webhook Channel</h2>
              <form onSubmit={handleAddSubmit} className="space-y-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="webhook-channel-id">
                    Channel ID
                  </label>
                  <input
                    id="webhook-channel-id"
                    data-testid="webhook-channel-id-input"
                    type="text"
                    placeholder="e.g. github-events"
                    value={channelId}
                    onChange={e => setChannelId(e.target.value)}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    autoComplete="off"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="webhook-display-name">
                    Display Name
                  </label>
                  <input
                    id="webhook-display-name"
                    data-testid="webhook-display-name-input"
                    type="text"
                    placeholder="GitHub Events"
                    value={displayName}
                    onChange={e => setDisplayName(e.target.value)}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    autoComplete="off"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="webhook-hmac">
                    HMAC Secret Env Var{" "}
                    <span className="font-normal text-muted-foreground">(optional)</span>
                  </label>
                  <input
                    id="webhook-hmac"
                    data-testid="webhook-hmac-input"
                    type="text"
                    placeholder="e.g. MY_WEBHOOK_SECRET"
                    value={hmacEnvVar}
                    onChange={e => setHmacEnvVar(e.target.value)}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    autoComplete="off"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Name of the environment variable holding the HMAC secret. The value is never
                    stored here.
                  </p>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-foreground" htmlFor="webhook-persona">
                    Persona
                  </label>
                  <input
                    id="webhook-persona"
                    data-testid="webhook-persona-input"
                    type="text"
                    placeholder="assistant"
                    value={persona}
                    onChange={e => setPersona(e.target.value)}
                    className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    autoComplete="off"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Persona used when handling inbound webhook messages. Defaults to{" "}
                    <span className="font-mono">assistant</span>.
                  </p>
                </div>
                {addError && (
                  <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {addError}
                  </p>
                )}
                <div className="flex justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setShowAddDialog(false);
                      setAddError(null);
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    size="sm"
                    data-testid="add-webhook-channel-submit"
                    disabled={addMutation.isPending}
                  >
                    {addMutation.isPending ? "Adding…" : "Add Channel"}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
