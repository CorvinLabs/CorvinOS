/**
 * UpdateCard — Manual update trigger + auto-update toggle
 *
 * Features:
 * - Check for available updates (non-blocking)
 * - Install updates with progress feedback
 * - Toggle auto-update on startup
 * - Platform-aware (PowerShell on Windows, Shell on Unix)
 * - Concurrent-update locking (only one update per tenant)
 */

import * as React from "react";
import { AlertCircle, Check, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { checkUpdate, installUpdate, getAutoUpdate, setAutoUpdate, type UpdateCheckResult, type UpdateInstallResult } from "@/lib/api";

interface UpdateCardProps {
  csrf: string;
}

export function UpdateCard({ csrf }: UpdateCardProps) {
  const qc = useQueryClient();

  // Check state
  const checkQuery = useQuery({
    queryKey: ["update-check"],
    queryFn: ({ signal }) => checkUpdate(signal),
    // Re-check every minute; every 3s while the updater runs (the console restarts mid-way,
    // so a failed poll is expected and simply retried).
    refetchInterval: (q) => (q.state.data?.running || q.state.status === "error" ? 3000 : 60000),
    retry: false,
  });

  // Auto-update state
  const autoUpdateQuery = useQuery({
    queryKey: ["auto-update"],
    queryFn: ({ signal }) => getAutoUpdate(signal),
  });

  // Install state
  const [installing, setInstalling] = React.useState(false);
  const [installError, setInstallError] = React.useState<string | null>(null);
  const [installSuccess, setInstallSuccess] = React.useState(false);
  const [installProgress, setInstallProgress] = React.useState<string | null>(null);

  // Auto-update toggle state
  const [savingAutoUpdate, setSavingAutoUpdate] = React.useState(false);
  const [autoUpdateError, setAutoUpdateError] = React.useState<string | null>(null);

  const check = checkQuery.data as UpdateCheckResult | undefined;
  const autoUpdateStatus = autoUpdateQuery.data;

  const handleInstall = async () => {
    setInstallError(null);
    setInstallSuccess(false);
    setInstalling(true);
    setInstallProgress("Starting update...");

    try {
      setInstallProgress("Downloading and installing updates...");
      const result = await installUpdate(csrf);

      if (result.success) {
        setInstallProgress(result.final_message);
        setInstallSuccess(true);
        // Refresh version check
        qc.invalidateQueries({ queryKey: ["update-check"] });
        setTimeout(() => setInstallSuccess(false), 5000);
      } else {
        throw new Error(result.final_message);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("409")) {
        setInstallError("Update already in progress. Please wait.");
      } else if (msg.includes("already_in_progress")) {
        setInstallError("Update already in progress. Please wait.");
      } else {
        setInstallError(msg);
      }
    } finally {
      setInstalling(false);
      setInstallProgress(null);
    }
  };

  const handleAutoUpdateToggle = async (next: boolean) => {
    setAutoUpdateError(null);
    setSavingAutoUpdate(true);
    try {
      await setAutoUpdate(next, csrf);
      qc.invalidateQueries({ queryKey: ["auto-update"] });
    } catch (e) {
      setAutoUpdateError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingAutoUpdate(false);
    }
  };

  const running = check?.running ?? false;
  const last = check?.last_update ?? null;
  const lastText = last?.at
    ? `${new Date(last.at * 1000).toLocaleString()} - ${
        last.status === "success" ? "succeeded"
        : last.status === "failed" ? "failed"
        : last.status === "interrupted" ? "interrupted"
        : "in progress"}${last.version_after && last.version_before && last.version_after !== last.version_before
        ? ` (v${last.version_before} -> v${last.version_after})` : ""}`
    : "never";
  const hasUpdate = check?.has_update ?? false;
  const currentVersion = check?.current_version ?? "unknown";
  const availableVersion = check?.available_version ?? null;
  const checkError = check?.error;
  const autoUpdateEnabled = autoUpdateStatus?.enabled ?? true;

  return (
    <>
      {/* Manual Update Card */}
      <Card>
        <CardContent className="pt-4 pb-3 space-y-3">
          {/* Header */}
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 min-w-0">
              <RefreshCw className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium">Update CorvinOS Core</span>
                  {currentVersion !== "unknown" && (
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      v{currentVersion}
                    </Badge>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  {checkError ? (
                    <span className="text-amber-600 dark:text-amber-400">
                      Check failed: {checkError}
                    </span>
                  ) : hasUpdate ? (
                    <span className="text-emerald-600 dark:text-emerald-400">
                      Update available: v{availableVersion}
                    </span>
                  ) : (
                    "You are on the latest version."
                  )}
                </p>
              </div>
            </div>

            {/* Install Button */}
            <div className="flex items-center gap-2 shrink-0">
              {installing && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
              {installSuccess && <Check className="h-4 w-4 text-emerald-500" />}
              <Button
                size="sm"
                onClick={handleInstall}
                disabled={installing || running || !hasUpdate || !!checkError}
                className="h-8 px-3 text-sm gap-1"
              >
                {installing || running ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Updating...
                  </>
                ) : installSuccess ? (
                  <>
                    <Check className="h-3.5 w-3.5" />
                    Done
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-3.5 w-3.5" />
                    Check & Install
                  </>
                )}
              </Button>
            </div>
          </div>

          <p className="text-[11px] text-muted-foreground" data-testid="last-update">
            Last update: {lastText}
          </p>
          {last?.status === "failed" && last.detail && (
            <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{last.detail}</p>
          )}

          {/* Progress message */}
          {installProgress && (
            <p className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
              {installProgress}
            </p>
          )}

          {/* Error message */}
          {installError && (
            <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5 flex items-start gap-2">
              <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{installError}</span>
            </p>
          )}

          {/* Loading state */}
          {checkQuery.isLoading && (
            <p className="text-xs text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-3 w-3 animate-spin" />
              Checking for updates...
            </p>
          )}
        </CardContent>
      </Card>

      {/* Auto-Update Card */}
      <Card>
        <CardContent className="pt-4 pb-3">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 min-w-0">
              <RefreshCw className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium">Auto-update on startup</span>
                  {autoUpdateStatus?.version && autoUpdateStatus.version !== "unknown" && (
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      v{autoUpdateStatus.version}
                    </Badge>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Automatically check for and install updates each time CorvinOS starts.
                  Disable if you manage versions manually or are offline.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {savingAutoUpdate && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
              {autoUpdateQuery.isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              ) : (
                <Switch
                  checked={autoUpdateEnabled}
                  onCheckedChange={handleAutoUpdateToggle}
                  disabled={savingAutoUpdate || autoUpdateQuery.isLoading}
                  aria-label="Auto-update on startup"
                />
              )}
            </div>
          </div>
          {autoUpdateError && (
            <p className="mt-2 text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">
              {autoUpdateError}
            </p>
          )}
        </CardContent>
      </Card>
    </>
  );
}
