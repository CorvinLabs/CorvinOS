/**
 * GraphInspector must render what the backend actually delivers.
 *
 * routes/vibe_engineering.py::get_audit_chain maps every real chain record to
 * {id, type, timestamp, hash, prev_hash, lom_hash, tenant_id, event_type,
 * details, severity}. None of the design-time typed fields (confidence,
 * entropy_score, decision_name, latency_ms, input …) is populated. On
 * 2026-09-04, the first click on a graph node crashed the page:
 * "Cannot read properties of undefined (reading 'toFixed')".
 *
 * One backend-shaped event per classified `type`; the Inspector must render
 * each without throwing and surface the chain record (event_type + details).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GraphInspector } from '@/pages/vibe-engineering/components/GraphInspector';
import type { AnyAuditEvent, AuditGraph } from '@/types/audit-graph';

const TYPES = ['skill_executed', 'learning_event', 'decision', 'context_snapshot', 'error'] as const;

function backendEvent(type: (typeof TYPES)[number], i: number): AnyAuditEvent {
  return {
    id: `f355e4a0-0000-4000-8000-00000000000${i}`,
    type,
    timestamp: '2026-09-03T11:08:53.513570',
    hash: `hash${i}`,
    prev_hash: i ? `hash${i - 1}` : '',
    lom_hash: '734f2c6c289ca751',
    tenant_id: '_default',
    event_type: `chain_event_${type}`,
    details: { layer_name: 'style', version: '1.0' },
    severity: 'INFO',
  } as AnyAuditEvent;
}

const events = TYPES.map((t, i) => backendEvent(t, i));
const graph: AuditGraph = {
  nodes: events.map((e) => ({ id: e.id, type: e.type, timestamp: e.timestamp, hash: e.hash, lom_hash: e.lom_hash, label: e.id, data: e })),
  edges: [],
  metadata: { chainHeight: events.length, nodeCount: events.length, edgeCount: 0, timespan: { start: '', end: '' }, snapshotFreshness_ms: 0 },
};

describe('GraphInspector with backend-shaped events', () => {
  for (const ev of events) {
    it(`renders a ${ev.type} chain record without throwing`, () => {
      expect(() => render(<GraphInspector event={ev} graph={graph} />)).not.toThrow();
      expect(screen.getByTestId('chain-record')).toHaveTextContent(`chain_event_${ev.type}`);
      expect(screen.getByTestId('chain-record')).toHaveTextContent('layer_name');
    });
  }
});
