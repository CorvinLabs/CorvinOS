/**
 * TdeAuditGraphPanel — "TDE Graph" tab of the chat Audit panel (ADR-0214).
 *
 * Thin wrapper around ComputeGraphView(mode="tde"): resolves which TDE turn
 * to show (the most recent one this chat session actually ran, stamped on
 * the ChatMessage via the `engine` stream event's `tde_run_id` field — see
 * chat_runtime.py::_stream_tde_turn / chat-registry.ts's "engine" case),
 * with a manual override input as a fallback for older turns that predate
 * that plumbing (their run id only ever appeared inside the free-text
 * "task.completed" summary, never as a structured field).
 */
import * as React from "react";
import { GitGraph } from "lucide-react";
import { useChatSession } from "@/lib/chat-registry";
import { ComputeGraphView } from "@/components/ComputeGraphView";

interface Props {
  sid: string;
}

export function TdeAuditGraphPanel({ sid }: Props) {
  const { messages } = useChatSession(sid);

  const latestTdeRunId = React.useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const runId = messages[i].tdeRunId;
      if (runId) return runId;
    }
    return null;
  }, [messages]);

  const latestTdeProgress = React.useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const progress = messages[i].tdeProgress;
      if (progress) return progress;
    }
    return null;
  }, [messages]);

  const [manualRunId, setManualRunId] = React.useState("");
  const activeRunId = manualRunId.trim() || latestTdeRunId;

  return (
    <div className="flex flex-col gap-3 p-4 h-full overflow-y-auto bg-slate-950">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <GitGraph className="h-3.5 w-3.5" />
        {latestTdeRunId ? (
          <span>
            Latest TDE turn in this chat: <span className="font-mono text-slate-200">{latestTdeRunId}</span>
          </span>
        ) : (
          <span>No TDE turn detected yet in this chat (run <code className="font-mono">/use-engine tiered_delegation &lt;task&gt;</code> to start one).</span>
        )}
      </div>

      {latestTdeProgress && (
        <div className="grid grid-cols-2 gap-2 text-xs bg-slate-900 rounded p-2 border border-slate-800">
          <div className="col-span-2 font-semibold text-slate-300 mb-1">TDE Delegation Metrics</div>
          <div className="flex justify-between">
            <span className="text-slate-400">Steps:</span>
            <span className="text-slate-200 font-mono">{latestTdeProgress.completed_steps}/{latestTdeProgress.total_steps}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">Delegated:</span>
            <span className="text-slate-200 font-mono">{latestTdeProgress.delegated_count}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">Local:</span>
            <span className="text-slate-200 font-mono">{latestTdeProgress.local_count}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">L34 Gate:</span>
            <span className={`font-mono ${latestTdeProgress.l34_forced ? "text-red-400" : "text-green-400"}`}>
              {latestTdeProgress.l34_forced ? "Blocked" : "Allowed"}
            </span>
          </div>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs text-slate-400">
        <span className="shrink-0">TDE run id override:</span>
        <input
          type="text"
          value={manualRunId}
          onChange={(e) => setManualRunId(e.target.value)}
          placeholder={latestTdeRunId ?? "tde-<epoch>-<hex>"}
          className="flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </label>

      {activeRunId ? (
        <ComputeGraphView mode="tde" runId={activeRunId} />
      ) : (
        <div className="text-xs text-slate-500 py-8 text-center">
          No TDE run selected — start a TDE turn or paste a run id above.
        </div>
      )}
    </div>
  );
}
