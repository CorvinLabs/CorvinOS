/**
 * Knowledge Graph Visualization Panel
 * Interactive, navigable graph visualization with zoom, pan, and entity details
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Network } from 'vis-network';
import 'vis-network/styles/vis-network.min.css';

interface Entity {
  id: string;
  type: string;
  title: string;
  status: string;
  tags: string[];
}

interface Relation {
  from_id: string;
  to_id: string;
  relation: string;
}

interface GraphData {
  entities: Entity[];
  relations: Relation[];
}

interface GraphVisualizationProps {
  data: GraphData;
  onEntitySelect?: (entity: Entity) => void;
  onNavigate?: (entityId: string) => void;
}

export const GraphVisualization: React.FC<GraphVisualizationProps> = ({
  data,
  onEntitySelect,
  onNavigate
}) => {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const networkRef = React.useRef<Network | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [hoveredEntity, setHoveredEntity] = useState<string | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(false);

  // Detect dark mode from system or Corvin settings
  useEffect(() => {
    const darkModeQuery = window.matchMedia('(prefers-color-scheme: dark)');
    setIsDarkMode(darkModeQuery.matches);
    darkModeQuery.addEventListener('change', (e) => setIsDarkMode(e.matches));
  }, []);

  // Enhanced color scheme with dark mode support
  const getNodeColor = (type: string, status: string) => {
    const lightColors: Record<string, Record<string, string>> = {
      decision: {
        proposed: '#FF9800', // Amber
        accepted: '#4CAF50', // Green
        verified: '#2196F3', // Blue
        superseded: '#9E9E9E', // Gray
      },
      concept: {
        proposed: '#FF7043', // Deep Orange
        accepted: '#66BB6A', // Light Green
        verified: '#42A5F5', // Light Blue
        superseded: '#BDBDBD',
      },
      idea: {
        proposed: '#FFB74D', // Light Orange
        accepted: '#81C784', // Green
        verified: '#64B5F6', // Blue
        superseded: '#E0E0E0',
      },
      implementation: {
        proposed: '#FDD835', // Amber
        accepted: '#A5D6A7', // Light Green
        verified: '#90CAF9', // Light Blue
        superseded: '#F5F5F5',
      },
    };

    const darkColors: Record<string, Record<string, string>> = {
      decision: {
        proposed: '#FFB74D', // Brighter orange for dark
        accepted: '#66BB6A', // Bright green
        verified: '#42A5F5', // Bright blue
        superseded: '#757575',
      },
      concept: {
        proposed: '#FF8A65',
        accepted: '#81C784',
        verified: '#42A5F5',
        superseded: '#9E9E9E',
      },
      idea: {
        proposed: '#FFD54F',
        accepted: '#A5D6A7',
        verified: '#90CAF9',
        superseded: '#BDBDBD',
      },
      implementation: {
        proposed: '#FFE082',
        accepted: '#C8E6C9',
        verified: '#BBDEFB',
        superseded: '#E0E0E0',
      },
    };

    const colors = isDarkMode ? darkColors : lightColors;
    return colors[type]?.[status] || '#808080';
  };

  // Build vis-network compatible data
  const networkData = useMemo(() => {
    const nodes = data.entities.map((entity) => ({
      id: entity.id,
      label: entity.title.substring(0, 30), // Truncate long titles
      color: getNodeColor(entity.type, entity.status),
      borderWidth: hoveredEntity === entity.id ? 4 : 2,
      font: {
        size: entity.type === 'decision' ? 16 : 14,
        color: '#000000',
      },
      title: `${entity.type.toUpperCase()}\n${entity.title}\nStatus: ${entity.status}`,
      shape: entity.type === 'decision' ? 'box' : 'circle',
      physics: true,
    }));

    const edges = data.relations.map((rel) => ({
      from: rel.from_id,
      to: rel.to_id,
      label: rel.relation,
      arrows: 'to',
      color: { color: '#CCCCCC', highlight: '#FF6B6B' },
      smooth: {
        type: 'continuous',
      },
      font: {
        size: 12,
        align: 'middle',
      },
    }));

    return { nodes, edges };
  }, [data.entities, data.relations, hoveredEntity]);

  // Initialize network visualization
  useEffect(() => {
    if (!containerRef.current || !data.entities.length) return;

    const options = {
      physics: {
        enabled: true,
        forceAtlas2Based: {
          gravitationalConstant: -26,
          centralGravity: 0.005,
          springLength: 200,
          springConstant: 0.08,
        },
        maxVelocity: 50,
        solver: 'forceAtlas2Based',
        timestep: 0.35,
        stabilization: { iterations: 150 },
      },
      interaction: {
        hover: true,
        navigationButtons: true,
        keyboard: true,
      },
      nodes: {
        font: {
          size: 16,
          face: 'Tahoma',
        },
      },
      edges: {
        arrows: {
          to: {
            enabled: true,
            scaleFactor: 0.5,
          },
        },
        smooth: {
          type: 'continuous',
        },
      },
      layout: {
        randomSeed: 42,
      },
    };

    try {
      networkRef.current = new Network(
        containerRef.current,
        networkData,
        options
      );

      // Click handler for entity selection
      networkRef.current.on('click', (params) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const entity = data.entities.find((e) => e.id === nodeId);
          if (entity) {
            setSelectedEntity(entity);
            onEntitySelect?.(entity);
          }
        }
      });

      // Hover handler
      networkRef.current.on('hoverNode', (params) => {
        setHoveredEntity(params.node);
      });

      networkRef.current.on('blurNode', () => {
        setHoveredEntity(null);
      });

      // Double-click to focus
      networkRef.current.on('doubleClick', (params) => {
        if (params.nodes.length > 0) {
          networkRef.current?.focus(params.nodes[0], {
            scale: 2,
            animation: {
              duration: 1000,
              easingFunction: 'easeInOutQuad',
            },
          });
          onNavigate?.(params.nodes[0]);
        }
      });
    } catch (error) {
      console.error('Failed to initialize network:', error);
    }

    return () => {
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
      }
    };
  }, [networkData, data.entities, onEntitySelect, onNavigate]);

  return (
    <div style={{ display: 'flex', gap: '20px', height: '100%' }}>
      {/* Graph visualization */}
      <div
        ref={containerRef}
        style={{
          flex: 1,
          border: '1px solid #e0e0e0',
          borderRadius: '8px',
          backgroundColor: '#f9f9f9',
          minHeight: '600px',
        }}
      />

      {/* Entity details panel */}
      <div
        style={{
          width: '300px',
          borderLeft: '1px solid #e0e0e0',
          paddingLeft: '20px',
          overflowY: 'auto',
        }}
      >
        {selectedEntity ? (
          <EntityDetailsPanel entity={selectedEntity} />
        ) : (
          <div style={{ color: '#999', textAlign: 'center', marginTop: '40px' }}>
            <p>Click an entity to view details</p>
            <p style={{ fontSize: '12px' }}>
              Double-click to focus on the graph
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

// Entity Details Side Panel
interface EntityDetailsPanelProps {
  entity: Entity;
}

const EntityDetailsPanel: React.FC<EntityDetailsPanelProps> = ({ entity }) => {
  const statusColors: Record<string, string> = {
    proposed: '#FFA500',
    accepted: '#4CAF50',
    verified: '#2196F3',
    superseded: '#9E9E9E',
  };

  return (
    <div style={{ padding: '0 0 20px 0' }}>
      <div
        style={{
          backgroundColor: statusColors[entity.status] || '#808080',
          color: 'white',
          padding: '12px',
          borderRadius: '6px',
          marginBottom: '16px',
        }}
      >
        <h3 style={{ margin: '0 0 8px 0', fontSize: '14px' }}>
          {entity.type.toUpperCase()}
        </h3>
        <div style={{ fontSize: '12px' }}>Status: {entity.status}</div>
      </div>

      <h4 style={{ marginTop: '0', marginBottom: '8px' }}>{entity.title}</h4>

      {entity.tags.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <p style={{ fontSize: '12px', color: '#666', marginBottom: '6px' }}>
            Tags
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {entity.tags.map((tag) => (
              <span
                key={tag}
                style={{
                  backgroundColor: '#e3f2fd',
                  color: '#1976d2',
                  padding: '4px 8px',
                  borderRadius: '4px',
                  fontSize: '12px',
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      <div style={{ fontSize: '12px', color: '#999' }}>
        <p>ID: {entity.id}</p>
        <p>Type: {entity.type}</p>
      </div>
    </div>
  );
};

export default GraphVisualization;
