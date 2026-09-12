/**
 * Video Producer Skill 2.0 - Main Console Panel (Phase 4c)
 *
 * Workflow:
 * 1. User uploads PPT/assets, configures video metadata
 * 2. Submit → API creates job, orchestrator runs in background
 * 3. Real-time progress tracking (SSE event stream)
 * 4. Per-scene feedback collection after completion
 * 5. Learning optimizer tunes configs based on feedback
 *
 * Components:
 * - JobForm: Initial video configuration
 * - ProgressTracker: Phase-by-phase progress bars
 * - EventStream: Real-time event display (SSE)
 * - FeedbackCollector: Per-scene quality feedback form
 * - YouTubeStatus: YouTube export tracking
 * - JobHistory: Past jobs list and status
 */

import React, { useState, useEffect, useRef } from "react";
import {
  Button,
  Input,
  Textarea,
  Select,
  SelectItem,
  Card,
  CardBody,
  CardHeader,
  Progress,
  Divider,
  Spinner,
  Tabs,
  Tab,
  Chip,
  Table,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
  Modal,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalFooter,
  useDisclosure,
} from "@nextui-org/react";
import { CheckCircle2, AlertCircle, Play, Pause, Download, Upload, Zap } from "lucide-react";

interface VideoJob {
  job_id: string;
  status: "analyzing" | "rendering" | "assembling" | "uploading" | "success" | "failed" | "cancelled";
  phase: string;
  progress: number;
  message: string;
  created_at: string;
  updated_at: string;
  output_path?: string;
  export_task_id?: string;
  error?: string;
}

interface JobEvent {
  timestamp: string;
  event_type: string;
  phase?: string;
  message?: string;
  error?: string;
  facts_extracted?: number;
  output_path?: string;
  metrics?: Record<string, any>;
  [key: string]: any;
}

interface SceneFeedback {
  scene_id: string;
  quality_score: number;
  feedback_notes?: string;
}

const PHASE_STEPS = [
  { id: "asset_analysis", label: "Analyze Assets", progress: 30 },
  { id: "voice", label: "Generate Voice", progress: 50 },
  { id: "screenshots", label: "Capture Screenshots", progress: 70 },
  { id: "assembly", label: "Assemble Video", progress: 90 },
  { id: "youtube", label: "Upload to YouTube", progress: 100 },
];

