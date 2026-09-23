/**
 * Install Flow Modal — Step-by-step wizard for marketplace plugin installation.
 *
 * Flow:
 * 1. Dependency validation: Load & display dependency tree
 * 2. Version selection: Pick version (defaults to latest)
 * 3. Review: Show what will be installed (root + all transitive deps)
 * 4. Execute: Run install and show progress
 * 5. Confirm: Success or error, with next action button
 *
 * ADR-0892: Each step is real work, not UI theatre — no progress bars that
 * aren't backed by actual operations.
 */

import { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, ArrowRight, CheckCircle, ChevronRight, Loader2, Package, AlertTriangle
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { ApiError } from "@/lib/api/client";
import { KEY_INDEX, KEY_INSTALLED } from "../header";
import { KEY_CAPABILITIES, KEY_MANIFEST } from "../tabs/browse";
import type { TabId } from "../tabs";
import type { IndexPlugin } from "../api";
import {
  getPluginDependencies, startInstallJob, getInstallProgress, enablePlugin,
  type DependencyNode, type InstallPlan, type InstallJob,
} from "../api";

type FlowStep = "dependencies" | "version" | "review" | "execute" | "confirm";

interface Props {
  plugin: IndexPlugin;
  csrf: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
  onGoTo?: (tab: TabId) => void;
}

function DependencyTree({ node, level = 0 }: { node: DependencyNode; level?: number }) {
  const indent = level * 16;
  return (
    <div style={{ marginLeft: `${indent}px` }} className="text-sm space-y-1">
      <div className="flex items-center gap-2">
        {node.children.length > 0 && <ChevronRight size={16} />}
        <span className={node.missing ? "line-through text-muted-foreground" : ""}>
          {node.plugin_id || node.index_id}
        </span>
        {node.installed && <Badge variant="secondary" className="text-xs">already installed</Badge>}
        {node.missing && <Badge variant="danger" className="text-xs">missing</Badge>}
      </div>
      {node.reason && <div className="text-xs text-destructive ml-6">{node.reason}</div>}
      {node.children.map((child) => (
        <DependencyTree key={child.index_id} node={child} level={level + 1} />
      ))}
    </div>
  );
}

function StepIndicator({ step, steps }: { step: FlowStep; steps: FlowStep[] }) {
  const index = steps.indexOf(step);
  return (
    <div className="flex gap-2 mb-6">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <div
            className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold ${
              i < index ? "bg-emerald-600 text-white" :
              i === index ? "bg-accent text-white" :
              "bg-muted text-muted-foreground"
            }`}
          >
            {i < index ? <CheckCircle size={16} /> : i + 1}
          </div>
          {i < steps.length - 1 && (
            <div className={`h-0.5 w-8 ${i < index ? "bg-emerald-600" : "bg-muted"}`} />
          )}
        </div>
      ))}
    </div>
  );
}

export function InstallFlowModal({ plugin, csrf, open, onOpenChange, onSuccess, onGoTo }: Props) {
  const qc = useQueryClient();
  const [step, setStep] = useState<FlowStep>("dependencies");
  const [version, setVersion] = useState(plugin.version);
  const [jobId, setJobId] = useState<string | null>(null);
  const STEPS: FlowStep[] = ["dependencies", "version", "review", "execute", "confirm"];

  // Step 1: Load dependencies
  const depQuery = useQuery({
    queryKey: ["marketplace", "dependencies", plugin.id],
    queryFn: ({ signal }) => getPluginDependencies(plugin.id, signal),
    enabled: open && step === "dependencies",
  });

  // Step 4: Execute install
  const installMutation = useMutation({
    mutationFn: () => startInstallJob(plugin.id, version, csrf),
    onSuccess: (job) => {
      setJobId(job.job_id);
      setStep("execute");
    },
  });

  // Step 4: Poll install progress
  const progressQuery = useQuery({
    queryKey: ["marketplace", "install-job", jobId],
    queryFn: ({ signal }) => getInstallProgress(jobId as string, signal),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "completed" || s === "failed" ? false : 300;
    },
    retry: false,
  });

  // A plugin that declares a console panel puts it in the sidebar only once
  // ENABLED (state.PluginLifecycle._sync_console_panel), and the sidebar reads
  // the manifest, not the plugin registry directly — so every path that can
  // change a plugin's enabled state must invalidate both.
  const invalidateAfterEnableChange = () => {
    qc.invalidateQueries({ queryKey: ["marketplace"] });
    qc.invalidateQueries({ queryKey: [...KEY_INSTALLED] });
    qc.invalidateQueries({ queryKey: [...KEY_INDEX] });
    qc.invalidateQueries({ queryKey: [...KEY_MANIFEST] });
    qc.invalidateQueries({ queryKey: [...KEY_CAPABILITIES] });
  };

  // Step 5: install always leaves the plugin disabled (ADR-0124 Inv. 6 — enable
  // is its own deliberate, audited, hot-loading step, never implicit in
  // install). This is the SAME one-click action as the Installed tab's
  // "Enable" button, offered right here so the operator does not have to go
  // find it — `consentGranted` mirrors `requires_consent` exactly like
  // `installed.tsx`'s `Row` does, so a plugin that never needed consent does
  // not get one recorded regardless.
  const [enableMsg, setEnableMsg] = useState<string | null>(null);
  const enableMutation = useMutation({
    mutationFn: ({ registryId, consent }: { registryId: string; consent: boolean }) =>
      enablePlugin(registryId, csrf, consent),
    onSuccess: () => {
      setEnableMsg("Enabled — the panel is now in the sidebar.");
      invalidateAfterEnableChange();
    },
    onError: (e) =>
      setEnableMsg(
        e instanceof ApiError && e.status === 403
          ? `Refused: ${e.message}`
          : "Not enabled — see the Installed tab for details.",
      ),
  });

  // Auto-advance from dependencies to version selection
  useEffect(() => {
    if (step === "dependencies" && depQuery.isSuccess && depQuery.data) {
      // Give user a moment to see the dependency tree
      const timer = setTimeout(() => {
        if (!depQuery.data!.dependency_tree.missing) {
          // Only auto-advance if deps resolved OK
          setStep("version");
        }
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [step, depQuery.isSuccess, depQuery.data]);

  // Handle install completion
  useEffect(() => {
    const job = progressQuery.data;
    if (job && (job.status === "completed" || job.status === "failed")) {
      setStep("confirm");
      if (job.status === "completed") {
        invalidateAfterEnableChange();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progressQuery.data, qc]);

  const handleClose = () => {
    onOpenChange(false);
    setStep("dependencies");
    setJobId(null);
    setEnableMsg(null);
    enableMutation.reset();
  };

  const handleSuccess = () => {
    onSuccess();
    handleClose();
  };

  const plan = depQuery.data;
  const job = progressQuery.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Install {plugin.name}</DialogTitle>
          <DialogDescription>
            Follow the steps to install this plugin and its dependencies
          </DialogDescription>
        </DialogHeader>

        <StepIndicator step={step} steps={STEPS} />

        {/* Step 1: Dependencies */}
        {step === "dependencies" && (
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold mb-2">Checking dependencies…</h3>
              {depQuery.isLoading && (
                <div className="flex justify-center py-8">
                  <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
                </div>
              )}
              {depQuery.isError && (
                <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 flex gap-2 text-sm text-destructive">
                  <AlertCircle size={16} className="shrink-0 mt-0.5" />
                  <span>Failed to load dependencies: check your connection</span>
                </div>
              )}
              {plan && (
                <div className="space-y-3">
                  {plan.dependency_tree.missing ? (
                    <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 flex gap-2 text-sm text-destructive">
                      <AlertTriangle size={16} className="shrink-0 mt-0.5" />
                      <div>
                        <div className="font-semibold">Cannot resolve: {plan.dependency_tree.reason}</div>
                        <div className="text-xs mt-1 opacity-80">This plugin cannot be installed on this build.</div>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="text-sm text-muted-foreground">
                        {plan.total_new === 0
                          ? `No additional dependencies required.`
                          : `Will install ${plan.total_new} new plugin${plan.total_new !== 1 ? "s" : ""}`}
                        {plan.total_existing > 0 && ` (${plan.total_existing} already installed)`}
                      </div>
                      {plan.total_new > 0 && (
                        <div className="p-3 rounded-lg bg-muted/30 border border-border">
                          <div className="flex items-center gap-2 mb-2 text-sm font-semibold">
                            <Package size={16} />
                            Dependency tree
                          </div>
                          <DependencyTree node={plan.dependency_tree} />
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>Cancel</Button>
              <Button
                disabled={depQuery.isLoading || depQuery.isError || plan?.dependency_tree.missing}
                onClick={() => setStep("version")}
              >
                Next: Choose version <ArrowRight size={16} className="ml-2" />
              </Button>
            </div>
          </div>
        )}

        {/* Step 2: Version Selection */}
        {step === "version" && (
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold mb-2">Choose a version</h3>
              <div className="text-sm text-muted-foreground mb-3">
                Latest: v{plugin.version}
                {version !== plugin.version && ` (you selected v${version})`}
              </div>
              <select
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value={plugin.version}>
                  {plugin.version} (latest)
                </option>
                {/* In a real implementation, fetch version history from the marketplace */}
              </select>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setStep("dependencies")}>Back</Button>
              <Button onClick={() => setStep("review")}>
                Next: Review <ArrowRight size={16} className="ml-2" />
              </Button>
            </div>
          </div>
        )}

        {/* Step 3: Review */}
        {step === "review" && plan && (
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold mb-2">Review installation</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between py-2 border-b">
                  <span className="text-muted-foreground">Root plugin</span>
                  <span className="font-mono">{plan.root_plugin_id}</span>
                </div>
                <div className="flex justify-between py-2 border-b">
                  <span className="text-muted-foreground">Version</span>
                  <span>v{version}</span>
                </div>
                <div className="flex justify-between py-2 border-b">
                  <span className="text-muted-foreground">Dependencies to install</span>
                  <span className="font-semibold">{plan.total_new}</span>
                </div>
                <div className="flex justify-between py-2 border-b">
                  <span className="text-muted-foreground">Already installed</span>
                  <span>{plan.total_existing}</span>
                </div>
                {plan.to_install.length > 0 && (
                  <div className="py-2 border-b">
                    <span className="text-muted-foreground block mb-1">Will be installed:</span>
                    <div className="text-xs space-y-0.5">
                      {plan.to_install.map((id) => (
                        <div key={id} className="font-mono text-muted-foreground">• {id}</div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setStep("version")}>Back</Button>
              <Button onClick={() => installMutation.mutate()} disabled={installMutation.isPending}>
                {installMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                Install now
              </Button>
            </div>
          </div>
        )}

        {/* Step 4: Execute */}
        {step === "execute" && job && (
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold mb-3">Installing…</h3>
              <div className="space-y-2">
                <Progress value={job.progress} />
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 size={14} className="animate-spin" />
                  {job.message} · {job.progress}%
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Step 5: Confirm */}
        {step === "confirm" && job && (
          <div className="space-y-4">
            {job.status === "completed" ? (
              <div className="space-y-3">
                <div className="p-3 rounded-lg bg-emerald-600/10 border border-emerald-600/20 flex gap-2 text-sm text-emerald-700 dark:text-emerald-400">
                  <CheckCircle size={16} className="shrink-0 mt-0.5" />
                  <div>
                    <div className="font-semibold">Installation completed</div>
                    <div className="text-xs mt-1 opacity-80">
                      {enableMutation.isSuccess
                        ? "Enabled — its panel, if it has one, is already in the sidebar."
                        : "Installed, but not yet enabled. Enabling is a separate, audited step — the sidebar panel (if this plugin has one) appears only once enabled."}
                    </div>
                  </div>
                </div>

                {!enableMutation.isSuccess && job.registry_id && (
                  <div className="p-3 rounded-lg bg-muted/30 border border-border space-y-2">
                    <Button
                      size="sm"
                      onClick={() => enableMutation.mutate({
                        registryId: job.registry_id as string,
                        consent: job.requires_consent ?? false,
                      })}
                      disabled={enableMutation.isPending}
                    >
                      {enableMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                      {job.requires_consent ? "Enable now (grants consent)" : "Enable now"}
                    </Button>
                    {enableMsg && <p className="text-xs text-muted-foreground">{enableMsg}</p>}
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={handleClose}>Close</Button>
                  <Button onClick={() => { onGoTo?.("installed"); handleSuccess(); }}>
                    Go to installed tab
                  </Button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 flex gap-2 text-sm text-destructive">
                  <AlertCircle size={16} className="shrink-0 mt-0.5" />
                  <div>
                    <div className="font-semibold">Installation failed</div>
                    <div className="text-xs mt-1 opacity-80">{job.error || "Unknown error"}</div>
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={() => { setJobId(null); setStep("review"); }}>Try again</Button>
                  <Button variant="outline" onClick={handleClose}>Close</Button>
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
