/**
 * Layers tab (ADR-2222, ADR-2224 Phase 2–3) — UI for layer definition
 * lifecycle: create via LLM, run quality gates + enforcement, transition through
 * proposed → accepted → deployed.
 *
 * Consolidated into Forge as a tab on 2026-10-06 (operator request): Layer
 * Forge used to be its own top-level nav entry (/app/layer-forge) and its own
 * page component (pages/layer-forge.tsx). It now lives here as one more
 * Forge sub-surface, next to Tools/Skills/OS-Skills — reachable at
 * /app/forge?tab=layers. The old /app/layer-forge URL still works: App.tsx
 * redirects it here (see the `layer-forge` <Route> there). The backend API
 * (/v1/console/layer-forge/*) is unchanged — this is a frontend-only move.
 *
 * API client note: this file imports `api`/`ApiError` from `@/lib/api/client`
 * (the same import the original pages/layer-forge.tsx used), while forge.tsx
 * imports `api` from `@/lib/api`. These are NOT two different clients —
 * `@/lib/api` (src/lib/api.ts) is a barrel that re-exports `api`/`ApiError`
 * from `./api/client`, i.e. the exact same module as `@/lib/api/client`
 * (src/lib/api/client.ts). Both paths resolve to one singleton fetch
 * wrapper with the same auth/CSRF/timeout handling; several other
 * components/forge/* files already import from one path or the other
 * (e.g. AutonomousForgePanel.tsx uses `@/lib/api/client` directly). Kept as
 * the direct `/client` import here to minimize the diff from the moved file.
 *
 * BASE path bug found + fixed during this move (live-browser 404 report,
 * 2026-10-06): `api()` already prepends its own `BASE = "/v1/console"`
 * (lib/api/client.ts) to every path passed to it — forge.tsx's calls are all
 * `api('/forge/...')`, never `api('/v1/console/forge/...')`. The original
 * pages/layer-forge.tsx instead declared a LOCAL `const BASE =
 * "/api/layer-forge"` and called `api(\`${BASE}/definitions\`)`, which
 * concatenated to `/v1/console` + `/api/layer-forge/definitions` — a path
 * nothing serves (the backend router, core/console/corvin_console/routes/
 * layer_forge.py, is mounted with NO extra prefix under `/v1/console` and its
 * own decorators already say `/layer-forge/...`, so the real path is
 * `/v1/console/layer-forge/definitions`). This 404'd from the day the panel
 * shipped (58c267f86) — the list view silently rendered its 404 empty/error
 * state, never a crash, so it read as "working, just no data yet". Fixed
 * here by dropping the stray local BASE/`/api` segment entirely; see
 * tests/unit/layers-tab.test.tsx for the regression test (asserts the actual
 * resolved fetch URL, not a mocked function call — a mock of `api()` itself
 * would not have caught this). pages/layer-forge-analytics.tsx has the same
 * defect (worse: it bypasses `api()` with a raw `fetch` missing `/v1/console`
 * entirely) and was fixed in the same commit, though that page's
 * redesign/consolidation is out of this task's scope.
 *
 * List view: table of all definitions with status, gate count, versions
 * Detail view: manifest, all verdicts (gates, enforcement), transition buttons
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2, AlertCircle, ChevronRight, Sparkles } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";

// api() already prepends "/v1/console" — see the file-level comment above.
const BASE = "/layer-forge";

export interface LayerDefinition {
  id: string;
  version: string;
  status: "proposed" | "accepted" | "deployed" | "superseded";
  targets: Array<{ layer_id: string }>;
  quality_gates: Array<{ gate_id: string }>;
  enforcement_rules: Array<{ rule_id: string }>;
  dependencies?: Array<{ id: string }>;
  _created_at?: number;
  _promoted_at?: number;
}

export interface LayerForgeListResponse {
  items: LayerDefinition[];
  count: number;
}

// Additional fields from the detail endpoint, if any — currently identical.
export type LayerForgeDetailResponse = LayerDefinition;

const statusColors: Record<string, "outline" | "secondary" | "ok" | "default"> = {
  proposed: "outline",
  accepted: "secondary",
  deployed: "ok",
  superseded: "default",
};

const statusLabels: Record<string, string> = {
  proposed: "Proposed",
  accepted: "Accepted",
  deployed: "Deployed",
  superseded: "Superseded",
};

function formatDate(timestamp?: number): string {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface PlanResponse {
  status: "SUCCESS" | "FAILED";
  manifest?: Record<string, unknown>;
  error?: string;
  phase?: string;
}

interface CreateResponse {
  status: "SUCCESS" | "FAILED";
  error?: string;
  phase?: string;
}

/** "Forge a Layer" — the LLM-PLAN entry point (ADR-2224/2225) that was
 *  missing entirely: the backend has supported POST /layer-forge/plan since
 *  that ADR landed, but nothing in the console ever called it. Lives in
 *  Generator as a fourth sub-tab (2026-10-06, operator request) alongside
 *  Skill/Tool/Plugin Forge — "the one place to create something" (see the
 *  comment in forge.tsx) — rather than inside this file's own LayersTab,
 *  which stays a pure browse surface like Tools/Skills/OS-Skills. Two-step
 *  flow, deliberately NOT the run/poll Generator protocol (ADR-2217/
 *  ADR-0672) Skill/Tool/Plugin Forge share: plan() is a single synchronous
 *  LLM call, not a polled background job, so it needs none of that
 *  machinery — it only happens to live in the same tab group. Step 1 (plan)
 *  only generates a manifest preview — nothing is persisted or audited as a
 *  registry entry yet. Step 2 (create) submits that manifest through the
 *  real pipeline (validate → test → enforce → review → audit → write),
 *  which is where it actually lands in the tenant's audit chain. Invalidates
 *  the shared `["layer-forge","definitions"]` query on success so the
 *  Layers tab shows the new entry whenever it's next viewed, regardless of
 *  where in the app this panel is rendered. */
