import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatDate(ts: number | string | null | undefined): string {
  if (ts == null) return "—";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["B", "KiB", "MiB", "GiB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/** "3 minutes ago" / "in 2 hours" — the one relative-time formatter the console
 *  needs (CustomRepositoryCard's "Last checked"). Kept local instead of pulling
 *  in date-fns for a single call site. */
export function formatRelativeToNow(value: string | number | Date | null | undefined): string {
  if (value == null) return "—";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const deltaSec = (d.getTime() - Date.now()) / 1000;
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31536000], ["month", 2592000], ["day", 86400],
    ["hour", 3600], ["minute", 60], ["second", 1],
  ];
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, sec] of units) {
    if (Math.abs(deltaSec) >= sec || unit === "second") {
      return rtf.format(Math.round(deltaSec / sec), unit);
    }
  }
  return "—";
}
