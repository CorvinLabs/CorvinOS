import React, { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LearningLoopGrid } from "@/components/learning-loop-grid";
import { LearningLoopDetail } from "@/components/learning-loop-detail";
import { useLoops } from "@/hooks/use-learning-loops";

/**
 * The Learning Loops surface, mounted in two places (ADR-0908):
 * the standalone `/app/learning-loops` panel and the Learnings dashboard's
 * "Learning Loops" tab. One component, so the two can never drift into showing
 * different numbers for the same loops.
 */
export const LearningLoopsView: React.FC = () => {
  const { loops, window, loading, error } = useLoops();
  const [selectedLoop, setSelectedLoop] = useState<string | null>(null);
  const [tab, setTab] = useState("grid");

  const selectedLoopData = useMemo(
    () => loops?.find((l) => l.loop_id === selectedLoop) ?? null,
    [loops, selectedLoop],
  );

  const select = (loopId: string) => {
    setSelectedLoop(loopId);
    setTab("detail");
  };

  const measured = (loops ?? []).filter((l) => l.health.score !== null).length;

  return (
    <div className="space-y-4">
      {error && (
        <Card className="border-destructive/40 bg-destructive/10">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="grid">All loops ({loops?.length ?? 0})</TabsTrigger>
          <TabsTrigger value="detail" disabled={!selectedLoopData}>
            {selectedLoopData ? selectedLoopData.skill_id || selectedLoopData.plugin_id : "Details"}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="grid">
          <Card>
            <CardHeader>
              <CardTitle>Learning loop status</CardTitle>
              {/* A narrowed total never travels without the window it was counted
                  over — the scan is bounded, and a truncated pass describes a
                  different period than a complete one. */}
              {!loading && loops && loops.length > 0 && (
                <p className="text-sm text-muted-foreground">
                  {measured} of {loops.length} loops have a measured health score
                  {window ? (
                    <>
                      {" · "}
                      counted over {window.scanned_events.toLocaleString("en-US")} learning events
                      {window.truncated && " (scan bound reached — older events are outside this window)"}
                    </>
                  ) : null}
                </p>
              )}
            </CardHeader>
            <CardContent>
              <LearningLoopGrid
                loops={loops || []}
                loading={loading}
                onSelect={select}
                selected={selectedLoop}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="detail">
          {selectedLoopData ? (
            <LearningLoopDetail loop={selectedLoopData} />
          ) : (
            <Card>
              <CardContent className="pt-6 text-sm text-muted-foreground">
                Select a loop to view details.
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default LearningLoopsView;
