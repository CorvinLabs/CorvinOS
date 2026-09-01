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

import React, { useEffect, useState } from "react";
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

async function fetchSkillsStatus(tenantId: string = "_default"): Promise<SkillsStatusResponse> {
  const response = await fetch(`/api/skills/status?tenant_id=${tenantId}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

const StatusBadge = ({ status }: { status: string }) => {
  const config = {
    healthy: { bg: "bg-green-100", text: "text-green-800", icon: CheckCircle },
    degraded: { bg: "bg-yellow-100", text: "text-yellow-800", icon: AlertTriangle },
    error: { bg: "bg-red-100", text: "text-red-800", icon: AlertCircle },
  };

  const { bg, text, icon: Icon } = config[status as keyof typeof config] || config.healthy;

  return (
    <div className={`${bg} ${text} px-3 py-1 rounded-full flex items-center gap-1 text-sm font-medium`}>
      <Icon size={14} />
      {status}
    </div>
  );
};

const ScoreBar = ({ score }: { score: number | null }) => {
  if (score === null) return <span className="text-gray-400">No data</span>;

  const percentage = Math.round(score * 100);
  const color = score >= 0.8 ? "bg-green-500" : score >= 0.5 ? "bg-yellow-500" : "bg-red-500";

  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
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
    queryFn: () => fetchSkillsStatus(),
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
          <div className="text-center py-8 text-gray-500">Loading...</div>
        </CardContent>
      </Card>
    );
  }

  if (error || data?.error) {
    return (
      <Card className="border-red-200 bg-red-50">
        <CardHeader>
          <CardTitle className="text-red-900">OS-Skills Overview</CardTitle>
          <CardDescription className="text-red-800">Failed to load skills</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-red-700">{error?.message || data?.error}</p>
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
            <div className="text-center py-8 text-gray-500">
              No active skills found. Skills are installed but not yet running.
            </div>
          </CardContent>
        ) : (
          <CardContent>
            <div className="space-y-3">
              {skills.map((skill) => (
                <div
                  key={skill.id}
                  className="p-3 border rounded-lg hover:bg-gray-50 cursor-pointer transition"
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
                        <div className="text-xs text-gray-600 mb-1">Learning Score</div>
                        <ScoreBar score={skill.score} />
                      </div>

                      <div className="grid grid-cols-3 gap-3 text-xs">
                        <div className="flex items-center gap-1 text-gray-600">
                          <TrendingUp size={14} />
                          <span>{skill.runs_24h} runs (24h)</span>
                        </div>
                        {skill.errors_24h > 0 && (
                          <div className="flex items-center gap-1 text-red-600">
                            <AlertCircle size={14} />
                            <span>{skill.errors_24h} errors</span>
                          </div>
                        )}
                        {skill.last_run && (
                          <div className="flex items-center gap-1 text-gray-600">
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
        <Card className="border-red-200 bg-red-50">
          <CardHeader>
            <CardTitle className="text-red-900">Error loading skill metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-red-800">{this.state.error?.message || "Unknown error"}</p>
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
      <Card className="border-blue-200 bg-blue-50">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-blue-900">Skill Details: {skillId}</CardTitle>
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
          {isLoading && <div className="text-center py-8 text-gray-500">Loading metrics...</div>}
          {error && <div className="text-center py-8 text-red-500">Failed to load metrics</div>}
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

interface MarketplaceAction {
  type: "installed" | "available" | "update-available";
  source: "bundled" | "marketplace" | "custom";
}
