/**
 * Encodings for the Video Producer's production-report signals (Quality tab).
 *
 * The backend (routes/video_producer_api.py → video_quality.read_report) passes every signal
 * through as the job measured it; this module only decides how a signal READS: its label, how its
 * value, threshold, verdict and basis ("n of N") are written, and which colour token marks the
 * verdict. Kept out of the component so the number → text mapping is testable (a wrong mapping is
 * invisible in a screenshot until a job hits the case).
 *
 * Rules:
 *  - nothing is invented: a value the report did not carry is "not measured", never 0;
 *  - a share is written as a percentage only over a real sample (MIN_SHARE_SAMPLE scenes);
 *    below that the panel shows "n of N" and says why;
 *  - verdict colours are the --viz-verdict-* tokens (validated for both themes), never --accent,
 *    and always come with a text label - colour never carries the verdict alone.
 */

export type SignalVerdict = "pass" | "warn" | "fail" | "skip" | "info" | "unknown";

export interface ReportSignal {
  id: string;
  kind: "runtime" | "posthoc" | null;
  value: number | null;
  unit: string;
  threshold: number | Record<string, number> | null;
  verdict: SignalVerdict;
  basis: { n: number | null; of: number | null };
  valid: boolean;
  invalidated_by: string | null;
  reason: string | null;
  detail: Record<string, unknown> | null;
}

export type ReportState = "present" | "missing" | "not_written" | "unreadable";

export interface ProductionReport {
  state: ReportState;
  reason?: string;
  signals: ReportSignal[];
  schema?: number;
  plugin_version?: string | null;
  status?: string | null;
  failed_stage?: string | null;
  error_class?: string | null;
  language?: string | null;
  storyboard_llm?: string | null;
  total_seconds?: number | null;
  stages?: Array<{ name: string | null; seconds: number | null }>;
  degraded_scenes?: { n: number | null; of: number | null };
  degradations_by_kind?: Record<string, number>;
  verdicts?: Record<string, number>;
}

/** Below this many scenes a share is written as "n of N", not as a percentage. */
export const MIN_SHARE_SAMPLE = 10;

/** The signal whose failure invalidates the gated signals (ADR rule, mirrored from the plugin). */
export const GATE_SIGNAL = "degradation_rate";

const LABELS: Record<string, { label: string; what: string }> = {
  degradation_rate: { label: "Degraded scenes", what: "Scenes that did not turn out as intended (fallback slide, lower voice tier, quote instead of the chosen template)." },
  layout_clean: { label: "Slide overlaps kept", what: "Overlapping slide elements that stayed in the final video." },
  fact_support: { label: "Unsupported claims", what: "Narration claims the grounding sources do not support (grounded tasks only)." },
  mux_integrity: { label: "Audio and picture intact", what: "Every clip at least as long as its voice, final audio within 3 % of the picture, no truncated clip." },
  plan_estimate_error: { label: "Planned vs narrated length", what: "The storyboard's length estimate next to the narrated length. Information only - the narration decides the length." },
  beats_provenance: { label: "Reveals on fallback timing", what: "Animated slides whose reveals were timed by the fallback instead of the narration. Information only." },
  style_fidelity: { label: "Style colour fidelity", what: "Rendered accent colour against the custom style's accent (colour difference ΔE), and no Corvin mark." },
  still_time: { label: "Still time", what: "Share of the runtime in still stretches of 5 s or more." },
  cue_alignment: { label: "Reveal-to-word alignment", what: "Seconds between a reveal and the spoken word." },
  language_parity: { label: "Language parity", what: "The two language versions carry the same scenes and numbers." },
  revision_locality: { label: "Revision locality", what: "Share of the base video's scenes kept unchanged in a revision." },
  canvas_clean: { label: "Clean canvases", what: "Canvas scenes whose first render had no collision, nothing outside the drawing area and no detached arrow." },
  canvas_authoring: { label: "Canvases valid first try", what: "Canvases the storyboard model wrote that validated on the first try (and after the one repair round). Information only." },
  asset_hygiene: { label: "Uploaded images clean", what: "Uploaded images used in the video carry no metadata, stay inside the size caps and keep no query string in their source." },
};

export function signalLabel(id: string): string {
  return LABELS[id]?.label ?? id.replace(/_/g, " ");
}

export function signalDescription(id: string): string | null {
  return LABELS[id]?.what ?? null;
}

const VERDICT_LABEL: Record<SignalVerdict, string> = {
  pass: "Pass", warn: "Warning", fail: "Fail", skip: "Not measured", info: "Information", unknown: "Unknown verdict",
};

export function verdictLabel(v: SignalVerdict): string {
  return VERDICT_LABEL[v] ?? VERDICT_LABEL.unknown;
}

