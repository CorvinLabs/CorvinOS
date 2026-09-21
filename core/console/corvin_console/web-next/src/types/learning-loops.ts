// Shape of the Learning Loops API (ADR-0907/0908).
//
// One definition, imported by the page, the grid and the detail view. The three
// used to carry their own copies of `LoopEntry`, which is how the grid kept
// rendering a non-optional `health.score` after the backend started returning
// null for a loop nothing has measured.

export type LoopStatus = "active" | "dormant" | "stale" | "degrading" | "unknown";

/** "plugin" = declared in a plugin manifest; the rest are loops CorvinOS runs itself. */
export type LoopOrigin = "plugin" | "os_skill" | "cel_stage";

export interface LoopHealth {
  /** Null when nothing has measured this loop's health — never treat as 0. */
  score: number | null;
  trend: "up" | "down" | "flat";
  previous_score?: number | null;
  /** What a present score counts, e.g. "29 of 31 recorded task outcomes succeeded". */
  basis?: string;
}

export interface LoopEntry {
  loop_id: string;
  plugin_id: string;
  skill_id?: string | null;
  status: LoopStatus;
  health: LoopHealth;
  last_event?: string | null;
  event_count_7d: number;
  event_count_total?: number;
  event_count_30d?: number;
  description?: string;
  origin?: LoopOrigin;
  /** Which store the numbers were read from. */
  event_source?: string;
}

/** The window a narrowed total was counted over — rendered next to the total. */
export interface ListWindow {
  scanned_events: number;
  truncated: boolean;
}

export interface LoopListResponse {
  loops: LoopEntry[];
  total: number;
  timestamp: string;
  window?: ListWindow;
}

export interface TrendPoint {
  date: string;
  /** Null on a day that recorded no outcome — a gap, not a zero. */
  health_score: number | null;
  event_count: number;
}

export interface LoopTrend {
  points: TrendPoint[];
  min_score: number | null;
  max_score: number | null;
  avg_score: number | null;
}

export interface LoopEvent {
  timestamp: string;
  event_type: string;
  skill_id?: string | null;
  signal?: string | null;
  outcome?: string | null;
  metadata?: Record<string, unknown>;
}

export const ORIGIN_LABEL: Record<LoopOrigin, string> = {
  plugin: "Plugin",
  os_skill: "OS skill",
  cel_stage: "CEL stage",
};
