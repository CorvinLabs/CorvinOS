/**
 * Skill-Creator UI Panel — Console Quality subsystem
 *
 * Integrated into LDD page (/app/ldd)
 * Generates reusable skills via 6-phase orchestration
 */

import React, { useState, useEffect } from "react";
import { Loader2, Brain, CheckCircle, AlertCircle, Trash2, Cpu } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

interface GenerationRun {
  run_id: string;
  status: "pending" | "running" | "success" | "failed";
  phase: string;
  progress: number;
  message: string;
  /** Engine that ran the phases: claude_code (Max subscription) | api | local */
  engine?: string;
  /** Canonical phase order, served by the backend so the two cannot drift. */
  phases?: string[];
  /** Raw engine error, shown under the operator-facing message. */
  error?: string | null;
  skill?: SkillInfo;
}

interface SkillInfo {
  name: string;
  purpose: string;
  scope: "assistant" | "project" | "global";
  quality: number;
  iterations: number;
  dependencies: string[];
}

interface GeneratedSkill {
  name: string;
  file: string;
  created_at: string;
}

/**
 * The five phases SkillCreatorOrchestrator.create_skill actually reports.
 * The panel used to label a six-phase pipeline (API-Design, Dialectical,
 * Ideation, …) that no backend emitted, so the header never changed from
 * its hardcoded "API-Design" and a run looked stuck.
 */
const PHASE_LABELS: Record<string, string> = {
  planning: "📋 Planning",
  validation: "🔍 Validation",
  ldd_iteration: "🔁 LDD Iteration",
  review: "🎯 Adversarial Review",
  promotion: "🚀 Promotion",
};

const DEFAULT_PHASES = ["planning", "validation", "ldd_iteration", "review", "promotion"];

const ENGINE_LABELS: Record<string, string> = {
  claude_code: "Claude subscription (Claude Code CLI)",
  api: "Anthropic API key",
  local: "Local templates (no engine)",
};

