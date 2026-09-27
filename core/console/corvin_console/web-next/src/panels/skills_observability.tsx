/**
 * Skills Observability Dashboard Panel (ADR-0722)
 *
 * Real-time observability into OS-Skill learning loop:
 * - Execution latency (p50/p95/p99)
 * - Confidence trends (7-day rolling)
 * - Feedback volume & ratio
 * - A/B test results
 *
 * Compliance: GDPR Art. 5/30/32, ADR-0763 (console production surface)
 * Data: Real metrics from audit trail, no fabrication
 * Update Interval: 5s poll (operator request for real-time observability)
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Loader2, Download } from 'lucide-react';
import { useAuth } from '@/hooks/useAuth';
import { useSkillsMetrics } from '@/hooks/useSkillsMetrics';
import { TimeRangeSelector } from '@/components/filters/TimeRangeSelector';
import SkillLatencyChart from './charts/SkillLatencyChart';
import SkillConfidenceTrends from './charts/SkillConfidenceTrends';
import SkillFeedbackChart from './charts/SkillFeedbackChart';
import ABTestResults from './charts/ABTestResults';
import styles from './skills_observability.module.css';

export type TimeRange = '1d' | '7d' | '30d';

interface SkillsObservabilityPanelProps {
  tenantId?: string;
}

/**
 * Main Skills Observability Panel Component
 *
 * Features:
 * - Four tabs with real-time metric visualization
 * - Time range selector (1d/7d/30d)
 * - CSV export
 * - Tenant-scoped data (GDPR isolation)
 * - Auto-poll every 5s (configurable)
 * - Error handling & loading states
 */
export const SkillsObservabilityPanel: React.FC<SkillsObservabilityPanelProps> = ({
  tenantId: propTenantId
}) => {
  const { sessionId, tenantId: authTenantId } = useAuth();
  const tenantId = propTenantId || authTenantId;

  // State
  const [timeRange, setTimeRange] = useState<TimeRange>('7d');
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  // Data fetching hook (real data from API, no fabrication)
  const {
    latency,
    confidence,
    feedback,
    abTests,
    isLoading,
    error,
    refetch,
    lastUpdated
  } = useSkillsMetrics(
    timeRange,
    selectedSkill,
    tenantId
  );

  // Auto-poll every 5 seconds (operator request for real-time observability)
  useEffect(() => {
    if (!tenantId) return;

    const pollInterval = setInterval(() => {
      refetch();
    }, 5000);

    return () => clearInterval(pollInterval);
  }, [refetch, tenantId, timeRange, selectedSkill]);

  // CSV export handler
  const handleExport = useCallback(async () => {
    if (!tenantId) {
      console.error('Cannot export: missing tenant_id');
      return;
    }

    setIsExporting(true);
    try {
      const response = await fetch(
        `/v1/skills-observability/export/csv?time_range=${timeRange}&metrics=all&tenant_id=${tenantId}`,
        { headers: { 'X-Session-Id': sessionId } }
      );

      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }

      // Trigger download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `skills-metrics-${timeRange}-${new Date().toISOString()}.csv`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      console.error('Export error:', err);
      // TODO: Show user-facing error toast
    } finally {
      setIsExporting(false);
    }
  }, [timeRange, tenantId, sessionId]);

  // Render helpers
  const renderError = () => (
    <div className={styles.errorBanner} role="alert">
      <span>⚠️ Error loading metrics: {error?.message || 'Unknown error'}</span>
      <Button size="sm" onClick={() => refetch()}>Retry</Button>
    </div>
  );

  const renderLoading = () => (
    <div className={styles.loadingSpinner}>
      <Loader2 className="animate-spin" size={32} />
      <p>Loading metrics...</p>
    </div>
  );

  const renderLastUpdated = () => (
    <div className={styles.lastUpdated}>
      Updated: {lastUpdated ? new Date(lastUpdated).toLocaleTimeString() : 'Never'}
    </div>
  );

  return (
    <div
      className={styles.container}
      data-testid="skills-observability"
      data-tenant-id={tenantId}
    >
      {/* Header Section */}
      <header className={styles.header}>
        <div className={styles.titleSection}>
          <h1 className={styles.title}>Skills Observability</h1>
          <p className={styles.description}>
            Real-time monitoring of OS-Skill execution, confidence, feedback, and A/B tests
          </p>
        </div>

        {/* Controls */}
        <div className={styles.controls}>
          <TimeRangeSelector
            value={timeRange}
            onChange={setTimeRange}
            options={['1d', '7d', '30d']}
          />

          <Button
            onClick={handleExport}
            disabled={isExporting || isLoading || !!error}
            variant="outline"
            size="sm"
          >
            <Download size={16} className={styles.buttonIcon} />
            {isExporting ? 'Exporting...' : 'Export CSV'}
          </Button>
        </div>
      </header>

      {/* Status Messages */}
      {error && renderError()}
      {isLoading && renderLoading()}
      {renderLastUpdated()}

      {/* Main Tabs */}
      {!isLoading && !error && (
        <Tabs defaultValue="latency" className={styles.tabsContainer}>
          <TabsList className={styles.tabsList}>
            <TabsTrigger value="latency" className={styles.tabsTrigger}>
              Execution Latency
            </TabsTrigger>
            <TabsTrigger value="confidence" className={styles.tabsTrigger}>
              Confidence Trends
            </TabsTrigger>
            <TabsTrigger value="feedback" className={styles.tabsTrigger}>
              Feedback Stats
            </TabsTrigger>
            <TabsTrigger value="ab-tests" className={styles.tabsTrigger}>
              A/B Test Results
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: Latency */}
          <TabsContent value="latency" className={styles.tabContent}>
            <div className={styles.chartWrapper}>
              {latency ? (
                <SkillLatencyChart
                  data={latency}
                  timeRange={timeRange}
                  onSkillSelect={setSelectedSkill}
                />
              ) : (
                <div className={styles.emptyState}>
                  No latency data available for this time range
                </div>
              )}
            </div>
          </TabsContent>

          {/* Tab 2: Confidence */}
          <TabsContent value="confidence" className={styles.tabContent}>
            <div className={styles.chartWrapper}>
              {confidence ? (
                <SkillConfidenceTrends
                  data={confidence}
                  timeRange={timeRange}
                  selectedSkill={selectedSkill}
                  onSkillSelect={setSelectedSkill}
                />
              ) : (
                <div className={styles.emptyState}>
                  No confidence data available
                </div>
              )}
            </div>
          </TabsContent>

          {/* Tab 3: Feedback */}
          <TabsContent value="feedback" className={styles.tabContent}>
            <div className={styles.chartWrapper}>
              {feedback ? (
                <SkillFeedbackChart
                  data={feedback}
                  timeRange={timeRange}
                  selectedSkill={selectedSkill}
                  onSkillSelect={setSelectedSkill}
                />
              ) : (
                <div className={styles.emptyState}>
                  No feedback data available
                </div>
              )}
            </div>
          </TabsContent>

          {/* Tab 4: A/B Tests */}
          <TabsContent value="ab-tests" className={styles.tabContent}>
            <div className={styles.chartWrapper}>
              {abTests && abTests.length > 0 ? (
                <ABTestResults data={abTests} />
              ) : (
                <div className={styles.emptyState}>
                  No active A/B tests at this time
                </div>
              )}
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
};

export default SkillsObservabilityPanel;
