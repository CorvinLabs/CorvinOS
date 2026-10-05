/**
 * Small circular identity marker — initials by default, or an icon.
 * Visual language shared by every conversation surface: the single-session
 * chat, group chat, and direct A2A thread (mirrors the now-deleted Agent
 * Hub relay panel's PeerAvatar).
 */
import * as React from "react";
import { cn } from "@/lib/utils";

function initials(label: string): string {
  const words = label.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function ChatAvatar({
  label, icon: Icon, size = "sm", className,
}: {
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full bg-accent/15 font-medium text-accent ring-1 ring-accent/25",
        size === "md" ? "h-9 w-9 text-xs" : "h-7 w-7 text-[10px]",
        className,
      )}
    >
      {Icon ? <Icon className={size === "md" ? "h-4 w-4" : "h-3.5 w-3.5"} /> : initials(label)}
    </div>
  );
}
