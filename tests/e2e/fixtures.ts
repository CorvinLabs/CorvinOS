/**
 * E2E Test Fixtures for Task Graph Visualization
 *
 * Provides reusable test data:
 * - Small/medium/large graph fixtures
 * - Malformed graph examples
 * - Test task IDs
 */

import { test as base } from '@playwright/test';

/** API endpoints */
export const API_BASE = 'http://127.0.0.1:8765/api';
export const TASK_GRAPH_ENDPOINT = (taskId: string) => `${API_BASE}/tasks/${taskId}/graph`;

/** Small graph: 10 nodes, 12 edges */
export const smallGraph = {
  task_id: 'task_small_001',
  created_at: '2026-08-24T10:00:00Z',
  nodes: {
    'start_decision': {
      id: 'start_decision',
      type: 'decision',
      timestamp: '2026-08-24T10:00:00Z',
      data: {
        text: 'Analyze requirements',
        phase: 'planning',
        confidence: 0.95,
      },
    },
    'analysis_checkpoint': {
      id: 'analysis_checkpoint',
      type: 'checkpoint',
      timestamp: '2026-08-24T10:05:00Z',
      data: {
        iteration_num: 1,
        context_size: 8000,
        reduction_pct: 0.15,
      },
    },
    'impl_decision': {
      id: 'impl_decision',
      type: 'decision',
      timestamp: '2026-08-24T10:10:00Z',
      data: {
        text: 'Select implementation approach',
        phase: 'design',
        confidence: 0.87,
      },
    },
    'impl_error': {
      id: 'impl_error',
      type: 'error',
      timestamp: '2026-08-24T10:15:00Z',
      data: {
        error_type: 'ValidationError',
        message: 'Schema mismatch detected',
        severity: 'warning',
      },
    },
    'recovery_checkpoint': {
      id: 'recovery_checkpoint',
      type: 'checkpoint',
      timestamp: '2026-08-24T10:20:00Z',
      data: {
        iteration_num: 2,
        context_size: 7500,
        reduction_pct: 0.20,
      },
    },
    'test_decision': {
      id: 'test_decision',
      type: 'decision',
      timestamp: '2026-08-24T10:25:00Z',
      data: {
        text: 'Verify implementation',
        phase: 'testing',
        confidence: 0.92,
      },
    },
    'context_node': {
      id: 'context_node',
      type: 'context',
      timestamp: '2026-08-24T10:30:00Z',
      data: {
        context_type: 'memory_palace',
        reduction_pct: 0.25,
        retained_keys: ['focus', 'constraints', 'prior_decisions'],
      },
    },
    'metric_node': {
      id: 'metric_node',
      type: 'metric',
      timestamp: '2026-08-24T10:35:00Z',
      data: {
        metric_type: 'latency',
        value: 2.45,
        unit: 'seconds',
      },
    },
    'subgoal_node': {
      id: 'subgoal_node',
      type: 'subgoal',
      timestamp: '2026-08-24T10:40:00Z',
      data: {
        description: 'Complete unit tests',
        parent_goal: 'impl_decision',
        status: 'in_progress',
      },
    },
    'final_checkpoint': {
      id: 'final_checkpoint',
      type: 'checkpoint',
      timestamp: '2026-08-24T10:45:00Z',
      data: {
        iteration_num: 3,
        context_size: 6000,
        reduction_pct: 0.30,
      },
    },
  },
  edges: [
    {
      from_id: 'start_decision',
      to_id: 'analysis_checkpoint',
      edge_type: 'hard_dependency',
      label: 'analysis complete',
      metadata: { confidence: 0.99 },
    },
    {
      from_id: 'analysis_checkpoint',
      to_id: 'impl_decision',
      edge_type: 'temporal',
      label: 'iteration 1 → 2',
      metadata: { phase: 'checkpoint_sequence' },
    },
    {
      from_id: 'impl_decision',
      to_id: 'impl_error',
      edge_type: 'data_flow',
      label: 'validation',
      metadata: { confidence: 0.85 },
    },
    {
      from_id: 'impl_error',
      to_id: 'recovery_checkpoint',
      edge_type: 'data_flow',
      label: 'recovery',
      metadata: { error_type: 'ValidationError' },
    },
    {
      from_id: 'recovery_checkpoint',
      to_id: 'test_decision',
      edge_type: 'temporal',
      label: 'iteration 2 → 3',
      metadata: { phase: 'checkpoint_sequence' },
    },
    {
      from_id: 'test_decision',
      to_id: 'context_node',
      edge_type: 'soft_dependency',
      label: 'context refresh',
      metadata: { confidence: 0.75 },
    },
    {
      from_id: 'context_node',
      to_id: 'final_checkpoint',
      edge_type: 'data_flow',
      label: 'context snapshot',
      metadata: { reduction_pct: 0.25 },
    },
    {
      from_id: 'final_checkpoint',
      to_id: 'metric_node',
      edge_type: 'soft_dependency',
      label: 'measurement point',
      metadata: { metric_type: 'latency' },
    },
    {
      from_id: 'impl_decision',
      to_id: 'subgoal_node',
      edge_type: 'soft_dependency',
      label: 'spawned subgoal',
      metadata: { confidence: 0.80 },
    },
    {
      from_id: 'subgoal_node',
      to_id: 'recovery_checkpoint',
      edge_type: 'hard_dependency',
      label: 'subgoal complete',
      metadata: { confidence: 0.90 },
    },
    {
      from_id: 'recovery_checkpoint',
      to_id: 'context_node',
      edge_type: 'temporal',
      label: 'checkpoint → context',
      metadata: {},
    },
    {
      from_id: 'test_decision',
      to_id: 'final_checkpoint',
      edge_type: 'hard_dependency',
      label: 'testing complete',
      metadata: { confidence: 0.95 },
    },
  ],
  nodes_by_type: {
    decision: ['start_decision', 'impl_decision', 'test_decision'],
    checkpoint: ['analysis_checkpoint', 'recovery_checkpoint', 'final_checkpoint'],
    error: ['impl_error'],
    context: ['context_node'],
    metric: ['metric_node'],
    subgoal: ['subgoal_node'],
  },
  iterations: {
    1: 'analysis_checkpoint',
    2: 'recovery_checkpoint',
    3: 'final_checkpoint',
  },
};

