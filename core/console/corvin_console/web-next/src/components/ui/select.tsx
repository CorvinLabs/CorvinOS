import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Lightweight <select> wrapper styled to match shadcn primitives.
 * Avoids the full Radix Select dependency for v1 of the console — a
 * native select is sufficient for the small, finite enums we use here.
 */
export interface SelectProps
  extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  placeholder?: string;
}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, placeholder, ...props }, ref) => (
    <div className="relative">
      <select
        ref={ref}
        className={cn(
          "flex h-10 w-full appearance-none rounded-md border border-input bg-background pl-3 pr-9 py-2 text-sm",
          "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {placeholder !== undefined && (
          <option value="" disabled hidden>
            {placeholder}
          </option>
        )}
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
    </div>
  ),
);
Select.displayName = "Select";

// Compat shims for Radix-UI Select API (used in engine-config.tsx)
const SelectTrigger = React.forwardRef<HTMLSelectElement, { children: React.ReactNode; value?: string; onValueChange?: (value: string) => void; className?: string }>(
  ({ className, ...props }, ref) => <Select ref={ref} className={className} {...props} />
);
SelectTrigger.displayName = "SelectTrigger";

const SelectContent = ({ children }: { children: React.ReactNode }) => <>{children}</>;
SelectContent.displayName = "SelectContent";

const SelectItem = React.forwardRef<HTMLOptionElement, { value: string; children: React.ReactNode }>(
  ({ value, children }, ref) => <option ref={ref} value={value}>{children}</option>
);
SelectItem.displayName = "SelectItem";

const SelectValue = () => null;
SelectValue.displayName = "SelectValue";

export { Select, SelectTrigger, SelectContent, SelectItem, SelectValue };
