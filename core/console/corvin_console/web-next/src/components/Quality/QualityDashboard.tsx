/**
 * QualityDashboard — Parent layout for Specification-as-Loss visualization
 * (ADR-0731, ADR-0733)
 */

import React, { useState, useEffect } from "react";
import { QualityMetrics } from "./types";
import MetricsSummary from "./MetricsSummary";
import ConvergenceChart from "./ConvergenceChart";
import SpecHistoryTree from "./SpecHistoryTree";

interface Props {
  taskId: string;
}

export default function QualityDashboard({ taskId }: Props) {
  const [metrics, setMetrics] = useState<QualityMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const resp = await fetch(`/v1/console/quality/task/${taskId}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        setMetrics(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, [taskId]);

  if (loading) return <div className="p-4">Loading quality metrics...</div>;
  if (error) return <div className="p-4 text-red-600">Error: {error}</div>;
  if (!metrics) return <div className="p-4">No metrics available</div>;

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-bold">Quality Dashboard</h1>
      <p className="text-gray-600">Task: {taskId}</p>

      {/* Summary card */}
      <MetricsSummary metrics={metrics} />

      {/* Convergence trajectory */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Convergence Trajectory</h2>
        <ConvergenceChart data={metrics.convergence_history} />
      </div>

      {/* Spec history tree */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Specification History</h2>
        <SpecHistoryTree
          constraints={metrics.spec_constraints}
          specVersion={metrics.spec_version}
        />
      </div>

      {/* Export */}
      <button
        onClick={() => exportMetrics(taskId)}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
      >
        Export Metrics (CSV)
      </button>
    </div>
  );
}

async function exportMetrics(taskId: string) {
  try {
    const resp = await fetch("/v1/console/quality/metrics/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_id: taskId,
        format: "csv",
        include_audit: true,
        include_spec_history: true,
      }),
    });
    if (!resp.ok) throw new Error(`Export failed: ${resp.status}`);

    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `quality_metrics_${taskId}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Export error: ${err}`);
  }
}
