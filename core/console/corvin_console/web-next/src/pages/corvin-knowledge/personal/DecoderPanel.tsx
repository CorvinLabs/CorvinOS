/**
 * The personal decoder for one idea: Seed · Shape · Choices · Plan · Done, one step per click.
 *
 * Each step shows its check results and the critic's objections; "open issues" is the share of
 * failed checks, measured by plain rules, never the model's own opinion, and never the number of objections (the critic
 * always names about three). The critic's first open objection is the main point; the others stay visible but quiet,
 * because measured on real runs the first one is far more often a real defect. Nothing runs by itself.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import * as pk from "@/lib/api/personal-kb";

const ORDER = ["seed", "shape", "choices", "plan", "done"] as const;

export function DecoderPanel({ idea, csrf, onChanged }: { idea: pk.PersonalEntity; csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const key = ["personal-kb", "decoder", idea.uid];
  const q = useQuery({ queryKey: key, queryFn: ({ signal }) => pk.getDecoder(idea.uid, signal), retry: false });
  const [msg, setMsg] = useState<string | null>(null);
  const [reason, setReason] = useState<Record<number, string>>({});
  const done = () => { qc.invalidateQueries({ queryKey: ["personal-kb"] }); onChanged(); };
  const fail = (e: unknown) => setMsg(e instanceof ApiError ? (e.status === 429 ? "Please wait a moment." : e.message) : "That did not work.");
  const step = useMutation({
    mutationFn: (stage?: string) => pk.unfoldIdea(idea.uid, csrf, stage),
    onSuccess: (r) => { setMsg(r.gate_ok ? `${pk.STAGE_LABELS[r.stage]} is ready — ${pk.openIssuesLabel(r.loss)}.` : `${pk.STAGE_LABELS[r.stage]} needs work: ${r.failures.join("; ")}.`); done(); },
    onError: fail,
  });
  const obj = useMutation({
    mutationFn: ({ i, action, text }: { i: number; action: "fix" | "dismiss"; text: string }) =>
      pk.resolveObjection(idea.uid, i, action, csrf, reason[i] ?? "", text),
    onSuccess: done, onError: fail,
  });
  if (q.isPending) return <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />;
  if (q.isError) return <p className="text-xs text-destructive">The development steps could not be loaded.</p>;
  const d = q.data.decoder;
  const cur = ORDER.indexOf(d.stage);
  const busy = step.isPending || obj.isPending;
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3 space-y-3" data-testid="personal-decoder">
      <ol className="flex flex-wrap items-center gap-2 text-xs" aria-label="Development steps">
        {ORDER.map((s, i) => (
          <li key={s} className="flex items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 border ${i <= cur ? "border-accent text-foreground" : "border-border text-muted-foreground"}`}
              aria-current={i === cur ? "step" : undefined}>{pk.STAGE_LABELS[s]}</span>
            {i < ORDER.length - 1 && <span className="text-muted-foreground">›</span>}
          </li>
        ))}
        <span className="ml-auto text-muted-foreground">{pk.openIssuesLabel(Object.keys(d.gates ?? {}).length ? d.loss : null)} · {d.iterations}/5 revisions</span>
      </ol>
      {q.data.critic_check?.last === "missed" && (
        <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="personal-critic-canary-missed">
          The last test of the checker missed a problem planted on purpose. Read its remarks with extra care.
        </p>
      )}
      {Object.entries(d.gates).filter(([, g]) => !g.ok).map(([s, g]) => (
        <p key={s} className="text-xs text-amber-700 dark:text-amber-300">{pk.STAGE_LABELS[s]} is missing: {g.failures.join("; ")}</p>
      ))}
      {(() => {
        const { main, rest, earlier, done: resolved } = pk.splitObjections(d.objections, d.last_stage ?? d.stage);
        // Remarks on an earlier step offer Dismiss only: reworking an earlier step replaces its drafts and uses a revision,
        // which the Redo button asks about first.
        const row = ([o, i]: [pk.DecoderObjection, number], loud: boolean, rework = true) => (
          <li key={i} className="flex flex-wrap items-center gap-2" data-testid={loud ? "personal-objection-main" : "personal-objection-other"}>
            <Badge variant={o.resolved ? "secondary" : loud ? "warn" : "outline"}>{o.category}</Badge>
            <span className={o.resolved ? "line-through text-muted-foreground" : loud ? "" : "text-muted-foreground"}>{o.text}</span>
            {!o.resolved && <>
              {rework && <Button size="sm" variant="outline" disabled={!csrf || busy} onClick={() => obj.mutate({ i, action: "fix", text: o.text })}>Rework this step</Button>}
              <Input className="h-8 max-w-[220px]" placeholder="Why it does not matter" value={reason[i] ?? ""} onChange={(e) => setReason({ ...reason, [i]: e.target.value })} />
              <Button size="sm" variant="ghost" disabled={!csrf || busy || !(reason[i] ?? "").trim()} onClick={() => obj.mutate({ i, action: "dismiss", text: o.text })}>Dismiss</Button>
            </>}
          </li>
        );
        return <>
          {main && <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Main point from the check</p>
            <ul className="space-y-2 text-sm">{row(main, true)}</ul>
          </div>}
          {rest.length > 0 && <details className="text-sm" data-testid="personal-objections-other">
            <summary className="cursor-pointer text-xs text-muted-foreground">Other remarks on this step ({rest.length})</summary>
            <ul className="mt-2 space-y-2">{rest.map((x) => row(x, false))}</ul>
          </details>}
          {earlier.length > 0 && <details className="text-sm" data-testid="personal-objections-earlier">
            <summary className="cursor-pointer text-xs text-muted-foreground">Open remarks on earlier steps ({earlier.length})</summary>
            <ul className="mt-2 space-y-2">{earlier.map((x) => (
              <li key={x[1]} className="space-y-1"><span className="text-xs text-muted-foreground">{pk.STAGE_LABELS[x[0].stage] ?? x[0].stage}</span>
                <ul>{row(x, false, false)}</ul></li>))}</ul>
          </details>}
          {resolved.length > 0 && <ul className="space-y-1 text-xs">{resolved.map((x) => row(x, false, false))}</ul>}
        </>;
      })()}
      <div className="flex flex-wrap items-center gap-2">
        {q.data.next_stage && (
          <Button size="sm" variant="accent" disabled={!csrf || busy} onClick={() => step.mutate(undefined)} data-testid="personal-decoder-next">
            {busy && <Loader2 className="w-4 h-4 animate-spin" />} Next step: {pk.STAGE_LABELS[q.data.next_stage]}
          </Button>
        )}
        {cur >= 1 && d.stage !== "done" && (
          <Button size="sm" variant="outline" disabled={!csrf || busy} onClick={() => { if (window.confirm(`Redo ${pk.STAGE_LABELS[ORDER[cur]]}? Its untouched drafts are replaced; tasks and notes you already changed are kept.`)) step.mutate(ORDER[cur]); }}>Redo {pk.STAGE_LABELS[ORDER[cur]]}</Button>
        )}
        {d.stage === "plan" && <span className="text-xs text-muted-foreground">The plan's tasks are under Tasks, Only for me.</span>}
      </div>
      {msg && <p role="status" className="text-xs text-muted-foreground">{msg}</p>}
    </div>
  );
}
