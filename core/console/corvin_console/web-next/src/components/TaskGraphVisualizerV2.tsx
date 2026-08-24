import React, { useEffect, useRef, useState } from 'react';

interface TaskNode {
  id: string;
  type: string;
  timestamp: string;
  data: Record<string, any>;
  status?: 'completed' | 'running' | 'queued' | 'failed' | 'skipped';
}

interface TaskEdge {
  from_id: string;
  to_id: string;
  edge_type: string;
  label: string;
}

interface TaskGraph {
  task_id: string;
  nodes: Record<string, TaskNode>;
  edges: TaskEdge[];
}

const statusColors: Record<string, string> = {
  completed: '#22c55e',
  running: '#3b82f6',
  queued: '#eab308',
  failed: '#ef4444',
  skipped: '#9ca3af',
};

export const TaskGraphVisualizerV2: React.FC<{ taskId?: string }> = ({ taskId = 'demo' }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [graph, setGraph] = useState<TaskGraph | null>(null);
  const [selectedNode, setSelectedNode] = useState<TaskNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>('all');

  useEffect(() => {
    const fetchGraph = async () => {
      try {
        const res = await fetch(`/v1/console/api/tasks/${taskId}/graph`);
        if (!res.ok) throw new Error('Failed to fetch graph');
        const data = await res.json();
        setGraph(data);
      } catch (err) {
        console.error('Graph fetch failed:', err);
        setGraph(createDemoGraph());
      } finally {
        setLoading(false);
      }
    };
    fetchGraph();
  }, [taskId]);

  useEffect(() => {
    if (!graph || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    ctx.fillStyle = '#fafafa';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const nodes = Object.values(graph.nodes);
    const filteredNodes = filter === 'all'
      ? nodes
      : nodes.filter(n => n.status === filter);

    const nodePositions = new Map<string, { x: number; y: number }>();
    filteredNodes.forEach((node, i) => {
      const col = i % 10;
      const row = Math.floor(i / 10);
      nodePositions.set(node.id, {
        x: 100 + col * 100,
        y: 100 + row * 100,
      });
    });

    ctx.strokeStyle = '#ddd';
    ctx.lineWidth = 1;
    graph.edges.forEach(edge => {
      const from = nodePositions.get(edge.from_id);
      const to = nodePositions.get(edge.to_id);
      if (from && to) {
        ctx.beginPath();
        ctx.moveTo(from.x, from.y);
        ctx.lineTo(to.x, to.y);
        ctx.stroke();
      }
    });

    filteredNodes.forEach(node => {
      const pos = nodePositions.get(node.id);
      if (!pos) return;

      const isSelected = selectedNode?.id === node.id;
      const radius = isSelected ? 15 : 12;
      const color = statusColors[node.status || 'skipped'] || '#9ca3af';

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2);
      ctx.fill();

      if (isSelected) {
        ctx.strokeStyle = '#000';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      ctx.fillStyle = '#000';
      ctx.font = '10px Arial';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(node.id.slice(0, 6), pos.x, pos.y);
    });
  }, [graph, selectedNode, filter]);

  if (loading) {
    return <div className="p-4 text-center">Loading task graph...</div>;
  }

  return (
    <div className="flex h-screen gap-4 p-4 bg-gray-50">
      <div className="flex-1 border rounded-lg bg-white shadow overflow-hidden flex flex-col">
        <div className="p-3 border-b bg-gray-100 flex gap-3 items-center justify-between">
          <div className="flex gap-2">
            <label className="text-sm font-medium">Filter:</label>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="border px-2 py-1 text-sm rounded"
            >
              <option value="all">All Tasks</option>
              <option value="completed">✅ Completed</option>
              <option value="running">🔵 Running</option>
              <option value="queued">🟡 Queued</option>
              <option value="failed">🔴 Failed</option>
            </select>
          </div>
          <span className="text-sm text-gray-600">
            {Object.keys(graph?.nodes || {}).length} tasks
          </span>
        </div>
        <canvas ref={canvasRef} className="flex-1 cursor-pointer" />
      </div>

      {selectedNode && (
        <div className="w-80 border rounded-lg bg-white shadow-lg flex flex-col overflow-hidden">
          <div className="bg-gradient-to-r from-indigo-500 to-indigo-600 text-white p-4">
            <h3 className="font-bold text-lg">{selectedNode.id}</h3>
            <p className="text-sm opacity-90">{selectedNode.type}</p>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase">Status</label>
              <div className="flex items-center gap-2 mt-1">
                <div
                  className="w-4 h-4 rounded-full"
                  style={{ backgroundColor: statusColors[selectedNode.status || 'skipped'] }}
                />
                <span className="font-medium">{selectedNode.status || 'unknown'}</span>
              </div>
            </div>
            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase">Timestamp</label>
              <p className="text-sm text-gray-700 mt-1">{selectedNode.timestamp}</p>
            </div>
            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase">Details</label>
              <pre className="text-xs bg-gray-100 p-2 rounded mt-1 max-h-48 overflow-y-auto">
                {JSON.stringify(selectedNode.data, null, 2)}
              </pre>
            </div>
          </div>
          <button
            onClick={() => setSelectedNode(null)}
            className="w-full p-3 border-t text-gray-600 hover:bg-gray-100 text-sm font-medium"
          >
            Close
          </button>
        </div>
      )}
    </div>
  );
};

function createDemoGraph(): TaskGraph {
  return {
    task_id: 'demo',
    nodes: {
      'task-001': { id: 'task-001', type: 'model_train', timestamp: '2026-08-24T21:00:00Z', status: 'completed', data: { duration: 120 } },
      'task-002': { id: 'task-002', type: 'data_prep', timestamp: '2026-08-24T20:55:00Z', status: 'completed', data: { rows: 50000 } },
      'task-003': { id: 'task-003', type: 'validation', timestamp: '2026-08-24T21:05:00Z', status: 'running', data: { progress: 0.65 } },
      'task-004': { id: 'task-004', type: 'deploy', timestamp: '2026-08-24T21:10:00Z', status: 'queued', data: {} },
    },
    edges: [
      { from_id: 'task-002', to_id: 'task-001', edge_type: 'dependency', label: 'input' },
      { from_id: 'task-001', to_id: 'task-003', edge_type: 'dependency', label: 'model' },
      { from_id: 'task-003', to_id: 'task-004', edge_type: 'dependency', label: 'approval' },
    ],
  };
}
