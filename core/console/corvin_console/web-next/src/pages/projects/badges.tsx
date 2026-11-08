/** Class and access badges: always the same shape, place and words (Gestalt similarity); label + icon + tone, never colour alone. */
import { Building2, Eye, Globe, Lock, Pencil, ShieldAlert } from "lucide-react";
import type { Access, DataClass } from "@/lib/api/projects";
import { ACCESS_META, CLASS_META } from "./roles";

const SHAPE = { globe: Globe, building: Building2, lock: Lock, shield: ShieldAlert } as const;
const TONE = {
  plain: "border-border text-muted-foreground",
  care: "border-amber-600/50 text-amber-800 dark:text-amber-300",
  strict: "border-red-600/50 text-red-800 dark:text-red-300",
} as const;

export function ClassBadge({ cls }: { cls: DataClass }) {
  const m = CLASS_META[cls];
  const Icon = SHAPE[m.shape];
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] ${TONE[m.tone]}`}
      title={m.hint} data-testid="class-badge" data-class={cls}>
      <Icon className="h-3 w-3 shrink-0" aria-hidden /> {m.label}
    </span>
  );
}

export function AccessBadge({ access }: { access: Access }) {
  const m = ACCESS_META[access];
  const Icon = access === "read" ? Eye : Pencil;
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
      title={`${m.label}. ${m.hint}`} data-testid="access-badge" data-access={access}>
      <Icon className="h-3 w-3 shrink-0" aria-hidden /> {m.short}
    </span>
  );
}
