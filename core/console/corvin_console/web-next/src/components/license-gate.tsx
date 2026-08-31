/**
 * LicenseBadge + LicenseGate — ADR-0092 UI components.
 *
 * LicenseBadge  — compact tier indicator (pill), used in nav + page headers.
 * LimitBadge    — inline badge showing a feature limit (e.g. "5 / 10 peers").
 * LicenseGate   — wraps any UI element and dims it + shows upgrade tooltip
 *                 when the feature is locked by the current licence tier.
 * FeatureFlag   — renders children only when a boolean feature is enabled.
 */

import * as React from "react";
import { Lock, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LicenseInfo } from "@/lib/api";

// ── Tier colours ──────────────────────────────────────────────────────────────

const TIER_STYLES: Record<string, string> = {
  free:         "bg-zinc-700/60 text-zinc-300",
  starter:      "bg-blue-900/60 text-blue-200",
  professional: "bg-indigo-800/60 text-indigo-200",
  enterprise:   "bg-amber-800/60 text-amber-200",
};

const TIER_LABEL: Record<string, string> = {
  free:         "Free",
  starter:      "Starter",
  professional: "Professional",
  enterprise:   "Enterprise",
};

// ── LicenseBadge ──────────────────────────────────────────────────────────────

export function LicenseBadge({ tier }: { tier: string }) {
  const style = TIER_STYLES[tier] ?? "bg-zinc-700/60 text-zinc-300";
  const label = TIER_LABEL[tier] ?? tier;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        style,
      )}
    >
      {label}
    </span>
  );
}

// ── LimitBadge ────────────────────────────────────────────────────────────────

interface LimitBadgeProps {
  label: string;
  used?: number;
  limit: number | null;
  unit?: string;
  className?: string;
}

export function LimitBadge({ label, used, limit, unit = "", className }: LimitBadgeProps) {
  const unlimited = limit === null || limit === undefined;
  const pct = unlimited || used === undefined ? 0 : Math.min((used / (limit as number)) * 100, 100);
  const over = !unlimited && used !== undefined && used >= (limit as number);

  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className={cn("font-mono", over && "text-red-400")}>
          {used !== undefined ? `${used} / ` : ""}
          {unlimited ? "∞" : `${limit}${unit}`}
        </span>
      </div>
      {!unlimited && used !== undefined && (
        <div className="h-1 rounded-full bg-muted overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all",
              over ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-accent",
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ── LicenseGate ───────────────────────────────────────────────────────────────

interface LicenseGateProps {
  /** True when the feature IS available on the current licence. */
  allowed: boolean;
  /** Short description shown in the tooltip. */
  reason?: string;
  children: React.ReactNode;
  className?: string;
  "data-testid"?: string;
}

export function LicenseGate({ allowed, reason, children, className, "data-testid": testId }: LicenseGateProps) {
  if (allowed) return <>{children}</>;

  return (
    <div className={cn("relative", className)} data-testid={testId}>
      {/* Dim the locked content */}
      <div className="pointer-events-none select-none opacity-35">{children}</div>

      {/* Overlay lock indicator */}
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="flex items-center gap-1.5 rounded-md bg-background/90 px-2 py-1 text-[11px] font-medium text-muted-foreground shadow border border-border/60">
          <Lock className="h-3 w-3 shrink-0" />
          <span>{reason ?? "Upgrade required"}</span>
          <a
            href="https://corvin-labs.com/pricing"
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent hover:underline"
          >
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>
    </div>
  );
}

// ── FeatureFlag ───────────────────────────────────────────────────────────────

interface FeatureFlagProps {
  licenseInfo: LicenseInfo | undefined;
  feature: string;
  fallback?: React.ReactNode;
  children: React.ReactNode;
}

/** Renders `children` only when the feature flag is true in the active licence. */
export function FeatureFlag({ licenseInfo, feature, fallback, children }: FeatureFlagProps) {
  const enabled = licenseInfo?.features?.[feature] === true;
  if (enabled) return <>{children}</>;
  return fallback ? <>{fallback}</> : null;
}

// ── Helper: is engine allowed? ─────────────────────────────────────────────

export function isEngineAllowed(licenseInfo: LicenseInfo | undefined, engineId: string): boolean {
  const allowed = licenseInfo?.limits?.engines_allowed;
  if (allowed === null || allowed === undefined) return true; // null = unlimited
  if (Array.isArray(allowed)) return allowed.includes(engineId);
  return true;
}

export function isBridgeAllowed(licenseInfo: LicenseInfo | undefined, channel: string): boolean {
  const allowed = licenseInfo?.limits?.bridges_allowed;
  if (allowed === null || allowed === undefined) return true;
  if (Array.isArray(allowed)) return allowed.includes(channel);
  return true;
}

export function isAdapterAllowed(licenseInfo: LicenseInfo | undefined, adapterId: string): boolean {
  const allowed = licenseInfo?.limits?.datasource_adapters_allowed;
  if (allowed === null || allowed === undefined) return true; // null = unlimited (paid tier)
  if (Array.isArray(allowed)) return allowed.includes(adapterId);
  return true;
}