export function ForgeLayerPanel({ onCreated }: { onCreated?: () => void }) {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [layerId, setLayerId] = useState("");
  const [intent, setIntent] = useState("");
  const [manifest, setManifest] = useState<Record<string, unknown> | null>(null);
  const [created, setCreated] = useState<{ id: string; version: string } | null>(null);

  const plan = useMutation({
    mutationFn: () =>
      api<PlanResponse>(`${BASE}/plan`, {
        method: "POST",
        body: { layer_id: layerId.trim(), intent: intent.trim() },
        csrf: session?.csrf_token ?? "",
      }),
    onSuccess: (data) => {
      setCreated(null);
      setManifest(data.manifest ?? null);
    },
    onError: () => setManifest(null),
  });

  const create = useMutation({
    mutationFn: () =>
      api<CreateResponse>(`${BASE}/definitions`, {
        method: "POST",
        body: { manifest },
        csrf: session?.csrf_token ?? "",
      }),
    onSuccess: () => {
      const m = manifest as { id?: string; version?: string } | null;
      setCreated({ id: String(m?.id ?? layerId), version: String(m?.version ?? "") });
      setManifest(null);
      setLayerId("");
      setIntent("");
      void qc.invalidateQueries({ queryKey: ["layer-forge", "definitions"] });
      onCreated?.();
    },
  });

  const canPlan = layerId.trim().length > 0 && intent.trim().length > 0 && !plan.isPending;

  return (
    <Card data-testid="forge-layer-panel">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-accent" />
          <CardTitle className="text-base">Forge a Layer</CardTitle>
        </div>
        <CardDescription>
          Describe what the new layer should do. An LLM drafts the manifest (targets,
          quality gates, enforcement rules) for review before anything is created.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-[160px_1fr] gap-3 items-start">
          <div className="space-y-1.5">
            <Label htmlFor="layer-id">Layer ID</Label>
            <Input
              id="layer-id"
              data-testid="layer-id-input"
              placeholder="L34"
              value={layerId}
              onChange={(e) => setLayerId(e.target.value)}
              disabled={plan.isPending || create.isPending}
              className="font-mono text-sm"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="layer-intent">Intent</Label>
            <Textarea
              id="layer-intent"
              data-testid="layer-intent-input"
              placeholder="Audit downstream of L10 and enforce the boundary at build time"
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              disabled={plan.isPending || create.isPending}
              rows={2}
              className="text-sm"
            />
          </div>
        </div>

        <Button
          size="sm"
          disabled={!canPlan}
          onClick={() => plan.mutate()}
          data-testid="plan-layer-button"
        >
          {plan.isPending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
          Plan
        </Button>

        {plan.isError && (
          <p className="text-sm text-destructive" data-testid="plan-layer-error">
            {plan.error instanceof Error ? plan.error.message : "Plan request failed."}
          </p>
        )}

        {manifest && (
          <Card className="bg-muted/50">
            <CardHeader>
              <CardTitle className="text-sm">Manifest preview</CardTitle>
              <CardDescription>Nothing is created yet — review, then confirm.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <pre className="text-xs font-mono whitespace-pre-wrap bg-background border rounded p-3 max-h-64 overflow-auto">
                {JSON.stringify(manifest, null, 2)}
              </pre>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="default"
                  disabled={create.isPending}
                  onClick={() => create.mutate()}
                  data-testid="create-layer-button"
                >
                  {create.isPending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                  Create
                </Button>
                <Button size="sm" variant="outline" onClick={() => setManifest(null)} disabled={create.isPending}>
                  Discard
                </Button>
              </div>
              {create.isError && (
                <p className="text-sm text-destructive" data-testid="create-layer-error">
                  {create.error instanceof Error ? create.error.message : "Create request failed."}
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {created && (
          <p className="text-sm text-muted-foreground" data-testid="create-layer-success">
            Created {created.id}@{created.version} as <Badge variant="outline">proposed</Badge>. See the{" "}
            <Link to="/app/forge?tab=layers" className="text-primary hover:underline">
              Layers tab
            </Link>
            .
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ListView({ definitions, onSelect }: { definitions: LayerDefinition[]; onSelect: (d: LayerDefinition) => void }) {
  if (definitions.length === 0) {
    return (
      <div className="py-12 text-center">
        <p className="text-muted-foreground">No layer definitions yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {definitions.map((def) => (
        <button
          key={`${def.id}@${def.version}`}
          onClick={() => onSelect(def)}
          className="w-full text-left p-4 border rounded-lg hover:bg-accent/50 transition-colors"
        >
          <div className="flex items-center justify-between">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-sm font-semibold">{def.id}</span>
                <span className="text-muted-foreground text-xs">v{def.version}</span>
                <Badge variant={statusColors[def.status]}>
                  {statusLabels[def.status]}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground">
                {def.targets.length} target(s) · {def.quality_gates.length} gate(s) · {def.enforcement_rules.length} rule(s)
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                Created {formatDate(def._created_at)}
                {def._promoted_at && ` · Promoted ${formatDate(def._promoted_at)}`}
              </div>
            </div>
            <ChevronRight className="w-4 h-4 text-muted-foreground" />
          </div>
        </button>
      ))}
    </div>
  );
}

function DetailView({ definition, onBack }: { definition: LayerDefinition | null; onBack: () => void }) {
  if (!definition) return null;

  const transitionStates: Record<string, string[]> = {
    proposed: ["accepted"],
    accepted: ["deployed", "proposed"],
    deployed: ["superseded"],
    superseded: [],
  };

  const nextStates = transitionStates[definition.status] || [];

  return (
    <div className="space-y-6">
      <Button variant="outline" onClick={onBack} className="mb-4">
        ← Back
      </Button>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="font-mono">{definition.id}@{definition.version}</CardTitle>
              <CardDescription>Layer definition manifest</CardDescription>
            </div>
            <Badge variant={statusColors[definition.status]}>
              {statusLabels[definition.status]}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <h4 className="text-sm font-semibold mb-2">Targets</h4>
            <div className="flex flex-wrap gap-2">
              {definition.targets.map((t) => (
                <Badge key={t.layer_id} variant="outline">
                  {t.layer_id}
                </Badge>
              ))}
            </div>
          </div>

          <div>
            <h4 className="text-sm font-semibold mb-2">Quality Gates ({definition.quality_gates.length})</h4>
            <div className="grid grid-cols-2 gap-2">
              {definition.quality_gates.map((g) => (
                <Badge key={g.gate_id} variant="secondary" className="text-xs">
                  {g.gate_id}
                </Badge>
              ))}
            </div>
            {definition.quality_gates.length === 0 && (
              <p className="text-xs text-muted-foreground">No quality gates defined.</p>
            )}
          </div>

          <div>
            <h4 className="text-sm font-semibold mb-2">Enforcement Rules ({definition.enforcement_rules.length})</h4>
            <div className="grid grid-cols-2 gap-2">
              {definition.enforcement_rules.map((r) => (
                <Badge key={r.rule_id} variant="secondary" className="text-xs">
                  {r.rule_id}
                </Badge>
              ))}
            </div>
            {definition.enforcement_rules.length === 0 && (
              <p className="text-xs text-muted-foreground">No enforcement rules defined.</p>
            )}
          </div>

          <div>
            <h4 className="text-sm font-semibold mb-2">Dependencies ({(definition.dependencies || []).length})</h4>
            <div className="flex flex-wrap gap-2">
              {(definition.dependencies || []).map((d) => (
                <Badge key={d.id} variant="outline" className="text-xs">
                  {d.id}
                </Badge>
              ))}
            </div>
            {(definition.dependencies || []).length === 0 && (
              <p className="text-xs text-muted-foreground">No dependencies.</p>
            )}
          </div>

          <div className="text-xs text-muted-foreground border-t pt-4 space-y-1">
            <p>Created: {formatDate(definition._created_at)}</p>
            {definition._promoted_at && <p>Promoted: {formatDate(definition._promoted_at)}</p>}
          </div>
        </CardContent>
      </Card>

      {nextStates.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Transition</CardTitle>
            <CardDescription>Promote to next status</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex gap-2">
              {nextStates.map((state) => (
                <Button key={state} size="sm" disabled>
                  {state === "proposed" && "Revert to Proposed"}
                  {state === "accepted" && "Accept"}
                  {state === "deployed" && "Deploy"}
                  {state === "superseded" && "Supersede"}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              Transitions are audit-first and require CSRF token. Implement via POST to the transition endpoint.
            </p>
          </CardContent>
        </Card>
      )}

      {nextStates.length === 0 && (
        <Card className="bg-muted/50">
          <CardContent className="py-4">
            <p className="text-sm text-muted-foreground">No valid transitions from {definition.status} state.</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/** Rendered caption — also the deploy marker (a string literal) proving the
 *  consolidated bundle (not the old standalone page) is what's live. */
export const MARKER_LAYER_FORGE =
  "Layer Forge is now a Forge tab — layer definitions with quality gates and enforcement rules.";

export default function LayersTab() {
  const [selectedDef, setSelectedDef] = useState<LayerDefinition | null>(null);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["layer-forge", "definitions"],
    queryFn: ({ signal }) => api<LayerForgeListResponse>(`${BASE}/definitions`, { signal }),
    refetchInterval: 60_000,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="py-16 flex justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError) {
    const off = error instanceof ApiError && error.status === 404;
    return (
      <div className="max-w-5xl">
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle size={18} /> {off ? "Layer Forge is not available on this build." : "Failed to load layer definitions."}
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-muted-foreground text-sm">{MARKER_LAYER_FORGE}</p>
        {/* Forging a new layer lives in Generator (2026-10-06) — "the one
            place to create something" — next to Skill/Tool/Plugin Forge;
            this tab stays a pure browse surface like Tools/Skills/OS-Skills.
            Phase 3B analytics stayed a standalone panel (own route, own
            charts, no shared List+Detail surface) — both linked here for
            discoverability since neither has a sidebar entry of its own. */}
        <div className="flex items-center gap-3 shrink-0">
          <Link
            to="/app/forge?tab=generator&sub=layer"
            className="text-xs text-primary hover:underline whitespace-nowrap"
          >
            Forge a Layer →
          </Link>
          <Link
            to="/app/layer-forge-analytics"
            className="text-xs text-primary hover:underline whitespace-nowrap"
          >
            View Analytics →
          </Link>
        </div>
      </div>

      {selectedDef ? (
        <DetailView definition={selectedDef} onBack={() => setSelectedDef(null)} />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Definitions</CardTitle>
            <CardDescription>
              {data.count} layer definition{data.count === 1 ? "" : "s"} across all versions and statuses
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ListView definitions={data.items} onSelect={setSelectedDef} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
