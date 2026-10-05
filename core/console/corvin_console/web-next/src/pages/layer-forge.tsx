/**
 * Layer Forge Panel (ADR-2222, ADR-2224 Phase 2–3) — UI for layer definition
 * lifecycle: create via LLM, run quality gates + enforcement, transition through
 * proposed → accepted → deployed.
 *
 * List view: table of all definitions with status, gate count, versions
 * Detail view: manifest, all verdicts (gates, enforcement), transition buttons
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, AlertCircle, ChevronRight } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, ApiError } from "@/lib/api/client";

const BASE = "/api/layer-forge";

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

export interface LayerForgeDetailResponse extends LayerDefinition {
  // Additional fields from the detail endpoint
}

const statusColors: Record<string, string> = {
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
                <Badge variant={statusColors[def.status] as any}>
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
            <Badge variant={statusColors[definition.status] as any}>
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

/** Rendered caption — also the deploy marker (a string literal). */
export const MARKER_LAYER_FORGE = "Layer Forge panel showing layer definitions with quality gates and enforcement rules.";

export function LayerForgePage() {
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
      <div className="max-w-7xl mx-auto p-6">
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
    <div className="max-w-7xl mx-auto p-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold mb-2">Layer Forge</h1>
        <p className="text-muted-foreground text-sm">
          {MARKER_LAYER_FORGE}
        </p>
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
