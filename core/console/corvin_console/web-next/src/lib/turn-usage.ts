/**
 * Per-turn model + token line for the chat badge. Values come from the
 * `turn_usage` stream event (CLI `init` model + `result` usage); a missing
 * value is shown as "unknown", never estimated. While the turn still runs,
 * nothing is unknown yet: `formatPendingTurnUsage` names the routed model and
 * says the counts are pending.
 */
export interface TurnUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_input_tokens: number | null;
  cache_creation_input_tokens: number | null;
}

const nf = new Intl.NumberFormat("en-US");

/** A reported count, or null when the CLI did not report it. Never coerces to 0. */
function count(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function parseTurnUsage(raw: unknown): TurnUsage | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  return {
    input_tokens: count(r.input_tokens),
    output_tokens: count(r.output_tokens),
    cache_read_input_tokens: count(r.cache_read_input_tokens),
    cache_creation_input_tokens: count(r.cache_creation_input_tokens),
  };
}

/** A dated snapshot and its family alias name the same model (claude-x-20251001 ≈ claude-x). */
function sameModel(a: string, b: string): boolean {
  const strip = (id: string) => id.replace(/-\d{8}$/, "");
  return strip(a) === strip(b);
}

/**
 * `routed` is the model the selector chose before the spawn. It is named only when
 * the confirmed model differs from it, or when the confirmed model is unknown.
 */
export function formatTurnUsage(
  model: string | null | undefined,
  usage: TurnUsage | null | undefined,
  routed?: string | null,
): string {
  const m = model ? model : "unknown";
  let head = `Model: ${m}`;
  if (routed && (!model || !sameModel(model, routed))) head += ` (routed: ${routed})`;
  if (!usage) return `${head} · Tokens: unknown`;
  const cache: string[] = [];
  if (usage.cache_read_input_tokens) cache.push(`${nf.format(usage.cache_read_input_tokens)} read`);
  if (usage.cache_creation_input_tokens) cache.push(`${nf.format(usage.cache_creation_input_tokens)} written`);
  const num = (v: number | null) => (v === null ? "unknown" : nf.format(v));
  return (
    `${head} · Tokens: ${num(usage.input_tokens)} in / ${num(usage.output_tokens)} out` +
    (cache.length ? ` · Cache: ${cache.join(", ")}` : "")
  );
}

/**
 * The line of a RUNNING turn, before `turn_usage` arrived: the model the selector
 * routed it to (from the `engine` event) and "pending" for what is not reported yet.
 * Never "unknown" - that word is kept for a value the engine did not report.
 */
export function formatPendingTurnUsage(routed: string | null | undefined): string {
  return `Model: ${routed ? `${routed} (routed)` : "pending"} · Tokens: pending`;
}
