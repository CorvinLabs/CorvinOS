/**
 * Phase 5 Stream 1: Inspector Panel Component
 *
 * Features:
 * - Media upload (audio/video)
 * - Real-time analysis status
 * - Results display + confidence score
 * - Quality feedback form (1-5 stars)
 * - Trend chart (7-day rolling avg)
 * - Analysis history table (last 100)
 */

import React, { useState, useEffect, useCallback } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface AnalysisResult {
  media_id: string;
  pipeline_id: string;
  overall_confidence: number;
  audio_result?: {
    quality: string;
    confidence_score: number;
    snr: number;
  };
  video_result?: {
    quality: string;
    confidence_score: number;
    sharpness: number;
  };
  timestamp: string;
}

interface TrendData {
  period_days: number;
  analyses: number;
  average_confidence: number;
  trend: string;
  samples: Array<{ media_id: string; confidence: number; timestamp: string }>;
}

export default function InspectorPanel() {
  const [activeTab, setActiveTab] = useState("upload");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(
    null
  );
  const [history, setHistory] = useState<AnalysisResult[]>([]);
  const [trends, setTrends] = useState<TrendData | null>(null);
  const [feedbackScore, setFeedbackScore] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Stream 1: Upload and analyze
  const handleAnalyze = useCallback(async () => {
    if (!audioFile && !videoFile) {
      setError("Please select at least one file");
      return;
    }

    setIsAnalyzing(true);
    setError(null);

    try {
      const response = await fetch("/v1/inspection/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          media_id: `media_${Date.now()}`,
          audio_path: audioFile?.name,
          video_path: videoFile?.name,
        }),
      });

      if (!response.ok) throw new Error("Analysis failed");

      const result = await response.json();
      setAnalysisResult(result);
      setHistory((prev) => [result, ...prev].slice(0, 100));
      setActiveTab("results");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setIsAnalyzing(false);
    }
  }, [audioFile, videoFile]);

  // Load trends on mount
  useEffect(() => {
    const loadTrends = async () => {
      try {
        const response = await fetch("/v1/inspection/trends?days=7");
        if (response.ok) {
          setTrends(await response.json());
        }
      } catch (err) {
        console.error("Failed to load trends:", err);
      }
    };

    loadTrends();
    const interval = setInterval(loadTrends, 60000); // Refresh every minute
    return () => clearInterval(interval);
  }, []);

  // Stream 2: Feedback submission
  const handleFeedback = useCallback(async (score: number) => {
    if (!analysisResult) return;

    try {
      await fetch("/v1/inspection/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          media_id: analysisResult.media_id,
          score,
          type: analysisResult.video_result ? "video" : "audio",
        }),
      });

      setFeedbackScore(score);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Feedback failed");
    }
  }, [analysisResult]);

  // Confidence color mapping
  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 0.85) return "text-green-600";
    if (confidence >= 0.70) return "text-yellow-600";
    return "text-red-600";
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-3xl font-bold">Audio/Video Inspector</h1>
        <p className="text-gray-600">
          Upload media files for quality analysis and real-time feedback
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="upload">Upload</TabsTrigger>
          <TabsTrigger value="results">Results</TabsTrigger>
          <TabsTrigger value="trends">Trends</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        {/* Stream 1: Upload Tab */}
        <TabsContent value="upload">
          <Card>
            <CardHeader>
              <CardTitle>Submit Media for Analysis</CardTitle>
              <CardDescription>
                Upload audio or video files for quality assessment
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {error && (
                <div className="rounded-md bg-red-50 p-4 text-sm text-red-700">
                  {error}
                </div>
              )}

              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium mb-2">
                    Audio File
                  </label>
                  <Input
                    type="file"
                    accept="audio/*"
                    onChange={(e) => setAudioFile(e.target.files?.[0] || null)}
                  />
                  {audioFile && (
                    <p className="text-sm text-gray-600 mt-1">
                      Selected: {audioFile.name}
                    </p>
                  )}
                </div>

                <div>
                  <label className="block text-sm font-medium mb-2">
                    Video File
                  </label>
                  <Input
                    type="file"
                    accept="video/*"
                    onChange={(e) => setVideoFile(e.target.files?.[0] || null)}
                  />
                  {videoFile && (
                    <p className="text-sm text-gray-600 mt-1">
                      Selected: {videoFile.name}
                    </p>
                  )}
                </div>
              </div>

              <Button
                onClick={handleAnalyze}
                disabled={isAnalyzing || (!audioFile && !videoFile)}
                className="w-full"
              >
                {isAnalyzing ? "Analyzing..." : "Analyze"}
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Stream 1: Results Tab */}
        <TabsContent value="results">
          <Card>
            <CardHeader>
              <CardTitle>Analysis Results</CardTitle>
              <CardDescription>
                Latest analysis with quality metrics
              </CardDescription>
            </CardHeader>
            <CardContent>
              {analysisResult ? (
                <div className="space-y-4">
                  {/* Overall Confidence */}
                  <div className="rounded-lg border p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-gray-600">
                          Overall Confidence
                        </p>
                        <p
                          className={`text-3xl font-bold ${getConfidenceColor(
                            analysisResult.overall_confidence
                          )}`}
                        >
                          {(
                            analysisResult.overall_confidence * 100
                          ).toFixed(1)}%
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-gray-500">
                          {analysisResult.timestamp}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Audio Results */}
                  {analysisResult.audio_result && (
                    <div className="rounded-lg border p-4 bg-blue-50">
                      <h3 className="font-semibold mb-2">Audio Analysis</h3>
                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <div>
                          <span className="text-gray-600">Quality:</span>
                          <span className="ml-2 font-medium">
                            {analysisResult.audio_result.quality}
                          </span>
                        </div>
                        <div>
                          <span className="text-gray-600">SNR:</span>
                          <span className="ml-2 font-medium">
                            {analysisResult.audio_result.snr.toFixed(1)} dB
                          </span>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Video Results */}
                  {analysisResult.video_result && (
                    <div className="rounded-lg border p-4 bg-green-50">
                      <h3 className="font-semibold mb-2">Video Analysis</h3>
                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <div>
                          <span className="text-gray-600">Quality:</span>
                          <span className="ml-2 font-medium">
                            {analysisResult.video_result.quality}
                          </span>
                        </div>
                        <div>
                          <span className="text-gray-600">Sharpness:</span>
                          <span className="ml-2 font-medium">
                            {(
                              analysisResult.video_result.sharpness * 100
                            ).toFixed(1)}%
                          </span>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Feedback Form */}
                  <div className="rounded-lg border p-4">
                    <h3 className="font-semibold mb-3">Rate Quality</h3>
                    <div className="flex gap-2">
                      {[1, 2, 3, 4, 5].map((score) => (
                        <Button
                          key={score}
                          variant={
                            feedbackScore === score ? "default" : "outline"
                          }
                          onClick={() => handleFeedback(score / 5)}
                          className="w-12 h-12"
                        >
                          {score}★
                        </Button>
                      ))}
                    </div>
                    {feedbackScore && (
                      <p className="text-sm text-green-600 mt-2">
                        ✓ Feedback recorded
                      </p>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-center py-8 text-gray-600">
                  No analysis yet. Upload a file to get started.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Stream 3: Trends Tab */}
        <TabsContent value="trends">
          <Card>
            <CardHeader>
              <CardTitle>7-Day Trends</CardTitle>
              <CardDescription>
                Confidence score trends over the last week
              </CardDescription>
            </CardHeader>
            <CardContent>
              {trends ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-4">
                    <div className="rounded-lg border p-4">
                      <p className="text-sm text-gray-600">Total Analyses</p>
                      <p className="text-2xl font-bold">{trends.analyses}</p>
                    </div>
                    <div className="rounded-lg border p-4">
                      <p className="text-sm text-gray-600">Avg Confidence</p>
                      <p className="text-2xl font-bold">
                        {(trends.average_confidence * 100).toFixed(1)}%
                      </p>
                    </div>
                    <div className="rounded-lg border p-4">
                      <p className="text-sm text-gray-600">Trend</p>
                      <p className="text-lg font-bold capitalize">
                        {trends.trend}
                      </p>
                    </div>
                  </div>

                  {/* Mini Chart Stub */}
                  <div className="rounded-lg border p-4 bg-gray-50 h-48 flex items-center justify-center text-gray-500">
                    📊 Trend Chart (Recharts integration in Phase 6)
                  </div>
                </div>
              ) : (
                <div className="text-center py-8 text-gray-600">
                  Loading trends...
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Stream 3: History Tab */}
        <TabsContent value="history">
          <Card>
            <CardHeader>
              <CardTitle>Analysis History</CardTitle>
              <CardDescription>
                Last {Math.min(history.length, 100)} analyses
              </CardDescription>
            </CardHeader>
            <CardContent>
              {history.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Media ID</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Confidence</TableHead>
                      <TableHead>Timestamp</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.slice(0, 10).map((result) => (
                      <TableRow key={result.media_id}>
                        <TableCell className="font-mono text-sm">
                          {result.media_id.substring(0, 12)}...
                        </TableCell>
                        <TableCell>
                          {result.audio_result && result.video_result
                            ? "Audio+Video"
                            : result.audio_result
                              ? "Audio"
                              : "Video"}
                        </TableCell>
                        <TableCell>
                          <span
                            className={getConfidenceColor(
                              result.overall_confidence
                            )}
                          >
                            {(
                              result.overall_confidence * 100
                            ).toFixed(0)}%
                          </span>
                        </TableCell>
                        <TableCell className="text-sm text-gray-600">
                          {new Date(
                            result.timestamp
                          ).toLocaleDateString()}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-8 text-gray-600">
                  No analyses yet.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
