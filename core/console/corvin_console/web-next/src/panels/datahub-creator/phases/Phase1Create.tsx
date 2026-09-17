/**
 * Phase 1: Create Project
 *
 * User specifies project name, goals, and skills to track.
 */

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MultiSelect } from "@/components/ui/multi-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, AlertCircle } from "lucide-react";

interface Phase1CreateProps {
  projectId?: string;
  isEdit?: boolean;
  onProjectCreated: (projectId: string) => void;
}

const AVAILABLE_SKILLS = [
  { id: "os.delegation_router", label: "Delegation Router" },
  { id: "os.context_adapter", label: "Context Adapter" },
  { id: "os.workflow_optimizer", label: "Workflow Optimizer" },
  { id: "os.security_orchestrator", label: "Security Orchestrator" },
  { id: "os.flow_guard", label: "Flow Guard" },
];

export function Phase1Create({ projectId, isEdit, onProjectCreated }: Phase1CreateProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [goalInput, setGoalInput] = useState("");

  async function handleCreate() {
    if (!name.trim()) {
      setError("Project name is required");
      return;
    }

    if (selectedSkills.length === 0) {
      setError("Please select at least one skill");
      return;
    }

    try {
      setLoading(true);
      setError(null);

      const response = await fetch("/v1/console/datahub/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || undefined,
          goals: goals,
          selected_skills: selectedSkills,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to create project");
      }

      const data = await response.json();
      onProjectCreated(data.project_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  function addGoal() {
    if (goalInput.trim()) {
      setGoals([...goals, goalInput.trim()]);
      setGoalInput("");
    }
  }

  function removeGoal(index: number) {
    setGoals(goals.filter((_, i) => i !== index));
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-2">
        <Label htmlFor="project-name">Project Name *</Label>
        <Input
          id="project-name"
          placeholder="e.g., Skill Generation Sprint Q1 2026"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={loading}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="description">Description</Label>
        <Textarea
          id="description"
          placeholder="What are you trying to achieve with this project?"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={loading}
          rows={3}
        />
      </div>

      <div className="space-y-2">
        <Label>Skills to Track *</Label>
        <MultiSelect
          options={AVAILABLE_SKILLS}
          selected={selectedSkills}
          onChange={setSelectedSkills}
          disabled={loading}
          placeholder="Select skills to monitor..."
        />
      </div>

      <div className="space-y-2">
        <Label>Success Criteria / Goals</Label>
        <div className="space-y-2">
          <div className="flex gap-2">
            <Input
              placeholder="Enter a goal (e.g., 'Improve skill confidence by 20%')"
              value={goalInput}
              onChange={(e) => setGoalInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addGoal()}
              disabled={loading}
            />
            <Button onClick={addGoal} disabled={loading || !goalInput.trim()}>
              Add
            </Button>
          </div>
          {goals.length > 0 && (
            <div className="space-y-1">
              {goals.map((goal, i) => (
                <div key={i} className="flex items-center justify-between p-2 bg-muted rounded">
                  <span className="text-sm">{goal}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeGoal(i)}
                    disabled={loading}
                  >
                    ✕
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex gap-2">
        <Button onClick={handleCreate} disabled={loading}>
          {loading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {isEdit ? "Update Project" : "Create Project"}
        </Button>
      </div>
    </div>
  );
}
