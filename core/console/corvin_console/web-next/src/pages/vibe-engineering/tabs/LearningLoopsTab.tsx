import { LearningLoopsView } from '@/components/learning-loops-view';

/**
 * Learnings dashboard → "Learning Loops" tab (ADR-0908).
 *
 * Renders the same component the standalone /app/learning-loops panel does,
 * rather than a second implementation reading the same endpoints: two views of
 * one set of loops that can disagree is worse than one view in two places.
 */
export function LearningLoopsTab() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h2 className="text-xl font-semibold">Learning Loops</h2>
        <p className="text-sm text-muted-foreground">
          Feedback loops this install records events for. Also available as its own panel at{' '}
          <code className="rounded bg-muted px-1 py-0.5">/app/learning-loops</code>.
        </p>
      </div>
      <LearningLoopsView />
    </div>
  );
}

export default LearningLoopsTab;
