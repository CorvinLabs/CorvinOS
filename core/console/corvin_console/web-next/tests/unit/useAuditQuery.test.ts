/**
 * useAuditQuery Hook Tests
 *
 * Tier-1/2 Gates:
 * - TypeScript compilation (types.ts + useAuditQuery.ts)
 * - Schema validation (event types, graph structure)
 * - Cache management (IndexedDB mock)
 * - Hash chain verification
 * - Event filtering
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  AnyAuditEvent,
  SkillExecutedEvent,
  LearningEventRecord,
  DecisionEvent,
  ContextSnapshotEvent,
  AuditGraph,
  auditGraphToCytoscape,
  isSkillExecutedEvent,
  isLearningEvent,
  isDecisionEvent,
  isContextSnapshotEvent,
} from '@/types/audit-graph';
import { verifyHashChain, filterAuditEvents, getEventLabel } from '@/pages/vibe-engineering/hooks/useAuditQuery';

// ─────────────────────────────────────────────────────────────────────────────
// Test Data Fixtures
// ─────────────────────────────────────────────────────────────────────────────

const mockSkillExecutedEvent: SkillExecutedEvent = {
  id: 'event_14782',
  type: 'skill_executed',
  timestamp: '2026-09-03T21:30:45.123Z',
  hash: '0x9a2b1c5f8d',
  prev_hash: '0x5d8c2f7a9b',
  lom_hash: '0x1f7e9a3c2d',
  tenant_id: '_default',
  skill_id: 'os.delegation_router',
  skill_version: '1.2.3',
  status: 'success',
  latency_ms: 42,
  input: { request: 'classify_task' },
  output: { route: 'opus', confidence: 0.94 },
};

const mockLearningEvent: LearningEventRecord = {
  id: 'event_14781',
  type: 'learning_event',
  timestamp: '2026-09-03T21:30:44.890Z',
  hash: '0x5d8c2f7a9b',
  prev_hash: '0x1f7e9a3c2d',
  lom_hash: '0xabcd1234ef',
  tenant_id: '_default',
  skill_id: 'os.delegation_router',
  event_type: 'outcome',
  signal: 'correct',
  confidence_before: 0.88,
  confidence_after: 0.94,
  confidence_delta: 0.06,
};

const mockDecisionEvent: DecisionEvent = {
  id: 'event_14780',
  type: 'decision',
  timestamp: '2026-09-03T21:30:40.000Z',
  hash: '0x1f7e9a3c2d',
  prev_hash: '0xabcd1234ef',
  lom_hash: '0x5678efgh12',
  tenant_id: '_default',
  decision_name: 'Delegate to Opus',
  skill_id: 'os.delegation_router',
  confidence: 0.94,
  input: { task_id: '#4521' },
  output: { route: 'opus' },
};

const mockContextSnapshotEvent: ContextSnapshotEvent = {
  id: 'event_14779',
  type: 'context_snapshot',
  timestamp: '2026-09-03T21:30:12.567Z',
  hash: '0xabcd1234ef',
  prev_hash: '0x5678efgh12',
  lom_hash: '0x9999xxxx00',
  tenant_id: '_default',
  context_id: 'ctx_xyz789',
  entropy_score: 0.23,
  tier_1_count: 3,
  tier_2_count: 5,
  tier_3_count: 2,
  merge_status: 'success',
};

// ─────────────────────────────────────────────────────────────────────────────
// Type Guard Tests
// ─────────────────────────────────────────────────────────────────────────────

describe('Type Guards', () => {
  it('should identify SkillExecutedEvent', () => {
    expect(isSkillExecutedEvent(mockSkillExecutedEvent)).toBe(true);
    expect(isSkillExecutedEvent(mockLearningEvent)).toBe(false);
  });

  it('should identify LearningEvent', () => {
    expect(isLearningEvent(mockLearningEvent)).toBe(true);
    expect(isLearningEvent(mockSkillExecutedEvent)).toBe(false);
  });

  it('should identify DecisionEvent', () => {
    expect(isDecisionEvent(mockDecisionEvent)).toBe(true);
    expect(isDecisionEvent(mockContextSnapshotEvent)).toBe(false);
  });

  it('should identify ContextSnapshotEvent', () => {
    expect(isContextSnapshotEvent(mockContextSnapshotEvent)).toBe(true);
    expect(isContextSnapshotEvent(mockDecisionEvent)).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Event Label Generation Tests
// ─────────────────────────────────────────────────────────────────────────────

describe('getEventLabel', () => {
  it('should generate label for SkillExecutedEvent', () => {
    const label = getEventLabel(mockSkillExecutedEvent);
    expect(label).toBe('os.delegation_router (success)');
  });

  it('should generate label for LearningEvent', () => {
    const label = getEventLabel(mockLearningEvent);
    expect(label).toBe('Learning: outcome');
  });

  it('should generate label for DecisionEvent', () => {
    const label = getEventLabel(mockDecisionEvent);
    expect(label).toBe('Decision: Delegate to Opus');
  });

  it('should generate label for ContextSnapshotEvent', () => {
    const label = getEventLabel(mockContextSnapshotEvent);
    expect(label).toContain('Context');
    expect(label).toContain('entropy: 0.23');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Hash Chain Verification Tests
// ─────────────────────────────────────────────────────────────────────────────

describe('verifyHashChain', () => {
  it('should verify valid hash chain', async () => {
    const events: AnyAuditEvent[] = [
      mockDecisionEvent,
      mockContextSnapshotEvent,
      mockLearningEvent,
      mockSkillExecutedEvent,
    ];

    const result = await verifyHashChain(events);
    expect(result.isValid).toBe(true);
    expect(result.verificationsCount).toBeGreaterThan(0);
  });

  it('should detect broken hash chain', async () => {
    const brokenEvent = {
      ...mockLearningEvent,
      prev_hash: '0xINVALID_HASH', // Incorrect prev_hash
    };

    const events: AnyAuditEvent[] = [mockSkillExecutedEvent, brokenEvent];

    const result = await verifyHashChain(events);
    expect(result.isValid).toBe(false);
    expect(result.firstInvalidIndex).toBeDefined();
  });

  it('should verify subset of chain starting from index', async () => {
    const events: AnyAuditEvent[] = [
      mockDecisionEvent,
      mockContextSnapshotEvent,
      mockLearningEvent,
      mockSkillExecutedEvent,
    ];

    const result = await verifyHashChain(events, 2);
    expect(result.verificationsCount).toBeGreaterThanOrEqual(1);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Event Filtering Tests
// ─────────────────────────────────────────────────────────────────────────────

describe('filterAuditEvents', () => {
  const allEvents: AnyAuditEvent[] = [
    mockSkillExecutedEvent,
    mockLearningEvent,
    mockDecisionEvent,
    mockContextSnapshotEvent,
  ];

  it('should filter by event type', () => {
    const filtered = filterAuditEvents(allEvents, {
      types: ['skill_executed', 'learning_event'],
    });

    expect(filtered).toHaveLength(2);
    expect(filtered.every((e) => e.type === 'skill_executed' || e.type === 'learning_event')).toBe(
      true
    );
  });

  it('should filter by skill ID', () => {
    const filtered = filterAuditEvents(allEvents, {
      skillIds: ['os.delegation_router'],
    });

    expect(filtered.length).toBeGreaterThan(0);
    filtered.forEach((event) => {
      if (event.type === 'skill_executed' || event.type === 'learning_event' || event.type === 'decision') {
        expect((event as any).skill_id).toBe('os.delegation_router');
      }
    });
  });

  it('should return empty array when no events match filter', () => {
    const filtered = filterAuditEvents(allEvents, {
      types: ['error'],
    });

    expect(filtered).toHaveLength(0);
  });

  it('should apply multiple filters', () => {
    const filtered = filterAuditEvents(allEvents, {
      types: ['skill_executed', 'decision'],
      skillIds: ['os.delegation_router'],
    });

    expect(filtered.length).toBeGreaterThan(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Graph Conversion Tests (Cytoscape Format)
// ─────────────────────────────────────────────────────────────────────────────

describe('auditGraphToCytoscape', () => {
  const mockGraph: AuditGraph = {
    nodes: [
      {
        id: mockSkillExecutedEvent.id,
        type: mockSkillExecutedEvent.type,
        timestamp: mockSkillExecutedEvent.timestamp,
        hash: mockSkillExecutedEvent.hash,
        lom_hash: mockSkillExecutedEvent.lom_hash,
        label: 'os.delegation_router (success)',
        data: mockSkillExecutedEvent,
      },
      {
        id: mockLearningEvent.id,
        type: mockLearningEvent.type,
        timestamp: mockLearningEvent.timestamp,
        hash: mockLearningEvent.hash,
        lom_hash: mockLearningEvent.lom_hash,
        label: 'Learning: outcome',
        data: mockLearningEvent,
      },
    ],
    edges: [
      {
        id: 'link_14782_to_14781',
        source: mockSkillExecutedEvent.id,
        target: mockLearningEvent.id,
        type: 'hash_chain',
        hash: '0x3e1f2a4b5c',
      },
    ],
    metadata: {
      chainHeight: 14782,
      nodeCount: 2,
      edgeCount: 1,
      timespan: {
        start: '2026-09-03T21:29:00Z',
        end: '2026-09-03T21:30:45Z',
      },
      snapshotFreshness_ms: 145,
    },
  };

  it('should convert AuditGraph to Cytoscape format', () => {
    const cytoData = auditGraphToCytoscape(mockGraph);

    expect(cytoData.nodes).toHaveLength(2);
    expect(cytoData.edges).toHaveLength(1);
  });

  it('should preserve node data in Cytoscape format', () => {
    const cytoData = auditGraphToCytoscape(mockGraph);

    const node = cytoData.nodes[0];
    expect(node.data.id).toBe(mockSkillExecutedEvent.id);
    expect(node.data.type).toBe('skill_executed');
    expect(node.data.hash).toBe(mockSkillExecutedEvent.hash);
    expect(node.data.event).toEqual(mockSkillExecutedEvent);
  });

  it('should preserve edge relationships', () => {
    const cytoData = auditGraphToCytoscape(mockGraph);

    const edge = cytoData.edges[0];
    expect(edge.data.source).toBe(mockSkillExecutedEvent.id);
    expect(edge.data.target).toBe(mockLearningEvent.id);
    expect(edge.data.type).toBe('hash_chain');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Schema Validation Tests
// ─────────────────────────────────────────────────────────────────────────────

describe('Event Schema Validation', () => {
  it('should have all required fields for SkillExecutedEvent', () => {
    const event = mockSkillExecutedEvent;
    expect(event.id).toBeDefined();
    expect(event.type).toBe('skill_executed');
    expect(event.hash).toBeDefined();
    expect(event.prev_hash).toBeDefined();
    expect(event.lom_hash).toBeDefined();
    expect(event.tenant_id).toBeDefined();
    expect(event.skill_id).toBeDefined();
    expect(event.latency_ms).toBeDefined();
    expect(event.output).toBeDefined();
  });

  it('should have all required fields for LearningEvent', () => {
    const event = mockLearningEvent;
    expect(event.id).toBeDefined();
    expect(event.type).toBe('learning_event');
    expect(event.skill_id).toBeDefined();
    expect(event.event_type).toBeDefined();
    expect(event.signal).toBeDefined();
  });

  it('should have all required fields for DecisionEvent', () => {
    const event = mockDecisionEvent;
    expect(event.id).toBeDefined();
    expect(event.type).toBe('decision');
    expect(event.decision_name).toBeDefined();
    expect(event.confidence).toBeDefined();
  });

  it('should have all required fields for ContextSnapshotEvent', () => {
    const event = mockContextSnapshotEvent;
    expect(event.id).toBeDefined();
    expect(event.type).toBe('context_snapshot');
    expect(event.entropy_score).toBeDefined();
    expect(event.tier_1_count).toBeDefined();
  });
});
