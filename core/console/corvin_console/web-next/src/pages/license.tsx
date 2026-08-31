/**
 * License page — ADR-0092 M3 UI.
 *
 * Shows:
 * - Active tier + subscription status
 * - All limits with free-tier comparison
 * - Per-customer feature flags (features dict)
 * - Custom metadata (custom dict)
 * - Key file instructions + tier comparison table
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  ExternalLink,
  Key,
  Lock,
  RefreshCw,
  Shield,
  Sparkles,
  XCircle,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { applyLicenseKey, getLicenseInfo } from "@/lib/api";
import { LicenseBadge } from "@/components/license-gate";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric",
  }).format(new Date(ts * 1000));
}

function fmtLimit(val: unknown): string {
  if (val === null || val === undefined) return "Unlimited";
  if (Array.isArray(val)) return val.length === 0 ? "None" : val.join(", ");
  if (typeof val === "boolean") return val ? "Enabled" : "Disabled";
  return String(val);
}

const LIMIT_LABELS: Record<string, string> = {
  compute_units_per_day: "Compute units / day",
  a2a_peers_max:         "A2A peer connections",
  workflows_concurrent:  "Concurrent workflows",
  tenants_max:           "Tenant slots",
  rag_providers_max:     "RAG providers",
  bridges_allowed:       "Allowed bridges",
  engines_allowed:       "Allowed engines",
  data_residency:        "Data residency zone",
  // Reserved tier differentiators with no enforcement chokepoint yet
  // (cloud-phase features — see operator/license/limits.py). Tagged
  // "(roadmap)" so the console does not advertise a paid feature that
  // ships no enforced behaviour.
  audit_export:          "Audit export (roadmap)",
  sso_enabled:           "Single Sign-On (SSO) (roadmap)",
  enterprise_portal:     "Enterprise portal (roadmap)",
};

// ── Section collapse ──────────────────────────────────────────────────────────

function Section({
  title, icon, children, defaultOpen = true,
}: {
  title: string; icon?: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center gap-2 px-4 py-3 bg-muted/40 text-sm font-medium hover:bg-muted/60 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        {icon}
        {title}
        <span className="ml-auto">
          {open
            ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
            : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </span>
      </button>
      {open && <div className="p-4">{children}</div>}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function LicensePage() {
  const qc = useQueryClient();
  const { session } = useAuth();
  const [keyInput, setKeyInput] = React.useState("");
  const [applySuccess, setApplySuccess] = React.useState<string | null>(null);

  const { data: lic, isLoading, error } = useQuery({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
    retry: 1,
  });

  const applyMutation = useMutation({
    mutationFn: (key: string) =>
      applyLicenseKey(key, session!.csrf_token),
    onSuccess: (res) => {
      setKeyInput("");
      setApplySuccess(`Key applied — tier: ${res.tier}${res.issued_to ? ` (${res.issued_to})` : ""}`);
      qc.invalidateQueries({ queryKey: ["license", "info"] });
    },
  });

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (error || !lic) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <Card>
          <CardContent className="pt-6 text-center text-muted-foreground">
            <AlertCircle className="mx-auto mb-2 h-8 w-8 text-red-400" />
            <p>Could not load license information.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const daysUntilExpiry = lic.subscription_active_until
    ? Math.ceil((lic.subscription_active_until - Date.now() / 1000) / 86400)
    : null;
  const expiryWarning = daysUntilExpiry !== null && daysUntilExpiry <= 30 && daysUntilExpiry > 0;

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Key className="h-6 w-6 text-accent" />
            License
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Current tier, feature limits, and per-customer configuration.
          </p>
        </div>
        <Button variant="ghost" size="sm"
          onClick={() => qc.invalidateQueries({ queryKey: ["license", "info"] })}>
          <RefreshCw className="h-4 w-4 mr-1" />
          Refresh
        </Button>
      </div>

      {/* Status card */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              <span className={cn("h-2 w-2 rounded-full shrink-0",
                lic.loaded ? "bg-emerald-500 animate-pulse" : "bg-zinc-500")} />
              <LicenseBadge tier={lic.tier} />
              {lic.issued_to && (
                <span className="text-sm text-muted-foreground">{lic.issued_to}</span>
              )}
            </div>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              {lic.jti_prefix && (
                <span className="font-mono text-xs">jti: {lic.jti_prefix}…</span>
              )}
              {lic.expires_at && (
                <span className="flex items-center gap-1">
                  <Clock className="h-3.5 w-3.5" />
                  {fmtDate(lic.expires_at)}
                  {daysUntilExpiry !== null && daysUntilExpiry > 0 && (
                    <span className={expiryWarning ? "text-amber-400" : ""}>
                      ({daysUntilExpiry}d)
                    </span>
                  )}
                </span>
              )}
            </div>
          </div>

          {expiryWarning && (
            <div className="mt-4 flex items-center gap-2 rounded-md bg-amber-500/10 border border-amber-400/30 px-3 py-2 text-sm text-amber-300">
              <AlertCircle className="h-4 w-4 shrink-0" />
              Licence expires in {daysUntilExpiry} day{daysUntilExpiry !== 1 ? "s" : ""}.{" "}
              <a href="https://corvin-labs.com/pricing" target="_blank" rel="noopener noreferrer"
                className="underline underline-offset-2">Renew now</a>
            </div>
          )}

          {!lic.loaded && (
            <div className="mt-4 flex items-center gap-2 rounded-md bg-zinc-800/60 border border-zinc-600/40 px-3 py-2 text-sm text-muted-foreground">
              <Lock className="h-4 w-4 shrink-0" />
              No licence key — Free tier defaults apply.{" "}
              <a href="https://corvin-labs.com/pricing" target="_blank" rel="noopener noreferrer"
                className="underline underline-offset-2 text-accent">Get a key</a>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Limits */}
      <Section title="Feature Limits" icon={<Zap className="h-4 w-4 text-accent" />}>
        <div className="space-y-2">
          {Object.entries(LIMIT_LABELS).map(([key, label]) => {
            const val = lic.limits[key];
            const freeVal = lic.free_tier[key];
            const isUnlimited = val === null;
            const isAtFree = JSON.stringify(val) === JSON.stringify(freeVal);
            const locked = !lic.loaded || isAtFree;
            return (
              <div key={key}
                className="flex items-center justify-between py-2 border-b border-border/40 last:border-0 text-sm">
                <div className="flex items-center gap-2">
                  {isUnlimited
                    ? <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
                    : locked
                      ? <Lock className="h-4 w-4 text-zinc-500 shrink-0" />
                      : <CheckCircle2 className="h-4 w-4 text-accent shrink-0" />}
                  <span className={locked && !isUnlimited ? "text-muted-foreground" : undefined}>
                    {label}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-right">
                  <span className={cn("font-mono text-xs",
                    isUnlimited ? "text-emerald-400" : locked ? "text-muted-foreground" : "text-foreground")}>
                    {fmtLimit(val)}
                  </span>
                  {!lic.loaded && freeVal !== null && freeVal !== val && (
                    <span className="text-[10px] text-zinc-600">
                      (free: {fmtLimit(freeVal)})
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Feature flags */}
      {Object.keys(lic.features).length > 0 && (
        <Section title="Feature Flags" icon={<Sparkles className="h-4 w-4 text-accent" />}>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(lic.features).map(([key, enabled]) => (
              <div key={key} className="flex items-center gap-2 text-sm">
                {enabled
                  ? <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
                  : <XCircle className="h-4 w-4 text-zinc-500 shrink-0" />}
                <span className={!enabled ? "text-muted-foreground" : undefined}>
                  {key.replace(/_/g, " ")}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Custom metadata */}
      {Object.keys(lic.custom).length > 0 && (
        <Section title="Custom Configuration" icon={<Shield className="h-4 w-4 text-accent" />} defaultOpen={false}>
          <div className="space-y-1 font-mono text-xs">
            {Object.entries(lic.custom).map(([key, val]) => (
              <div key={key} className="flex items-start gap-4 py-1 border-b border-border/30 last:border-0">
                <span className="text-muted-foreground w-40 shrink-0">{key}</span>
                <span className="text-foreground break-all">{JSON.stringify(val)}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Key input */}
      <Section title="Apply License Key" icon={<Key className="h-4 w-4 text-accent" />} defaultOpen={!lic.loaded}>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Paste your <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">CORVIN-…</code> license key below.
            The key is saved to <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">~/.corvin/global/license.key</code> and activated immediately.
          </p>
          <Textarea
            placeholder="CORVIN-eyJ…"
            value={keyInput}
            onChange={(e) => { setKeyInput(e.target.value); setApplySuccess(null); applyMutation.reset(); }}
            className="font-mono text-xs min-h-[80px] resize-y"
          />
          {applyMutation.error && (
            <div className="flex items-center gap-2 text-sm text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {(applyMutation.error as { message?: string })?.message ?? "Failed to apply key"}
            </div>
          )}
          {applySuccess && (
            <div className="flex items-center gap-2 text-sm text-emerald-400">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              {applySuccess}
            </div>
          )}
          <Button
            size="sm"
            disabled={!keyInput.trim() || applyMutation.isPending}
            onClick={() => applyMutation.mutate(keyInput.trim())}
          >
            {applyMutation.isPending ? "Applying…" : "Apply Key"}
          </Button>
        </div>
      </Section>

      {/* Key instructions */}
      <Section title="Key Setup" icon={<Key className="h-4 w-4 text-muted-foreground" />} defaultOpen={false}>
        <div className="space-y-3 text-sm text-muted-foreground">
          <p>
            Session Token (SesT) at{" "}
            <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">
              ~/.config/corvin-voice/session.key
            </code>{" "}
            (mode 0600). Picked up on next bridge restart.
          </p>
          <p>
            Subscription Token (ST) at{" "}
            <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">
              ~/.config/corvin-voice/subscription.key
            </code>.
            The refresh daemon exchanges it for a new SesT every 48h.
          </p>
          <p>
            Or set{" "}
            <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">CORVIN_LICENSE_KEY</code>{" "}
            environment variable.
          </p>
          <a href="https://corvin-labs.com/pricing" target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-accent hover:underline">
            Get your key at corvin-labs.com <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </Section>

      {/* Tier comparison */}
      <Section title="Tier Comparison" icon={<Shield className="h-4 w-4 text-muted-foreground" />} defaultOpen={false}>
        {(() => {
          const isFree = lic.tier === "free";
          const colFree = isFree
            ? "font-bold text-foreground"
            : "text-muted-foreground";
          const colUniv = !isFree
            ? "font-bold text-accent"
            : "text-muted-foreground";
          const rows: [string, string, string][] = [
            ["Agentic Compute / day", "1",  "∞"],
            ["A2A peer connections", "1",   "∞"],
            ["Concurrent workflows", "1",   "∞"],
            ["Tenant slots",         "1",   "∞"],
            ["RAG providers",        "1",   "∞"],
            ["Bridges",              "All", "All"],
            ["Engines",              "All", "All"],
            ["Audit export (roadmap)", "—", "✓"],
            ["Data residency zone",  "—",   "✓"],
            ["SSO (roadmap)",        "—",   "✓"],
            ["WORM archive",         "—",   "✓"],
          ];
          return (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-2 pr-4 text-muted-foreground font-medium">Feature</th>
                    <th className={cn("text-center py-2 px-4 font-semibold", isFree ? "text-foreground" : "text-muted-foreground")}>
                      Community
                      {isFree && <span className="ml-1 text-[10px] bg-muted rounded px-1 py-0.5 font-normal">active</span>}
                    </th>
                    <th className={cn("text-center py-2 px-4 font-semibold", !isFree ? "text-accent" : "text-muted-foreground")}>
                      Member
                      {!isFree && <span className="ml-1 text-[10px] bg-accent/20 text-accent rounded px-1 py-0.5 font-normal">active</span>}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {rows.map(([feat, fr, un]) => (
                    <tr key={feat}>
                      <td className="py-1.5 pr-4 text-muted-foreground">{feat}</td>
                      <td className={cn("py-1.5 px-4 text-center", colFree)}>{fr}</td>
                      <td className={cn("py-1.5 px-4 text-center", colUniv)}>{un}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3 text-[11px] text-muted-foreground">
                Member = everything unlocked · €10 / device / month · cancel anytime
              </div>
            </div>
          );
        })()}
        <div className="mt-4 text-center">
          <a href="https://corvin-labs.com/pricing" target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-accent text-sm hover:underline">
            Get Member at corvin-labs.com <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
      </Section>
    </div>
  );
}
