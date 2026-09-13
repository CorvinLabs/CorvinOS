/**
 * Video Quality Metrics Panel — displays input validation, encoding, color metrics
 */

import React, { useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { useQuery } from '@tanstack/react-query';

interface ValidationMetrics {
  passed: number;
  warned: number;
  failed: number;
}

interface EncodingMetrics {
  codec: string;
  resolution: string;
  bitrate: string;
  ffmpeg_preset: string;
}

interface ColorMetrics {
  input_space: string;
  output_space: string;
}

interface PerSceneMetrics {
  scene_id: string;
  validation_status: 'pass' | 'warn' | 'fail';
  validation_confidence: number;
  encoding_codec: string;
  encoding_bitrate: string;
}

interface QualityMetricsData {
  job_id: string;
  status: string;
  validation: ValidationMetrics;
  encoding: EncodingMetrics;
  color: ColorMetrics;
  per_scene: PerSceneMetrics[];
}

interface VideoQualityMetricsProps {
  jobId: string;
}

export function VideoQualityMetrics({ jobId }: VideoQualityMetricsProps) {
  const [metrics, setMetrics] = useState<QualityMetricsData | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['video-quality-metrics', jobId],
    queryFn: async () => {
      const response = await fetch(`/v1/console/video/jobs/${jobId}/quality-metrics`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to fetch metrics');
      return response.json() as Promise<QualityMetricsData>;
    },
  });

  useEffect(() => {
    if (data) {
      setMetrics(data);
    }
  }, [data]);

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading metrics...</div>;
  if (error) return <div className="text-sm text-red-600">Failed to load metrics</div>;
  if (!metrics) return null;

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h3 className="text-lg font-semibold mb-3">Quality Metrics</h3>

        {/* Input Validation Summary */}
        <div className="space-y-2 mb-4">
          <p className="text-sm font-medium">Input Validation</p>
          <div className="grid grid-cols-3 gap-2 text-sm">
            <div className="bg-green-50 p-2 rounded">
              <p className="text-xs text-muted-foreground">Passed</p>
              <p className="font-semibold text-green-700">{metrics.validation.passed}/8</p>
            </div>
            <div className="bg-yellow-50 p-2 rounded">
              <p className="text-xs text-muted-foreground">Warned</p>
              <p className="font-semibold text-yellow-700">{metrics.validation.warned}</p>
            </div>
            <div className="bg-red-50 p-2 rounded">
              <p className="text-xs text-muted-foreground">Failed</p>
              <p className="font-semibold text-red-700">{metrics.validation.failed}</p>
            </div>
          </div>
        </div>

        {/* Encoding Parameters */}
        <div className="space-y-2 mb-4">
          <p className="text-sm font-medium">Encoding</p>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Codec</p>
              <p className="font-mono">{metrics.encoding.codec}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Resolution</p>
              <p className="font-mono">{metrics.encoding.resolution}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Bitrate</p>
              <p className="font-mono">{metrics.encoding.bitrate}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Preset</p>
              <p className="font-mono">{metrics.encoding.ffmpeg_preset}</p>
            </div>
          </div>
        </div>

        {/* Color Processing */}
        <div className="space-y-2 mb-4">
          <p className="text-sm font-medium">Color Processing</p>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Input Space</p>
              <p className="font-mono">{metrics.color.input_space}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Output Space</p>
              <p className="font-mono">{metrics.color.output_space}</p>
            </div>
          </div>
        </div>
      </Card>

      {/* Per-Scene Breakdown */}
      {metrics.per_scene.length > 0 && (
        <Card className="p-4">
          <h3 className="text-lg font-semibold mb-3">Per-Scene Metrics</h3>
          <div className="space-y-2">
            {metrics.per_scene.map((scene) => (
              <div
                key={scene.scene_id}
                className="flex items-center justify-between p-3 bg-muted rounded text-sm"
              >
                <div className="flex-1">
                  <p className="font-medium">{scene.scene_id}</p>
                  <p className="text-xs text-muted-foreground">
                    {scene.encoding_codec} @ {scene.encoding_bitrate}
                  </p>
                </div>
                <div className="flex gap-2">
                  <span
                    className={`px-2 py-1 rounded text-xs font-semibold ${
                      scene.validation_status === 'pass'
                        ? 'bg-green-100 text-green-800'
                        : scene.validation_status === 'warn'
                        ? 'bg-yellow-100 text-yellow-800'
                        : 'bg-red-100 text-red-800'
                    }`}
                  >
                    {scene.validation_status.toUpperCase()}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {(scene.validation_confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

export default VideoQualityMetrics;
