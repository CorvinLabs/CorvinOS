/**
 * Tool Forge / Plugin Forge — describe it, a real engine run builds it
 * (ADR-2217). One component for both kinds: the run/poll/phase protocol is
 * the one Skill Forge uses, only the result differs.
 *
 *   tool   — the run executes the generated tool's test cases in the Forge
 *            sandbox and registers it only when every case passed and no
 *            reviewer confirmed a security finding. It then shows in Tools.
 *   plugin — the run plans, scaffolds, compiles and reviews a plugin
 *            (optionally with a console panel) and STAGES it. It is never
 *            installed here; it shows in Marketplace → Forged.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle, Cpu, Hammer, Loader2, Package, XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import {
  getForgeRunStatus,
  startForgeGeneration,
  type ForgeKind,
  type ForgeRunStatus,
} from "@/lib/api/forge-creator";

const PHASE_LABELS: Record<string, string> = {
  planning: "Planning",
  validation: "Validation",
  sandbox_test: "Sandbox tests",
  classification: "Classification",
  generation: "Generation",
  checks: "Checks",
  review: "Adversarial review",
  promotion: "Registration",
  staging: "Staging",
};

const ENGINE_LABELS: Record<string, string> = {
  claude_code: "Claude subscription (Claude Code CLI)",
  api: "Anthropic API key",
  local: "No engine",
};

const COPY: Record<ForgeKind, { title: string; description: string; label: string; placeholder: string; cta: string }> = {
  tool: {
    title: "Tool Forge",
    description:
      "Describe a tool. The engine writes it with its own test cases, the cases run in the Forge's " +
      "bubblewrap sandbox, three reviewers check correctness, security and scope, and the tool is " +
      "registered only if every test passes and the security review completes without a confirmed " +
      "finding. The tests show the tool runs as its own cases expect — read them before relying on it.",
    label: "What tool do you want to create?",
    placeholder: "e.g., 'a tool that counts words and sentences in a text' or 'convert CSV text to JSON rows'",
    cta: "Generate Tool",
  },
  plugin: {
    title: "Plugin Forge",
    description:
      "Describe a plugin. The engine plans it, the Plugin Builder writes docs and a scaffold, the code is " +
      "compiled and reviewed, and the result is staged in Marketplace → Forged. Forged plugins are never " +
      "installed from the console: to use one, review it and move it into the marketplace contributor " +
      "tree, where it installs as unsigned community code.",
    label: "What plugin do you want to create?",
    placeholder: "e.g., 'a plugin that summarises the daily weather from open-meteo'",
    cta: "Generate Plugin",
  },
};

/** The run id survives a tab switch (the tab unmounts) and a reload, per kind. */
const runKey = (kind: ForgeKind) => `forge-creator-run:${kind}`;

function storedRun(kind: ForgeKind): string | null {
  try {
    return window.sessionStorage.getItem(runKey(kind));
  } catch {
    return null;
  }
}

function storeRun(kind: ForgeKind, runId: string | null) {
  try {
    if (runId) window.sessionStorage.setItem(runKey(kind), runId);
    else window.sessionStorage.removeItem(runKey(kind));
  } catch {
    /* storage unavailable — the run is simply not re-attached */
  }
}

