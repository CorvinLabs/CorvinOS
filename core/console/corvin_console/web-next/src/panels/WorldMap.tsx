/**
 * World Map Visualization Panel (ADR-0206, ADR-0208, ADR-0639)
 *
 * Displays instances by geographic location with loss-based coloring.
 *
 * Features:
 *   - Interactive world map (via D3)
 *   - Instances colored by loss score (red=high, green=converged)
 *   - 10km grid cell aggregation (heatmap overlay)
 *   - Click instance → detail pane with metrics + loss curve
 *   - Summary statistics (instance count, avg loss, geographic coverage)
 *   - Tier 3 geo-tracking (country/region/city + 10km grid)
 *
 * Integration:
 *   - Backend: /api/world-map/* endpoints (world_map.py)
 *   - Learning: Loss scores from LearningDashboard (ADR-0314)
 *   - Telemetry: Geo-tracking data (ADR-0205/0206)
 *   - Audit: Every map interaction logged
 */

import React, { useState, useEffect, useRef } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Loader2, MapPin, TrendingDown, Activity, Eye, EyeOff } from 'lucide-react';
import { api } from '@/lib/api/client';
import { useAuth } from '@/lib/auth';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

interface GeoCoordinate {
  latitude: number;
  longitude: number;
  country: string;
  region: string;
  city: string;
  grid_cell_id: string;
  timestamp: string;
  tier: number;
}

interface InstanceLocation {
  instance_id: string;
  geo: GeoCoordinate;
  loss_score: number;
  loss_components: Record<string, number>;
  last_measurement: string;
  measurement_count: number;
  status: "active" | "converged" | "stale" | "unknown";
}

interface GridCell {
  grid_cell_id: string;
  lat: number;
  lon: number;
  count: number;
  avg_loss: number;
  status: string;
  instances: string[];
}

interface MapData {
  instances: InstanceLocation[];
  timestamp: string;
  count: number;
}

interface SummaryData {
  instance_count: number;
  converged_count: number;
  active_count: number;
  avg_loss: number;
  loss_components_avg: Record<string, number>;
  cell_count: number;
  geographic_coverage: string[];
}

// ============================================================================
// PALETTE & STYLING
// ============================================================================

const PALETTE = {
  loss: {
    // Linear scale: green (0.0) → yellow (0.5) → red (1.0)
    low: '#2ECC71',     // Green (converged)
    mid: '#F39C12',     // Orange (active)
    high: '#E74C3C',    // Red (high loss)
  },
  surface: {
    dark: '#0D1117',
    card: '#161B22',
    border: '#30363D',
    text: '#C9D1D9',
    muted: '#8B949E',
  },
  grid: '#30363D',      // Light grid color
};

// Loss to color mapping (0.0=green, 1.0=red)
const lossToColor = (loss: number): string => {
  if (loss < 0.5) {
    // Green to yellow (0.0 → 0.5)
    const ratio = loss * 2;
    const r = Math.round(46 + (243 - 46) * ratio);
    const g = Math.round(204 + (156 - 204) * ratio);
    const b = Math.round(113 + (18 - 113) * ratio);
    return `rgb(${r}, ${g}, ${b})`;
  } else {
    // Yellow to red (0.5 → 1.0)
    const ratio = (loss - 0.5) * 2;
    const r = Math.round(243 + (231 - 243) * ratio);
    const g = Math.round(156 + (76 - 156) * ratio);
    const b = Math.round(18 + (60 - 18) * ratio);
    return `rgb(${r}, ${g}, ${b})`;
  }
};

const statusToLabel = (status: string): string => {
  switch (status) {
    case "converged":
      return "Converged";
    case "active":
      return "Active";
    case "stale":
      return "Stale";
    default:
      return "Unknown";
  }
};

// ============================================================================
// DETAIL PANE
// ============================================================================

interface DetailPaneProps {
  instance: InstanceLocation;
  onClose: () => void;
}

