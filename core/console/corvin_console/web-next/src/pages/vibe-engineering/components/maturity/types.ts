/**
 * 9D Learning Loops Maturity Score types
 */

export interface LoopScores {
  // Tier 1: Core Loops (6)
  confidence: number;
  routing: number;
  context: number;
  workflow: number;
  data_flow: number;
  security: number;

  // Tier 2: Infrastructure Loops (6)
  memory: number;
  skills: number;
  plugins: number;
  audit: number;
  compliance: number;
  system: number;

  // Tier 3: Meta Loop (1)
  meta_convergence: number;
}

export interface MaturityData {
  overallScore: number;
  status: string;
  tier1Avg: number;
  tier2Avg: number;
  metaScore: number;
  lastUpdated: string;
  trend: {
    direction: 'up' | 'down' | 'stable';
    value: number;
  };
  projection: number;
}

export interface LoopDetail {
  name: string;
  tier: 1 | 2 | 3;
  score: number;
  status: string;
  icon: string;
}

export const LOOP_DETAILS: Record<keyof Omit<LoopScores, 'meta_convergence'>, LoopDetail> = {
  // Tier 1
  confidence: { name: 'Confidence', tier: 1, score: 0, status: '', icon: '⭐' },
  routing: { name: 'Routing', tier: 1, score: 0, status: '', icon: '🛣️' },
  context: { name: 'Context', tier: 1, score: 0, status: '', icon: '📚' },
  workflow: { name: 'Workflow', tier: 1, score: 0, status: '', icon: '⚙️' },
  data_flow: { name: 'Data Flow', tier: 1, score: 0, status: '', icon: '🌊' },
  security: { name: 'Security', tier: 1, score: 0, status: '', icon: '🔒' },

  // Tier 2
  memory: { name: 'Memory', tier: 2, score: 0, status: '', icon: '🧠' },
  skills: { name: 'Skills', tier: 2, score: 0, status: '', icon: '🎯' },
  plugins: { name: 'Plugins', tier: 2, score: 0, status: '', icon: '🔌' },
  audit: { name: 'Audit', tier: 2, score: 0, status: '', icon: '📋' },
  compliance: { name: 'Compliance', tier: 2, score: 0, status: '', icon: '✅' },
  system: { name: 'System', tier: 2, score: 0, status: '', icon: '🖥️' },
};

export function getScoreColor(score: number): string {
  // 0-2: red, 2-4: orange, 4-6: yellow, 6-8: lime, 8-9: cyan, 9-10: purple
  if (score < 2) return 'hsl(0, 100%, 50%)';      // red
  if (score < 4) return 'hsl(30, 100%, 50%)';     // orange
  if (score < 6) return 'hsl(60, 100%, 50%)';     // yellow
  if (score < 8) return 'hsl(120, 100%, 50%)';    // lime
  if (score < 9) return 'hsl(180, 100%, 50%)';    // cyan
  return 'hsl(270, 100%, 50%)';                   // purple
}

export function getScoreLabel(score: number): string {
  if (score < 2) return 'DEAD';
  if (score < 4) return 'NASCENT';
  if (score < 6) return 'LEARNING';
  if (score < 8) return 'MATURE';
  if (score < 9) return 'OPTIMIZED';
  return 'TRAINED';
}
