/**
 * Stream 3: Flow Guard Feedback Form
 * Question: "Is this data flow safe?" (yes/no/request-exception)
 * Wires to: POST /v1/console/learning/flow-guard/policy-feedback
 */

import React, { useState } from 'react';
import { useForm } from 'react-hook-form';
import { AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { useSkillFeedbackAPI } from '@/hooks/useSkillFeedbackAPI';
import { validateReason } from '@/lib/feedback-validators';
import { PreferenceFeedbackRequest, PreferenceChoice } from '@/types/feedback';

interface FlowGuardFormData {
  dataClass: string;
  engine: string;
  destination: string;
  preference: PreferenceChoice;
  justification?: string;
  durationHours: number;
}

export function FlowGuardFeedbackForm() {
  const { register, handleSubmit, formState: { errors }, reset, watch } =
    useForm<FlowGuardFormData>({
      defaultValues: {
        preference: 'deterministic',
        durationHours: 24,
      },
    });

  const { loading, error, success, submitPreferenceFeedback, reset: resetApi } = useSkillFeedbackAPI();
  const [justError, setJustError] = useState<string | null>(null);
  const preference = watch('preference');

  const onSubmit = async (data: FlowGuardFormData) => {
    // Validate justification if requesting exception
    if (data.preference === 'llm' && data.justification) {
      const validation = validateReason(data.justification);
      if (!validation.valid) {
        setJustError(validation.error);
        return;
      }
    }

    const request: PreferenceFeedbackRequest = {
      feedback_type: 'preference_feedback',
      skill_id: 'os.flow_guard',
      preference: data.preference,
      policy_class: data.dataClass,
      reason: data.justification,
    };

    await submitPreferenceFeedback(request, {
      onSuccess: () => {
        reset();
        setJustError(null);
        setTimeout(() => resetApi(), 3000);
      },
      onError: (err) => {
        console.error('Flow Guard feedback submission failed:', err);
      },
    });
  };

  return (
    <div className="w-full max-w-md rounded-lg border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 p-6">
      <h2 className="text-lg font-semibold mb-4">Flow Guard Feedback</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400 mb-6">
        Is this data flow safe to allow?
      </p>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        {/* Data Class (read-only) */}
        <div>
          <label className="block text-sm font-medium mb-1">Data Classification</label>
          <select
            {...register('dataClass')}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm disabled:opacity-50"
            disabled
          >
            <option value="">Select data class...</option>
            <option value="public">Public</option>
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted (PII)</option>
          </select>
        </div>

        {/* Engine (read-only) */}
        <div>
          <label className="block text-sm font-medium mb-1">Target Engine</label>
          <select
            {...register('engine')}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm disabled:opacity-50"
            disabled
          >
            <option value="">Select engine...</option>
            <option value="claude_api">Claude API</option>
            <option value="external_api">External API</option>
            <option value="local_cache">Local Cache</option>
          </select>
        </div>

        {/* Destination (read-only) */}
        <div>
          <label className="block text-sm font-medium mb-1">Destination</label>
          <input
            type="text"
            {...register('destination')}
            placeholder="e.g., https://api.example.com"
            disabled
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm disabled:opacity-50"
          />
        </div>

        {/* Policy Preference */}
        <div>
          <label className="block text-sm font-medium mb-3">
            Should this flow be:
          </label>
          <div className="space-y-2">
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                value="deterministic"
                {...register('preference')}
                className="w-4 h-4"
              />
              <span className="text-sm">Deterministic (strict rules)</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                value="llm"
                {...register('preference')}
                className="w-4 h-4"
              />
              <span className="text-sm">Request exception (LLM-gated)</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                value="neither"
                {...register('preference')}
                className="w-4 h-4"
              />
              <span className="text-sm">Unsure</span>
            </label>
          </div>
        </div>

        {/* Justification (if exception requested) */}
        {preference === 'llm' && (
          <div>
            <label className="block text-sm font-medium mb-1">
              Justification (required for exception, max 500 chars)
            </label>
            <textarea
              {...register('justification', { maxLength: 500 })}
              placeholder="Why should this exception be approved?"
              rows={3}
              maxLength={500}
              className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm resize-none"
            />
            {justError && (
              <p className="text-sm text-red-600 dark:text-red-400 mt-1">{justError}</p>
            )}
          </div>
        )}

        {/* Duration (for exceptions) */}
        {preference === 'llm' && (
          <div>
            <label className="block text-sm font-medium mb-1">Exception Duration (hours)</label>
            <input
              type="number"
              {...register('durationHours', { min: 1, max: 720 })}
              min="1"
              max="720"
              className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
            />
            <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
              (1–30 days, defaults to 24h)
            </p>
          </div>
        )}

        {/* Error Message */}
        {error && (
          <div className="flex gap-2 p-3 rounded bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900">
            <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
          </div>
        )}

        {/* Success Message */}
        {success && (
          <div className="flex gap-2 p-3 rounded bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-900">
            <CheckCircle className="w-4 h-4 text-green-600 dark:text-green-400 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-green-700 dark:text-green-300">
              {preference === 'llm'
                ? 'Exception request submitted for approval!'
                : 'Policy feedback recorded!'}
            </p>
          </div>
        )}

        {/* Submit Button */}
        <button
          type="submit"
          disabled={loading || success}
          className="w-full py-2 px-4 rounded bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium text-sm flex items-center justify-center gap-2 transition-colors"
        >
          {loading && <Loader className="w-4 h-4 animate-spin" />}
          {loading ? 'Submitting...' : 'Submit Feedback'}
        </button>
      </form>
    </div>
  );
}
