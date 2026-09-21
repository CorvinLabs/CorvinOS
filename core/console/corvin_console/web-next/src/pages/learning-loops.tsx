import React from "react";
import { LearningLoopsView } from "@/components/learning-loops-view";

/**
 * Standalone Learning Loops panel (ADR-0908).
 *
 * The surface itself lives in `LearningLoopsView`, which the Learnings
 * dashboard also mounts as a tab — one component, one set of numbers.
 */
function LearningLoopsPageComponent() {
  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-3xl font-bold">Learning Loops</h1>
        <p className="text-muted-foreground">
          Every feedback loop this install runs — OS skills, pipeline stages and plugin-declared loops
        </p>
      </div>
      <LearningLoopsView />
    </div>
  );
}

export default LearningLoopsPageComponent;