/** Medium graph: 50 nodes, 120 edges (for stress testing) */
export const mediumGraph = generateGraph('task_medium_001', 50, 120);

/** Large graph: 500 nodes, 1200 edges (performance testing) */
export const largeGraph = generateGraph('task_large_001', 500, 1200);

/** Malformed graph: broken edge references */
export const malformedGraph = {
  task_id: 'task_malformed_001',
  created_at: '2026-08-24T10:00:00Z',
  nodes: {
    'node_a': {
      id: 'node_a',
      type: 'decision',
      timestamp: '2026-08-24T10:00:00Z',
      data: { text: 'Decision A' },
    },
    'node_b': {
      id: 'node_b',
      type: 'checkpoint',
      timestamp: '2026-08-24T10:05:00Z',
      data: { iteration_num: 1 },
    },
  },
  edges: [
    // Valid edge
    {
      from_id: 'node_a',
      to_id: 'node_b',
      edge_type: 'hard_dependency',
      label: 'valid edge',
      metadata: {},
    },
    // Broken edge: to_id doesn't exist
    {
      from_id: 'node_b',
      to_id: 'node_nonexistent',
      edge_type: 'data_flow',
      label: 'broken edge',
      metadata: {},
    },
    // Broken edge: from_id doesn't exist
    {
      from_id: 'node_missing',
      to_id: 'node_a',
      edge_type: 'soft_dependency',
      label: 'another broken edge',
      metadata: {},
    },
  ],
  nodes_by_type: {
    decision: ['node_a'],
    checkpoint: ['node_b'],
  },
  iterations: { 1: 'node_b' },
};

/** Empty graph: 0 nodes */
export const emptyGraph = {
  task_id: 'task_empty_001',
  created_at: '2026-08-24T10:00:00Z',
  nodes: {},
  edges: [],
  nodes_by_type: {},
  iterations: {},
};

/** Graph with cycles (invalid DAG) */
export const cyclicGraph = {
  task_id: 'task_cyclic_001',
  created_at: '2026-08-24T10:00:00Z',
  nodes: {
    'node_1': {
      id: 'node_1',
      type: 'decision',
      timestamp: '2026-08-24T10:00:00Z',
      data: { text: 'Decision 1' },
    },
    'node_2': {
      id: 'node_2',
      type: 'decision',
      timestamp: '2026-08-24T10:01:00Z',
      data: { text: 'Decision 2' },
    },
    'node_3': {
      id: 'node_3',
      type: 'decision',
      timestamp: '2026-08-24T10:02:00Z',
      data: { text: 'Decision 3' },
    },
  },
  edges: [
    // Creates cycle: 1 → 2 → 3 → 1
    {
      from_id: 'node_1',
      to_id: 'node_2',
      edge_type: 'hard_dependency',
      label: '1 → 2',
      metadata: {},
    },
    {
      from_id: 'node_2',
      to_id: 'node_3',
      edge_type: 'hard_dependency',
      label: '2 → 3',
      metadata: {},
    },
    {
      from_id: 'node_3',
      to_id: 'node_1',
      edge_type: 'hard_dependency',
      label: '3 → 1 (creates cycle)',
      metadata: {},
    },
  ],
  nodes_by_type: {
    decision: ['node_1', 'node_2', 'node_3'],
  },
  iterations: {},
};