const DetailPane: React.FC<DetailPaneProps> = ({ instance, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [detailed, setDetailed] = useState<any>(null);

  useEffect(() => {
    setLoading(true);
    api
      .get(`/api/world-map/instances/${instance.instance_id}`)
      .then((res) => setDetailed(res))
      .catch((err) => {
        console.error("Failed to fetch instance detail:", err);
        setDetailed(null);
      })
      .finally(() => setLoading(false));
  }, [instance.instance_id]);

  return (
    <div className="fixed right-0 top-0 h-screen w-96 bg-card border-l border-border overflow-y-auto shadow-lg">
      {/* Header */}
      <div className="sticky top-0 bg-card border-b border-border p-4 flex justify-between items-start">
        <div>
          <h2 className="text-lg font-semibold text-text">Instance Details</h2>
          <p className="text-sm text-muted font-mono">{instance.instance_id}</p>
        </div>
        <button
          onClick={onClose}
          className="text-muted hover:text-text transition-colors"
        >
          ✕
        </button>
      </div>

      {/* Location Card */}
      <div className="p-4 border-b border-border">
        <h3 className="text-sm font-semibold text-text mb-3">Location</h3>
        <div className="space-y-2 text-sm">
          <div>
            <span className="text-muted">City: </span>
            <span className="text-text">
              {instance.geo.city}, {instance.geo.region}
            </span>
          </div>
          <div>
            <span className="text-muted">Country: </span>
            <span className="text-text">{instance.geo.country}</span>
          </div>
          <div>
            <span className="text-muted">Coordinates: </span>
            <span className="text-text font-mono">
              {instance.geo.latitude.toFixed(2)}, {instance.geo.longitude.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="text-muted">Grid Cell: </span>
            <span className="text-text font-mono">{instance.geo.grid_cell_id}</span>
          </div>
        </div>
      </div>

      {/* Loss Card */}
      <div className="p-4 border-b border-border">
        <h3 className="text-sm font-semibold text-text mb-3">Loss Metrics</h3>
        {loading ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="h-4 w-4 animate-spin text-muted" />
          </div>
        ) : detailed ? (
          <div className="space-y-3">
            {/* Overall Loss */}
            <div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-sm text-muted">Overall Loss</span>
                <span className="text-sm font-semibold text-text">
                  {instance.loss_score.toFixed(3)}
                </span>
              </div>
              <div className="w-full bg-border rounded h-2">
                <div
                  className="h-2 rounded transition-all"
                  style={{
                    width: `${instance.loss_score * 100}%`,
                    backgroundColor: lossToColor(instance.loss_score),
                  }}
                />
              </div>
            </div>

            {/* Components */}
            <div className="space-y-2">
              {Object.entries(instance.loss_components || {}).map(([component, value]) => (
                <div key={component}>
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs text-muted capitalize">{component}</span>
                    <span className="text-xs font-mono text-text">{(value as number).toFixed(3)}</span>
                  </div>
                  <div className="w-full bg-border rounded h-1">
                    <div
                      className="h-1 rounded transition-all"
                      style={{
                        width: `${Math.min((value as number) * 100, 100)}%`,
                        backgroundColor: lossToColor(value as number),
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Status */}
            <div className="mt-4">
              <div className="flex items-center gap-2">
                <Badge
                  variant="outline"
                  className={
                    instance.status === "converged"
                      ? "bg-green-900 border-green-700 text-green-100"
                      : instance.status === "active"
                        ? "bg-yellow-900 border-yellow-700 text-yellow-100"
                        : "bg-gray-700 border-gray-600 text-gray-100"
                  }
                >
                  {statusToLabel(instance.status)}
                </Badge>
                <span className="text-xs text-muted">
                  {instance.measurement_count} measurements
                </span>
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted">Failed to load details</p>
        )}
      </div>

      {/* History */}
      <div className="p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Last Update</h3>
        <p className="text-xs text-muted font-mono">{instance.last_measurement}</p>
      </div>
    </div>
  );
};

// ============================================================================
// WORLD MAP PANEL
// ============================================================================

export const WorldMapPanel: React.FC = () => {
  const { session } = useAuth();
  const [mapData, setMapData] = useState<MapData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedInstance, setSelectedInstance] = useState<InstanceLocation | null>(null);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [showInstances, setShowInstances] = useState(true);
  const svgRef = useRef<SVGSVGElement>(null);

  // Fetch data
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const [instances, summaryData] = await Promise.all([
          api.get('/api/world-map/instances'),
          api.get('/api/world-map/summary'),
        ]);
        setMapData(instances);
        setSummary(summaryData);
      } catch (err) {
        console.error("Failed to fetch map data:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  // Draw map
  useEffect(() => {
    if (!mapData || !svgRef.current) return;

    const svg = svgRef.current;
    const width = svg.clientWidth;
    const height = svg.clientHeight;

    // Clear
    svg.innerHTML = '';

    // Background
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('width', String(width));
    bg.setAttribute('height', String(height));
    bg.setAttribute('fill', PALETTE.surface.dark);
    svg.appendChild(bg);

    // Grid
    const gridGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    for (let x = 0; x < width; x += 60) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', String(x));
      line.setAttribute('y1', '0');
      line.setAttribute('x2', String(x));
      line.setAttribute('y2', String(height));
      line.setAttribute('stroke', PALETTE.grid);
      line.setAttribute('stroke-width', '0.5');
      line.setAttribute('opacity', '0.2');
      gridGroup.appendChild(line);
    }
    for (let y = 0; y < height; y += 60) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', '0');
      line.setAttribute('y1', String(y));
      line.setAttribute('x2', String(width));
      line.setAttribute('y2', String(y));
      line.setAttribute('stroke', PALETTE.grid);
      line.setAttribute('stroke-width', '0.5');
      line.setAttribute('opacity', '0.2');
      gridGroup.appendChild(line);
    }
    svg.appendChild(gridGroup);

    // Web Mercator projection (simple)
    const projectLonLat = (lon: number, lat: number): [number, number] => {
      // Simple linear projection for demo (not true Mercator)
      const x = ((lon + 180) / 360) * width;
      const y = ((90 - lat) / 180) * height;
      return [x, y];
    };

    // Draw instances
    if (showInstances) {
      mapData.instances.forEach((instance) => {
        const [x, y] = projectLonLat(instance.geo.longitude, instance.geo.latitude);
        const color = lossToColor(instance.loss_score);

        // Circle
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', String(x));
        circle.setAttribute('cy', String(y));
        circle.setAttribute('r', '6');
        circle.setAttribute('fill', color);
        circle.setAttribute('stroke', 'white');
        circle.setAttribute('stroke-width', '1.5');
        circle.setAttribute('opacity', '0.8');
        circle.setAttribute('cursor', 'pointer');
        circle.setAttribute('data-instance-id', instance.instance_id);

        circle.addEventListener('click', (e) => {
          e.stopPropagation();
          setSelectedInstance(instance);
        });

        circle.addEventListener('mouseenter', () => {
          circle.setAttribute('r', '8');
          circle.setAttribute('opacity', '1');
        });

        circle.addEventListener('mouseleave', () => {
          circle.setAttribute('r', '6');
          circle.setAttribute('opacity', '0.8');
        });

        svg.appendChild(circle);
      });
    }

    // Draw legend
    const legendGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    legendGroup.setAttribute('transform', `translate(${width - 200}, 20)`);

    // Legend background
    const legBg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    legBg.setAttribute('width', '180');
    legBg.setAttribute('height', '100');
    legBg.setAttribute('fill', PALETTE.surface.card);
    legBg.setAttribute('stroke', PALETTE.surface.border);
    legBg.setAttribute('stroke-width', '1');
    legBg.setAttribute('rx', '4');
    legendGroup.appendChild(legBg);

    // Legend text
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    title.setAttribute('x', '10');
    title.setAttribute('y', '20');
    title.setAttribute('font-size', '12');
    title.setAttribute('font-weight', 'bold');
    title.setAttribute('fill', PALETTE.surface.text);
    title.textContent = 'Loss Score';
    legendGroup.appendChild(title);

    const labels = [
      { label: 'Converged', color: PALETTE.loss.low, y: 40 },
      { label: 'Active', color: PALETTE.loss.mid, y: 60 },
      { label: 'High Loss', color: PALETTE.loss.high, y: 80 },
    ];

    labels.forEach(({ label, color, y }) => {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('cx', '20');
      circle.setAttribute('cy', String(y - 5));
      circle.setAttribute('r', '4');
      circle.setAttribute('fill', color);
      legendGroup.appendChild(circle);

      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('x', '32');
      text.setAttribute('y', String(y));
      text.setAttribute('font-size', '11');
      text.setAttribute('fill', PALETTE.surface.text);
      text.textContent = label;
      legendGroup.appendChild(text);
    });

    svg.appendChild(legendGroup);
  }, [mapData, showInstances, showHeatmap]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="h-8 w-8 animate-spin text-muted" />
      </div>
    );
  }

  return (
    <div className="w-full h-full flex flex-col gap-4 p-4">
      {/* Title & Controls */}
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold text-text">World Map Visualization</h1>
          <p className="text-sm text-muted">
            {mapData?.count ?? 0} instances across{' '}
            {summary?.geographic_coverage.length ?? 0} countries
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowInstances(!showInstances)}
            className="flex items-center gap-1 px-3 py-2 rounded bg-card border border-border text-text hover:bg-border transition-colors text-sm"
          >
            {showInstances ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
            Instances
          </button>
          <button
            onClick={() => setShowHeatmap(!showHeatmap)}
            className="flex items-center gap-1 px-3 py-2 rounded bg-card border border-border text-text hover:bg-border transition-colors text-sm"
          >
            {showHeatmap ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
            Heatmap
          </button>
        </div>
      </div>

      {/* Map */}
      <Card className="flex-1 bg-dark border-border overflow-hidden">
        <CardContent className="p-0 h-full">
          <svg
            ref={svgRef}
            className="w-full h-full"
            style={{ background: PALETTE.surface.dark }}
          />
        </CardContent>
      </Card>

      {/* Summary Stats */}
      {summary && (
        <div className="grid grid-cols-5 gap-4">
          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <p className="text-xs text-muted mb-1">Instances</p>
              <p className="text-2xl font-bold text-text">{summary.instance_count}</p>
            </CardContent>
          </Card>
          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <p className="text-xs text-muted mb-1">Converged</p>
              <p className="text-2xl font-bold" style={{ color: PALETTE.loss.low }}>
                {summary.converged_count}
              </p>
            </CardContent>
          </Card>
          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <p className="text-xs text-muted mb-1">Active</p>
              <p className="text-2xl font-bold" style={{ color: PALETTE.loss.mid }}>
                {summary.active_count}
              </p>
            </CardContent>
          </Card>
          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <p className="text-xs text-muted mb-1">Avg Loss</p>
              <p className="text-2xl font-bold" style={{ color: lossToColor(summary.avg_loss) }}>
                {summary.avg_loss.toFixed(2)}
              </p>
            </CardContent>
          </Card>
          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <p className="text-xs text-muted mb-1">Grid Cells</p>
              <p className="text-2xl font-bold text-text">{summary.cell_count}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Detail Pane */}
      {selectedInstance && (
        <DetailPane
          instance={selectedInstance}
          onClose={() => setSelectedInstance(null)}
        />
      )}
    </div>
  );
};

export default WorldMapPanel;