export default function ForgeCreatorPanel({
  kind,
  onCreated,
  onOpenTools,
}: {
  kind: ForgeKind;
  onCreated?: () => void;
  onOpenTools?: () => void;
}) {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [request, setRequest] = useState("");
  const [panelRequest, setPanelRequest] = useState("");
  const [runId, setRunIdState] = useState<string | null>(() => storedRun(kind));
  const [error, setError] = useState<string | null>(null);
  const copy = COPY[kind];
  const setRunId = (id: string | null) => {
    storeRun(kind, id);
    setRunIdState(id);
  };

  const run = useQuery<ForgeRunStatus>({
    queryKey: ["forge-creator", "run", runId],
    queryFn: ({ signal }) => getForgeRunStatus(runId!, signal),
    enabled: !!runId,
    refetchInterval: (query) => {
      if (query.state.error) return false;
      const s = query.state.data?.status;
      return s === "success" || s === "failed" ? false : 1000;
    },
  });

  // Runs live in the console's memory: after a restart the status route
  // answers 404. Say so and free the form instead of spinning forever.
  useEffect(() => {
    if (!run.error || !runId) return;
    setError("This run is no longer known to the console (it may have restarted). Start a new one.");
    setRunId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.error, runId]);

  useEffect(() => {
    if (run.data?.status !== "success") return;
    void qc.invalidateQueries({ queryKey: ["forge-creator", "plugins"] });
    onCreated?.();
  }, [run.data?.status, qc, onCreated]);

  const start = useMutation({
    mutationFn: () => startForgeGeneration(kind, request.trim(), session?.csrf_token ?? "", panelRequest),
    onSuccess: (data) => {
      setRunId(data.run_id);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const isRunning = !!runId && !run.error && (!run.data || run.data.status === "running");
  const status = runId ? run.data : undefined;

  return (
    <Card data-testid={`${kind}-forge`}>
      <CardHeader>
        <div className="flex items-center gap-2">
          {kind === "tool" ? <Hammer className="h-5 w-5 text-accent" /> : <Package className="h-5 w-5 text-accent" />}
          <CardTitle className="text-base">{copy.title}</CardTitle>
        </div>
        <CardDescription>{copy.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-3">
          <Label htmlFor={`${kind}-request`} className="text-sm font-medium">{copy.label}</Label>
          <Textarea
            id={`${kind}-request`}
            data-testid={`${kind}-request`}
            placeholder={copy.placeholder}
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            disabled={isRunning}
            rows={3}
            className="font-mono text-xs"
          />
          {kind === "plugin" && (
            <>
              <Label htmlFor="plugin-panel-request" className="text-sm font-medium">
                Console panel (optional)
              </Label>
              <Textarea
                id="plugin-panel-request"
                data-testid="plugin-panel-request"
                placeholder="Leave empty for no panel, or describe what the plugin's panel should show"
                value={panelRequest}
                onChange={(e) => setPanelRequest(e.target.value)}
                disabled={isRunning}
                rows={2}
                className="font-mono text-xs"
              />
            </>
          )}
          <Button
            onClick={() => (request.trim().length < 10
              ? setError("Describe it in at least 10 characters")
              : start.mutate())}
            disabled={isRunning || start.isPending || !request.trim() || !session}
            className="w-full"
            data-testid={`${kind}-submit`}
          >
            {(isRunning || start.isPending) && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isRunning ? "Generating…" : copy.cta}
          </Button>
          <p className="text-[10px] text-muted-foreground">
            A run takes several minutes and is charged to your configured engine (Claude subscription
            or API key). Member feature.
          </p>
        </div>

        {error && (
          <div role="alert" className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {status && (
          <div className="space-y-3 rounded-md border border-accent/40 bg-accent/5 p-3" data-testid={`${kind}-run`}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">Generation progress</h3>
              <Badge variant="outline" className="font-mono text-xs">{status.status.toUpperCase()}</Badge>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Cpu className="h-3 w-3" />
              <span>Engine: {ENGINE_LABELS[status.engine] || status.engine}</span>
            </div>
            <ol className="flex flex-wrap gap-2 text-xs">
              {status.phases.map((p) => {
                const idx = status.phases.indexOf(status.phase);
                const here = status.phases.indexOf(p);
                const done = status.status === "success" || here < idx;
                const current = here === idx && status.status === "running";
                return (
                  <li
                    key={p}
                    className={
                      "rounded-sm border px-2 py-0.5 " +
                      (done ? "border-accent/50 text-foreground" : current ? "border-accent font-medium" : "border-border/60 text-muted-foreground")
                    }
                  >
                    {PHASE_LABELS[p] || p}
                  </li>
                );
              })}
            </ol>
            <p className="text-xs" data-testid={`${kind}-run-message`}>{status.message}</p>
            {status.tool && (
              <ToolResult
                tool={status.tool}
                registered={status.status === "success"}
                onOpenTools={onOpenTools}
              />
            )}
            {status.plugin && (
              <div className="space-y-2 text-xs" data-testid="plugin-result">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="font-mono">{status.plugin.plugin_id}</code>
                  <Badge variant="outline">{status.plugin.kind}</Badge>
                  <Badge variant="outline">Tier {status.plugin.tier}</Badge>
                  <Badge variant="secondary">Not installed</Badge>
                  {status.plugin.panel && <Badge variant="outline">Has panel</Badge>}
                </div>
                <Findings items={status.plugin.findings} skipped={status.plugin.review_skipped} />
                <Link to="/app/marketplace?tab=forged" className="text-accent underline">
                  Review it in Marketplace → Forged
                </Link>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ToolResult({
  tool,
  registered,
  onOpenTools,
}: {
  tool: NonNullable<ForgeRunStatus["tool"]>;
  registered: boolean;
  onOpenTools?: () => void;
}) {
  return (
    <div className="space-y-2 text-xs" data-testid="tool-result">
      <div className="flex flex-wrap items-center gap-2">
        <code className="font-mono">{tool.name}</code>
        <Badge variant="outline">
          {tool.tests.passed}/{tool.tests.total} sandbox tests
        </Badge>
        {tool.sandbox.length > 0 && <Badge variant="outline">sandbox: {tool.sandbox.join(", ")}</Badge>}
      </div>
      <p className="text-muted-foreground">{tool.description}</p>
      <ul className="space-y-1">
        {tool.tests.cases.map((c) => (
          <li key={c.index} className="flex items-start gap-2 font-mono text-[11px]">
            {c.passed ? <CheckCircle className="mt-0.5 h-3 w-3 shrink-0 text-accent" /> : <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />}
            <span className="break-all">
              {JSON.stringify(c.input)} → {c.passed ? JSON.stringify(c.output) : c.error}
            </span>
          </li>
        ))}
      </ul>
      <Findings items={tool.findings} skipped={false} />
      {registered && onOpenTools && (
        <button type="button" onClick={onOpenTools} className="text-accent underline" data-testid="open-tools">
          Open it in Tools
        </button>
      )}
    </div>
  );
}

export function Findings({ items, skipped }: { items: { dimension: string; summary: string; verdict: string }[]; skipped: boolean }) {
  if (skipped) return <p className="text-muted-foreground">Review skipped — no engine was available.</p>;
  const real = items.filter((f) => f.verdict !== "refuted");
  if (real.length === 0) return <p className="text-muted-foreground">Reviewers raised no finding.</p>;
  return (
    <ul className="space-y-1" data-testid="review-findings">
      {real.map((f, i) => (
        <li key={i} className="text-[11px]">
          <Badge variant={f.verdict === "confirmed" ? "danger" : "outline"} className="mr-1 text-[10px]">
            {f.dimension} · {f.verdict}
          </Badge>
          {f.summary}
        </li>
      ))}
    </ul>
  );
}
