/**
 * Conversation settings — the same form when starting a conversation and (live) while it runs.
 * Controlled: the owner decides when a change is sent (start = with the request, running = PATCH).
 */
import * as React from "react";
import { Textarea } from "@/components/ui/textarea";
import type { ConversationSettings } from "@/lib/api/federation";
import { LIMITS, clampInt } from "@/lib/agent-conversation-view";

const field = "h-9 w-full rounded-md border bg-background px-2 text-sm";

function Range({
  label, hint, value, limits, unit, onChange, testid,
}: {
  label: string; hint: string; value: number; limits: readonly [number, number];
  unit: string; onChange: (v: number) => void; testid: string;
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-foreground/80">{label}</span>
        <span className="tabular-nums text-muted-foreground" data-testid={`${testid}-value`}>
          {value} {unit}
        </span>
      </span>
      <input type="range" min={limits[0]} max={limits[1]} value={value} className="w-full accent-[hsl(var(--accent))]"
             data-testid={testid} aria-label={label}
             onChange={(e) => onChange(clampInt(Number(e.target.value), limits))} />
      <span className="block text-[11px] text-muted-foreground">{hint}</span>
    </label>
  );
}

export function SettingsPanel({
  value, onChange, names, disabled,
}: {
  value: ConversationSettings;
  onChange: (next: ConversationSettings) => void;
  names: { local?: string; peer?: string };
  disabled?: boolean;
}) {
  const set = (patch: Partial<ConversationSettings>) => onChange({ ...value, ...patch });
  const note = (side: "local" | "peer", text: string) =>
    set({ role_notes: { ...value.role_notes, [side]: text } });
  return (
    <fieldset disabled={disabled} className="space-y-4" data-testid="conversation-settings">
      <Range label="Reply length" unit="words" limits={LIMITS.max_words} value={value.max_words}
             hint="Upper bound each agent is asked to keep to." testid="setting-max-words"
             onChange={(v) => set({ max_words: v })} />
      <Range label="Pause between turns" unit="s" limits={LIMITS.pace_s} value={value.pace_s}
             hint="Time to jump in before the next agent answers. Your message ends the wait early."
             testid="setting-pace" onChange={(v) => set({ pace_s: v })} />
      {(["local", "peer"] as const).map((side) => (
        <label key={side} className="block space-y-1">
          <span className="text-xs font-medium text-foreground/80">
            Instruction for {side === "local" ? names.local ?? "your agent" : names.peer ?? "the peer agent"}
          </span>
          <Textarea rows={2} maxLength={LIMITS.role_note} value={value.role_notes[side]}
                    className={`${field} h-auto min-h-[3.5rem] py-1.5 text-sm`}
                    placeholder={side === "local" ? "e.g. Argue for the cheaper option." : "Optional — shown to this agent as your instruction."}
                    data-testid={`setting-note-${side}`} aria-label={`Instruction for the ${side} agent`}
                    onChange={(e) => note(side, e.target.value)} />
        </label>
      ))}
      <p className="text-[11px] text-muted-foreground">
        Settings apply from the next turn. What the peer installation may do is set in the peer's own
        permissions, not here.
      </p>
    </fieldset>
  );
}
