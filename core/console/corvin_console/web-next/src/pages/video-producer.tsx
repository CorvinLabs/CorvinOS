/**
 * Video Producer Skill 2.0 — Task-Orchestration UI Panel
 *
 * User-centric interface for video generation via natural language tasks.
 * Orchestrates Corvin Skills 2.0 with LLM-generated storyboards.
 */

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Upload, Play, Settings, Trash2, Download } from "lucide-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

const BASE_URL = "/api";

// API client
const videoApi = {
  async createJob(task: string) {
    const response = await fetch(`${BASE_URL}/v1/video/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task }),
    });
    if (!response.ok) throw new Error("Failed to create job");
    return response.json();
  },

  async getJob(jobId: string) {
    const response = await fetch(`${BASE_URL}/v1/video/jobs/${jobId}`);
    if (!response.ok) throw new Error("Failed to get job");
    return response.json();
  },

  async listJobs(limit = 20, offset = 0) {
    const response = await fetch(`${BASE_URL}/v1/video/jobs?limit=${limit}&offset=${offset}`);
    if (!response.ok) throw new Error("Failed to list jobs");
    return response.json();
  },

  async updateSettings(settings: Record<string, any>) {
    const response = await fetch(`${BASE_URL}/v1/video/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    if (!response.ok) throw new Error("Failed to update settings");
    return response.json();
  },

  async getSettings() {
    const response = await fetch(`${BASE_URL}/v1/video/settings`);
    if (!response.ok) throw new Error("Failed to get settings");
    return response.json();
  },
};

// Textarea component
function Textarea({
  value,
  onChange,
  placeholder,
  rows = 3,
  className = "",
}: {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  placeholder: string;
  rows?: number;
  className?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      rows={rows}
      className={`w-full px-3 py-2 border rounded bg-background text-foreground ${className}`}
    />
  );
}

// Status badge helper
function StatusBadge({ status }: { status: string }) {
  const variants: Record<string, string> = {
    pending: "bg-yellow-100 text-yellow-800",
    storyboard_generating: "bg-blue-100 text-blue-800",
    skills_running: "bg-purple-100 text-purple-800",
    complete: "bg-green-100 text-green-800",
    error: "bg-red-100 text-red-800",
  };
  return (
    <span className={`px-2 py-1 rounded text-xs font-medium ${variants[status] || variants.pending}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function VideoProducerPage() {
  const queryClient = useQueryClient();
  const [userTask, setUserTask] = useState("");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [outputFolder, setOutputFolder] = useState("~/.corvin/video-producer/videos");
  const [ttsEngine, setTtsEngine] = useState("azure");
  const [maxDuration, setMaxDuration] = useState(60);

  // Fetch jobs list
  const jobsQuery = useQuery({
    queryKey: ["video-jobs"],
    queryFn: () => videoApi.listJobs(),
    refetchInterval: 5000,
  });

  // Fetch selected job details
  const jobDetailsQuery = useQuery({
    queryKey: ["video-job", selectedJobId],
    queryFn: () => (selectedJobId ? videoApi.getJob(selectedJobId) : null),
    enabled: !!selectedJobId,
    refetchInterval: 3000,
  });

  // Create job mutation
  const createJobMutation = useMutation({
    mutationFn: (task: string) => videoApi.createJob(task),
    onSuccess: (response) => {
      setSelectedJobId(response.job_id);
      setUserTask("");
      jobsQuery.refetch();
    },
  });

  const handleCreateJob = () => {
    if (!userTask.trim()) return;
    createJobMutation.mutate(userTask);
  };

  const handleSaveSettings = async () => {
    await videoApi.updateSettings({
      output_folder: outputFolder,
      tts_engine: ttsEngine,
      max_duration_minutes: maxDuration,
    });
  };

  const selectedJob = jobDetailsQuery.data;

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Video Producer</h1>
        <p className="text-muted-foreground">
          Create videos from natural language tasks. Orchestrates Corvin Skills 2.0 with LLM-generated storyboards.
        </p>
      </div>

      {/* Section 1: Task Input */}
      <Card className="p-6">
        <h2 className="text-xl font-semibold mb-4">Create Video</h2>
        <div className="space-y-4">
          <Textarea
            placeholder="Task: Erstelle ein Video über Corvin's Plugin System mit Screenshots und Erklärungen. Dauer: 5 Minuten."
            value={userTask}
            onChange={(e) => setUserTask(e.target.value)}
            rows={4}
          />
          <Button
            onClick={handleCreateJob}
            disabled={!userTask.trim() || createJobMutation.isPending}
            className="w-full gap-2"
          >
            {createJobMutation.isPending ? "Creating..." : "Start Production"}
          </Button>
          {createJobMutation.isError && (
            <div className="bg-red-100 border border-red-400 text-red-700 px-3 py-2 rounded">
              Failed to create job. Try again.
            </div>
          )}
        </div>
      </Card>

      {/* Section 2: Job Details & Progress */}
      {selectedJob && (
        <Card className="p-6">
          <h2 className="text-xl font-semibold mb-4">Production Status</h2>
          <div className="space-y-4">
            <div>
              <p className="text-sm text-muted-foreground">Status</p>
              <StatusBadge status={selectedJob.status} />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Task</p>
              <p className="text-sm">{selectedJob.task}</p>
            </div>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-xs text-muted-foreground">Created</p>
                <p>{new Date(selectedJob.created_at).toLocaleString()}</p>
              </div>
              {selectedJob.started_at && (
                <div>
                  <p className="text-xs text-muted-foreground">Started</p>
                  <p>{new Date(selectedJob.started_at).toLocaleString()}</p>
                </div>
              )}
            </div>
            {selectedJob.error_message && (
              <div className="bg-red-100 border border-red-400 text-red-700 px-3 py-2 rounded">
                <p className="text-sm font-medium">Error</p>
                <p className="text-sm">{selectedJob.error_message}</p>
              </div>
            )}
            {selectedJob.status === "complete" && (
              <div className="bg-green-100 border border-green-400 text-green-700 px-3 py-2 rounded">
                <p className="text-sm font-medium">✓ Video Ready</p>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Section 3: Video Gallery */}
      <Card className="p-6">
        <h2 className="text-xl font-semibold mb-4">My Videos</h2>
        {jobsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Loading...</p>
        ) : jobsQuery.data?.jobs?.length === 0 ? (
          <p className="text-muted-foreground text-sm">No videos yet. Create one to get started.</p>
        ) : (
          <div className="grid gap-2 max-h-96 overflow-y-auto">
            {jobsQuery.data?.jobs?.map((job: any) => (
              <div
                key={job.id}
                onClick={() => setSelectedJobId(job.id)}
                className={`p-3 border rounded cursor-pointer transition ${
                  selectedJobId === job.id
                    ? "bg-muted border-primary"
                    : "hover:bg-muted/50 border-muted-foreground/20"
                }`}
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{job.task.substring(0, 50)}...</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(job.created_at).toLocaleString()}
                    </p>
                  </div>
                  <StatusBadge status={job.status} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Section 4: Settings */}
      <Card className="p-6">
        <h2 className="text-xl font-semibold mb-4">Settings</h2>
        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Output Folder</label>
            <Input
              value={outputFolder}
              onChange={(e) => setOutputFolder(e.target.value)}
              placeholder="~/.corvin/video-producer/videos or custom path"
              className="mt-1"
            />
          </div>
          <div>
            <label className="text-sm font-medium">TTS Engine</label>
            <select
              value={ttsEngine}
              onChange={(e) => setTtsEngine(e.target.value)}
              className="w-full mt-1 px-3 py-2 border rounded bg-background"
            >
              <option value="azure">Azure Text-to-Speech</option>
              <option value="google">Google TTS</option>
              <option value="local">Local (Hermes)</option>
            </select>
          </div>
          <div>
            <label className="text-sm font-medium">Max Video Duration (minutes)</label>
            <Input
              type="number"
              value={maxDuration}
              onChange={(e) => setMaxDuration(Math.max(0, parseInt(e.target.value) || 0))}
              className="mt-1"
              min="0"
            />
            <p className="text-xs text-muted-foreground mt-1">0 = unlimited</p>
          </div>
          <Button onClick={handleSaveSettings} variant="outline" className="w-full">
            Save Settings
          </Button>
        </div>
      </Card>
    </div>
  );
}

export default VideoProducerPage;
