/**
 * Method Discovery — discovered workstyle patterns (ADR-0548, Phase 1).
 *
 * Renders what `GET /v1/console/learning/patterns` actually returns. There is
 * no mock-data path on purpose: this panel is the operator-facing half of an
 * audit proof, and a component that renders plausible numbers when the backend
 * is down is worse than one that says nothing — it would show "learned"
 * patterns the audit chain does not contain.
 *
 * Deliberately NOT registered in PANELS/NAV_GROUPS. On 2026-09-05 the operator
 * collapsed Vibe Engineering to a single panel and retired four duplicate
 * sidebar views; adding a fifth would re-open exactly that. It mounts as a
 * section inside the Learning Dashboard instead. If it ever does get its own
 * route, it needs BOTH registrations plus a GATED_FLAGS entry — see CLAUDE.md.
 *
 * EU AI Act Art. 50: every pattern shows its confidence derivation (N, success
 * rate, recency and the four factors), not just a score. The operator has to be
 * able to see WHY something was called a method.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

// ─ Types (mirror routes/learning.py::MethodPatternsResponse) ────────────────

export interface ConfidenceDerivation {
  confidence: number;
  base_rate: number;
  success_boost: number;
  sample_size_boost: number;
  recency_boost: number;
  sequence_length: number;
  observation_count: number;
  success_rate: number;
  days_since_last_observation: number;
  exotic_combo: boolean;
  capped: boolean;
}

export interface MethodPattern {
  pattern_id: string;
  pattern_name: string;
  task_type: string;
  skill_sequence: string[];
  success_rate: number;
  observation_count: number;
  confidence_score: number;
  first_observed: string;
  last_observed: string;
  observation_ids: string[];
  user_confirmed: boolean;
  discovered: boolean;
  confidence_derivation: ConfidenceDerivation;
  confidence_explanation: string;
}

export interface MethodPatternsResponse {
  tenant_id: string;
  threshold: number;
  observation_count: number;
  chain_verified: boolean;
  chain_error: string | null;
  patterns: MethodPattern[];
}

// ─ Presentation helpers ─────────────────────────────────────────────────────

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** Green once the pattern cleared the bar, amber while it is still a candidate. */
const confidenceColor = (p: MethodPattern) =>
  p.discovered ? '#22c55e' : p.confidence_score >= 0.5 ? '#f59e0b' : '#94a3b8';

const ConfidenceBar: React.FC<{ pattern: MethodPattern; threshold: number }> = ({
  pattern,
  threshold,
}) => (
  <div style={{ position: 'relative', height: 6, background: '#1e293b', borderRadius: 3 }}>
    <div
      style={{
        width: `${Math.min(100, pattern.confidence_score * 100)}%`,
        height: '100%',
        background: confidenceColor(pattern),
        borderRadius: 3,
      }}
    />
    {/* The discovery threshold, drawn in place so "how far off is it?" is visible. */}
    <div
      title={`discovery threshold ${threshold}`}
      style={{
        position: 'absolute',
        left: `${threshold * 100}%`,
        top: -2,
        width: 1,
        height: 10,
        background: '#e2e8f0',
      }}
    />
  </div>
);

