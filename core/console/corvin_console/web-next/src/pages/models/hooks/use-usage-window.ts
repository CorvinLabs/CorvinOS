/** The ONE counting window (ADR-0760) and its two operator actions.
 *
 *  The window is server-side: `/v1/engine/config`, `/v1/engine/model-usage`
 *  and the cost status all filter on the same epoch. This hook is one READ of
 *  `status.window` plus two mutations — `reset` (count from now) and `clear`
 *  (show full history; nothing was ever deleted, ADR-0760). The header is the
 *  only caller of the mutations; tabs read the window for their caption and
 *  never move it (ADR-0764: a narrowed total never travels without its window).
 *
 *  Every reader of the epoch is invalidated on success — including
 *  `['engine-config']`, which the old cost panel did not know about, so the
 *  Routing tile stayed stale for up to 30 s after a reset. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { postUsageEpoch } from "../api";
import { COST_STATUS_KEY, useCostOptimizerStatus } from "./use-cost-status";

const EPOCH_READERS = [COST_STATUS_KEY, ["model-usage"], ["engine-config"]] as const;

export function useUsageWindow(csrf: string) {
  const q = useCostOptimizerStatus(false);
  const qc = useQueryClient();
  const invalidate = () => {
    for (const key of EPOCH_READERS) qc.invalidateQueries({ queryKey: [...key] });
  };
  const reset = useMutation({ mutationFn: () => postUsageEpoch({}, csrf), onSuccess: invalidate });
  const clear = useMutation({
    mutationFn: () => postUsageEpoch({ clear: true }, csrf),
    onSuccess: invalidate,
  });
  return { window: q.data?.window, loading: q.isLoading, reset, clear };
}
