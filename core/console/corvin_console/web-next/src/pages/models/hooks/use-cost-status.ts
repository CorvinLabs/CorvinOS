/** ONE status query for the whole Models console (ADR-0885 §3).
 *
 *  The header, the Usage & Cost tab and the Learning tab all read this key, so
 *  TanStack dedupes them into one fetch. It polls only while a data tab is
 *  active (`pollActive`); the header and the Catalog read it with a stale
 *  time — the status endpoint reads the audit chain, and the old panel polled
 *  it every 5 s regardless of what was on screen. */
import { useQuery } from "@tanstack/react-query";
import { getCostStatus } from "../api";

export const COST_STATUS_KEY = ["cost-optimizer-status"] as const;

export function useCostOptimizerStatus(pollActive: boolean) {
  return useQuery({
    queryKey: COST_STATUS_KEY,
    queryFn: ({ signal }) => getCostStatus(signal),
    refetchInterval: pollActive ? 5000 : false,
    staleTime: 30_000,
  });
}
