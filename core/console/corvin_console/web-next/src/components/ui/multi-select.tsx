import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Multi-select over a small, finite option list. Rendered as a chip row of
 * the current selection plus a checkbox list — no popover, no Radix, in the
 * same spirit as select.tsx. Options carry an `id` and a display `label`.
 */
export interface MultiSelectOption {
  id: string;
  label: string;
}

export interface MultiSelectProps {
  options: readonly MultiSelectOption[];
  selected: string[];
  onChange: (selected: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function MultiSelect({
  options,
  selected,
  onChange,
  disabled = false,
  placeholder = "Select…",
  className,
}: MultiSelectProps) {
  const listId = React.useId();
  const toggle = (id: string) => {
    if (disabled) return;
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  };
  const labelOf = (id: string) => options.find((o) => o.id === id)?.label ?? id;

  return (
    <div className={cn("space-y-2", className)}>
      <div
        className={cn(
          "flex min-h-10 flex-wrap items-center gap-1 rounded-md border border-input bg-background px-3 py-2 text-sm",
          disabled && "cursor-not-allowed opacity-50",
        )}
      >
        {selected.length === 0 ? (
          <span className="text-muted-foreground">{placeholder}</span>
        ) : (
          selected.map((id) => (
            <span
              key={id}
              className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs text-secondary-foreground"
            >
              {labelOf(id)}
              <button
                type="button"
                aria-label={`Remove ${labelOf(id)}`}
                disabled={disabled}
                onClick={() => toggle(id)}
                className="rounded-full hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))
        )}
      </div>
      <ul id={listId} className="grid gap-1 sm:grid-cols-2" aria-multiselectable="true">
        {options.map((opt) => {
          const checked = selected.includes(opt.id);
          return (
            <li key={opt.id}>
              <label
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                  checked ? "border-primary/60 bg-primary/5" : "border-border hover:bg-muted/40",
                  disabled && "cursor-not-allowed",
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => toggle(opt.id)}
                  className="h-4 w-4 accent-[hsl(var(--primary))]"
                />
                <span>{opt.label}</span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
