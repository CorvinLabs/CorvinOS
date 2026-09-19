/**
 * Video Quality Metrics Dashboard — Real-time Quality + Learning Integration
 *
 * Displays per-scene quality scores, optimizer convergence, and feedback metrics
 * from the video production pipeline. Integrated with ADR-0314 learning events.
 *
 * ADR-0695 Phase 2 — Video Quality Metrics UI
 */

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AlertCircle, TrendingUp, CheckCircle, AlertTriangle } from "lucide-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";

const API_BASE = "/v1/console/video";

interface QualityMetrics {
  job_id: string;
  status: string;
  validation: {
    passed: number;
    warned: number;
    failed: number;
  };
  encoding: {
    codec: string;
    resolution: string;
    bitrate: string;
    ffmpeg_preset: string;
  };
  color: {
    input_space: string;
    output_space: string;
  };
  per_scene: Array<{
    scene_id: string;
    validation_status: "pass" | "warn" | "fail";
    validation_confidence: number;
    encoding_codec: string;
    encoding_bitrate: string;
  }>;
}

interface LearningMetrics {
  job_id: string;
  total_feedback_events: number;
  optimizer_iterations: number;
  average_confidence: number;
  convergence_trend: Array<{
    iteration: number;
    confidence: number;
    feedback_count: number;
  }>;
  per_scene_feedback: Array<{
    scene_id: string;
    feedback_score: number;
    feedback_count: number;
  }>;
}

/**
 * Fetch quality metrics for a video job
 */
async function fetchQualityMetrics(jobId: string): Promise<QualityMetrics> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/quality-metrics`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error("Failed to fetch quality metrics");
  return response.json();
}

/**
 * Fetch learning metrics for a video job
 */
async function fetchLearningMetrics(jobId: string): Promise<LearningMetrics> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/learning-metrics`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error("Failed to fetch learning metrics");
  return response.json();
}

/**
 * Summary card for video metadata
 */
function SummaryCard({
  encoding,
  validation,
}: {
  encoding: QualityMetrics["encoding"];
  validation: QualityMetrics["validation"];
}) {
  const total = validation.passed + validation.warned + validation.failed;
  const passRate = ((validation.passed / total) * 100).toFixed(1);

  return (
    <Card className="p-6 bg-gradient-to-r from-blue-50 to-indigo-50">
      <h3 className="text-lg font-semibold mb-4">Video Summary</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Resolution</p>
          <p className="font-medium">{encoding.resolution}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Bitrate</p>
          <p className="font-medium">{encoding.bitrate}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Codec</p>
          <p className="font-medium">{encoding.codec.toUpperCase()}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Validation Pass Rate</p>
          <p className="font-medium text-green-600">{passRate}%</p>
        </div>
      </div>
    </Card>
  );
}

/**
 * Audio Quality per-scene chart
 */
