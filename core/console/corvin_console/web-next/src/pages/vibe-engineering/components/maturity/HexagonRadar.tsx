/**
 * Hexagon Radar Chart — 9D Learning Loops Visualization
 *
 * Displays:
 * - Outer hexagon: Tier 1 loops (6 vertices)
 * - Inner hexagon: Tier 2 loops (6 vertices)
 * - Center: Meta loop score + overall status
 *
 * Colors: score-based gradient
 */

import React, { useMemo } from 'react';
import { LoopScores, getScoreColor } from './types';

interface HexagonRadarProps {
  loopScores: LoopScores;
}

// Tier 1 loop names (outer hexagon, 6 vertices)
const TIER1_LOOPS = [
  { key: 'confidence', label: 'Confidence' },
  { key: 'routing', label: 'Routing' },
  { key: 'context', label: 'Context' },
  { key: 'workflow', label: 'Workflow' },
  { key: 'data_flow', label: 'Data Flow' },
  { key: 'security', label: 'Security' },
] as const;

// Tier 2 loop names (inner hexagon, 6 vertices)
const TIER2_LOOPS = [
  { key: 'memory', label: 'Memory' },
  { key: 'skills', label: 'Skills' },
  { key: 'plugins', label: 'Plugins' },
  { key: 'audit', label: 'Audit' },
  { key: 'compliance', label: 'Compliance' },
  { key: 'system', label: 'System' },
] as const;

const CANVAS_SIZE = 500;
const CENTER_X = CANVAS_SIZE / 2;
const CENTER_Y = CANVAS_SIZE / 2;
const OUTER_RADIUS = 150;
const INNER_RADIUS = 100;
const RING_STEP = 30;

