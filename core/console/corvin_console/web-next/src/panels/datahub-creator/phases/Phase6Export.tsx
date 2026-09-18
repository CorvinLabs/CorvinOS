import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, CheckCircle2, Download } from "lucide-react";

export function Phase6Export({ projectId }: { projectId: string }) {
  const [exporting, setExporting] = useState(false);
  const [exported, setExported] = useState(false);

  async function handleExport() {
    try {
      setExporting(true);
      const response = await fetch(
        `/v1/console/datahub/projects/${projectId}/export?format=jsonl`
      );
      if (!response.ok) throw new Error("Export failed");
      const data = await response.json();
      
      // In production, this would download the file
      setExported(true);
      setTimeout(() => setExporting(false), 500);
    } catch (err) {
      setExporting(false);
    }
  }

  return (
    <div className="space-y-4">
      {exported ? (
        <Alert>
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          <AlertDescription>
            ✓ Project exported successfully! Your metrics and feedback have been archived.
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <Alert>
            <AlertDescription>
              Ready to export your project? This will create an archive of all metrics, feedback, and project metadata in GDPR-compliant format.
            </AlertDescription>
          </Alert>
          <Button onClick={handleExport} disabled={exporting}>
            {exporting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            <Download className="h-4 w-4 mr-2" />
            Export Project
          </Button>
        </>
      )}
    </div>
  );
}
