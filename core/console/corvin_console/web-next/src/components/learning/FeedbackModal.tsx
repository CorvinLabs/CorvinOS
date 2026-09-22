/**
 * Feedback Modal (Stream 2: Stories 1-4)
 *
 * Feedback collection UI:
 * 1. Modal after skill execution
 * 2. Auto-capture context (skill_id, duration, confidence)
 * 3. Feedback types: outcome (yes/no), confidence (1-5), preference
 * 4. Email notification on submit
 */

import React, { useState } from 'react';

interface FeedbackModalProps {
  skillId: string;
  skillOutput: string;
  executionDurationMs: number;
  confidence: number;
  onClose: () => void;
}

export const FeedbackModal: React.FC<FeedbackModalProps> = ({
  skillId,
  skillOutput,
  executionDurationMs,
  confidence,
  onClose,
}) => {
  const [feedbackType, setFeedbackType] = useState<'outcome' | 'confidence' | 'preference'>('outcome');
  const [outcomeValue, setOutcomeValue] = useState<0 | 1 | undefined>();
  const [confidenceValue, setConfidenceValue] = useState<1 | 2 | 3 | 4 | 5>(3);
  const [preferenceComment, setPreferenceComment] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async () => {
    setLoading(true);
    try {
      let value = 0.5;
      if (feedbackType === 'outcome') {
        value = outcomeValue ?? 0.5;
      } else if (feedbackType === 'confidence') {
        value = confidenceValue / 5;
      }

      const response = await fetch('/v1/console/learning/feedback/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_id: skillId,
          signal_type: feedbackType,
          value,
          comment: preferenceComment || undefined,
        }),
      });

      if (response.ok) {
        setSubmitted(true);
        setTimeout(() => {
          onClose();
        }, 2000);
      }
    } catch (err) {
      console.error('Feedback submission error:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4">
        {/* Header */}
        <div className="bg-gradient-to-r from-blue-500 to-blue-600 px-6 py-4 text-white">
          <h2 className="text-lg font-semibold">How did {skillId} perform?</h2>
          <p className="text-sm text-blue-100 mt-1">Your feedback helps us improve</p>
        </div>

        {submitted ? (
          <div className="px-6 py-12 text-center">
            <div className="inline-block bg-green-100 rounded-full p-3 mb-4">
              <svg className="w-6 h-6 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                  clipRule="evenodd"
                />
              </svg>
            </div>
            <h3 className="font-semibold text-gray-800">Thank you!</h3>
            <p className="text-sm text-gray-600 mt-2">Feedback recorded and sent to our team</p>
          </div>
        ) : (
          <>
            {/* Context Information */}
            <div className="px-6 py-4 bg-gray-50 border-b text-sm">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-gray-600">Skill</div>
                  <div className="font-mono text-xs text-gray-800">{skillId}</div>
                </div>
                <div>
                  <div className="text-gray-600">Duration</div>
                  <div className="text-gray-800">{executionDurationMs}ms</div>
                </div>
                <div>
                  <div className="text-gray-600">Confidence</div>
                  <div className="text-gray-800">{(confidence * 100).toFixed(0)}%</div>
                </div>
                <div>
                  <div className="text-gray-600">Output</div>
                  <div className="text-gray-800 truncate">{skillOutput.substring(0, 20)}...</div>
                </div>
              </div>
            </div>

            {/* Feedback Type Selection */}
            <div className="px-6 py-4 border-b">
              <label className="text-sm font-medium text-gray-700 block mb-3">
                Feedback Type
              </label>
              <div className="space-y-2">
                {[
                  { value: 'outcome' as const, label: 'Outcome: Did it work? (yes/no)' },
                  { value: 'confidence' as const, label: 'Confidence: Rate 1-5 stars' },
                  { value: 'preference' as const, label: 'Preference: Style feedback' },
                ].map((option) => (
                  <label key={option.value} className="flex items-center">
                    <input
                      type="radio"
                      name="feedbackType"
                      value={option.value}
                      checked={feedbackType === option.value}
                      onChange={() => setFeedbackType(option.value)}
                      className="w-4 h-4 text-blue-600"
                    />
                    <span className="ml-2 text-sm text-gray-700">{option.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Feedback Input */}
            <div className="px-6 py-4 border-b">
              {feedbackType === 'outcome' && (
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-700 block">Did it work?</label>
                  <div className="flex gap-3">
                    <button
                      onClick={() => setOutcomeValue(1)}
                      className={`flex-1 py-2 px-3 rounded font-medium transition ${
                        outcomeValue === 1
                          ? 'bg-green-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      ✓ Yes
                    </button>
                    <button
                      onClick={() => setOutcomeValue(0)}
                      className={`flex-1 py-2 px-3 rounded font-medium transition ${
                        outcomeValue === 0
                          ? 'bg-red-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      ✗ No
                    </button>
                  </div>
                </div>
              )}

              {feedbackType === 'confidence' && (
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-700 block">Rate confidence</label>
                  <div className="flex gap-2">
                    {[1, 2, 3, 4, 5].map((star) => (
                      <button
                        key={star}
                        onClick={() => setConfidenceValue(star as 1 | 2 | 3 | 4 | 5)}
                        className={`text-2xl transition ${
                          star <= confidenceValue ? 'text-yellow-400' : 'text-gray-300'
                        }`}
                      >
                        ★
                      </button>
                    ))}
                  </div>
                  <div className="text-xs text-gray-500">{confidenceValue}/5 stars</div>
                </div>
              )}

              {feedbackType === 'preference' && (
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-700 block">
                    Feedback comment (optional)
                  </label>
                  <textarea
                    value={preferenceComment}
                    onChange={(e) => setPreferenceComment(e.target.value)}
                    placeholder="e.g., 'Prefer concise output' or 'Too verbose'"
                    className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:ring-blue-500 focus:border-blue-500"
                    rows={3}
                  />
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="px-6 py-4 bg-gray-50 rounded-b-lg flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 py-2 px-4 border border-gray-300 rounded font-medium text-gray-700 hover:bg-gray-100 transition"
              >
                Skip
              </button>
              <button
                onClick={handleSubmit}
                disabled={loading || (feedbackType === 'outcome' && outcomeValue === undefined)}
                className="flex-1 py-2 px-4 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? 'Submitting...' : 'Submit Feedback'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default FeedbackModal;
