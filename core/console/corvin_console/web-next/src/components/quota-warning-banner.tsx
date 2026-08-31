/**
 * Storage Quota Warning Banner
 * Shows when IndexedDB usage > 80%
 */

import React from "react";
import { AlertCircle, XCircle } from "lucide-react";
import { useIDBQuota, formatBytes } from "@/hooks/use-idb-quota";
import { Button } from "@/components/ui/button";

export function QuotaWarningBanner() {
  const { quota } = useIDBQuota();
  const [dismissed, setDismissed] = React.useState(false);

  if (!quota || !quota.isWarning || dismissed) {
    return null;
  }

  const Icon = quota.isCritical ? XCircle : AlertCircle;
  const bgColor = quota.isCritical ? "bg-red-50" : "bg-yellow-50";
  const borderColor = quota.isCritical ? "border-red-200" : "border-yellow-200";
  const textColor = quota.isCritical ? "text-red-900" : "text-yellow-900";
  const progressColor = quota.isCritical ? "bg-red-300" : "bg-yellow-300";

  return (
    <div
      className={`mx-4 mt-4 rounded-lg border ${borderColor} ${bgColor} p-3`}
    >
      <div className="flex items-start gap-3">
        <Icon className={`h-5 w-5 shrink-0 ${textColor} mt-0.5`} />
        <div className="flex-1 min-w-0">
          <h3 className={`font-semibold text-sm ${textColor}`}>
            {quota.isCritical
              ? "IndexedDB Storage Critical"
              : "IndexedDB Storage Warning"}
          </h3>
          <p className={`text-xs ${textColor} mt-1`}>
            Using {quota.percentUsed}% of available storage (
            {formatBytes(quota.usage)} / {formatBytes(quota.quota)}). Tasks
            older than 30 days are automatically deleted.
          </p>

          {/* Progress bar */}
          <div className="mt-2 h-2 w-full bg-gray-200 rounded-full overflow-hidden">
            <div
              className={progressColor}
              style={{ width: `${Math.min(quota.percentUsed, 100)}%` }}
            />
          </div>

          {quota.isCritical && (
            <p className={`text-xs ${textColor} mt-2 font-medium`}>
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
