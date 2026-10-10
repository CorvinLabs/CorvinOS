import type * as React from "react";
import { cn } from "@/lib/utils";

// Standard button attributes (aria-label, title, data-testid, id …) pass through: a switch with no accessible
// name is announced as "switch" and nothing else. Existing call sites are unaffected.
interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onClick" | "role" | "type" | "onChange"> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}

export function Switch({ checked, onCheckedChange, disabled, className, ...rest }: SwitchProps) {
  return (
    <button
      {...rest}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-accent" : "bg-input",
        className,
      )}
    >
      <span
        className={cn(
          "pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg transition-transform",
          checked ? "translate-x-4" : "translate-x-0",
        )}
      />
    </button>
  );
}
