import React from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle } from "lucide-react";

export function Phase5Collaboration({ projectId }: { projectId: string }) {
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
