// Formatting helpers shared by the Learning Loops panel (ADR-0908).
//
// Dates are pinned to en-US (ADR-0764): the console renders one locale on
// every install, so a number or date never changes shape with the browser's
// language setting.

export const formatTime = (timestamp?: string) => {
  if (!timestamp) return "—";
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return "—";
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(mins / 60);
  const days = Math.floor(hours / 24);

  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
};

export const formatHealth = (score: number) => `${(score * 100).toFixed(0)}%`;

// Status is a nominal category, so it is coloured by identity, not by a ramp
// keyed on the health value (which the health bar already encodes as length).
// Every pair carries a dark-mode variant — the console is `data-theme` driven,
// and a bare `bg-green-100 text-green-800` renders as near-white on dark.
export const getStatusColor = (status: string): string => {
  const colors: Record<string, string> = {
    active:
      "bg-emerald-100 text-emerald-800 border-emerald-300 " +
      "dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/40",
    dormant:
      "bg-amber-100 text-amber-800 border-amber-300 " +
      "dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/40",
    stale:
      "bg-orange-100 text-orange-800 border-orange-300 " +
      "dark:bg-orange-500/15 dark:text-orange-300 dark:border-orange-500/40",
    degrading:
      "bg-red-100 text-red-800 border-red-300 " +
      "dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/40",
    // Not a fifth health state — the absence of an age. A source that does not
    // date its records cannot support "stale", which is a claim that a loop
    // stopped emitting. Deliberately neutral, so it does not read as a verdict.
    unknown: "bg-muted text-muted-foreground border-border",
  };
  return colors[status] || "bg-muted text-muted-foreground border-border";
};
