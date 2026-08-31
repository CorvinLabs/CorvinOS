import * as React from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  /** Bold headline, e.g. "No tools yet". */
  title: string;
  /** One-to-two sentence explanation of the page's purpose and how to begin. */
  description: string;
  /** Optional lucide icon component, rendered muted above the title. */
  icon?: React.ComponentType<{ className?: string }>;
  /** Optional call-to-action (button or link), rendered below the description. */
  action?: React.ReactNode;
  /** Extra classes for the outer Card. */
  className?: string;
}

/**
 * Reusable empty-state card for data-driven console pages.
 *
 * Renders a centered, dashed-border card with an optional muted icon, a bold
 * title, a muted-foreground description, and an optional action below. Mirrors
 * the inline empty-state pattern used across the console:
 *
 *   <Card className="border-dashed">
 *     <CardContent className="py-12 text-center"> … </CardContent>
 *   </Card>
 */
export function EmptyState({
  title,
  description,
  icon: Icon,
  action,
  className,
}: EmptyStateProps) {
  return (
    <Card className={cn("border-dashed", className)}>
      <CardContent className="flex flex-col items-center py-12 text-center">
        {Icon && <Icon className="mb-3 h-8 w-8 text-muted-foreground/40" />}
        <p className="font-medium">{title}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
        {action && <div className="mt-4">{action}</div>}
      </CardContent>
    </Card>
  );
}
