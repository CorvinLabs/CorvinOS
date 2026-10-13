import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Save, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { HelpTooltip } from "@/components/ui/help-tooltip";
import { getCostOptimize, setCostOptimize } from "@/lib/api";
import {
  describeState,
  fromPercent,
  inputMaxPercent,
  toPercent,
  type CostOptimizeResponse,
} from "@/lib/cost-optimize";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<string, string> = {
  active: "text-emerald-600 dark:text-emerald-400",
  unlimited: "text-emerald-600 dark:text-emerald-400",
  paused: "text-amber-600 dark:text-amber-400",
  off: "text-muted-foreground",
  measuring: "text-muted-foreground",
};

/**
 * Settings card for the Free-tier allowance of AUTOMATIC model downgrading.
 * It shows what is IN FORCE (server-resolved), the measured month with its
 * window above the number, and lets the operator lower — on Member also raise —
 * the limit. Pinned models are never limited; the copy says so.
 */
export function CostOptimizeCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["cost-optimize"],
    queryFn: ({ signal }) => getCostOptimize(signal),
    // Poll only while the meter is still warming up after a console start.
    refetchInterval: (query) => {
      const d = query.state.data as CostOptimizeResponse | undefined;
      return d && d.state.reason === "meter_warming" ? 3000 : 60_000;
    },
  });

  const [draft, setDraft] = React.useState<string>("");
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (q.data && !dirty) {
      const p = toPercent(q.data.choice.cap_ratio ?? q.data.effective.ratio);
      setDraft(p === null ? "" : String(p));
    }
  }, [q.data, dirty]);

  const data = q.data;
  const text = data ? describeState(data) : null;
  const maxPct = data ? inputMaxPercent(data) : 0;
  const parsed = draft.trim() === "" ? null : Number(draft);
  const invalid =
    parsed !== null && (fromPercent(parsed) === undefined || parsed > maxPct);
  const isMember = data?.tier === "member";

  const save = async (value: number | null) => {
    setSaving(true);
    setError(null);
    try {
      await setCostOptimize({ cap_ratio: value }, csrf);
      setSaved(true);
      setDirty(false);
      setTimeout(() => setSaved(false), 2500);
      await qc.invalidateQueries({ queryKey: ["cost-optimize"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onSave = () => {
    if (invalid) return;
    const ratio = parsed === null ? null : fromPercent(parsed);
    void save(ratio === undefined ? null : ratio);
  };

  const onReset = () => {
    setDirty(false);
    setError(null);
    void save(null);
  };

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-4" data-testid="cost-optimize-card">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium">Automatic model saving</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Let CorvinOS choose a cheaper model for simple turns.
              {data && !isMember
                ? ` On the Free tier this is limited to ${toPercent(data.ceiling.ratio)} % measured saving per month.`
                : ""}{" "}
              Models you pin in Models are never limited.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {saved && <Check className="h-4 w-4 text-emerald-500" />}
            {dirty && !saving && (
              <>
                <Button
                  variant="ghost" size="sm" className="h-7 px-2 text-xs"
                  onClick={() => { setDirty(false); setError(null); void qc.invalidateQueries({ queryKey: ["cost-optimize"] }); }}
                >
                  <X className="h-3 w-3 mr-1" />Cancel
                </Button>
                <Button size="sm" className="h-7 px-3 text-xs" onClick={onSave} disabled={invalid}>
                  <Save className="h-3 w-3 mr-1" />Save
                </Button>
              </>
            )}
          </div>
        </div>

        {q.isLoading && (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {q.isError && (
          <p className="text-xs text-muted-foreground">
            Not available on this build.
          </p>
        )}

        {data && text && (
          <>
            <div className="space-y-1">
              {text.window && (
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{text.window}</p>
              )}
              <p className={cn("text-sm font-medium", TONE_CLASS[text.tone])} data-testid="cost-optimize-headline">
                {text.headline}
              </p>
              <p className="text-xs text-muted-foreground">{text.detail}</p>
              {text.reference && <p className="text-[11px] text-muted-foreground">{text.reference}</p>}
            </div>

            <div className="space-y-1 max-w-xs">
              <div className="flex items-center gap-1.5">
                <Label className="text-xs font-medium" htmlFor="cost-optimize-limit">
                  Monthly saving limit
                </Label>
                <HelpTooltip title="Monthly saving limit" side="top" width="sm">
                  The share of this month’s spend that automatic saving may remove. 0 turns it off.
                  {isMember
                    ? " Leave empty for no limit."
                    : ` Free can lower this, up to ${toPercent(data.ceiling.ratio)} %.`}
                </HelpTooltip>
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  id="cost-optimize-limit"
                  type="number"
                  min={0}
                  max={maxPct}
                  step={0.5}
                  value={draft}
                  placeholder={isMember ? "no limit" : String(toPercent(data.ceiling.ratio))}
                  onChange={(e) => { setDraft(e.target.value); setDirty(true); setSaved(false); setError(null); }}
                  className={cn(
                    "w-full rounded-md border bg-background px-2.5 py-1 text-sm font-mono tabular-nums shadow-sm focus:outline-none focus:ring-1 focus:ring-ring",
                    invalid ? "border-destructive" : "border-input",
                  )}
                />
                <span className="text-[11px] text-muted-foreground shrink-0">%</span>
                <Button
                  variant="ghost" size="sm" className="h-7 px-2 text-xs shrink-0"
                  onClick={onReset} disabled={saving || data.choice.cap_ratio === null}
                >
                  Use default
                </Button>
              </div>
              {invalid && (
                <p className="text-[11px] text-destructive">
                  Enter a value between 0 and {maxPct} %.
                </p>
              )}
            </div>

            {text.clampNotice && (
              <p className="text-xs text-amber-600 dark:text-amber-400 bg-amber-500/10 rounded px-2 py-1.5">
                {text.clampNotice}
              </p>
            )}
          </>
        )}

        {error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
