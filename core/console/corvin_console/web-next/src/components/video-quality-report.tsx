/**
 * Production signals of one Video Producer job (Quality tab): one row per named signal with its
 * value, threshold, verdict and basis ("n of N"), exactly as the job's production report recorded
 * them. Encodings live in @/lib/video-quality-signals; this file only lays them out.
 */
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  GATE_SIGNAL, reportAbsence, signalLabel, signalRows, verdictSummary, verdictToken,
  type ProductionReport, type SignalVerdict,
} from "@/lib/video-quality-signals";

function VerdictMark({ v }: { v: SignalVerdict }) {
  const t = verdictToken(v);
  return (
    <span aria-hidden className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
      style={t.filled ? { background: t.color } : { border: `1.5px solid ${t.color}` }} />
  );
}

export function ProductionSignals({ report }: { report: ProductionReport | null | undefined }) {
  const absent = reportAbsence(report);
  if (absent || !report) {
    return (
      <Card data-testid="signals-card">
        <CardHeader className="pb-2"><CardTitle className="text-base">Production signals</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground" data-testid="signals-absent">{absent}</p></CardContent>
      </Card>
    );
  }
  const rows = signalRows(report);
  const gate = report.signals.find((s) => s.id === GATE_SIGNAL);
  const facts = [
    report.plugin_version ? `Producer ${report.plugin_version}` : null,
    report.storyboard_llm ? `storyboard by ${report.storyboard_llm}` : null,
    report.total_seconds != null ? `${report.total_seconds.toFixed(1)} s to produce` : null,
  ].filter(Boolean).join(" · ");
  return (
    <Card data-testid="signals-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Production signals</CardTitle>
        <CardDescription data-testid="signals-summary">
          Recorded by the job itself when it finished: {verdictSummary(report)}.{facts ? ` ${facts}.` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {report.status && report.status !== "complete" && (
          <p className="text-sm" data-testid="signals-failed-stage">
            The job ended {report.status === "cancelled" ? "cancelled" : "with an error"}
            {report.failed_stage ? ` in the ${report.failed_stage} stage` : ""}{report.error_class ? ` (${report.error_class})` : ""}.
          </p>
        )}
        {gate?.verdict === "fail" && (
          <p className="text-sm text-muted-foreground" data-testid="signals-gate-note">
            "{signalLabel(GATE_SIGNAL)}" failed, so the signals it gates are marked not valid: a degraded scene would flatter them.
          </p>
        )}
        {rows.length === 0 ? <p className="text-sm text-muted-foreground">The report carries no signals.</p> : (
          <div className="rounded-lg border border-border overflow-x-auto">
            <table className="w-full text-sm" data-testid="signals-table">
              <thead className="bg-muted/40 border-b border-border text-xs text-muted-foreground"><tr>
                <th className="text-left px-3 py-2 font-medium">Signal</th>
                <th className="text-left px-3 py-2 font-medium">Value</th>
                <th className="text-left px-3 py-2 font-medium">Threshold</th>
                <th className="text-left px-3 py-2 font-medium">Verdict</th>
                <th className="text-right px-3 py-2 font-medium whitespace-nowrap">n of N</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className={`border-b border-border last:border-b-0 align-top ${r.valid ? "" : "opacity-70"}`} data-testid={`signal-${r.id}`}>
                    <td className="px-3 py-2">
                      <div className="font-medium" title={r.description ?? undefined}>{r.label}</div>
                      {r.note && <div className="text-xs text-muted-foreground mt-0.5" data-testid={`signal-note-${r.id}`}>{r.note}</div>}
                    </td>
                    <td className="px-3 py-2 tabular-nums" data-testid={`signal-value-${r.id}`}>{r.value}</td>
                    <td className="px-3 py-2 text-muted-foreground" data-testid={`signal-threshold-${r.id}`}>{r.threshold}</td>
                    <td className="px-3 py-2 whitespace-nowrap" data-testid={`signal-verdict-${r.id}`}>
                      <span className="inline-flex items-center gap-1.5"><VerdictMark v={r.verdict} />{r.verdictText}{r.valid ? "" : " · not valid"}</span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap" data-testid={`signal-basis-${r.id}`}>{r.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
