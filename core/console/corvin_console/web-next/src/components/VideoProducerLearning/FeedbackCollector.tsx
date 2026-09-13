/**
 * Feedback Collector Component for Video Producer Learning (Phase 4b)
 *
 * Allows operator to submit 1–5 scale feedback on video quality with optional notes.
 * Integrates with /v1/console/video/jobs/{job_id}/feedback endpoint.
 */

import React, { useState } from 'react';

export interface FeedbackCollectorProps {
  jobId: string;
  sceneId: string;
  onSubmitted?: (feedback: FeedbackSubmission) => void;
  onError?: (error: string) => void;
}

export interface FeedbackSubmission {
  rating: number;
  feedbackType: 'quality' | 'relevance' | 'correctness';
  workerNotes?: string;
}

const FEEDBACK_TYPES = [
  { value: 'quality', label: 'Video Quality' },
  { value: 'relevance', label: 'Narrative Relevance' },
  { value: 'correctness', label: 'Factual Correctness' },
] as const;

export const FeedbackCollector: React.FC<FeedbackCollectorProps> = ({
  jobId,
  sceneId,
  onSubmitted,
  onError,
}) => {
  const [rating, setRating] = useState<number>(3);
  const [feedbackType, setFeedbackType] = useState<'quality' | 'relevance' | 'correctness'>('quality');
  const [notes, setNotes] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);

    try {
      const response = await fetch(
        `/v1/console/video/jobs/${jobId}/feedback`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scene_id: sceneId,
            feedback_type: feedbackType,
            rating,
            worker_notes: notes || undefined,
          }),
        }
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to submit feedback');
      }

      setSuccess(true);
      onSubmitted?.({ rating, feedbackType, workerNotes: notes });

      // Reset form
      setTimeout(() => {
        setRating(3);
        setFeedbackType('quality');
        setNotes('');
        setSuccess(false);
      }, 2000);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      onError?.(errorMsg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="feedback-collector p-4 border rounded-lg bg-gray-50">
      <h3 className="text-lg font-semibold mb-4">Provide Feedback</h3>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Feedback Type */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Feedback Type
          </label>
          <select
            value={feedbackType}
            onChange={(e) => setFeedbackType(e.target.value as any)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {FEEDBACK_TYPES.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
        </div>

        {/* Rating Scale */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Rating: {rating}/5
          </label>
          <div className="flex items-center gap-4">
            {[1, 2, 3, 4, 5].map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRating(r)}
                className={`w-10 h-10 rounded-full font-semibold transition-colors ${
                  rating === r
                    ? 'bg-blue-500 text-white'
                    : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                }`}
              >
                {r}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-500 mt-2">
            1 = Poor, 5 = Excellent
          </p>
        </div>

        {/* Notes */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Notes (Optional)
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value.slice(0, 500))}
            placeholder="e.g., 'voice is too fast', 'slide layout is cramped'"
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            rows={3}
            maxLength={500}
          />
          <p className="text-xs text-gray-500 mt-1">
            {notes.length}/500 characters
          </p>
        </div>

        {/* Submit Button */}
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full px-4 py-2 bg-blue-500 text-white rounded-md hover:bg-blue-600 disabled:bg-gray-400 transition-colors"
        >
          {isSubmitting ? 'Submitting...' : 'Submit Feedback'}
        </button>

        {/* Success Message */}
        {success && (
          <div className="p-3 bg-green-100 text-green-700 rounded-md">
            ✓ Feedback recorded! Learning loop updated.
          </div>
        )}
      </form>
    </div>
  );
};

export default FeedbackCollector;
