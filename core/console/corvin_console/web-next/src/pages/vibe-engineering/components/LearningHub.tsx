import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { TrendingUp, Sparkles, CheckCircle2 } from 'lucide-react';
import type { VibeData } from '../hooks/useVibeData';

interface Props {
  data: VibeData;
}

export function LearningHub({ data }: Props) {
  const talent = data.talent;
  const talentPercentage = Math.round(talent.score);

  return (
    <div className="space-y-4">
      {/* Talent Score */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            Talent Score
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-end gap-4">
            <div className="text-4xl font-bold text-primary">{talentPercentage}%</div>
            <div className="flex-1 space-y-1">
              <div className="text-xs text-muted-foreground">Context</div>
              <div className="w-full bg-secondary rounded-full h-2">
                <div
                  className="bg-blue-500 h-2 rounded-full"
                  style={{ width: `${Math.round(talent.context_relevance * 100)}%` }}
                />
              </div>
              <div className="text-xs text-muted-foreground">Decision Quality</div>
              <div className="w-full bg-secondary rounded-full h-2">
                <div
                  className="bg-green-500 h-2 rounded-full"
                  style={{ width: `${Math.round(talent.decision_quality * 100)}%` }}
                />
              </div>
              <div className="text-xs text-muted-foreground">Outcome Accuracy</div>
              <div className="w-full bg-secondary rounded-full h-2">
                <div
                  className="bg-purple-500 h-2 rounded-full"
                  style={{ width: `${Math.round(talent.outcome_accuracy * 100)}%` }}
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Feedback Events */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Recent Feedback</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="space-y-1 max-h-24 overflow-y-auto">
            <div className="flex items-center justify-between text-xs p-2 bg-secondary/30 rounded">
              <span>User approved decision X</span>
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            </div>
            <div className="flex items-center justify-between text-xs p-2 bg-secondary/30 rounded">
              <span>Skill Y improved to 0.7</span>
              <Badge variant="default" className="text-xs">⭐</Badge>
            </div>
            <div className="flex items-center justify-between text-xs p-2 bg-secondary/30 rounded">
              <span>Entropy detected</span>
              <Badge variant="secondary" className="text-xs">⚠️</Badge>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Active Skills */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            Active Skills
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="space-y-2">
            {[
              { name: 'Decision Quality', grade: 0.85, origin: 'builtin' },
              { name: 'Context Analysis', grade: 0.72, origin: 'skill-forge' },
              { name: 'Risk Assessment', grade: 0.91, origin: 'community' },
            ].map(skill => (
              <div key={skill.name} className="flex items-center justify-between p-2 bg-secondary/20 rounded">
                <div className="flex-1">
                  <p className="text-xs font-medium">{skill.name}</p>
                  <p className="text-[10px] text-muted-foreground">{skill.origin}</p>
                </div>
                <div className="text-right">
                  <div className="text-sm font-bold">
                    {Math.round(skill.grade * 100)}%
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Learning Rate */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Learning Rate</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2">
          <div className="text-center p-2 bg-secondary/30 rounded">
            <p className="text-2xl font-bold">42</p>
            <p className="text-xs text-muted-foreground">Tasks Completed</p>
          </div>
          <div className="text-center p-2 bg-secondary/30 rounded">
            <p className="text-2xl font-bold">0.87</p>
            <p className="text-xs text-muted-foreground">Avg Confidence</p>
          </div>
          <div className="text-center p-2 bg-secondary/30 rounded">
            <p className="text-2xl font-bold">18</p>
            <p className="text-xs text-muted-foreground">Feedback Loops</p>
          </div>
          <div className="text-center p-2 bg-secondary/30 rounded">
            <p className="text-2xl font-bold">94%</p>
            <p className="text-xs text-muted-foreground">Success Rate</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
