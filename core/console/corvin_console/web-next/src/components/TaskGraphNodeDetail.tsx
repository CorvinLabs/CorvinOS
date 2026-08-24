/**
 * Task Graph Node Detail Modal
 * ADR-0400: Graph-Native Task Execution Model — Phase 2 Visualization
 *
 * Displays full node data, incoming/outgoing edges, and related nodes.
 * Closes on ESC or click outside.
 */

import { useEffect, useRef, useState } from "react";
import { X, Copy, CheckCircle2 } from "lucide-react";
import { TaskGraph } from "@/lib/taskGraphViz";

interface TaskGraphNodeDetailProps {
  graph: TaskGraph;
  nodeId: string | null;
  onClose: () => void;
}

interface CopyState {
  copied: boolean;
  timeout?: NodeJS.Timeout;
}

export function TaskGraphNodeDetail({
  graph,
  nodeId,
  onClose,
}: TaskGraphNodeDetailProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const copyStateRef = useRef<CopyState>({ copied: false });
  const [copyState, setCopyState] = useState(false);

  // The `nodeId` guard lives BELOW every hook on purpose. It used to sit above
  // useState/useEffect, so a closed modal ran 2 hooks and an open one ran 5 —
  // React aborts that render ("rendered more hooks than during the previous
  // render") and the detail modal never appeared.
  const isOpen = Boolean(nodeId && graph.nodes[nodeId as string]);

  // Close on ESC
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, isOpen]);

  // Close on click outside
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        modalRef.current &&
        e.target instanceof Element &&
        !modalRef.current.contains(e.target)
      ) {
        onClose();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onClose, isOpen]);

  // Narrowing form, so `nodeId` is a string for the rest of the render.
  if (!nodeId || !graph.nodes[nodeId]) {
    return null;
  }

  const node = graph.nodes[nodeId];

  // Find related edges
  const incomingEdges = graph.edges.filter((e) => e.to_id === nodeId);
  const outgoingEdges = graph.edges.filter((e) => e.from_id === nodeId);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopyState(true);
      clearTimeout(copyStateRef.current.timeout);
      copyStateRef.current.timeout = setTimeout(() => {
        setCopyState(false);
      }, 2000);
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div
        ref={modalRef}
        className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-lg bg-white shadow-2xl dark:bg-slate-900"
      >
        {/* Header */}
        <div className="sticky top-0 border-b border-gray-200 bg-gray-50 px-6 py-4 dark:border-slate-700 dark:bg-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className="h-3 w-3 rounded-full"
              style={{
                backgroundColor:
                  {
                    decision: "#3b82f6",
                    error: "#ef4444",
                    checkpoint: "#10b981",
                    context: "#a3a3a3",
                    metric: "#f59e0b",
                    subgoal: "#8b5cf6",
                  }[node.type] || "#a3a3a3",
              }}
            />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              {node.type}
            </h2>
            <span className="text-xs font-mono text-gray-600 dark:text-gray-400">
              {nodeId.substring(0, 12)}...
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
            aria-label="Close modal"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Basic Info */}
          <section>
            <h3 className="mb-3 font-semibold text-gray-900 dark:text-white">
              Node Information
            </h3>
            <div className="space-y-2 text-sm">
              <div className="flex items-center justify-between rounded bg-gray-50 px-3 py-2 dark:bg-slate-800">
                <span className="text-gray-600 dark:text-gray-400">ID:</span>
                <div className="flex items-center gap-2">
                  <code className="font-mono text-gray-900 dark:text-white">
                    {nodeId}
                  </code>
                  <button
                    onClick={() => handleCopy(nodeId)}
                    className="p-1 hover:bg-gray-200 dark:hover:bg-slate-700 rounded"
                    title="Copy node ID"
                  >
                    {copyState ? (
                      <CheckCircle2 className="h-4 w-4 text-green-600" />
                    ) : (
                      <Copy className="h-4 w-4 text-gray-500" />
                    )}
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between rounded bg-gray-50 px-3 py-2 dark:bg-slate-800">
                <span className="text-gray-600 dark:text-gray-400">Type:</span>
                <span className="font-mono text-gray-900 dark:text-white">
                  {node.type}
                </span>
              </div>

              <div className="flex items-center justify-between rounded bg-gray-50 px-3 py-2 dark:bg-slate-800">
                <span className="text-gray-600 dark:text-gray-400">
                  Timestamp:
                </span>
                <div className="flex items-center gap-2">
                  <code className="font-mono text-gray-900 dark:text-white">
                    {new Date(node.timestamp).toLocaleString()}
                  </code>
                  <button
                    onClick={() => handleCopy(node.timestamp)}
                    className="p-1 hover:bg-gray-200 dark:hover:bg-slate-700 rounded"
                    title="Copy timestamp"
                  >
                    {copyState ? (
                      <CheckCircle2 className="h-4 w-4 text-green-600" />
                    ) : (
                      <Copy className="h-4 w-4 text-gray-500" />
                    )}
                  </button>
                </div>
              </div>
            </div>
          </section>

          {/* Node Data */}
          {Object.keys(node.data).length > 0 && (
            <section>
              <h3 className="mb-3 font-semibold text-gray-900 dark:text-white">
                Data
              </h3>
              <div className="rounded bg-gray-50 p-3 dark:bg-slate-800">
                <pre className="overflow-auto font-mono text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words max-h-48">
                  {JSON.stringify(node.data, null, 2)}
                </pre>
              </div>
            </section>
          )}

          {/* Incoming Edges */}
          {incomingEdges.length > 0 && (
            <section>
              <h3 className="mb-3 font-semibold text-gray-900 dark:text-white">
                Incoming Edges ({incomingEdges.length})
              </h3>
              <div className="space-y-2">
                {incomingEdges.map((edge, idx) => (
                  <div
                    key={idx}
                    className="rounded border border-gray-200 bg-gray-50 p-3 dark:border-slate-700 dark:bg-slate-800"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono text-xs text-gray-600 dark:text-gray-400">
                        From:
                      </span>
                      <code className="font-mono text-xs text-gray-900 dark:text-white break-all">
                        {edge.from_id.substring(0, 20)}...
                      </code>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs text-gray-600 dark:text-gray-400">
                        Edge:
                      </span>
                      <span className="font-mono text-xs text-gray-900 dark:text-white">
                        {edge.edge_type}
                      </span>
                    </div>
                    {edge.label && (
                      <div className="mt-1 pt-1 border-t border-gray-200 dark:border-slate-700">
                        <span className="text-xs text-gray-600 dark:text-gray-400">
                          Label:
                        </span>
                        <p className="font-mono text-xs text-gray-900 dark:text-white">
                          {edge.label}
                        </p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Outgoing Edges */}
          {outgoingEdges.length > 0 && (
            <section>
              <h3 className="mb-3 font-semibold text-gray-900 dark:text-white">
                Outgoing Edges ({outgoingEdges.length})
              </h3>
              <div className="space-y-2">
                {outgoingEdges.map((edge, idx) => (
                  <div
                    key={idx}
                    className="rounded border border-gray-200 bg-gray-50 p-3 dark:border-slate-700 dark:bg-slate-800"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono text-xs text-gray-600 dark:text-gray-400">
                        To:
                      </span>
                      <code className="font-mono text-xs text-gray-900 dark:text-white break-all">
                        {edge.to_id.substring(0, 20)}...
                      </code>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs text-gray-600 dark:text-gray-400">
                        Edge:
                      </span>
                      <span className="font-mono text-xs text-gray-900 dark:text-white">
                        {edge.edge_type}
                      </span>
                    </div>
                    {edge.label && (
                      <div className="mt-1 pt-1 border-t border-gray-200 dark:border-slate-700">
                        <span className="text-xs text-gray-600 dark:text-gray-400">
                          Label:
                        </span>
                        <p className="font-mono text-xs text-gray-900 dark:text-white">
                          {edge.label}
                        </p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* No relations */}
          {incomingEdges.length === 0 && outgoingEdges.length === 0 && (
            <section>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                No incoming or outgoing edges.
              </p>
            </section>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-gray-200 bg-gray-50 px-6 py-3 dark:border-slate-700 dark:bg-slate-800">
          <button
            onClick={onClose}
            className="text-sm font-medium text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