/** Test task IDs for parameterized tests */
export const testTaskIds = [
  'task_small_001',
  'task_medium_001',
  'task_large_001',
  'task_nonexistent',
];

/**
 * Generate a procedural graph for performance testing
 */
function generateGraph(taskId: string, nodeCount: number, edgeCount: number) {
  const nodes: Record<string, any> = {};
  const nodeIds: string[] = [];
  const nodeTypes = ['decision', 'checkpoint', 'error', 'context', 'metric'];

  // Create nodes
  for (let i = 0; i < nodeCount; i++) {
    const nodeId = `node_${i}`;
    const nodeType = nodeTypes[i % nodeTypes.length];
    nodes[nodeId] = {
      id: nodeId,
      type: nodeType,
      timestamp: new Date(Date.now() - (nodeCount - i) * 1000).toISOString(),
      data: {
        text: `Node ${i}`,
        value: Math.random(),
      },
    };
    nodeIds.push(nodeId);
  }

  // Create edges (respecting node count)
  const edges: any[] = [];
  const actualEdgeCount = Math.min(edgeCount, nodeCount * (nodeCount - 1));
  for (let i = 0; i < actualEdgeCount; i++) {
    const fromIdx = Math.floor(Math.random() * (nodeCount - 1));
    const toIdx = Math.floor(Math.random() * (nodeCount - 1)) + 1;

    // Ensure from < to for DAG property
    const [from, to] = fromIdx < toIdx ? [fromIdx, toIdx] : [toIdx, fromIdx];

    edges.push({
      from_id: nodeIds[from],
      to_id: nodeIds[to],
      edge_type: ['hard_dependency', 'soft_dependency', 'data_flow', 'temporal'][
        i % 4
      ],
      label: `edge_${i}`,
      metadata: { weight: Math.random() },
    });
  }

  const nodesByType: Record<string, string[]> = {};
  nodeTypes.forEach(type => {
    nodesByType[type] = Object.values(nodes)
      .filter((n: any) => n.type === type)
      .map((n: any) => n.id);
  });

  return {
    task_id: taskId,
    created_at: new Date().toISOString(),
    nodes,
    edges,
    nodes_by_type: nodesByType,
    iterations: {},
  };
}

/**
 * Test fixture with graph data and utilities
 */
export const test = base.extend<{
  graphFixtures: {
    smallGraph: typeof smallGraph;
    mediumGraph: typeof mediumGraph;
    largeGraph: typeof largeGraph;
    emptyGraph: typeof emptyGraph;
    malformedGraph: typeof malformedGraph;
    cyclicGraph: typeof cyclicGraph;
  };
  taskGraphAPI: {
    fetchGraph: (taskId: string) => Promise<any>;
    queryGraph: (taskId: string, query: string) => Promise<any>;
    getSnapshot: (taskId: string, timestamp: string) => Promise<any>;
  };
}>({
  graphFixtures: async ({}, use) => {
    await use({
      smallGraph,
      mediumGraph,
      largeGraph,
      emptyGraph,
      malformedGraph,
      cyclicGraph,
    });
  },

  taskGraphAPI: async ({}, use) => {
    const api = {
      async fetchGraph(taskId: string) {
        const response = await fetch(TASK_GRAPH_ENDPOINT(taskId));
        return response.json();
      },

      async queryGraph(taskId: string, queryType: string) {
        const params = new URLSearchParams({ type: queryType });
        const response = await fetch(
          `${TASK_GRAPH_ENDPOINT(taskId)}/query?${params}`
        );
        return response.json();
      },

      async getSnapshot(taskId: string, timestamp: string) {
        const params = new URLSearchParams({ t: timestamp });
        const response = await fetch(
          `${TASK_GRAPH_ENDPOINT(taskId)}/snapshot?${params}`
        );
        return response.json();
      },
    };

    await use(api);
  },
});
