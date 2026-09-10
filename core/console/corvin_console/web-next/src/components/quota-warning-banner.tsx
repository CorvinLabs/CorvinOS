/**
 * Storage Quota Warning Banner
 * Shows when IndexedDB usage > 80%
 */

import React from "react";
import { AlertCircle, XCircle } from "lucide-react";
import { useIDBQuota, formatBytes } from "@/hooks/use-idb-quota";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function QuotaWarningBanner() {
  const { quota } = useIDBQuota();
  const [dismissed, setDismissed] = React.useState(false);

  if (!quota || !quota.isWarning || dismissed) {
    return null;
  }

  const Icon = quota.isCritical ? XCircle : AlertCircle;
  const borderColor = quota.isCritical ? "border-destructive/40" : "border-amber-500/30";
  const bgColor = quota.isCritical ? "bg-destructive/10" : "bg-amber-500/10";
  const textColor = quota.isCritical ? "text-destructive" : "text-amber-700 dark:text-amber-400";
  const progressColor = quota.isCritical ? "bg-destructive" : "bg-amber-500";

  return (
    <div
      className={cn("mx-4 mt-4 rounded-lg border p-3", borderColor, bgColor)}
    >
      <div className="flex items-start gap-3">
        <Icon className={cn("h-5 w-5 shrink-0 mt-0.5", textColor)} />
        <div className="flex-1 min-w-0">
          <h3 className={cn("font-semibold text-sm", textColor)}>
            {quota.isCritical
              ? "IndexedDB Storage Critical"
              : "IndexedDB Storage Warning"}
          </h3>
          <p className={cn("text-xs mt-1", textColor)}>
            Using {quota.percentUsed}% of available storage (
            {formatBytes(quota.usage)} / {formatBytes(quota.quota)}). Tasks
            older than 30 days are automatically deleted.
          </p>

          {/* Progress bar */}
          <div className="mt-2 h-2 w-full bg-muted rounded-full overflow-hidden">
            <div
              className={progressColor}
              style={{ width: `${Math.min(quota.percentUsed, 100)}%` }}
            />
          </div>

          {quota.isCritical && (
            <p className={cn("text-xs mt-2 font-medium", textColor)}>
              ⚠️ Please review and delete old tasks to free up space.
            </p>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setDismissed(true)}
          className="shrink-0"
          title="Dismiss"
        >
          ✕
        </Button>
      </div>
    </div>
  );
}
