import * as React from "react";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface HelpTooltipProps {
  title: string;
  children: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
  width?: "sm" | "md" | "lg";
  className?: string;
}

export function HelpTooltip({
  title,
  children,
  side = "top",
  align = "center",
  width = "md",
  className,
}: HelpTooltipProps) {
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const closeTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const openPanel = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    setOpen(true);
  };

  const scheduleClose = () => {
    closeTimer.current = setTimeout(() => setOpen(false), 150);
  };

  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  React.useEffect(() => () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);

  const widthClass = { sm: "w-52", md: "w-64", lg: "w-80" }[width];

  const positionClass = {
    top: {
      start:  "bottom-full mb-2.5 left-0",
      center: "bottom-full mb-2.5 left-1/2 -translate-x-1/2",
      end:    "bottom-full mb-2.5 right-0",
    },
    bottom: {
      start:  "top-full mt-2.5 left-0",
      center: "top-full mt-2.5 left-1/2 -translate-x-1/2",
      end:    "top-full mt-2.5 right-0",
    },
    left: {
      start:  "right-full mr-2.5 top-0",
      center: "right-full mr-2.5 top-1/2 -translate-y-1/2",
      end:    "right-full mr-2.5 bottom-0",
    },
    right: {
      start:  "left-full ml-2.5 top-0",
      center: "left-full ml-2.5 top-1/2 -translate-y-1/2",
      end:    "left-full ml-2.5 bottom-0",
    },
  }[side][align];

  // Small caret arrow pointing toward the trigger
  const caretClass = {
    top:    "top-full left-1/2 -translate-x-1/2 -mt-px border-r border-b",
    bottom: "bottom-full left-1/2 -translate-x-1/2 -mb-px border-l border-t",
    left:   "left-full top-1/2 -translate-y-1/2 -ml-px border-r border-t",
    right:  "right-full top-1/2 -translate-y-1/2 -mr-px border-l border-b",
  }[side];

  return (
    <div
      ref={containerRef}
      className={cn("relative inline-flex items-center", className)}
      onMouseEnter={openPanel}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        aria-label={`Help: ${title}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className={cn(
          "inline-flex h-[1.1rem] w-[1.1rem] shrink-0 cursor-help items-center justify-center",
          "rounded-full border border-border/60 bg-muted/50 text-muted-foreground/60",
          "transition-all hover:border-accent/50 hover:bg-accent/10 hover:text-accent",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent",
        )}
      >
        <HelpCircle className="h-2.5 w-2.5" />
      </button>

      {open && (
        <div
          role="tooltip"
          onMouseEnter={openPanel}
          onMouseLeave={scheduleClose}
          className={cn(
            "absolute z-50 rounded-xl border border-border bg-popover/95 shadow-xl",
            "backdrop-blur-sm ring-1 ring-black/5",
            widthClass,
            positionClass,
          )}
        >
          {/* Caret */}
          <div
            className={cn(
              "absolute h-2 w-2 rotate-45 border border-border bg-popover",
              caretClass,
            )}
          />
          <div className="relative p-3">
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-accent">
              {title}
            </p>
            <div className="text-xs leading-relaxed text-popover-foreground/80 [&_strong]:font-semibold [&_strong]:text-popover-foreground [&_code]:rounded [&_code]:bg-muted/80 [&_code]:px-1 [&_code]:font-mono [&_code]:text-[10px]">
              {children}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
