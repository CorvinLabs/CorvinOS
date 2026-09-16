/**
 * Progress bar.
 *
 * Added 2026-09-16: skill-manager.tsx (Skill Forge Phase 5) has imported
 * `@/components/ui/progress` since it shipped, and the file did not exist —
 * `tsc` reported TS2307 and the page's chunk threw on load. esbuild does not
 * type-check, so the build stayed green and the failure only appeared in the
 * browser, which reads exactly like a stale bundle.
 *
 * No @radix-ui/react-progress dependency: this is a div with a width, and
 * adding a package to draw one is not worth the supply-chain surface.
 */
import * as React from "react";
import { cn } from "@/lib/utils";

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 0–100. Values outside the range are clamped; NaN renders as 0. */
  value?: number | null;
  /** Upper bound when the caller counts in something other than percent. */
  max?: number;
}

const Progress = React.forwardRef<HTMLDivElement, ProgressProps>(
  ({ className, value, max = 100, ...props }, ref) => {
    const raw = typeof value === "number" && Number.isFinite(value) ? value : 0;
    const bounded = Math.min(Math.max(raw, 0), max);
    const pct = max > 0 ? (bounded / max) * 100 : 0;

    return (
      <div
        ref={ref}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={bounded}
        className={cn(
          "relative h-2 w-full overflow-hidden rounded-full bg-muted",
          className,
        )}
        {...props}
      >
        <div
          className="h-full bg-primary transition-[width] duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    );
  },
);
Progress.displayName = "Progress";

export { Progress };
