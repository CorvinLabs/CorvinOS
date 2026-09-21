import React, { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LearningLoopGrid } from "@/components/learning-loop-grid";
import { LearningLoopDetail } from "@/components/learning-loop-detail";
import { useLoops } from "@/hooks/use-learning-loops";

function LearningLoopsPageComponent() {
  const { loops, loading, error } = useLoops();
  const [selectedLoop, setSelectedLoop] = useState<string | null>(null);
  const selectedLoopData = loops?.find((l) => l.loop_id === selectedLoop);

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Learning Loops</h1>
        <p className="text-gray-600">Monitor plugin and skill learning loops</p>
      </div>

      {error && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-800">{error}</CardContent>
        </Card>
      )}

      <Tabs defaultValue="grid" className="w-full">
        <TabsList>
          <TabsTrigger value="grid">All Loops ({loops?.length || 0})</TabsTrigger>
          <TabsTrigger value="detail" disabled={!selectedLoop}>
            Details
          </TabsTrigger>
        </TabsList>

        <TabsContent value="grid">
          <Card>
            <CardHeader>
              <CardTitle>Learning Loop Status</CardTitle>
            </CardHeader>
            <CardContent>
              <LearningLoopGrid loops={loops || []} loading={loading} onSelect={setSelectedLoop} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="detail">
          {selectedLoopData ? (
            <LearningLoopDetail loop={selectedLoopData} />
          ) : (
            <Card>
              <CardContent className="pt-6 text-gray-600">Select a loop to view details</CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default LearningLoopsPageComponent;
