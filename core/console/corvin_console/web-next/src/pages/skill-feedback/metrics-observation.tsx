/**
 * Metrics Observation Form
 * Generic form for observing latency, accuracy, cost across all skills
 * Wires to: POST /v1/console/learning/metrics/observe
 */

import React, { useState } from 'react';
import { useForm } from 'react-hook-form';
import { AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { useSkillFeedbackAPI } from '@/hooks/useSkillFeedbackAPI';
import { validateMetricName, validateMetricValue } from '@/lib/feedback-validators';
import { MetricFeedbackRequest, MetricName } from '@/types/feedback';

interface MetricsFormData {
  skillId: string;
  metricName: MetricName;
  metricValue: string;
  context?: string;
}

const SKILL_OPTIONS = [
  { id: 'os.workflow_optimizer', label: 'Workflow Optimizer' },
  { id: 'os.security_orchestrator', label: 'Security Orchestrator' },
  { id: 'os.flow_guard', label: 'Flow Guard' },
];

const METRIC_OPTIONS: { id: MetricName; label: string; unit: string }[] = [
  { id: 'latency_ms', label: 'Latency', unit: 'ms' },
  { id: 'accuracy_percent', label: 'Accuracy', unit: '%' },
  { id: 'cost_usd', label: 'Cost', unit: '$' },
  { id: 'error_rate', label: 'Error Rate', unit: '%' },
  { id: 'throughput', label: 'Throughput', unit: 'req/s' },
];

export function MetricsObservationForm() {
  const { register, handleSubmit, formState: { errors }, reset, watch } =
    useForm<MetricsFormData>({
      defaultValues: {
        skillId: 'os.workflow_optimizer',
        metricName: 'latency_ms',
      },
    });

  const { loading, error, success, submitMetricFeedback, reset: resetApi } = useSkillFeedbackAPI();
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const selectedMetric = watch('metricName');
  const selectedMetricOption = METRIC_OPTIONS.find((m) => m.id === selectedMetric);

  const onSubmit = async (data: MetricsFormData) => {
    const errors: Record<string, string> = {};

    // Validate metric name
    const nameValidation = validateMetricName(data.metricName);
    if (!nameValidation.valid) {
      errors.metricName = nameValidation.error;
    }

    // Validate metric value
    const valueValidation = validateMetricValue(data.metricName, data.metricValue);
    if (!valueValidation.valid) {
      errors.metricValue = valueValidation.error;
    }

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }

    const request: MetricFeedbackRequest = {
      feedback_type: 'metric_observed',
      skill_id: data.skillId,
      metric_name: data.metricName,
      metric_value: parseFloat(data.metricValue),
      dimension: data.skillId.split('.')[1], // e.g., "workflow_optimizer"
    };

    await submitMetricFeedback(request, {
      onSuccess: () => {
        reset();
        setValidationErrors({});
        setTimeout(() => resetApi(), 3000);
      },
      onError: (err) => {
        console.error('Metric submission failed:', err);
      },
    });
  };

  return (
    <div className="w-full max-w-md rounded-lg border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 p-6">
      <h2 className="text-lg font-semibold mb-4">Observe Metric</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400 mb-6">
        Record latency, accuracy, cost, or error rate observations
      </p>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        {/* Skill Selection */}
        <div>
          <label className="block text-sm font-medium mb-1">Skill</label>
          <select
            {...register('skillId')}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
          >
            {SKILL_OPTIONS.map((skill) => (
              <option key={skill.id} value={skill.id}>
                {skill.label}
              </option>
            ))}
          </select>
        </div>

        {/* Metric Name Selection */}
        <div>
          <label className="block text-sm font-medium mb-1">Metric</label>
          <select
            {...register('metricName')}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
          >
            {METRIC_OPTIONS.map((metric) => (
              <option key={metric.id} value={metric.id}>
                {metric.label}
              </option>
            ))}
          </select>
        </div>

        {/* Metric Value */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Value {selectedMetricOption && `(${selectedMetricOption.unit})`}
          </label>
          <input
            type="number"
            step="0.01"
            {...register('metricValue')}
            placeholder={selectedMetricOption?.unit || 'Enter value'}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
          />
          {validationErrors.metricValue && (
            <p className="text-sm text-red-600 dark:text-red-400 mt-1">
              {validationErrors.metricValue}
            </p>
          )}
          {selectedMetricOption && (
            <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
              {selectedMetricOption.label} in {selectedMetricOption.unit}
            </p>
          )}
        </div>

        {/* Context (optional) */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Context (optional, max 500 chars)
          </label>
          <textarea
            {...register('context', { maxLength: 500 })}
            placeholder="e.g., 'During peak load', 'After model update'"
            rows={2}
            maxLength={500}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm resize-none"
          />
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
            <p className="text-sm text-green-700 dark:text-green-300">Metric recorded!</p>
          </div>
        )}

        {/* Submit Button */}
        <button
          type="submit"
          disabled={loading || success}
          className="w-full py-2 px-4 rounded bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium text-sm flex items-center justify-center gap-2 transition-colors"
        >
          {loading && <Loader className="w-4 h-4 animate-spin" />}
          {loading ? 'Recording...' : 'Record Metric'}
        </button>
      </form>
    </div>
  );
}
