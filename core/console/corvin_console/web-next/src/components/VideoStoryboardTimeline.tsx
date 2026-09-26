/**
 * VideoStoryboardTimeline.tsx — Phase 6c: Storyboard Visualization
 *
 * Real-time orchestration timeline with:
 * - Frame sequencing (TTS, Screenshot, FFmpeg, YouTube)
 * - Skill confidence display (ADR-0532)
 * - Credential rotation status (ADR-0565)
 * - Learning metrics (ADR-0314)
 * - Audit trail integration (ADR-0232)
 */

'use client';

import { useEffect, useState } from 'react';
import { ArrowRight, Lock, TrendingUp, AlertCircle, CheckCircle } from 'lucide-react';
import styles from './VideoStoryboardTimeline.module.css';

// ============================================================================
// Types
// ============================================================================

interface SkillConfidence {
  skillId: string;
  version: string;
  confidence: number;
  feedbackCount: number;
  accuracyTrend?: string;
  lastUpdated: string;
}

interface CredentialStatus {
  credentialId: string;
  credentialType: string;
  rotationStatus: string;
  lastRotatedAt?: string;
  nextRotationAt?: string;
  daysUntilRotation?: number;
  auditEventCount: number;
}

interface FrameState {
  frameId: string;
  workerType: string;
  status: 'pending' | 'running' | 'completed' | 'error';
  progress?: number;
  errorMessage?: string;
  metadata?: Record<string, any>;
  skillConfidence?: SkillConfidence;
  credentialStatus?: CredentialStatus;
  auditEventHash?: string;
  createdAt: string;
  completedAt?: string;
}

interface ExecutorStatus {
  totalFrames: number;
  completedFrames: number;
  failedFrames: number;
  currentFrameId?: string;
  isRunning: boolean;
  overallProgress: number;
  estimatedTimeRemaining?: number;
  learningMetrics?: {
    outcomeCount: number;
    averageConfidence: number;
    improvementTrend?: string;
    lastFeedbackAt?: string;
  };
  startedAt: string;
  updatedAt: string;
}

interface TimelineState {
  taskId: string;
  frames: FrameState[];
  executorStatus: ExecutorStatus;
  auditEventsCount: number;
  lastAuditHash?: string;
}

// ============================================================================
// Helper Functions
// ============================================================================

function getWorkerIcon(workerType: string): string {
  const icons: Record<string, string> = {
    tts: '🔊',
    screenshot: '📷',
    ffmpeg: '🎬',
    youtube: '📺',
  };
  return icons[workerType] || '⚙️';
}

function getStatusColor(status: string): string {
  const colors: Record<string, string> = {
    pending: 'var(--color-pending)',
    running: 'var(--color-running)',
    completed: 'var(--color-completed)',
    error: 'var(--color-error)',
  };
  return colors[status] || 'var(--color-default)';
}

function formatConfidence(confidence: number): string {
  const percent = Math.round(confidence * 100);
  return `${percent}%`;
}

function daysUntilRotation(days?: number): string {
  if (!days) return 'N/A';
  if (days <= 0) return '⚠️ Expired';
  if (days <= 7) return `⚠️ ${days}d left`;
  return `✅ ${days}d left`;
}

// ============================================================================
// Main Component
// ============================================================================