export function HexagonRadar({ loopScores }: HexagonRadarProps) {
  const data = useMemo(() => ({
    tier1: TIER1_LOOPS.map((loop) => ({
      label: loop.label,
      value: loopScores[loop.key as keyof LoopScores],
    })),
    tier2: TIER2_LOOPS.map((loop) => ({
      label: loop.label,
      value: loopScores[loop.key as keyof LoopScores],
    })),
    meta: loopScores.meta_convergence,
  }), [loopScores]);

  // Calculate polygon points (hexagon vertices at angles 0°, 60°, 120°, 180°, 240°, 300°)
  const getPolygonPoints = (radius: number, numVertices: number = 6): [number, number][] => {
    const points: [number, number][] = [];
    for (let i = 0; i < numVertices; i++) {
      const angle = (i * 360) / numVertices - 90; // Start at top
      const rad = (angle * Math.PI) / 180;
      const x = CENTER_X + radius * Math.cos(rad);
      const y = CENTER_Y + radius * Math.sin(rad);
      points.push([x, y]);
    }
    return points;
  };

  const getVertexPosition = (index: number, radius: number): [number, number] => {
    const angle = (index * 360) / 6 - 90;
    const rad = (angle * Math.PI) / 180;
    return [
      CENTER_X + radius * Math.cos(rad),
      CENTER_Y + radius * Math.sin(rad),
    ];
  };

  const getFilledPolygonPoints = (scores: Array<{ label: string; value: number }>, radius: number): string => {
    const points: [number, number][] = [];
    for (let i = 0; i < scores.length; i++) {
      const angle = (i * 360) / scores.length - 90;
      const rad = (angle * Math.PI) / 180;
      // Fill height is proportional to score (0-10)
      const fillRadius = (scores[i].value / 10) * radius;
      const x = CENTER_X + fillRadius * Math.cos(rad);
      const y = CENTER_Y + fillRadius * Math.sin(rad);
      points.push([x, y]);
    }
    return points.map((p) => `${p[0]},${p[1]}`).join(' ');
  };

  const outerHexPoints = getPolygonPoints(OUTER_RADIUS);
  const innerHexPoints = getPolygonPoints(INNER_RADIUS);
  const ringPoints = [
    getPolygonPoints(RING_STEP),
    getPolygonPoints(RING_STEP * 2),
    getPolygonPoints(RING_STEP * 3),
    getPolygonPoints(RING_STEP * 4),
    getPolygonPoints(RING_STEP * 5),
  ];

  const tier1FillPoints = getFilledPolygonPoints(data.tier1, OUTER_RADIUS);
  const tier2FillPoints = getFilledPolygonPoints(data.tier2, INNER_RADIUS);

  return (
    <div className="flex flex-col items-center gap-6">
      {/* SVG Radar Chart */}
      <div className="flex justify-center">
        <svg width={CANVAS_SIZE} height={CANVAS_SIZE} viewBox={`0 0 ${CANVAS_SIZE} ${CANVAS_SIZE}`}>
          {/* Background grid rings */}
          <g stroke="#30363D" strokeWidth="1" fill="none" opacity="0.5">
            {ringPoints.map((ring, i) => (
              <polygon
                key={`ring-${i}`}
                points={ring.map((p) => `${p[0]},${p[1]}`).join(' ')}
              />
            ))}
          </g>

          {/* Tier 2 (inner) filled area */}
          <polygon
            points={tier2FillPoints}
            fill="rgba(126, 208, 212, 0.2)"
            stroke="rgba(126, 208, 212, 0.4)"
            strokeWidth="2"
          />

          {/* Tier 1 (outer) filled area */}
          <polygon
            points={tier1FillPoints}
            fill="rgba(129, 212, 250, 0.15)"
            stroke="rgba(129, 212, 250, 0.3)"
            strokeWidth="2"
          />

          {/* Hexagon outlines */}
          <polygon
            points={outerHexPoints.map((p) => `${p[0]},${p[1]}`).join(' ')}
            fill="none"
            stroke="#30363D"
            strokeWidth="2"
            opacity="0.5"
          />

          <polygon
            points={innerHexPoints.map((p) => `${p[0]},${p[1]}`).join(' ')}
            fill="none"
            stroke="#30363D"
            strokeWidth="1"
            opacity="0.3"
          />

          {/* Tier 1 vertices + labels */}
          {data.tier1.map((loop, i) => {
            const [x, y] = getVertexPosition(i, OUTER_RADIUS);
            const color = getScoreColor(loop.value);
            return (
              <g key={`tier1-${i}`}>
                {/* Vertex point */}
                <circle cx={x} cy={y} r="6" fill={color} opacity="0.8" />

                {/* Label background */}
                <g>
                  <rect
                    x={x - 40}
                    y={y - 30}
                    width="80"
                    height="24"
                    rx="4"
                    fill="#0D1117"
                    stroke="#30363D"
                    strokeWidth="1"
                  />
                  <text
                    x={x}
                    y={y - 8}
                    textAnchor="middle"
                    fontSize="11"
                    fontWeight="600"
                    fill="#C9D1D9"
                  >
                    {loop.value.toFixed(1)}
                  </text>
                </g>
              </g>
            );
          })}

          {/* Tier 2 vertices + labels */}
          {data.tier2.map((loop, i) => {
            const [x, y] = getVertexPosition(i, INNER_RADIUS * 0.7);
            const color = getScoreColor(loop.value);
            return (
              <g key={`tier2-${i}`}>
                <circle cx={x} cy={y} r="4" fill={color} opacity="0.6" />
              </g>
            );
          })}

          {/* Center: Meta loop score + overall score */}
          <circle
            cx={CENTER_X}
            cy={CENTER_Y}
            r="40"
            fill="#0D1117"
            stroke="#30363D"
            strokeWidth="2"
          />

          <text
            x={CENTER_X}
            y={CENTER_Y - 8}
            textAnchor="middle"
            fontSize="20"
            fontWeight="700"
            fill="#58A6FF"
          >
            {(data.meta / 10 * 100).toFixed(0)}%
          </text>

          <text
            x={CENTER_X}
            y={CENTER_Y + 12}
            textAnchor="middle"
            fontSize="10"
            fill="#8B949E"
          >
            Meta: {data.meta.toFixed(1)}
          </text>
        </svg>
      </div>

      {/* Legend */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 text-xs w-full">
        {/* Tier 1 */}
        <div className="space-y-2">
          <div className="font-semibold text-[#8B949E] uppercase">Tier 1: Core</div>
          {data.tier1.map((loop, i) => (
            <div key={`legend1-${i}`} className="flex items-center gap-2">
              <div
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: getScoreColor(loop.value) }}
              />
              <span className="text-[#C9D1D9]">
                {loop.label}: <span className="font-semibold">{loop.value.toFixed(1)}</span>
              </span>
            </div>
          ))}
        </div>

        {/* Tier 2 */}
        <div className="space-y-2">
          <div className="font-semibold text-[#8B949E] uppercase">Tier 2: Infra</div>
          {data.tier2.map((loop, i) => (
            <div key={`legend2-${i}`} className="flex items-center gap-2">
              <div
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: getScoreColor(loop.value) }}
              />
              <span className="text-[#C9D1D9]">
                {loop.label}: <span className="font-semibold">{loop.value.toFixed(1)}</span>
              </span>
            </div>
          ))}
        </div>

        {/* Color scale */}
        <div className="space-y-2">
          <div className="font-semibold text-[#8B949E] uppercase">Score Scale</div>
          <div className="space-y-1 text-xs">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded" style={{ backgroundColor: 'hsl(0, 100%, 50%)' }} />
              <span>0-2: Dead</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded" style={{ backgroundColor: 'hsl(30, 100%, 50%)' }} />
              <span>2-4: Nascent</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded" style={{ backgroundColor: 'hsl(60, 100%, 50%)' }} />
              <span>4-6: Learning</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded" style={{ backgroundColor: 'hsl(120, 100%, 50%)' }} />
              <span>6-8: Mature</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded" style={{ backgroundColor: 'hsl(180, 100%, 50%)' }} />
              <span>8-9: Optimized</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded" style={{ backgroundColor: 'hsl(270, 100%, 50%)' }} />
              <span>9-10: Trained</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
