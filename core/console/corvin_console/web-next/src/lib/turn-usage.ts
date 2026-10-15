/**
 * Per-turn model + token line for the chat badge. Values come from the
 * `turn_usage` stream event (CLI `init` model + `result` usage); a missing
 * value is shown as "unknown", never estimated.
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

export function formatTurnUsage(model: string | null | undefined, usage: TurnUsage | null | undefined): string {
  const m = model ? model : "unknown";
  if (!usage) return `Model: ${m} · Tokens: unknown`;
  const cache: string[] = [];
  if (usage.cache_read_input_tokens) cache.push(`${nf.format(usage.cache_read_input_tokens)} read`);
  if (usage.cache_creation_input_tokens) cache.push(`${nf.format(usage.cache_creation_input_tokens)} written`);
  const num = (v: number | null) => (v === null ? "unknown" : nf.format(v));
  return (
    `Model: ${m} · Tokens: ${num(usage.input_tokens)} in / ${num(usage.output_tokens)} out` +
    (cache.length ? ` · Cache: ${cache.join(", ")}` : "")
  );
}
