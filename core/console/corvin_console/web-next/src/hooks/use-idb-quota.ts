/**
 * Hook to monitor IndexedDB storage quota
 * Shows warning when usage > 80%
 * ADR-0082 Phase 3: Cleanup & TTL
 */

import { useEffect, useState } from "react";

export interface StorageQuota {
  usage: number; // bytes
  quota: number; // bytes
  percentUsed: number; // 0-100
  isWarning: boolean; // > 80%
  isCritical: boolean; // > 95%
}

export function useIDBQuota() {
  const [quota, setQuota] = useState<StorageQuota | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const checkQuota = async () => {
      try {
        if (!("storage" in navigator) || !("estimate" in navigator.storage)) {
          console.warn("StorageManager API not available");
          return;
        }

        const estimate = await navigator.storage.estimate();
        const usage = estimate.usage || 0;
        const quota_limit = estimate.quota || 0;
        const percentUsed =
          quota_limit > 0 ? Math.round((usage / quota_limit) * 100) : 0;

        setQuota({
          usage,
          quota: quota_limit,
          percentUsed,
          isWarning: percentUsed > 80,
          isCritical: percentUsed > 95,
        });

        if (percentUsed > 80) {
          console.warn(
            `IndexedDB quota warning: ${percentUsed}% used (${Math.round(usage / 1024 / 1024)}MB / ${Math.round(quota_limit / 1024 / 1024)}MB)`
          );
        }
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        setError(error);
        console.error("Failed to check storage quota:", error);
      }
    };

    // Check immediately
    checkQuota();

    // Recheck every 5 minutes
    const interval = setInterval(checkQuota, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  return { quota, error };
}

export function formatBytes(bytes: number): string {
  const mb = bytes / 1024 / 1024;
  const gb = mb / 1024;
  if (gb > 0.1) return `${gb.toFixed(2)} GB`;
  if (mb > 0.1) return `${mb.toFixed(2)} MB`;
  return `${(bytes / 1024).toFixed(2)} KB`;
}
