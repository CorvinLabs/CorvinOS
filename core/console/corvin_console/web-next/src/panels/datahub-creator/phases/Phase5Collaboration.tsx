import React from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle } from "lucide-react";

// `projectId` is part of the props contract (the parent passes it) but
// this phase does not read it yet — named `_projectId` so the unused
// binding is explicit rather than an empty destructuring pattern.
export function Phase5Collaboration({ projectId: _projectId }: { projectId: string }) {
  return (
    <div className="space-y-4">
      <Alert>
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          Phase 5 (Collaboration) implementation in progress. Share projects with team members coming soon.
        </AlertDescription>
      </Alert>
    </div>
  );
}