const PatternCard: React.FC<{
  pattern: MethodPattern;
  threshold: number;
  onConfirm: (id: string) => void;
  busy: boolean;
}> = ({ pattern, threshold, onConfirm, busy }) => {
  const [open, setOpen] = useState(false);
  const d = pattern.confidence_derivation;

  return (
    <div
      data-testid="method-pattern-card"
      style={{
        border: '1px solid #1e293b',
        borderRadius: 8,
        padding: '14px 16px',
        marginBottom: 10,
        background: '#0b1220',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span
          style={{
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            padding: '2px 6px',
            borderRadius: 4,
            background: '#1e293b',
            color: '#94a3b8',
          }}
        >
          {pattern.task_type}
        </span>
        {pattern.discovered && (
          <span style={{ fontSize: 11, color: '#22c55e' }}>discovered</span>
        )}
        {pattern.user_confirmed && (
          <span style={{ fontSize: 11, color: '#38bdf8' }}>confirmed by you</span>
        )}
        <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', fontWeight: 700 }}>
          {pattern.confidence_score.toFixed(3)}
        </span>
      </div>

      <div style={{ fontSize: 13, marginBottom: 8, color: '#e2e8f0' }}>
        {pattern.skill_sequence.join('  →  ')}
      </div>

      <ConfidenceBar pattern={pattern} threshold={threshold} />

      <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 12, color: '#94a3b8' }}>
        <span>N = {pattern.observation_count}</span>
        <span>success {pct(pattern.success_rate)}</span>
        <span>last seen {d.days_since_last_observation}d ago</span>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          style={{
            marginLeft: 'auto',
            background: 'none',
            border: 'none',
            color: '#38bdf8',
            cursor: 'pointer',
            fontSize: 12,
          }}
        >
          {open ? 'hide' : 'why?'}
        </button>
        {!pattern.user_confirmed && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onConfirm(pattern.pattern_id)}
            style={{
              background: 'none',
              border: '1px solid #334155',
              borderRadius: 4,
              color: '#e2e8f0',
              cursor: busy ? 'wait' : 'pointer',
              fontSize: 12,
              padding: '1px 8px',
            }}
          >
            confirm
          </button>
        )}
      </div>

      {open && (
        <div
          style={{
            marginTop: 10,
            paddingTop: 10,
            borderTop: '1px solid #1e293b',
            fontSize: 12,
            color: '#94a3b8',
            fontFamily: 'ui-monospace, monospace',
            lineHeight: 1.7,
          }}
        >
          <div>{pattern.confidence_explanation}</div>
          <div style={{ marginTop: 6 }}>
            learned from {pattern.observation_ids.length} audited observations
            {pattern.observation_ids[0] && (
              <> · first {pattern.observation_ids[0].slice(0, 12)}…</>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// ─ Panel ────────────────────────────────────────────────────────────────────

export const MethodDiscoveryPanel: React.FC = () => {
  const [data, setData] = useState<MethodPatternsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api<MethodPatternsResponse>('/learning/patterns'));
      setError(null);
    } catch (e) {
      // Surface the failure instead of falling back to placeholder patterns.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const confirm = useCallback(
    async (patternId: string) => {
      setBusy(true);
      try {
        await api(`/learning/patterns/${encodeURIComponent(patternId)}/confirm`, {
          method: 'POST',
        });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div data-testid="method-discovery-panel">
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px', color: '#e2e8f0' }}>
        Method Discovery
      </h2>
      <p style={{ fontSize: 13, color: '#94a3b8', margin: '0 0 14px' }}>
        Workflow patterns learned from your completed tasks, stratified by task type.
        {data && ` ${data.observation_count} observations on the chain.`}
      </p>

      {/* A broken chain is shown, never swallowed — it is the one signal that
          says the evidence behind these patterns cannot be trusted. */}
      {data && !data.chain_verified && (
        <div
          data-testid="method-chain-broken"
          style={{
            border: '1px solid #b91c1c',
            background: '#1f0d0d',
            borderRadius: 6,
            padding: '8px 12px',
            marginBottom: 12,
            fontSize: 12,
            color: '#fca5a5',
          }}
        >
          Audit chain verification FAILED — {data.chain_error ?? 'unknown error'}. Patterns
          below cannot be treated as proven.
        </div>
      )}

      {loading && <div style={{ fontSize: 13, color: '#94a3b8' }}>Loading patterns…</div>}

      {error && !loading && (
        <div data-testid="method-discovery-error" style={{ fontSize: 13, color: '#fca5a5' }}>
          Could not load patterns: {error}
        </div>
      )}

      {data && !error && data.patterns.length === 0 && (
        <div style={{ fontSize: 13, color: '#94a3b8' }}>
          No patterns yet. Complete a few tasks of the same type and they will appear here.
        </div>
      )}

      {data &&
        !error &&
        data.patterns.map((p) => (
          <PatternCard
            key={p.pattern_id}
            pattern={p}
            threshold={data.threshold}
            onConfirm={confirm}
            busy={busy}
          />
        ))}
    </div>
  );
};

export default MethodDiscoveryPanel;