/** Colour token of a verdict mark. Only pass/warn/fail are filled; the rest are hollow. */
export function verdictToken(v: SignalVerdict): { color: string; filled: boolean } {
  if (v === "pass") return { color: "var(--viz-verdict-pass)", filled: true };
  if (v === "warn") return { color: "var(--viz-verdict-warn)", filled: true };
  if (v === "fail") return { color: "var(--viz-verdict-fail)", filled: true };
  return { color: "var(--viz-baseline)", filled: false };
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const trim = (x: number, digits = 2) => String(Number(x.toFixed(digits)));

export function basisText(s: Pick<ReportSignal, "basis">): string {
  const { n, of } = s.basis;
  return isNum(n) && isNum(of) ? `${n} of ${of}` : "—";
}

/** True when a share signal's sample is too small to state as a percentage. */
export function shareTooSmall(s: ReportSignal): boolean {
  return s.unit.startsWith("share of") && isNum(s.basis.of) && s.basis.of < MIN_SHARE_SAMPLE;
}

export function formatValue(s: ReportSignal): string {
  if (s.id === "plan_estimate_error") {
    const d = s.detail ?? {};
    const narrated = d["narrated_s"], plan = d["plan_s"];
    if (isNum(narrated) && isNum(plan)) return `${trim(narrated, 1)} s narrated · ${trim(plan, 1)} s planned`;
    return "not measured";
  }
  if (!isNum(s.value)) return "not measured";
  if (s.unit.startsWith("share of")) {
    const { n, of } = s.basis;
    if (isNum(n) && isNum(of)) {
      const noun = s.unit.replace(/^share of /, "");
      return of >= MIN_SHARE_SAMPLE ? `${Math.round(s.value * 100)} % (${n} of ${of} ${noun})` : `${n} of ${of} ${noun}`;
    }
    return "not measured";
  }
  if (s.unit.startsWith("delta E")) return `ΔE ${trim(s.value)}`;
  return s.unit ? `${trim(s.value)} ${s.unit}` : trim(s.value);
}

const THRESHOLD_PART: Record<string, (t: number) => string> = {
  max_share: (t) => `at most ${trim(t * 100, 1)} % of scenes`,
  pillow: (t) => (t === 0 ? "no fallback slide" : `at most ${t} fallback slides`),
  backend_fallback: (t) => (t === 0 ? "no storyboard model fallback" : `at most ${t} storyboard model fallbacks`),
  grounding_lost: (t) => (t === 0 ? "grounding never lost" : `grounding lost at most ${t} times`),
  clip_vs_voice_s: (t) => `each clip at least its voice length minus ${trim(t)} s`,
  final_av_share: (t) => `audio within ${trim(t * 100, 1)} % of the picture`,
  delta_e_max: (t) => `ΔE at most ${trim(t)}`,
  corvin_mark_frames: (t) => (t === 0 ? "no Corvin mark" : `Corvin mark on at most ${t} frames`),
};

export function formatThreshold(s: ReportSignal): string {
  const t = s.threshold;
  if (t === null || t === undefined) return s.verdict === "info" ? "information only" : "—";
  if (isNum(t)) {
    if (s.unit.startsWith("delta E")) return `ΔE at most ${trim(t)}`;
    if (t === 0) return `0 ${s.unit}`.trim();
    return `at most ${trim(t)} ${s.unit}`.trim();
  }
  return Object.entries(t)
    .map(([k, v]) => (THRESHOLD_PART[k] ? THRESHOLD_PART[k](v) : `${k.replace(/_/g, " ")} ${trim(v)}`))
    .join(" · ");
}

/** A plugin reason ("scene share above 10 %") as a sentence: capitalised, ending in a full stop. */
export function sentence(text: string): string {
  const t = text.trim();
  if (!t) return t;
  const cap = t[0].toUpperCase() + t.slice(1);
  return /[.!?]$/.test(cap) ? cap : `${cap}.`;
}

export interface SignalRow {
  id: string;
  label: string;
  description: string | null;
  value: string;
  threshold: string;
  verdict: SignalVerdict;
  verdictText: string;
  basis: string;
  valid: boolean;
  note: string | null;
}

/** The rows the panel draws, gate first, in the report's order otherwise. */
export function signalRows(report: ProductionReport): SignalRow[] {
  const sigs = [...report.signals].sort((a, b) => Number(b.id === GATE_SIGNAL) - Number(a.id === GATE_SIGNAL));
  return sigs.map((s) => {
    const notes: string[] = [];
    if (!s.valid) notes.push(`Not valid: ${s.invalidated_by ? `"${signalLabel(s.invalidated_by)}" failed` : "invalidated"}, so this value may flatter the video.`);
    if (s.reason) notes.push(sentence(s.reason));
    if (shareTooSmall(s)) notes.push(`Only ${s.basis.of} scenes - too few to state as a percentage.`);
    return {
      id: s.id,
      label: signalLabel(s.id),
      description: signalDescription(s.id),
      value: formatValue(s),
      threshold: formatThreshold(s),
      verdict: s.verdict,
      verdictText: verdictLabel(s.verdict),
      basis: basisText(s),
      valid: s.valid,
      note: notes.length ? notes.join(" ") : null,
    };
  });
}

/** What the panel says when there is no report to show. */
export function reportAbsence(report: ProductionReport | null | undefined): string | null {
  if (!report) return "No production report is available for this job.";
  switch (report.state) {
    case "present": return null;
    case "missing": return "No production report for this job. Videos produced before production reports were recorded have none, so no signals are shown.";
    case "not_written": return "This job's production report was not written, so no signals are available.";
    case "unreadable": return `The production report of this job could not be read (${(report.reason ?? "unknown").replace(/_/g, " ")}), so no signals are shown.`;
    default: return "No production report is available for this job.";
  }
}

/** One-line summary: "7 signals · 1 fail · 1 pass · …" in a fixed verdict order. */
export function verdictSummary(report: ProductionReport): string {
  const order: SignalVerdict[] = ["fail", "warn", "pass", "info", "skip", "unknown"];
  const counts = report.verdicts ?? {};
  const parts = order.filter((v) => (counts[v] ?? 0) > 0).map((v) => `${counts[v]} ${verdictLabel(v).toLowerCase()}`);
  return `${report.signals.length} signal${report.signals.length === 1 ? "" : "s"}${parts.length ? ` · ${parts.join(" · ")}` : ""}`;
}
