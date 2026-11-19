/**
 * Status-icon vocabulary for the console — the TypeScript mirror of
 * `corvin_operator/bridges/shared/status_markers.py`.
 *
 * ONE meaning per icon. The Python module is the source; its generated export
 * `status_markers.json` is what `tests/unit/status-markers.test.ts` compares
 * this table against, so a drift fails a test instead of shipping.
 * (The JSON is not imported directly: it lives outside this package and the
 * console must still build where only web-next is present.)
 *
 * Console code takes a status icon by NAME from here. A glyph typed as a
 * literal is a second copy — and is how 📌 ("decision / final result") came to
 * mark a baseline run and ✅ ("done / verified") an action that had not run.
 * Use an icon only for the status it names; decoration and brand marks are
 * lucide icons, not status glyphs.
 */

export const STATUS_ICONS = {
  /** done / succeeded / verified */
  done: "✅",
  /** failed / rejected / broken */
  failed: "❌",
  /** warning / risk / limitation */
  warning: "⚠️",
  /** note / context */
  note: "ℹ️",
  /** investigating / finding still open */
  investigating: "🔍",
  /** running / waiting */
  running: "⏳",
  /** suggestion / option for the user */
  suggestion: "💡",
  /** decision / final result */
  decision: "📌",
} as const;

export type StatusMarkerName = keyof typeof STATUS_ICONS;

/** [icon, meaning] in the order the vocabulary is taught. */
export const STATUS_MARKERS: ReadonlyArray<readonly [string, string]> = [
  [STATUS_ICONS.done, "done / succeeded / verified"],
  [STATUS_ICONS.failed, "failed / rejected / broken"],
  [STATUS_ICONS.warning, "warning / risk / limitation"],
  [STATUS_ICONS.note, "note / context"],
  [STATUS_ICONS.investigating, "investigating / finding still open"],
  [STATUS_ICONS.running, "running / waiting"],
  [STATUS_ICONS.suggestion, "suggestion / option for the user"],
  [STATUS_ICONS.decision, "decision / final result"],
];

/**
 * The icon for a status name. Throws on an unknown name: a typo must fail a
 * test, not render a missing icon.
 */
export function icon(name: StatusMarkerName): string {
  if (!Object.prototype.hasOwnProperty.call(STATUS_ICONS, name)) {
    throw new Error(`unknown status marker: ${String(name)}`);
  }
  return STATUS_ICONS[name];
}
