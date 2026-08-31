import * as React from "react";
import { Moon, Sun, Monitor } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Theme = "dark" | "light" | "auto";

const STORAGE_KEY = "corvin-theme";

function readStored(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "dark" || v === "light" || v === "auto") return v;
  } catch {
    /* localStorage may be blocked */
  }
  return "dark";
}

function applyTheme(t: Theme) {
  let effective: "dark" | "light" = "dark";
  if (t === "auto") {
    const mql = window.matchMedia?.("(prefers-color-scheme: light)");
    effective = mql?.matches ? "light" : "dark";
  } else {
    effective = t;
  }
  document.documentElement.setAttribute("data-theme", effective);
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = React.useState<Theme>(() => readStored());
  React.useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  React.useEffect(() => {
    if (theme !== "auto") return;
    const mql = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!mql) return;
    const handler = () => applyTheme("auto");
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [theme]);
  return [theme, setTheme];
}

export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setTheme] = useTheme();
  const next: Record<Theme, Theme> = { auto: "dark", dark: "light", light: "auto" };
  // Mark that the user has explicitly toggled — future one-time migrations won't reset their choice.
  const handleToggle = () => { try { localStorage.setItem("corvin-dark-default-v2", "1"); } catch {} setTheme(next[theme]); };
  const label: Record<Theme, string> = {
    auto: "System theme",
    dark: "Dark theme",
    light: "Light theme",
  };
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Theme: ${label[theme]} (click to switch)`}
      title={label[theme]}
      onClick={handleToggle}
      className={cn("text-muted-foreground hover:text-foreground", className)}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}
