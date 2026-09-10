/**
 * OS-Skills Overview Panel — Display active skills, learning scores, health status.
 *
 * Phase 5: Console Integration (Dashboard UI)
 * Displays:
 * - List of active skills with current score
 * - Health status (healthy/degraded/error)
 * - Quick metrics (runs_24h, errors_24h)
 * - Click to view detailed metrics
 */

import { api } from "@/lib/api/client";
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, CheckCircle, TrendingUp, Clock, AlertTriangle, Brain } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import SkillsMetricsChart from "./SkillsMetricsChart";

interface Skill {
  id: string;
  version: string;
  enabled: boolean;
  score: number | null;
  runs_24h: number;
  errors_24h: number;
  last_run: string | null;
  status: "healthy" | "degraded" | "error";
}

interface SkillsStatusResponse {
  tenant_id: string;
  skills: Skill[];
  timestamp: string;
  error?: string;
}

// Through the console API client: BASE-prefixed (/v1/console/api/skills/status —
// the bare /api/skills/status 404'd on prod AND the vite dev server) and sent with
// the session cookie. The backend scopes the answer to the SESSION's tenant
// (routes/skills_monitoring.py::get_skills_status); a tenant_id query param is
// never read, so none is sent.
function fetchSkillsStatus(signal?: AbortSignal): Promise<SkillsStatusResponse> {
  return api<SkillsStatusResponse>("/api/skills/status", { signal });
}

const STATUS_VARIANT: Record<string, "ok" | "warn" | "danger"> = {
  healthy: "ok",
  degraded: "warn",
  error: "danger",
};

const StatusBadge = ({ status }: { status: string }) => {
  const config = {
    healthy: { icon: CheckCircle },
    degraded: { icon: AlertTriangle },
    error: { icon: AlertCircle },
  };

  const { icon: Icon } = config[status as keyof typeof config] || config.healthy;

  return (
    <Badge variant={STATUS_VARIANT[status] ?? "outline"} className="gap-1">
      <Icon size={14} />
      {status}
    </Badge>
  );
};

const ScoreBar = ({ score }: { score: number | null }) => {
  if (score === null) return <span className="text-muted-foreground">No data</span>;

  const percentage = Math.round(score * 100);
  const color = score >= 0.8 ? "bg-emerald-500" : score >= 0.5 ? "bg-amber-500" : "bg-destructive";

  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-2 bg-muted rounded-full overflow-hidden">
        <div className={`${color} h-full`} style={{ width: `${percentage}%` }} />
      </div>
      <span className="text-sm font-semibold">{percentage}%</span>
    </div>
  );
};

export const SkillsOverviewPanel: React.FC = () => {
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["skills-status"],
    queryFn: ({ signal }) => fetchSkillsStatus(signal),
    refetchInterval: 5000, // Refresh every 5 seconds
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>OS-Skills Overview</CardTitle>
          <CardDescription>Loading skill metrics...</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">Loading...</div>
        </CardContent>
      </Card>
    );
  }

  if (error || data?.error) {
    return (
      <Card className="border-destructive/40 bg-destructive/10">
        <CardHeader>
          <CardTitle className="text-destructive">OS-Skills Overview</CardTitle>
          <CardDescription className="text-destructive/90">Failed to load skills</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">{error?.message || data?.error}</p>
        </CardContent>
      </Card>
    );
  }

  const skills = data?.skills || [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Brain size={20} />
            OS-Skills Overview
          </CardTitle>
          <CardDescription>
            {skills.length === 0
              ? "No skills active"
              : `${skills.length} skill${skills.length !== 1 ? "s" : ""} running`}
          </CardDescription>
        </CardHeader>

        {skills.length === 0 ? (
          <CardContent>
            <div className="text-center py-8 text-muted-foreground">
              No active skills found. Skills are installed but not yet running.
            </div>
          </CardContent>
        ) : (
          <CardContent>
            <div className="space-y-3">
              {skills.map((skill) => (
                <div
                  key={skill.id}
                  className="p-3 border border-border rounded-lg hover:bg-muted/50 cursor-pointer transition"
                  onClick={() => setSelectedSkill(skill.id)}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <h3 className="font-semibold text-sm">{skill.id}</h3>
                        <Badge variant="outline" className="text-xs">
                          v{skill.version}
                        </Badge>
                        <StatusBadge status={skill.status} />
                      </div>

                      <div className="mb-2">
                        <div className="text-xs text-muted-foreground mb-1">Learning Score</div>
                        <ScoreBar score={skill.score} />
                      </div>

                      <div className="grid grid-cols-3 gap-3 text-xs">
                        <div className="flex items-center gap-1 text-muted-foreground">
                          <TrendingUp size={14} />
                          <span>{skill.runs_24h} runs (24h)</span>
                        </div>
                        {skill.errors_24h > 0 && (
                          <div className="flex items-center gap-1 text-destructive">
                            <AlertCircle size={14} />
                            <span>{skill.errors_24h} errors</span>
                          </div>
                        )}
                        {skill.last_run && (
                          <div className="flex items-center gap-1 text-muted-foreground">
                            <Clock size={14} />
                            <span>Last run: {new Date(skill.last_run).toLocaleTimeString()}</span>
                          </div>
                        )}
                      </div>
                    </div>

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedSkill(skill.id);
                      }}
                    >
                      Details
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        )}
      </Card>

      {selectedSkill && (
        <SkillDetailsModal skillId={selectedSkill} onClose={() => setSelectedSkill(null)} />
      )}
    </div>
  );
};

// Phase 6: Skill Details Modal with Charts
interface SkillDetailsModalProps {
  skillId: string;
  onClose: () => void;
}

// Error boundary for SkillDetailsModal (fixes Issue 2)
class SkillDetailsErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <Card className="border-destructive/40 bg-destructive/10">
          <CardHeader>
            <CardTitle className="text-destructive">Error loading skill metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-destructive">{this.state.error?.message || "Unknown error"}</p>
          </CardContent>
        </Card>
      );
    }

    return this.props.children;
  }
}

const SkillDetailsModal: React.FC<SkillDetailsModalProps> = ({ skillId, onClose }) => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skill-metrics", skillId],
    queryFn: async () => {
      const response = await fetch(`/api/skills/${skillId}/metrics?tenant_id=_default`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    refetchInterval: 10000, // Update every 10s (lower frequency than list)
  });

  return (
    <SkillDetailsErrorBoundary>
      <Card className="border-accent/30 bg-accent/5">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-foreground">Skill Details: {skillId}</CardTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
            >
              ✕
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading && <div className="text-center py-8 text-muted-foreground">Loading metrics...</div>}
          {error && <div className="text-center py-8 text-destructive">Failed to load metrics</div>}
          {data && <SkillsMetricsChart data={data} />}
        </CardContent>
      </Card>
    </SkillDetailsErrorBoundary>
  );
};

export default SkillsOverviewPanel;

// Phase 5.3: Marketplace Integration (minimal MVP)
// TODO: Link to Corvin-Marketplace (ADR-0511)
// For now: show skill source + install status

export interface MarketplaceAction {
  type: "installed" | "available" | "update-available";
  source: "bundled" | "marketplace" | "custom";
}