export const SkillCreatorPanel: React.FC = () => {
  const [userRequest, setUserRequest] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentRun, setCurrentRun] = useState<GenerationRun | null>(null);
  const [generatedSkills, setGeneratedSkills] = useState<GeneratedSkill[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Load existing skills on mount
  useEffect(() => {
    loadGeneratedSkills();
  }, []);

  // Poll generation status
  useEffect(() => {
    if (!currentRun || currentRun.status !== "running") return;

    const interval = setInterval(() => {
      checkGenerationStatus(currentRun.run_id);
    }, 1000);

    return () => clearInterval(interval);
  }, [currentRun]);

  const handleGenerate = async () => {
    if (!userRequest.trim()) {
      setError("Please enter a skill description");
      return;
    }

    setError(null);
    setIsGenerating(true);

    try {
      const response = await fetch("/v1/console/skill-creator/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_request: userRequest,
          async: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`Generation failed: ${response.statusText}`);
      }

      const data = await response.json();
      setCurrentRun({
        run_id: data.run_id,
        status: "running",
        phase: "planning",
        progress: 5,
        engine: data.engine,
        message: "Starting skill generation (Phase 1/5)...",
      });

      setUserRequest("");
      setTimeout(() => checkGenerationStatus(data.run_id), 500);
    } catch (err) {
      setError(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setIsGenerating(false);
    }
  };

  const checkGenerationStatus = async (runId: string) => {
    try {
      const response = await fetch(`/v1/console/skill-creator/status/${runId}`);

      if (!response.ok) throw new Error("Status check failed");

      const data: GenerationRun = await response.json();
      setCurrentRun(data);

      if (data.status === "success" || data.status === "failed") {
        setIsGenerating(false);
        if (data.status === "success") loadGeneratedSkills();
      }
    } catch (err) {
      console.error("Status check error:", err);
    }
  };

  const loadGeneratedSkills = async () => {
    try {
      const response = await fetch("/v1/console/skill-creator/skills");
      if (!response.ok) return;
      const data = await response.json();
      setGeneratedSkills(data.skills || []);
    } catch (err) {
      console.error("Error loading skills:", err);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Brain className="h-5 w-5 text-accent" />
          <CardTitle className="text-base">Skill Creator</CardTitle>
        </div>
        <CardDescription>
          Generate reusable skills using 5-phase LDD orchestration (Planning, Validation, LDD Iteration, Adversarial Review, Promotion) — runs on your Claude subscription via the Claude Code engine, no API key required.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Generation Form */}
        <div className="space-y-3">
          <Label htmlFor="skill-request" className="text-sm font-medium">
            What skill do you want to create?
          </Label>
          <Textarea
            id="skill-request"
            placeholder="e.g., 'Create a skill that validates JSON files and reports errors' or 'erzeuge einen Skill für CSV-Analyse'"
            value={userRequest}
            onChange={(e) => setUserRequest(e.target.value)}
            disabled={isGenerating}
            rows={3}
            className="font-mono text-xs"
          />
          <Button
            onClick={handleGenerate}
            disabled={isGenerating || !userRequest.trim()}
            className="w-full"
          >
            {isGenerating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isGenerating ? "Generating Skill..." : "Generate Skill"}
          </Button>
        </div>

        {/* Error Display */}
        {error && (
          <div className="flex items-start justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <div className="flex items-start gap-3">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setError(null);
                setIsGenerating(false);
                setCurrentRun(null);
              }}
              className="shrink-0"
            >
              Dismiss
            </Button>
          </div>
        )}

        {/* Generation Progress */}
        {currentRun && (
          <div className="space-y-3 rounded-md border border-accent/40 bg-accent/5 p-3">
            <div className="flex items-center justify-between">
              <h3 className="font-medium text-sm">Generation Progress</h3>
              <Badge variant="outline" className="font-mono text-xs">
                {currentRun.status.toUpperCase()}
              </Badge>
            </div>

            <div className="space-y-2 text-xs text-muted-foreground">
              <div className="flex justify-between items-center gap-2">
                <span className="font-mono text-[10px]">Run: {currentRun.run_id.slice(0, 12)}...</span>
                <span className="text-sm font-medium">
                  {PHASE_LABELS[currentRun.phase] || currentRun.phase}
                </span>
              </div>
              {currentRun.engine && (
                <div className="flex items-center gap-2">
                  <Cpu className="h-3 w-3 shrink-0" />
                  <span data-testid="engine-label">
                    Engine: {ENGINE_LABELS[currentRun.engine] || currentRun.engine}
                  </span>
                </div>
              )}
            </div>

            {/* Phase stepper — which of the five phases the run reached */}
            <ol className="flex flex-wrap gap-1" data-testid="phase-stepper">
              {(currentRun.phases || DEFAULT_PHASES).map((phase, idx) => {
                const activeIdx = (currentRun.phases || DEFAULT_PHASES).indexOf(currentRun.phase);
                const done = currentRun.status === "success" || (activeIdx > -1 && idx < activeIdx);
                const active = idx === activeIdx && currentRun.status === "running";
                const failed = idx === activeIdx && currentRun.status === "failed";
                return (
                  <li
                    key={phase}
                    data-phase={phase}
                    data-state={failed ? "failed" : active ? "active" : done ? "done" : "pending"}
                    className={
                      "rounded-sm border px-1.5 py-0.5 text-[10px] " +
                      (failed
                        ? "border-destructive/50 bg-destructive/10 text-destructive"
                        : active
                        ? "border-accent bg-accent/15 text-foreground"
                        : done
                        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                        : "border-border/60 text-muted-foreground")
                    }
                  >
                    {PHASE_LABELS[phase] || phase}
                  </li>
                );
              })}
            </ol>

            {/* Progress Bar */}
            <div className="h-2 w-full overflow-hidden rounded-full border border-border/60 bg-muted/30">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.min(currentRun.progress, 100)}%` }}
              />
            </div>

            <p className="text-xs text-muted-foreground">{currentRun.message}</p>

            {/* Success Details */}
            {currentRun.status === "success" && currentRun.skill && (
              <div className="mt-3 space-y-2 rounded-sm border border-emerald-500/40 bg-emerald-500/5 p-2">
                <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
                  <CheckCircle className="h-4 w-4" />
                  <span className="text-sm font-medium">Skill Generated Successfully</span>
                </div>

                <div className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span className="font-mono text-muted-foreground">Name:</span>
                    <code className="text-foreground">{currentRun.skill.name}</code>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-mono text-muted-foreground">Purpose:</span>
                    <span className="text-foreground">{currentRun.skill.purpose}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-mono text-muted-foreground">Scope:</span>
                    <Badge variant="secondary" className="h-5 text-[10px]">
                      {currentRun.skill.scope}
                    </Badge>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-mono text-muted-foreground">Quality:</span>
                    <Badge className="h-5 text-[10px]">
                      {(currentRun.skill.quality * 100).toFixed(0)}%
                    </Badge>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-mono text-muted-foreground">Iterations:</span>
                    <span className="text-foreground">{currentRun.skill.iterations}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Failed Status */}
            {currentRun.status === "failed" && (
              <div
                className="space-y-1 rounded-sm border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive"
                data-testid="generation-error"
              >
                <div>❌ {currentRun.message}</div>
                {currentRun.error && currentRun.error !== currentRun.message && (
                  <details>
                    <summary className="cursor-pointer opacity-80">Engine detail</summary>
                    <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px] opacity-90">
                      {currentRun.error}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>
        )}

        {/* Generated Skills List */}
        <div className="space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            Generated Skills
            <Badge variant="secondary" className="h-5 text-[10px]">
              {generatedSkills.length}
            </Badge>
          </h3>

          {generatedSkills.length === 0 ? (
            <p className="rounded-md border border-border/60 bg-muted/30 p-3 text-xs text-muted-foreground">
              No skills generated yet. Create your first skill above!
            </p>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {generatedSkills.map((skill) => (
                <div
                  key={skill.name}
                  className="flex items-center justify-between rounded-md border border-border/60 bg-card/40 p-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-xs font-medium">{skill.name}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {new Date(skill.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button size="sm" variant="outline" className="h-6 px-2 text-xs">
                      View
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

export default SkillCreatorPanel;