export function VideoStoryboardTimeline({ taskId }: { taskId: string }) {
  const [timeline, setTimeline] = useState<TimelineState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedFrameId, setExpandedFrameId] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Fetch timeline state
  useEffect(() => {
    const fetchTimeline = async () => {
      try {
        const response = await fetch(`/api/v1/timeline/state/${taskId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json() as TimelineState;
        setTimeline(data);
        setLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch timeline');
        setLoading(false);
      }
    };

    fetchTimeline();

    // Auto-refresh if executor is running
    let interval: NodeJS.Timeout | null = null;
    if (autoRefresh) {
      interval = setInterval(fetchTimeline, 3000); // Refresh every 3 seconds
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [taskId, autoRefresh]);

  if (loading) {
    return <div className={styles.loading}>Loading timeline...</div>;
  }

  if (error || !timeline) {
    return <div className={styles.error}>Error: {error || 'No timeline data'}</div>;
  }

  const { frames, executorStatus } = timeline;

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.header}>
        <h2>🎬 Storyboard Visualization</h2>
        <div className={styles.controls}>
          <label>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          <span className={styles.status}>
            {executorStatus.isRunning ? '🟢 Running' : '⚪ Idle'}
          </span>
        </div>
      </div>

      {/* Progress Bar */}
      <div className={styles.progressSection}>
        <div className={styles.progressBar}>
          <div
            className={styles.progressFill}
            style={{ width: `${executorStatus.overallProgress}%` }}
          />
        </div>
        <div className={styles.progressText}>
          {executorStatus.completedFrames}/{executorStatus.totalFrames} frames completed
          ({Math.round(executorStatus.overallProgress)}%)
        </div>
        {executorStatus.failedFrames > 0 && (
          <div className={styles.failedCount}>
            ⚠️ {executorStatus.failedFrames} failed
          </div>
        )}
      </div>

      {/* Learning Metrics (ADR-0314) */}
      {executorStatus.learningMetrics && (
        <div className={styles.metricsPanel}>
          <div className={styles.metricItem}>
            <TrendingUp size={20} />
            <div>
              <label>Skill Confidence</label>
              <span className={styles.metricValue}>
                {formatConfidence(executorStatus.learningMetrics.averageConfidence)}
              </span>
            </div>
          </div>
          <div className={styles.metricItem}>
            <span>📊</span>
            <div>
              <label>Feedback Received</label>
              <span className={styles.metricValue}>
                {executorStatus.learningMetrics.outcomeCount} outcomes
              </span>
            </div>
          </div>
          <div className={styles.metricItem}>
            <span>📈</span>
            <div>
              <label>Trend</label>
              <span className={styles.metricValue}>
                {executorStatus.learningMetrics.improvementTrend || '→'}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Frames Timeline */}
      <div className={styles.timeline}>
        {frames.map((frame, idx) => (
          <div key={frame.frameId} className={styles.frameContainer}>
            {/* Frame Card */}
            <div
              className={`${styles.frameCard} ${styles[`status-${frame.status}`]}`}
              onClick={() => setExpandedFrameId(
                expandedFrameId === frame.frameId ? null : frame.frameId
              )}
            >
              {/* Status Icon */}
              <div className={styles.statusIcon}>
                {frame.status === 'completed' && (
                  <CheckCircle size={24} color="var(--color-completed)" />
                )}
                {frame.status === 'error' && (
                  <AlertCircle size={24} color="var(--color-error)" />
                )}
                {frame.status === 'running' && (
                  <div className={styles.spinner} />
                )}
                {frame.status === 'pending' && (
                  <div className={styles.circle} />
                )}
              </div>

              {/* Frame Details */}
              <div className={styles.frameDetails}>
                <div className={styles.frameHeader}>
                  <span className={styles.frameIcon}>
                    {getWorkerIcon(frame.workerType)}
                  </span>
                  <span className={styles.frameId}>{frame.frameId}</span>
                  <span className={styles.workerType}>{frame.workerType}</span>
                  <span className={styles.status}>{frame.status}</span>
                </div>

                {frame.progress !== undefined && frame.status === 'running' && (
                  <div className={styles.frameProgress}>
                    <div className={styles.progressBar}>
                      <div
                        className={styles.progressFill}
                        style={{ width: `${frame.progress}%` }}
                      />
                    </div>
                    <span>{Math.round(frame.progress)}%</span>
                  </div>
                )}
              </div>

              {/* Skill Confidence Badge (ADR-0532) */}
              {frame.skillConfidence && (
                <div className={styles.skillBadge} title="Skill confidence">
                  <span className={styles.confidence}>
                    {formatConfidence(frame.skillConfidence.confidence)}
                  </span>
                  {frame.skillConfidence.accuracyTrend && (
                    <span className={styles.trend}>
                      {frame.skillConfidence.accuracyTrend}
                    </span>
                  )}
                </div>
              )}

              {/* Credential Status Icon (ADR-0565) */}
              {frame.credentialStatus && (
                <div
                  className={styles.credentialIcon}
                  title={`${frame.credentialStatus.rotationStatus}`}
                >
                  <Lock size={16} />
                </div>
              )}

              {/* Expand Button */}
              <button
                className={styles.expandButton}
                onClick={(e) => {
                  e.stopPropagation();
                  setExpandedFrameId(
                    expandedFrameId === frame.frameId ? null : frame.frameId
                  );
                }}
              >
                ▼
              </button>
            </div>

            {/* Expanded Details */}
            {expandedFrameId === frame.frameId && (
              <div className={styles.frameExpanded}>
                {/* Skill Info */}
                {frame.skillConfidence && (
                  <div className={styles.expandedSection}>
                    <h4>🤖 Skill: {frame.skillConfidence.skillId}</h4>
                    <div className={styles.infoGrid}>
                      <div>
                        <label>Version:</label>
                        <span>{frame.skillConfidence.version}</span>
                      </div>
                      <div>
                        <label>Confidence:</label>
                        <span>{formatConfidence(frame.skillConfidence.confidence)}</span>
                      </div>
                      <div>
                        <label>Feedback Events:</label>
                        <span>{frame.skillConfidence.feedbackCount}</span>
                      </div>
                      <div>
                        <label>Last Updated:</label>
                        <span>{new Date(frame.skillConfidence.lastUpdated).toLocaleTimeString()}</span>
                      </div>
                    </div>
                  </div>
                )}

                {/* Credential Info */}
                {frame.credentialStatus && (
                  <div className={styles.expandedSection}>
                    <h4>🔐 Credential: {frame.credentialStatus.credentialId}</h4>
                    <div className={styles.infoGrid}>
                      <div>
                        <label>Type:</label>
                        <span>{frame.credentialStatus.credentialType}</span>
                      </div>
                      <div>
                        <label>Status:</label>
                        <span className={styles[`status-${frame.credentialStatus.rotationStatus}`]}>
                          {frame.credentialStatus.rotationStatus}
                        </span>
                      </div>
                      <div>
                        <label>Days Until Rotation:</label>
                        <span>{daysUntilRotation(frame.credentialStatus.daysUntilRotation)}</span>
                      </div>
                      <div>
                        <label>Audit Events:</label>
                        <span>{frame.credentialStatus.auditEventCount}</span>
                      </div>
                    </div>
                  </div>
                )}

                {/* Audit Trail */}
                {frame.auditEventHash && (
                  <div className={styles.expandedSection}>
                    <h4>📋 Audit Trail</h4>
                    <div className={styles.auditHash}>
                      <code>{frame.auditEventHash.substring(0, 32)}...</code>
                      <a href={`#audit/${frame.frameId}`} className={styles.auditLink}>
                        View full trail
                      </a>
                    </div>
                  </div>
                )}

                {/* Error Details */}
                {frame.status === 'error' && frame.errorMessage && (
                  <div className={styles.expandedSection}>
                    <h4>⚠️ Error</h4>
                    <div className={styles.errorMessage}>{frame.errorMessage}</div>
                    <button
                      className={styles.retryButton}
                      onClick={() => handleRetryFrame(taskId, frame.frameId)}
                    >
                      🔄 Retry
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Arrow to next frame */}
            {idx < frames.length - 1 && (
              <div className={styles.arrow}>
                <ArrowRight size={20} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Executor Controls */}
      <div className={styles.controls}>
        <button
          className={styles.button}
          onClick={() => handleExecutorControl(taskId, 'pause')}
          disabled={!executorStatus.isRunning}
        >
          ⏸️ Pause
        </button>
        <button
          className={styles.button}
          onClick={() => handleExecutorControl(taskId, 'resume')}
          disabled={executorStatus.isRunning}
        >
          ▶️ Resume
        </button>
      </div>

      {/* Audit Trail Summary */}
      <div className={styles.auditSummary}>
        <p>📊 Audit Events: {timeline.auditEventsCount}</p>
        {timeline.lastAuditHash && (
          <code className={styles.hashCode}>{timeline.lastAuditHash.substring(0, 32)}...</code>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Helper Functions
// ============================================================================

async function handleExecutorControl(taskId: string, action: 'pause' | 'resume') {
  try {
    const response = await fetch(`/api/v1/timeline/executor/${taskId}/${action}`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    console.log(`Executor ${action} successful:`, data);
  } catch (err) {
    console.error(`Failed to ${action} executor:`, err);
  }
}

async function handleRetryFrame(taskId: string, frameId: string) {
  try {
    const response = await fetch(`/api/v1/timeline/executor/${taskId}/retry-frame/${frameId}`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    console.log('Frame retry initiated:', data);
  } catch (err) {
    console.error('Failed to retry frame:', err);
  }
}
