import { LearningLoopsView } from '@/components/learning-loops-view';

/**
 * Learnings dashboard → "Learning Loops" tab (ADR-0908).
 *
 * The only Learning Loops surface. A standalone /app/learning-loops panel
 * existed for a few hours on 2026-09-21 and was retired the same day: it
 * mounted this very component, so it was a second door to one room. That path
 * now redirects here (App.tsx → ?tab=loops).
 */
export function LearningLoopsTab() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h2 className="text-xl font-semibold">Learning Loops</h2>
        <p className="text-sm text-muted-foreground">
          Every feedback loop this install runs — OS skills, context-pipeline stages and
          plugin-declared loops
        </p>
      </div>
      <LearningLoopsView />
    </div>
  );
}

export default LearningLoopsTab;
