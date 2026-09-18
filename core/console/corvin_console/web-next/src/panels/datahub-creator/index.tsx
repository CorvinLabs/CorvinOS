/**
 * DataHub Creator Panel (TRACK I)
 *
 * 6-phase dashboard for project-centric skill management with learning visualization.
 *
 * Critical Path (Novice):     Create → Execute → Metrics → Export
 * Full Path (Power User):    Create → Execute → Metrics → Optimize → Collaborate → Export
 *
 * All phases navigable concurrently from tab interface; Phase 4–5 collapsed by default.
 */

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, CheckCircle2, AlertCircle, TrendingUp } from "lucide-react";

import { Phase1Create } from "./phases/Phase1Create";
import { Phase2Execute } from "./phases/Phase2Execute";
import { Phase3MetricsDashboard } from "./phases/Phase3MetricsDashboard";
import { Phase4OptimizationConfig } from "./phases/Phase4OptimizationConfig";
import { Phase5Collaboration } from "./phases/Phase5Collaboration";
import { Phase6Export } from "./phases/Phase6Export";

interface Project {
  project_id: string;
  name: string;
  description?: string;
  goals: string[];
  status: "created" | "executing" | "learning" | "converged" | "archived";
  current_phase: string;
  selected_skills: string[];
  created_at: string;
  updated_at: string;
  latest_metrics?: {
    avg_confidence: number;
    convergence_status: "in_progress" | "stalled" | "complete";
    skills_improved: number;
  };
}

interface DataHubCreatorPanelProps {
  projectId?: string;
  onProjectCreated?: (projectId: string) => void;
}

export function DataHubCreatorPanel({ projectId, onProjectCreated }: DataHubCreatorPanelProps) {
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("create");

  // Load project on mount or when projectId changes
  useEffect(() => {
    if (projectId) {
      loadProject(projectId);
    } else {
      setLoading(false);
    }
  }, [projectId]);

  async function loadProject(id: string) {
    try {
      setLoading(true);
      const response = await fetch(`/v1/console/datahub/projects/${id}`);
      if (!response.ok) throw new Error("Failed to load project");
      const data = await response.json();
      setProject(data);
      setActiveTab(data.current_phase);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  function handleProjectCreated(projectId: string) {
    loadProject(projectId);
    if (onProjectCreated) onProjectCreated(projectId);
    setActiveTab("execute");
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="animate-spin" />
      </div>
    );
  }

  if (error && !project) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!project) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a New Project</CardTitle>
          <CardDescription>
            Start by creating a project to track your skills and monitor learning progress.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Phase1Create onProjectCreated={handleProjectCreated} />
        </CardContent>
      </Card>
    );
  }

  // Project exists: show 6-phase dashboard
  return (
    <div className="space-y-4">
      {/* Project Header */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>{project.name}</CardTitle>
              <CardDescription>{project.description}</CardDescription>
            </div>
            <div className="flex gap-2">
              <Badge variant={project.status === "converged" ? "ok" : "secondary"}>
                {project.status}
              </Badge>
              {project.latest_metrics && (
                <Badge variant="outline">
                  <TrendingUp className="h-3 w-3 mr-1" />
                  {Math.round(project.latest_metrics.avg_confidence * 100)}%
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* 6-Phase Dashboard Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-6">
          <TabsTrigger value="create" className="text-xs">Phase 1: Create</TabsTrigger>
          <TabsTrigger value="execute" className="text-xs">Phase 2: Execute</TabsTrigger>
          <TabsTrigger value="metrics_dashboard" className="text-xs">Phase 3: Metrics</TabsTrigger>
          <TabsTrigger value="optimization_config" className="text-xs">Phase 4: Optimize</TabsTrigger>
          <TabsTrigger value="collaboration" className="text-xs">Phase 5: Share</TabsTrigger>
          <TabsTrigger value="export" className="text-xs">Phase 6: Export</TabsTrigger>
        </TabsList>

        {/* Phase 1: Create */}
        <TabsContent value="create">
          <Card>
            <CardHeader>
              <CardTitle>Phase 1: Create Project</CardTitle>
              <CardDescription>
                Project: {project.name} · {project.selected_skills.length} skills selected
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Phase1Create projectId={project.project_id} isEdit onProjectCreated={handleProjectCreated} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Phase 2: Execute */}
        <TabsContent value="execute">
          <Card>
            <CardHeader>
              <CardTitle>Phase 2: Execute & Collect Feedback</CardTitle>
              <CardDescription>
                Run your skills and provide feedback to enable learning. Feedback helps the optimizer improve performance.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Phase2Execute
                projectId={project.project_id}
                skills={project.selected_skills}
                onFeedbackSubmitted={() => loadProject(project.project_id)}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Phase 3: Metrics Dashboard */}
        <TabsContent value="metrics_dashboard">
          <Card>
            <CardHeader>
              <CardTitle>Phase 3: Learning Metrics Dashboard</CardTitle>
              <CardDescription>
                Watch your skills converge. Metrics update as feedback is processed by the learning optimizer.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Phase3MetricsDashboard
                projectId={project.project_id}
                metrics={project.latest_metrics}
                isConverged={project.status === "converged"}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Phase 4: Optimization Config (Power Users) */}
        <TabsContent value="optimization_config">
          <Card>
            <CardHeader>
              <CardTitle>Phase 4: Tune Optimizer (Advanced)</CardTitle>
              <CardDescription>
                Fine-tune learning parameters. Default settings work for most users.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Phase4OptimizationConfig projectId={project.project_id} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Phase 5: Collaboration */}
        <TabsContent value="collaboration">
          <Card>
            <CardHeader>
              <CardTitle>Phase 5: Collaborate</CardTitle>
              <CardDescription>
                Share your project with team members to collect additional feedback.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Phase5Collaboration projectId={project.project_id} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Phase 6: Export */}
        <TabsContent value="export">
          <Card>
            <CardHeader>
              <CardTitle>Phase 6: Export & Archive</CardTitle>
              <CardDescription>
                Export your final metrics and archive the project for future reference.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Phase6Export projectId={project.project_id} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Convergence Alert */}
      {project.status === "converged" && (
        <Alert>
          <CheckCircle2 className="h-4 w-4" />
          <AlertDescription>
            🎉 Learning complete! Your skills have converged. Next: review recommendations or export results.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}

export const panelConfig = {
  id: "datahub-creator",
  title: "DataHub Creator",
  icon: "📊",
  description: "6-phase project workspace with skill metrics and learning visualization",
  route: "/app/datahub-creator",
  component: DataHubCreatorPanel,
  requiredFlag: "datahub_creator_enabled",
};
