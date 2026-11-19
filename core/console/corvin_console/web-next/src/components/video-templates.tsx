/**
 * The video template picker in the Video Producer composer. The server owns the templates
 * (routes/video_producer_api.py `/video/templates*`) and the remembered choice; this file only draws the list and
 * reports a pick. A template that cannot be produced yet is shown with its reason and cannot be chosen.
 */
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, LayoutTemplate } from "lucide-react";
import { groupTemplates, templateFacts, templateName, type VideoTemplateList } from "@/lib/api/video-templates";

export function TemplateChip({ list, current, onPick, disabledReason, error }: {
  list: VideoTemplateList | undefined; current: string | null; onPick: (id: string) => void;
  /** Why the picker is off right now (a film or a revision has its own structure); null = usable. */
  disabledReason: string | null; error: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  useEffect(() => { if (disabledReason) setOpen(false); }, [disabledReason]);
  if (error) {
    return <span className="px-2.5 py-1 text-muted-foreground" data-testid="template-unavailable" title="Video templates are not available on this installation.">Templates unavailable</span>;
  }
  const groups = list ? groupTemplates(list.templates) : [];
  const label = disabledReason ? "n/a" : templateName(list, current);
  return (
    <div ref={box} className="relative" onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
      <button type="button" aria-haspopup="listbox" aria-expanded={open} data-testid="template-chip" disabled={!list || !!disabledReason}
        title={disabledReason ?? "What kind of video to make: length, structure and tone"} onClick={() => setOpen((o) => !o)}
        className="px-2.5 py-1 rounded-md inline-flex items-center gap-1 max-w-[16rem] text-muted-foreground hover:text-foreground border border-border disabled:opacity-50 disabled:hover:text-muted-foreground">
        <LayoutTemplate className="w-3 h-3 shrink-0" /><span className="truncate">Template: {label}</span><ChevronDown className="w-3 h-3 shrink-0" />
      </button>
      {open && list && (
        <div role="listbox" aria-label="Video template" data-testid="template-menu"
          className="absolute z-20 top-full mt-1 right-0 w-[22rem] max-w-[90vw] max-h-[60vh] overflow-y-auto rounded-md border border-border bg-card shadow-lg p-1">
          {groups.map((g) => (
            <div key={g.category} role="group" aria-label={g.label}>
              <p className="px-2 pt-1.5 pb-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">{g.label}</p>
              {g.items.map((t) => (
                <button key={t.id} type="button" role="option" aria-selected={current === t.id} aria-disabled={!t.available} data-testid={`template-option-${t.id}`}
                  disabled={!t.available} title={t.available ? t.audience : t.unavailable_reason}
                  onClick={() => { onPick(t.id); setOpen(false); }}
                  className="w-full flex items-start gap-2 text-left text-xs px-2 py-1.5 rounded hover:bg-muted/60 disabled:opacity-60 disabled:hover:bg-transparent">
                  <Check className={`w-3 h-3 mt-0.5 shrink-0 ${current === t.id ? "" : "invisible"}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      <span className="font-medium text-foreground">{t.name.en}</span>
                      {list.default_template_id === t.id && <span className="text-muted-foreground">remembered</span>}
                    </span>
                    <span className="block text-muted-foreground">{t.summary.en}</span>
                    <span className="block text-muted-foreground">{templateFacts(t)} · {t.scenes.min}-{t.scenes.max} scenes</span>
                    {!t.available && <span className="block text-muted-foreground" data-testid={`template-reason-${t.id}`}>Not available yet: {t.unavailable_reason}</span>}
                  </span>
                </button>
              ))}
            </div>
          ))}
          <p className="px-2 py-1 border-t border-border mt-1 text-[11px] text-muted-foreground">Your choice is remembered for the next video.</p>
        </div>
      )}
    </div>
  );
}