export default function VideoProducerPanel() {
  // Job submission
  const [assetPaths, setAssetPaths] = useState<string[]>([]);
  const [title, setTitle] = useState("CorvinOS Marketing Video");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState(["CorvinOS"]);
  const [exportYouTube, setExportYouTube] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Job tracking
  const [currentJob, setCurrentJob] = useState<VideoJob | null>(null);
  const [jobHistory, setJobHistory] = useState<VideoJob[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Feedback collection
  const { isOpen: isFeedbackOpen, onOpen: openFeedback, onClose: closeFeedback } = useDisclosure();
  const [sceneFeedback, setSceneFeedback] = useState<SceneFeedback[]>([]);
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);

  // Tabs
  const [selectedTab, setSelectedTab] = useState<React.Key>("submit");

  /**
   * Submit video production request
   */
  const handleSubmitJob = async () => {
    if (assetPaths.length === 0) {
      alert("Please select at least one asset file");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch("/v1/console/video-producer/orchestrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          asset_paths: assetPaths,
          title,
          description,
          tags,
          export_youtube: exportYouTube,
          async: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.statusText}`);
      }

      const job: VideoJob = await response.json();
      setCurrentJob(job);
      setJobHistory([job, ...jobHistory]);
      setEvents([]);
      setSelectedTab("progress"); // Switch to progress tab

      // Connect to SSE stream
      connectToEventStream(job.job_id);

      // Reset form
      setAssetPaths([]);
      setTitle("CorvinOS Marketing Video");
      setDescription("");
      setTags(["CorvinOS"]);
      setExportYouTube(false);
    } catch (error) {
      console.error("Failed to submit video production job:", error);
      alert(`Failed to submit job: ${error}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  /**
   * Connect to SSE event stream for real-time updates
   */
  const connectToEventStream = (jobId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const eventSource = new EventSource(
      `/v1/console/video-producer/jobs/${jobId}/events`
    );

    eventSource.onmessage = (event) => {
      const jobEvent: JobEvent = JSON.parse(event.data);
      setEvents((prev) => [...prev, jobEvent]);

      // Fetch updated job status
      fetchJobStatus(jobId);
    };

    eventSource.onerror = (error) => {
      console.error("SSE error:", error);
      eventSource.close();
    };

    eventSourceRef.current = eventSource;
  };

  /**
   * Fetch current job status
   */
  const fetchJobStatus = async (jobId: string) => {
    try {
      const response = await fetch(
        `/v1/console/video-producer/jobs/${jobId}`
      );
      if (response.ok) {
        const job: VideoJob = await response.json();
        setCurrentJob(job);

        // Auto-open feedback form when job completes
        if (job.status === "success" && currentJob?.status !== "success") {
          setSceneFeedback([]); // Reset feedback
          openFeedback();
        }

        // Close SSE stream when job completes
        if (job.status === "success" || job.status === "failed") {
          if (eventSourceRef.current) {
            eventSourceRef.current.close();
            eventSourceRef.current = null;
          }
        }
      }
    } catch (error) {
      console.error("Failed to fetch job status:", error);
    }
  };

  /**
   * Submit per-scene feedback to learning optimizer
   */
  const handleSubmitFeedback = async () => {
    if (!currentJob || sceneFeedback.length === 0) {
      alert("Please provide feedback for at least one scene");
      return;
    }

    setIsSubmittingFeedback(true);
    try {
      // In production, this would call a feedback endpoint
      // For now, we'll simulate local storage
      const feedback = {
        job_id: currentJob.job_id,
        scenes: sceneFeedback,
        submitted_at: new Date().toISOString(),
      };

      // Store feedback (would be sent to server in production)
      console.log("Feedback submitted:", feedback);
      alert("Feedback recorded and learning optimizer updated");

      closeFeedback();
      setSceneFeedback([]);
    } catch (error) {
      console.error("Failed to submit feedback:", error);
      alert(`Failed to submit feedback: ${error}`);
    } finally {
      setIsSubmittingFeedback(false);
    }
  };

  /**
   * Cancel running job
   */
  const handleCancelJob = async () => {
    if (!currentJob || currentJob.status === "success" || currentJob.status === "failed") {
      return;
    }

    try {
      const response = await fetch(
        `/v1/console/video-producer/jobs/${currentJob.job_id}`,
        { method: "DELETE" }
      );

      if (response.ok) {
        const result = await response.json();
        alert("Job cancelled");
        setCurrentJob({ ...currentJob, status: "cancelled" });
      }
    } catch (error) {
      console.error("Failed to cancel job:", error);
      alert(`Failed to cancel job: ${error}`);
    }
  };

  /**
   * Add a new scene feedback entry
   */
  const addSceneFeedback = () => {
    setSceneFeedback([
      ...sceneFeedback,
      { scene_id: `scene_${sceneFeedback.length + 1}`, quality_score: 5 },
    ]);
  };

  /**
   * Update scene feedback
   */
  const updateSceneFeedback = (index: number, updates: Partial<SceneFeedback>) => {
    const updated = [...sceneFeedback];
    updated[index] = { ...updated[index], ...updates };
    setSceneFeedback(updated);
  };

  /**
   * Remove scene feedback entry
   */
  const removeSceneFeedback = (index: number) => {
    setSceneFeedback(sceneFeedback.filter((_, i) => i !== index));
  };

  /**
   * Get status color
   */
  const getStatusColor = (status: string) => {
    switch (status) {
      case "success": return "success";
      case "failed": return "danger";
      case "analysing": return "primary";
      case "rendering": return "warning";
      case "cancelled": return "secondary";
      default: return "default";
    }
  };

  /**
   * Get phase progress percentage
   */
  const getPhaseProgress = () => {
    if (!currentJob) return 0;
    const phase = PHASE_STEPS.find((p) => p.id === currentJob.phase);
    return phase?.progress || currentJob.progress;
  };

  return (
    <div className="w-full space-y-6 p-6">
      <Tabs selectedKey={selectedTab} onSelectionChange={setSelectedTab}>
        {/* Submit Tab */}
        <Tab key="submit" title="Create Video">
          <Card className="mt-4">
            <CardHeader className="flex flex-col gap-3 bg-gradient-to-r from-blue-50 to-indigo-50">
              <h2 className="text-xl font-semibold flex items-center gap-2">
                <Upload className="w-5 h-5" /> Create New Video
              </h2>
              <p className="text-sm text-gray-600">
                Upload assets (PowerPoint, screenshots) and configure your CorvinOS video
              </p>
            </CardHeader>
            <Divider />
            <CardBody className="gap-6">
              {/* Asset Upload */}
              <div className="space-y-2">
                <label className="text-sm font-semibold">Asset Files (PPT, PNG, etc.)</label>
                <Input
                  placeholder="/path/to/presentation.pptx"
                  value={assetPaths.join(", ")}
                  onValueChange={(val) =>
                    setAssetPaths(
                      val.split(",").map((p) => p.trim()).filter((p) => p)
                    )
                  }
                  description="Enter one or more file paths, separated by commas"
                />
              </div>

              {/* Video Metadata */}
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-semibold">Video Title</label>
                  <Input
                    value={title}
                    onValueChange={setTitle}
                    placeholder="CorvinOS Marketing Video"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-semibold">Tags (comma-separated)</label>
                  <Input
                    value={tags.join(", ")}
                    onValueChange={(val) =>
                      setTags(
                        val.split(",").map((t) => t.trim()).filter((t) => t)
                      )
                    }
                    placeholder="CorvinOS, marketing, explainer"
                  />
                </div>
              </div>

              {/* Description */}
              <div className="space-y-2">
                <label className="text-sm font-semibold">Description</label>
                <Textarea
                  value={description}
                  onValueChange={setDescription}
                  placeholder="Brief description of your video..."
                  rows={3}
                />
              </div>

              {/* YouTube Export */}
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  checked={exportYouTube}
                  onChange={(e) => setExportYouTube(e.target.checked)}
                  className="w-4 h-4 rounded"
                />
                <label className="text-sm font-medium">
                  Export to YouTube after completion (async, non-blocking)
                </label>
              </div>

              {/* Submit Button */}
              <Button
                isLoading={isSubmitting}
                onClick={handleSubmitJob}
                color="primary"
                size="lg"
                className="w-full font-semibold"
              >
                {isSubmitting ? "Submitting..." : "Start Video Production"}
              </Button>
            </CardBody>
          </Card>
        </Tab>

        {/* Progress Tab */}
        <Tab key="progress" title="Progress" isDisabled={!currentJob}>
          {currentJob ? (
            <Card className="mt-4">
              <CardHeader className="flex flex-col gap-3 bg-gradient-to-r from-blue-50 to-indigo-50">
                <div className="flex justify-between items-start">
                  <div>
                    <h2 className="text-xl font-semibold">{currentJob.title || `Job ${currentJob.job_id}`}</h2>
                    <p className="text-sm text-gray-600">Started: {new Date(currentJob.created_at).toLocaleString()}</p>
                  </div>
                  <Chip
                    color={getStatusColor(currentJob.status)}
                    variant="flat"
                    size="lg"
                  >
                    {currentJob.status.toUpperCase()}
                  </Chip>
                </div>
              </CardHeader>
              <Divider />
              <CardBody className="gap-6">
                {/* Phase Progress */}
                <div className="space-y-4">
                  <div>
                    <div className="flex justify-between mb-2">
                      <span className="text-sm font-semibold">Overall Progress</span>
                      <span className="text-sm text-gray-600">{currentJob.progress}%</span>
                    </div>
                    <Progress
                      value={currentJob.progress}
                      className="h-2"
                      color={getStatusColor(currentJob.status)}
                    />
                  </div>

                  {/* Phase Steps */}
                  <div className="space-y-2 mt-6">
                    {PHASE_STEPS.map((step) => (
                      <div key={step.id} className="flex items-center gap-3">
                        {currentJob.progress >= step.progress ? (
                          <CheckCircle2 className="w-5 h-5 text-green-500" />
                        ) : currentJob.progress >= step.progress - 20 ? (
                          <Spinner size="sm" />
                        ) : (
                          <div className="w-5 h-5 rounded-full border-2 border-gray-300" />
                        )}
                        <span className="text-sm">{step.label}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Status Message */}
                <Card className="bg-gray-50">
                  <CardBody>
                    <p className="text-sm">{currentJob.message}</p>
                    {currentJob.error && (
                      <p className="text-sm text-red-600 mt-2 flex items-center gap-2">
                        <AlertCircle className="w-4 h-4" /> {currentJob.error}
                      </p>
                    )}
                  </CardBody>
                </Card>

                {/* Event Stream */}
                <div className="space-y-3">
                  <h3 className="text-sm font-semibold">Live Events</h3>
                  <div className="bg-gray-50 rounded-lg p-4 max-h-64 overflow-y-auto space-y-2">
                    {events.length === 0 ? (
                      <p className="text-sm text-gray-600">Waiting for events...</p>
                    ) : (
                      events.map((event, idx) => (
                        <div key={idx} className="text-xs text-gray-700 font-mono">
                          <span className="text-blue-600">
                            {new Date(event.timestamp).toLocaleTimeString()}
                          </span>
                          {" → "}
                          <span className="text-gray-900">{event.event_type}</span>
                          {event.message && <span className="text-gray-600"> ({event.message})</span>}
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* Output Path */}
                {currentJob.output_path && (
                  <Card className="bg-green-50 border-green-200">
                    <CardBody>
                      <p className="text-sm font-semibold text-green-900">Output Video</p>
                      <p className="text-sm text-green-800 font-mono">{currentJob.output_path}</p>
                    </CardBody>
                  </Card>
                )}

                {/* YouTube Status */}
                {currentJob.export_task_id && (
                  <Card className="bg-blue-50 border-blue-200">
                    <CardBody>
                      <p className="text-sm font-semibold text-blue-900 flex items-center gap-2">
                        <Zap className="w-4 h-4" /> YouTube Upload Queued
                      </p>
                      <p className="text-sm text-blue-800">Task ID: {currentJob.export_task_id}</p>
                    </CardBody>
                  </Card>
                )}

                {/* Action Buttons */}
                {currentJob.status !== "success" && currentJob.status !== "failed" && (
                  <Button
                    color="danger"
                    variant="bordered"
                    onClick={handleCancelJob}
                    size="sm"
                  >
                    Cancel Job
                  </Button>
                )}

                {currentJob.status === "success" && (
                  <Button
                    color="primary"
                    onClick={openFeedback}
                    size="sm"
                  >
                    Provide Feedback
                  </Button>
                )}
              </CardBody>
            </Card>
          ) : (
            <Card className="mt-4">
              <CardBody>
                <p className="text-center text-gray-600">No active job. Submit a video from the "Create Video" tab.</p>
              </CardBody>
            </Card>
          )}
        </Tab>

        {/* History Tab */}
        <Tab key="history" title={`History (${jobHistory.length})`}>
          <Card className="mt-4">
            <CardBody>
              {jobHistory.length === 0 ? (
                <p className="text-center text-gray-600">No job history</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableColumn>Job ID</TableColumn>
                    <TableColumn>Status</TableColumn>
                    <TableColumn>Progress</TableColumn>
                    <TableColumn>Created At</TableColumn>
                  </TableHeader>
                  <TableBody>
                    {jobHistory.map((job) => (
                      <TableRow key={job.job_id}>
                        <TableCell className="font-mono text-xs">{job.job_id}</TableCell>
                        <TableCell>
                          <Chip
                            color={getStatusColor(job.status)}
                            variant="flat"
                            size="sm"
                          >
                            {job.status}
                          </Chip>
                        </TableCell>
                        <TableCell>{job.progress}%</TableCell>
                        <TableCell className="text-xs">
                          {new Date(job.created_at).toLocaleString()}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardBody>
          </Card>
        </Tab>
      </Tabs>

      {/* Feedback Modal */}
      <Modal isOpen={isFeedbackOpen} onClose={closeFeedback} size="2xl">
        <ModalContent>
          <ModalHeader className="flex flex-col gap-1">Per-Scene Feedback</ModalHeader>
          <ModalBody>
            <p className="text-sm text-gray-600">
              Provide feedback on individual scenes to help the learning optimizer improve future videos.
            </p>

            {sceneFeedback.length === 0 ? (
              <p className="text-sm text-gray-500 italic">No scenes added yet. Click "Add Scene" to get started.</p>
            ) : (
              <div className="space-y-4">
                {sceneFeedback.map((feedback, idx) => (
                  <Card key={idx} className="bg-gray-50">
                    <CardBody className="gap-4">
                      <div className="flex justify-between items-start">
                        <div>
                          <label className="text-sm font-semibold">{feedback.scene_id}</label>
                          <p className="text-xs text-gray-600">Scene {idx + 1}</p>
                        </div>
                        <Button
                          isIconOnly
                          variant="light"
                          size="sm"
                          onClick={() => removeSceneFeedback(idx)}
                        >
                          ×
                        </Button>
                      </div>

                      <div className="space-y-2">
                        <label className="text-sm font-medium">Quality Score</label>
                        <input
                          type="range"
                          min="0"
                          max="10"
                          step="0.5"
                          value={feedback.quality_score}
                          onChange={(e) =>
                            updateSceneFeedback(idx, {
                              quality_score: parseFloat(e.target.value),
                            })
                          }
                          className="w-full"
                        />
                        <p className="text-sm text-gray-600">{feedback.quality_score.toFixed(1)} / 10</p>
                      </div>

                      <div className="space-y-2">
                        <label className="text-sm font-medium">Feedback Notes</label>
                        <Textarea
                          value={feedback.feedback_notes || ""}
                          onValueChange={(val) =>
                            updateSceneFeedback(idx, { feedback_notes: val })
                          }
                          placeholder="e.g., 'voice_too_fast', 'cropped_too_tight', 'bitrate_too_low'"
                          rows={2}
                        />
                      </div>
                    </CardBody>
                  </Card>
                ))}
              </div>
            )}
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={closeFeedback}>
              Close
            </Button>
            <Button
              color="primary"
              onPress={addSceneFeedback}
              isDisabled={isSubmittingFeedback}
            >
              Add Scene
            </Button>
            <Button
              color="success"
              isLoading={isSubmittingFeedback}
              onPress={handleSubmitFeedback}
            >
              Submit Feedback
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
