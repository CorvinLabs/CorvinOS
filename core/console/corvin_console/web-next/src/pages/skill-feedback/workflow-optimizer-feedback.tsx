/**
 * Stream 1: Workflow Optimizer Feedback Form
 * Question: "Was this the right agent for this task?" (yes/no/other)
 * Wires to: POST /v1/console/learning/workflow-optimizer/feedback
 */

import React, { useState } from 'react';
import { useForm } from 'react-hook-form';
import { AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { useSkillFeedbackAPI } from '@/hooks/useSkillFeedbackAPI';
import { validateReason } from '@/lib/feedback-validators';
import { OutcomeFeedbackRequest, OutcomeChoice } from '@/types/feedback';

interface WorkflowOptimizerFormData {
  taskId?: string;
  routedAgent: string;
  outcome: OutcomeChoice;
  reason?: string;
}

export function WorkflowOptimizerFeedbackForm() {
  const { register, handleSubmit, formState: { errors }, reset } = useForm<WorkflowOptimizerFormData>(
    {
      defaultValues: {
        outcome: 'yes',
      },
    }
  );

  const { loading, error, success, submitOutcomeFeedback, reset: resetApi } = useSkillFeedbackAPI();
  const [reasonError, setReasonError] = useState<string | null>(null);

  const onSubmit = async (data: WorkflowOptimizerFormData) => {
    // Validate reason
    if (data.reason) {
      const validation = validateReason(data.reason);
      if (!validation.valid) {
        setReasonError(validation.error);
        return;
      }
    }

    const request: OutcomeFeedbackRequest = {
      feedback_type: 'outcome_feedback',
      skill_id: 'os.workflow_optimizer',
      outcome: data.outcome,
      task_id: data.taskId,
      reason: data.reason,
    };

    await submitOutcomeFeedback(request, {
      onSuccess: () => {
        reset();
        setReasonError(null);
        setTimeout(() => resetApi(), 3000);
      },
      onError: (err) => {
        console.error('Feedback submission failed:', err);
      },
    });
  };

  return (
    <div className="w-full max-w-md rounded-lg border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 p-6">
      <h2 className="text-lg font-semibold mb-4">Workflow Optimizer Feedback</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400 mb-6">
        Was this the right agent for this task?
      </p>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        {/* Task ID (optional) */}
        <div>
          <label className="block text-sm font-medium mb-1">Task ID (optional)</label>
          <input
            type="text"
            {...register('taskId', { maxLength: 200 })}
            placeholder="auto-filled from task context"
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
          />
        </div>

        {/* Routed Agent (read-only) */}
        <div>
          <label className="block text-sm font-medium mb-1">Routed Agent</label>
          <select
            {...register('routedAgent')}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm disabled:opacity-50"
            disabled
          >
            <option value="">Select agent...</option>
            <option value="haiku">Haiku 4.5 (fast)</option>
            <option value="sonnet">Sonnet 5 (balanced)</option>
            <option value="opus">Opus 5 (powerful)</option>
          </select>
        </div>

        {/* Outcome Feedback */}
        <div>
          <label className="block text-sm font-medium mb-3">Was this routing correct?</label>
          <div className="space-y-2">
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                value="yes"
                {...register('outcome')}
                className="w-4 h-4"
              />
              <span className="text-sm">Yes, correct routing</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                value="no"
                {...register('outcome')}
                className="w-4 h-4"
              />
              <span className="text-sm">No, wrong agent</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                value="other"
                {...register('outcome')}
                className="w-4 h-4"
              />
              <span className="text-sm">Other/Unsure</span>
            </label>
          </div>
        </div>

        {/* Reason Text */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Reason (optional, max 500 chars)
          </label>
          <textarea
            {...register('reason', { maxLength: 500 })}
            placeholder="Why was this the right/wrong choice?"
            rows={3}
            maxLength={500}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm resize-none"
          />
          {reasonError && (
            <p className="text-sm text-red-600 dark:text-red-400 mt-1">{reasonError}</p>
          )}
        </div>

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
            <p className="text-sm text-green-700 dark:text-green-300">Feedback recorded!</p>
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