function AudioQualityChart({
  sceneData,
}: {
  sceneData: Array<{
    scene_id: string;
    confidence: number;
  }>;
}) {
  if (sceneData.length === 0) {
    return <div className="h-64 flex items-center text-muted-foreground">No scene data</div>;
  }

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">Per-Scene Audio Quality</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={sceneData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="scene_id" />
          <YAxis domain={[0, 1]} label={{ value: "Confidence", angle: -90, position: "insideLeft" }} />
          <Tooltip formatter={(value) => (value as number).toFixed(3)} />
          <Line
            type="monotone"
            dataKey="confidence"
            stroke="#3b82f6"
            dot={{ fill: "#3b82f6", r: 4 }}
            connectNulls
            name="Audio Quality Score"
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

/**
 * Optimizer convergence chart (confidence over iterations)
 */
function ConvergenceChart({
  convergenceTrend,
}: {
  convergenceTrend: LearningMetrics["convergence_trend"];
}) {
  if (convergenceTrend.length === 0) {
    return <div className="h-64 flex items-center text-muted-foreground">No convergence data</div>;
  }

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">Optimizer Convergence</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={convergenceTrend}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="iteration" label={{ value: "Iteration", position: "insideBottomRight", offset: -5 }} />
          <YAxis domain={[0, 1]} label={{ value: "Confidence", angle: -90, position: "insideLeft" }} />
          <Tooltip formatter={(value) => (typeof value === 'number' ? value.toFixed(3) : value)} />
          <Legend />
          <Line
            type="monotone"
            dataKey="confidence"
            stroke="#8b5cf6"
            dot={{ fill: "#8b5cf6", r: 4 }}
            name="Model Confidence"
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

/**
 * Per-scene validation status table
 */
function PerSceneTable({
  scenes,
}: {
  scenes: QualityMetrics["per_scene"];
}) {
  if (scenes.length === 0) {
    return <div className="p-4 text-center text-muted-foreground">No scene data</div>;
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "pass":
        return "bg-green-100 text-green-800";
      case "warn":
        return "bg-yellow-100 text-yellow-800";
      case "fail":
        return "bg-red-100 text-red-800";
      default:
        return "bg-gray-100 text-gray-800";
    }
  };

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">Per-Scene Validation Details</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="text-left py-2 px-3">Scene</th>
              <th className="text-left py-2 px-3">Status</th>
              <th className="text-left py-2 px-3">Confidence</th>
              <th className="text-left py-2 px-3">Codec</th>
              <th className="text-left py-2 px-3">Bitrate</th>
            </tr>
          </thead>
          <tbody>
            {scenes.map((scene) => (
              <tr key={scene.scene_id} className="border-b hover:bg-muted/50">
                <td className="py-2 px-3 font-mono text-xs">{scene.scene_id}</td>
                <td className="py-2 px-3">
                  <Badge className={getStatusColor(scene.validation_status)}>
                    {scene.validation_status.toUpperCase()}
                  </Badge>
                </td>
                <td className="py-2 px-3">{(scene.validation_confidence * 100).toFixed(0)}%</td>
                <td className="py-2 px-3">{scene.encoding_codec.toUpperCase()}</td>
                <td className="py-2 px-3">{scene.encoding_bitrate}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/**
 * Learning feedback summary
 */
function LearningFeedbackSummary({
  learningMetrics,
}: {
  learningMetrics: LearningMetrics;
}) {
  return (
    <Card className="p-6 bg-gradient-to-r from-purple-50 to-pink-50">
      <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <TrendingUp className="h-5 w-5 text-purple-600" />
        Learning & Optimization
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Feedback Events</p>
          <p className="text-2xl font-bold">{learningMetrics.total_feedback_events}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Optimizer Iterations</p>
          <p className="text-2xl font-bold">{learningMetrics.optimizer_iterations}</p>
        </div>
        <div className="col-span-2">
          <p className="text-xs text-muted-foreground mb-2">Average Confidence</p>
          <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-purple-600"
              style={{ width: `${(learningMetrics.average_confidence || 0) * 100}%` }}
            />
          </div>
          <p className="text-sm font-medium mt-1">
            {((learningMetrics.average_confidence || 0) * 100).toFixed(1)}%
          </p>
        </div>
      </div>
    </Card>
  );
}

/**
 * Main Quality Metrics Panel
 */
export function VideoQualityMetricsPanel() {
  // Get job_id from URL query params
  const searchParams = new URLSearchParams(window.location.search);
  const jobId = searchParams.get("job_id") || "unknown";

  const qualityQuery = useQuery({
    queryKey: ["video-quality", jobId],
    queryFn: () => fetchQualityMetrics(jobId),
    refetchInterval: 5000, // Poll every 5s
  });

  const learningQuery = useQuery({
    queryKey: ["video-learning", jobId],
    queryFn: () => fetchLearningMetrics(jobId),
    refetchInterval: 5000, // Poll every 5s
  });

  const isLoading = qualityQuery.isLoading || learningQuery.isLoading;
  const isError = qualityQuery.isError || learningQuery.isError;

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Video Quality Metrics</h1>
        <p className="text-muted-foreground">
          Real-time quality analysis and optimizer convergence for video job {jobId}
        </p>
      </div>

      {isLoading && (
        <Card className="p-6">
          <p className="text-muted-foreground">Loading quality metrics...</p>
        </Card>
      )}

      {isError && (
        <Card className="p-6 border-red-200 bg-red-50">
          <div className="flex gap-3 items-start">
            <AlertCircle className="h-5 w-5 text-red-600 mt-0.5" />
            <div>
              <p className="font-medium text-red-800">Failed to load metrics</p>
              <p className="text-sm text-red-700">Check that the video job ID is valid and the job has completed.</p>
            </div>
          </div>
        </Card>
      )}

      {qualityQuery.data && (
        <>
          <SummaryCard
            encoding={qualityQuery.data.encoding}
            validation={qualityQuery.data.validation}
          />

          {learningQuery.data && (
            <LearningFeedbackSummary learningMetrics={learningQuery.data} />
          )}

          <AudioQualityChart
            sceneData={qualityQuery.data.per_scene.map((s) => ({
              scene_id: s.scene_id,
              confidence: s.validation_confidence,
            }))}
          />

          {learningQuery.data && (
            <ConvergenceChart
              convergenceTrend={learningQuery.data.convergence_trend}
            />
          )}

          <PerSceneTable scenes={qualityQuery.data.per_scene} />
        </>
      )}
    </div>
  );
}

export default VideoQualityMetricsPanel;
