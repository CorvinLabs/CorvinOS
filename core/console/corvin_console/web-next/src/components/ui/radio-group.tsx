import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Lightweight radio group on native <input type="radio">, mirroring the
 * Radix API (RadioGroup value/onValueChange, RadioGroupItem value/id) so
 * call sites read the same. Like select.tsx, this avoids pulling in a Radix
 * package for a small, finite set of choices.
 */
interface RadioGroupContextValue {
  name: string;
  value: string;
  disabled: boolean;
  onValueChange: (value: string) => void;
}

const RadioGroupContext = React.createContext<RadioGroupContextValue | null>(null);

export interface RadioGroupProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "onChange"> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  name?: string;
}

const RadioGroup = React.forwardRef<HTMLDivElement, RadioGroupProps>(
  ({ className, value, defaultValue, onValueChange, disabled = false, name, children, ...props }, ref) => {
    const [internal, setInternal] = React.useState(defaultValue ?? "");
    const current = value !== undefined ? value : internal;
    const generatedName = React.useId();
    const handleChange = React.useCallback(
      (next: string) => {
        if (value === undefined) setInternal(next);
        onValueChange?.(next);
      },
      [onValueChange, value],
    );
    return (
      <RadioGroupContext.Provider
        value={{ name: name ?? generatedName, value: current, disabled, onValueChange: handleChange }}
      >
        <div ref={ref} role="radiogroup" className={cn("grid gap-2", className)} {...props}>
          {children}
        </div>
      </RadioGroupContext.Provider>
    );
  },
);
RadioGroup.displayName = "RadioGroup";

export interface RadioGroupItemProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "value" | "onChange"> {
  value: string;
}

const RadioGroupItem = React.forwardRef<HTMLInputElement, RadioGroupItemProps>(
  ({ className, value, disabled, ...props }, ref) => {
    const ctx = React.useContext(RadioGroupContext);
    if (!ctx) throw new Error("RadioGroupItem must be rendered inside a RadioGroup");
    return (
      <input
        ref={ref}
        type="radio"
        name={ctx.name}
        value={value}
        checked={ctx.value === value}
        disabled={disabled || ctx.disabled}
        onChange={() => ctx.onValueChange(value)}
        className={cn(
          "h-4 w-4 shrink-0 cursor-pointer border-border text-primary accent-[hsl(var(--primary))]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      />
    );
  },
);
RadioGroupItem.displayName = "RadioGroupItem";

export { RadioGroup, RadioGroupItem };
