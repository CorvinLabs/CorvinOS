/**
 * TdeAuditGraphPanel — "TDE Graph" tab of the chat Audit panel (ADR-0214).
 *
 * Thin wrapper around ComputeGraphView(mode="tde"): resolves which TDE turn
 * to show (the most recent one this chat session actually ran — stamped on
 * the ChatMessage live via the `engine`/`engine_progress` stream events and
 * re-derived from the persisted tde_progress on reload, see
 * chat-registry.ts / chat.tsx hydration), with a manual override input as a
 * fallback for older turns that predate that plumbing.
 */
import * as React from "react";
import { GitGraph } from "lucide-react";
import { useChatSession } from "@/lib/chat-registry";
import { ComputeGraphView } from "@/components/ComputeGraphView";

interface Props {
  sid: string;
}

// chat_runtime generates run ids as `tde-<epoch>-<token_hex(4)>`. The manual
// override only fires a request once the input matches this shape — anything
// else would 404 per keystroke and write one tde.audit_graph_viewed audit
// record per successful keystroke-hit (review 2026-07-24).
const RUN_ID_RE = /^tde-\d{1,20}-[0-9a-f]{8}$/i;

export function TdeAuditGraphPanel({ sid }: Props) {
  const { messages, streaming } = useChatSession(sid);

  // Resolve run id AND metrics from the SAME (latest) TDE message — two
  // independent backward scans could pair a newer run's graph with an older
  // run's metrics card (review 2026-07-24).
  const latestTde = React.useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.tdeRunId || m.tdeProgress) {
        return {
          runId: m.tdeRunId ?? m.tdeProgress?.run_id ?? null,
          progress: m.tdeProgress ?? null,
        };
      }
    }
    return { runId: null, progress: null };
  }, [messages]);

  const latestTdeRunId = latestTde.runId;
  const latestTdeProgress = latestTde.progress;

  const [manualRunId, setManualRunId] = React.useState("");
  const manualTrimmed = manualRunId.trim();
  const manualValid = RUN_ID_RE.test(manualTrimmed);
  const activeRunId = (manualValid ? manualTrimmed : "") || latestTdeRunId;

  const latencyDelta = latestTdeProgress?.latency_delta_pct;

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
        <div className="flex flex-col gap-2">
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
            <div className="flex justify-between">
              <span className="text-slate-400">Latency vs. local:</span>
              <span className="text-slate-200 font-mono">
                {typeof latencyDelta === "number"
                  ? `${latencyDelta > 0 ? "+" : ""}${latencyDelta.toFixed(1)}%`
                  : "n/a"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Token savings:</span>
              {/* ADR-0215 honesty: token_savings_pct is null until real
                  per-call token instrumentation exists — render the truth,
                  never an invented estimate (review 2026-07-24 removed the
                  fabricated "30-70%" placeholder). */}
              <span className="text-slate-500 font-mono">
                {latestTdeProgress.token_usage_instrumented &&
                 typeof latestTdeProgress.token_savings_pct === "number"
                  ? `${latestTdeProgress.token_savings_pct.toFixed(1)}%`
                  : "not measured"}
              </span>
            </div>
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
      {manualTrimmed !== "" && !manualValid && (
        <div className="text-[10px] text-amber-400">
          Not a TDE run id yet — expected shape <code className="font-mono">tde-&lt;epoch&gt;-&lt;8 hex&gt;</code>.
        </div>
      )}

      {activeRunId ? (
        // Poll while this chat is still streaming: the run id is stamped at
        // turn START, before any tde.* audit record exists — a one-shot
        // fetch latched a sticky 404 for the whole run (review 2026-07-24).
        <ComputeGraphView mode="tde" runId={activeRunId} pollMs={streaming ? 2000 : 0} />
      ) : (
        <div className="text-xs text-slate-500 py-8 text-center">
          No TDE run selected — start a TDE turn or paste a run id above.
        </div>
      )}
    </div>
  );
}
