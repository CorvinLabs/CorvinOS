import { dayLabel } from "@/lib/chat-time";

/** Where a thread crosses into a new calendar day — the rows below it show only the time. */
export function DaySeparator({ ts, nowS }: { ts: number; nowS: number }) {
  const label = dayLabel(ts, nowS);
  return (
    <div role="separator" aria-label={label} data-testid="chat-day-separator"
      className="flex items-center gap-3 py-1 text-[11px] font-medium text-muted-foreground">
      <span aria-hidden className="h-px flex-1 bg-border" />
      <span>{label}</span>
      <span aria-hidden className="h-px flex-1 bg-border" />
    </div>
  );
}
