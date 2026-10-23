/** One short text for a compute run parameter.
 *
 * A search-space object ({type, low, high}) used to render as "[object Object]" and a 25-point series ran off
 * the table; the experiment comparison now shows "float_uniform 0.05–1" and "[25 values]".
 */
export function formatParam(v: unknown): string {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.length > 6 ? `[${v.length} values]` : v.map(formatParam).join(", ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.low === "number" && typeof o.high === "number") return `${String(o.type ?? "range")} ${o.low}–${o.high}`;
    return JSON.stringify(o);
  }
  return String(v);
}
