/**
 * Phase 2: Execute & Collect Feedback
 *
 * User runs skills and provides inline feedback.
 * Feedback is immediately sent to Track B's outcome sink for optimizer training.
 */

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";

interface Phase2ExecuteProps {
  projectId: string;
  skills: string[];
  onFeedbackSubmitted: () => void;
}

interface SkillExecutionRecord {
  skill_id: string;
  execution_id: string;
  timestamp: string;
  output?: string;
  latency_ms: number;
  error?: string;
}

export function Phase2Execute({ projectId, skills, onFeedbackSubmitted }: Phase2ExecuteProps) {
  const [executions, setExecutions] = useState<SkillExecutionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<Record<string, {
    outcome?: "success" | "partial" | "failure";
    rating?: number;
    comment?: string;
  }>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function simulateSkillExecution(skillId: string) {
    try {
      setLoading(true);
      setError(null);

      // Simulate skill execution
      // In production, this would trigger the real skill
      const execution: SkillExecutionRecord = {
        skill_id: skillId,
        execution_id: `exec-${Date.now()}`,
        timestamp: new Date().toISOString(),
        output: `Skill ${skillId} executed successfully`,
        latency_ms: Math.random() * 500 + 50,
      };

      setExecutions([execution, ...executions]);
      setSuccess(`Skill "${skillId}" executed`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Execution failed");
    } finally {
      setLoading(false);
    }
  }

  async function submitFeedback(executionId: string, skillId: string) {
    try {
      setSubmitting(true);
      setError(null);

      const feedbackData = feedback[executionId] || {};

      const response = await fetch(`/v1/console/datahub/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          feedback: {
            skill_id: skillId,
            feedback_type: "outcome",
            signal: {
              success: feedbackData.outcome === "success",
              rating: feedbackData.rating || 3,
              outcome: feedbackData.outcome,
              comment: feedbackData.comment,
            },
          },
        }),
      });

      if (!response.ok) throw new Error("Failed to submit feedback");

      setSuccess("Feedback recorded! Optimizer is processing...");
      setFeedback((prev) => {
        const updated = { ...prev };
        delete updated[executionId];
        return updated;
      });

      // Notify parent
      setTimeout(onFeedbackSubmitted, 500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {success && (
        <Alert>
          <CheckCircle2 className="h-4 w-4" />
          <AlertDescription>{success}</AlertDescription>
        </Alert>
      )}

      {/* Run Skills */}
      <div className="space-y-4">
        <h3 className="font-semibold">Step 1: Run Your Skills</h3>
        <div className="grid grid-cols-1 gap-2">
          {skills.map((skillId) => (
            <Button
              key={skillId}
              onClick={() => simulateSkillExecution(skillId)}
              disabled={loading}
              variant="outline"
              className="justify-start"
            >
              {loading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Execute: {skillId}
            </Button>
          ))}
        </div>
      </div>

      {/* Feedback Collection */}
      {executions.length > 0 && (
        <div className="space-y-4">
          <h3 className="font-semibold">Step 2: Provide Feedback</h3>
          <div className="space-y-4">
            {executions.map((exec, idx) => (
              <Card key={exec.execution_id} className="p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium">{exec.skill_id}</p>
                    <p className="text-xs text-muted-foreground">{exec.execution_id}</p>
                  </div>
                  <Badge variant="outline">{exec.latency_ms.toFixed(0)}ms</Badge>
                </div>

                {exec.output && (
                  <div className="bg-muted p-2 rounded text-sm font-mono">
                    {exec.output}
                  </div>
                )}

                <div className="space-y-3">
                  <div>
                    <Label>Was this result successful?</Label>
                    <RadioGroup
                      value={feedback[exec.execution_id]?.outcome || ""}
                      onValueChange={(value) =>
                        setFeedback((prev) => ({
                          ...prev,
                          [exec.execution_id]: {
                            ...prev[exec.execution_id],
                            outcome: value as "success" | "partial" | "failure",
                          },
                        }))
                      }
                    >
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem value="success" id={`success-${idx}`} />
                        <Label htmlFor={`success-${idx}`}>✓ Success</Label>
                      </div>
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem value="partial" id={`partial-${idx}`} />
                        <Label htmlFor={`partial-${idx}`}>~ Partial</Label>
                      </div>
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem value="failure" id={`failure-${idx}`} />
                        <Label htmlFor={`failure-${idx}`}>✗ Failure</Label>
                      </div>
                    </RadioGroup>
                  </div>

                  <div>
                    <Label htmlFor={`rating-${idx}`}>Quality Rating (1–5)</Label>
                    <input
                      id={`rating-${idx}`}
                      type="range"
                      min="1"
                      max="5"
                      value={feedback[exec.execution_id]?.rating || 3}
                      onChange={(e) =>
                        setFeedback((prev) => ({
                          ...prev,
                          [exec.execution_id]: {
                            ...prev[exec.execution_id],
                            rating: parseInt(e.target.value),
                          },
                        }))
                      }
                      className="w-full"
                    />
                    <div className="flex justify-between text-xs text-muted-foreground mt-1">
                      <span>Poor</span>
                      <span className="font-semibold text-foreground">
                        {feedback[exec.execution_id]?.rating || 3} / 5
                      </span>
                      <span>Excellent</span>
                    </div>
                  </div>

                  <div>
                    <Label htmlFor={`comment-${idx}`}>Comments (optional)</Label>
                    <Textarea
                      id={`comment-${idx}`}
                      placeholder="Any observations? This helps the optimizer learn better."
                      value={feedback[exec.execution_id]?.comment || ""}
                      onChange={(e) =>
                        setFeedback((prev) => ({
                          ...prev,
                          [exec.execution_id]: {
                            ...prev[exec.execution_id],
                            comment: e.target.value,
                          },
                        }))
                      }
                      rows={2}
                    />
                  </div>

                  <Button
                    onClick={() => submitFeedback(exec.execution_id, exec.skill_id)}
                    disabled={submitting || !feedback[exec.execution_id]?.outcome}
                  >
                    {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                    Submit Feedback
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {executions.length === 0 && (
        <Alert>
          <AlertDescription>
            No executions yet. Click a skill button above to run it and start collecting feedback.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
