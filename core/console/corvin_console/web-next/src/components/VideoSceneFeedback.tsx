/**
 * Video Scene Feedback Component — Per-scene approve/reject/edit interface
 *
 * Users review completed video scenes and provide feedback:
 * - Approve: quality acceptable
 * - Reject: specify issue (too_blurry, too_compressed, color_wrong, hallucination)
 * - Edit: manual storyboard adjustment (future)
 */

import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Check, X, Edit2 } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

interface SceneData {
  scene_id: string;
  thumbnail?: string;
  validation_confidence: number;
  encoding_codec: string;
  encoding_bitrate: string;
}

interface VideoSceneFeedbackProps {
  jobId: string;
  scenes: SceneData[];
  onFeedbackSubmitted?: () => void;
}

type FeedbackType = 'approved' | 'too_blurry' | 'too_compressed' | 'color_wrong' | 'hallucination' | 'other';

interface FeedbackRequest {
  scene_id: string;
  feedback_type: FeedbackType;
  confidence: number;
}

export function VideoSceneFeedback({
  jobId,
  scenes,
  onFeedbackSubmitted,
}: VideoSceneFeedbackProps) {
  const queryClient = useQueryClient();
  const [selectedScene, setSelectedScene] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState<FeedbackType | null>(null);
  const [showRejectDialog, setShowRejectDialog] = useState(false);

  const feedbackMutation = useMutation({
    mutationFn: async (feedback: FeedbackRequest) => {
      const response = await fetch(
        `/v1/console/video/jobs/${jobId}/scenes/${feedback.scene_id}/feedback`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(feedback),
        }
      );
      if (!response.ok) throw new Error('Failed to submit feedback');
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['video-feedback', jobId] });
      setShowRejectDialog(false);
      setRejectReason(null);
      onFeedbackSubmitted?.();
    },
  });

  const handleApprove = async (sceneId: string) => {
    await feedbackMutation.mutateAsync({
      scene_id: sceneId,
      feedback_type: 'approved',
      confidence: 0.95,
    });
  };

  const handleReject = async () => {
    if (!selectedScene || !rejectReason) return;
    await feedbackMutation.mutateAsync({
      scene_id: selectedScene,
      feedback_type: rejectReason,
      confidence: 0.85,
    });
  };

  const reasonLabels: Record<FeedbackType, string> = {
    'approved': '✓ Approved',
    'too_blurry': '❌ Too Blurry',
    'too_compressed': '❌ Too Compressed',
    'color_wrong': '⚠️ Color Wrong',
    'hallucination': '⚠️ Hallucination',
    'other': '❌ Other Issue',
  };

  const reasonColors: Record<FeedbackType, string> = {
    'approved': 'bg-green-50',
    'too_blurry': 'bg-yellow-50',
    'too_compressed': 'bg-yellow-50',
    'color_wrong': 'bg-blue-50',
    'hallucination': 'bg-red-50',
    'other': 'bg-gray-50',
  };

  if (!scenes || scenes.length === 0) {
    return <div className="text-sm text-muted-foreground">No scenes to review</div>;
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold mb-3">Scene Feedback</h3>
        <p className="text-sm text-muted-foreground mb-4">
          Review each scene: Approve for use, or reject with reason. Feedback trains the optimizer.
        </p>
      </div>

      {/* Scene Grid */}
      <div className="grid gap-3">
        {scenes.map((scene) => (
          <Card
            key={scene.scene_id}
            className={`p-4 cursor-pointer transition ${
              selectedScene === scene.scene_id
                ? 'ring-2 ring-primary bg-muted'
                : 'hover:bg-muted/50'
            }`}
            onClick={() => setSelectedScene(scene.scene_id)}
          >
            <div className="flex items-start justify-between gap-4">
              {/* Thumbnail + Info */}
              <div className="flex-1 min-w-0">
                {scene.thumbnail && (
                  <img
                    src={scene.thumbnail}
                    alt={scene.scene_id}
                    className="w-full h-32 object-cover rounded mb-2"
                  />
                )}
                <p className="font-medium text-sm">{scene.scene_id}</p>
                <p className="text-xs text-muted-foreground">
                  {scene.encoding_codec} @ {scene.encoding_bitrate} | Confidence: {(scene.validation_confidence * 100).toFixed(0)}%
                </p>
              </div>

              {/* Action Buttons */}
              <div className="flex flex-col gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleApprove(scene.scene_id);
                  }}
                  disabled={feedbackMutation.isPending}
                  className="gap-2 w-24"
                >
                  <Check className="h-4 w-4" />
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedScene(scene.scene_id);
                    setShowRejectDialog(true);
                  }}
                  disabled={feedbackMutation.isPending}
                  className="gap-2 w-24"
                >
                  <X className="h-4 w-4" />
                  Reject
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* Reject Dialog */}
      {showRejectDialog && (
        <Card className="p-4 bg-yellow-50 border-yellow-200">
          <div className="space-y-3">
            <p className="text-sm font-medium">Why does this scene need improvement?</p>

            <div className="space-y-2">
              {(['too_blurry', 'too_compressed', 'color_wrong', 'hallucination', 'other'] as const).map(
                (reason) => (
                  <label key={reason} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="feedback_reason"
                      value={reason}
                      checked={rejectReason === reason}
                      onChange={(e) => setRejectReason(e.target.value as FeedbackType)}
                      className="h-4 w-4"
                    />
                    <span className="text-sm">{reasonLabels[reason]}</span>
                  </label>
                )
              )}
            </div>

            <div className="flex gap-2 pt-2">
              <Button
                size="sm"
                onClick={handleReject}
                disabled={!rejectReason || feedbackMutation.isPending}
              >
                Submit Feedback
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowRejectDialog(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        </Card>
      )}

      {/* Feedback Status */}
      {feedbackMutation.isPending && (
        <div className="text-sm text-muted-foreground animate-pulse">Submitting feedback...</div>
      )}
      {feedbackMutation.isError && (
        <div className="text-sm text-red-600">Failed to submit feedback</div>
      )}
    </div>
  );
}

export default VideoSceneFeedback;
