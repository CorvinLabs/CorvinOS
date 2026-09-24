/**
 * One definition of "live" for every query of the Tasks panel, and the pure
 * freshness verdict the page shows next to the title. The console-wide query
 * default is `refetchOnWindowFocus: false`; a panel that claims live data must
 * override it on EVERY query — the detail drawer once inherited it and lagged
 * the list by up to 10 s after the tab came back.
 */
export const LIVE_POLL_MS = 5_000;

export const LIVE_QUERY = {
  refetchInterval: LIVE_POLL_MS,
  refetchIntervalInBackground: false,
  refetchOnWindowFocus: "always",
  refetchOnMount: "always",
  staleTime: 0,
} as const;

/** Older than this without a successful poll = no longer "live". */
export const STALE_AFTER_MS = 3 * LIVE_POLL_MS;

export type Freshness =
  | { state: "live"; ageS: number }
  | { state: "delayed"; ageS: number }
  | { state: "offline"; ageS: number | null }
  | { state: "loading" };

/**
 * `updatedAt` = the last SUCCESSFUL fetch (TanStack `dataUpdatedAt`, 0 = never).
 * A failing poll with old data on screen is "offline" — never presented as live.
 */
export function freshness(updatedAt: number, failing: boolean, nowMs: number): Freshness {
  const ageS = updatedAt ? Math.max(0, Math.round((nowMs - updatedAt) / 1000)) : null;
  if (failing) return { state: "offline", ageS };
  if (ageS === null) return { state: "loading" };
  return ageS * 1000 > STALE_AFTER_MS ? { state: "delayed", ageS } : { state: "live", ageS };
}
