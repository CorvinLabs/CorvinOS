/**
 * Ready tab of the workflow inspector: the measured readiness report, the acceptance case
 * editor and the audited "Mark ready anyway" action. The report comes from a synthetic run
 * against a stub, and this panel always says so.
 */
import * as React from "react";
import { Loader2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { getReadiness, postReadyOverride, putAcceptance } from "@/lib/api/workflows";
import {
  ACCEPTANCE_EXAMPLE,
  STATUS_LABELS,
  formatAcceptance,
  parseAcceptance,
  signalLabel,
  statusClass,
  verdict,
} from "@/lib/workflow-readiness";

export interface ReadinessPanelProps {
  wid: string;
  csrf: string;
  /** Current design phase of the draft; "ready" hides the override. */
  phase: string;
  /** Called with the new phase after a successful override. */
  onPhaseChange: (phase: string) => void;
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function ReadinessPanel({ wid, csrf, phase, onPhaseChange }: ReadinessPanelProps) {
  const qc = useQueryClient();
  const key = React.useMemo(() => ["workflow-readiness", wid], [wid]);
  const report = useQuery({ queryKey: key, queryFn: () => getReadiness(wid), refetchOnWindowFocus: false });

  const [draft, setDraft] = React.useState<string | null>(null);
  const [caseError, setCaseError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [overrideOpen, setOverrideOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");
  const [overrideError, setOverrideError] = React.useState<string | null>(null);
  const [overriding, setOverriding] = React.useState(false);

  const stored = report.data?.acceptance ?? null;
  const text = draft ?? formatAcceptance(stored);

  const saveCase = async () => {
    const parsed = parseAcceptance(text);
    if (parsed.ok === false) {
      setCaseError(parsed.error);
      return;
    }
    setSaving(true);
    setCaseError(null);
    try {
      await putAcceptance(wid, parsed.value, csrf);
      setDraft(null);
      await qc.invalidateQueries({ queryKey: key });
    } catch (e) {
      setCaseError(messageOf(e));
    } finally {
      setSaving(false);
    }
  };

  const markReady = async () => {
    setOverriding(true);
    setOverrideError(null);
    try {
      const r = await postReadyOverride(wid, reason.trim(), csrf);
      setOverrideOpen(false);
      setReason("");
      onPhaseChange(r.phase);
      await qc.invalidateQueries({ queryKey: key });
      await qc.invalidateQueries({ queryKey: ["workflow", wid] });
    } catch (e) {
      setOverrideError(messageOf(e));
    } finally {
      setOverriding(false);
    }
  };

  const data = report.data;
  return (
    <div className="space-y-3 p-3 text-xs" data-testid="readiness-panel">
      <p className="text-[11px] leading-snug text-muted-foreground" data-testid="readiness-notice">
        Synthetic run: checks structure and the acceptance case, not real execution.
      </p>

      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Report</span>
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px]"
          onClick={() => void report.refetch()}
          disabled={report.isFetching}
          data-testid="readiness-check-now"
        >
          {report.isFetching ? <Loader2 className="h-3 w-3 animate-spin" /> : "Check now"}
        </Button>
      </div>

      {report.isError && (
        <p className="text-destructive" data-testid="readiness-error">
          The check could not run: {messageOf(report.error)}
        </p>
      )}

      {data && (
        <>
          <p className={cn("font-medium", data.ready ? "text-emerald-600 dark:text-emerald-400" : "text-foreground")} data-testid="readiness-verdict">
            {verdict(data)}
          </p>
          <ul className="space-y-1.5" data-testid="readiness-signals">
            {data.signals.map((s) => (
              <li key={s.name} className="rounded border border-border/60 px-2 py-1.5" data-testid={`readiness-signal-${s.name}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{signalLabel(s.name)}</span>
                  <Badge className={cn("text-[10px]", statusClass(s.status))}>{STATUS_LABELS[s.status]}</Badge>
                </div>
                <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{s.detail}</p>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="space-y-1.5">
        <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Acceptance case</span>
        <Textarea
          value={text}
          onChange={(e) => {
            setDraft(e.target.value);
            setCaseError(null);
          }}
          placeholder={ACCEPTANCE_EXAMPLE}
          className="h-40 font-mono text-[10px]"
          spellCheck={false}
          data-testid="readiness-acceptance-text"
        />
        {caseError && (
          <p className="text-destructive" data-testid="readiness-case-error">
            {caseError}
          </p>
        )}
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px]"
          onClick={() => void saveCase()}
          disabled={saving || !text.trim()}
          data-testid="readiness-save-case"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save case"}
        </Button>
      </div>

      {phase !== "ready" && (
        <div className="space-y-1.5 border-t border-border pt-3">
          {!overrideOpen ? (
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px]"
              onClick={() => setOverrideOpen(true)}
              data-testid="readiness-override-open"
            >
              Mark ready anyway
            </Button>
          ) : (
            <>
              <label className="block text-[11px] text-muted-foreground" htmlFor="readiness-reason">
                Why is this draft ready without a passing report? The reason is required.
              </label>
              <Input
                id="readiness-reason"
                value={reason}
                maxLength={300}
                onChange={(e) => setReason(e.target.value)}
                data-testid="readiness-override-reason"
              />
              {overrideError && (
                <p className="text-destructive" data-testid="readiness-override-error">
                  {overrideError}
                </p>
              )}
              <div className="flex gap-1.5">
                <Button
                  size="sm"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => void markReady()}
                  disabled={overriding || reason.trim().length === 0}
                  data-testid="readiness-override-confirm"
                >
                  {overriding ? <Loader2 className="h-3 w-3 animate-spin" /> : "Mark ready"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => {
                    setOverrideOpen(false);
                    setOverrideError(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
