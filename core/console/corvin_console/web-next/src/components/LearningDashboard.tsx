/**
 * TreeOfThoughts Learning Dashboard
 * Displays Pattern/Method/Framework tree with confidence scores
 */
import React, { useState, useEffect } from 'react';

interface TreeNode {
  id: string;
  level: 'pattern' | 'method' | 'framework';
  name: string;
  confidence: number;
  children: string[];
  calls_in_production: number;
  operator_notes: Array<[string, string, string]>;
  adr_link?: string;
}

interface ConfidenceGaugeProps {
  value: number;
}

const ConfidenceGauge: React.FC<ConfidenceGaugeProps> = ({ value }) => {
  const color = value < 0.3 ? '#ef4444' : value < 0.7 ? '#f59e0b' : '#10b981';
  const percentage = Math.round(value * 100);
  
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <div style={{
        width: '120px',
        height: '8px',
        backgroundColor: '#e5e7eb',
        borderRadius: '4px',
        overflow: 'hidden'
      }}>
        <div style={{
          width: `${percentage}%`,
          height: '100%',
          backgroundColor: color,
          transition: 'width 0.3s ease'
        }} />
      </div>
      <span style={{ fontSize: '14px', fontWeight: '500' }}>{percentage}%</span>
    </div>
  );
};

interface TreeViewProps {
  node: TreeNode;
  onSelect: (node: TreeNode) => void;
  selectedId?: string;
}

const TreeView: React.FC<TreeViewProps> = ({ node, onSelect, selectedId }) => {
  const [expanded, setExpanded] = useState(false);
  const isSelected = selectedId === node.id;
  
  return (
    <div style={{ marginLeft: '16px' }}>
      <div
        onClick={() => onSelect(node)}
        style={{
          padding: '8px',
          backgroundColor: isSelected ? '#dbeafe' : 'transparent',
          borderLeft: isSelected ? '3px solid #0284c7' : '3px solid transparent',
          cursor: 'pointer',
          borderRadius: '4px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {node.children?.length > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
              style={{ background: 'none', border: 'none', cursor: 'pointer' }}
            >
              {expanded ? '▼' : '▶'}
            </button>
          )}
          <span style={{ fontWeight: '500' }}>{node.name}</span>
          <span style={{ fontSize: '12px', color: '#666' }}>
            [{node.level}]
          </span>
        </div>
        <ConfidenceGauge value={node.confidence} />
      </div>
      
      {expanded && node.children && node.children.map((childId, idx) => (
        <div key={idx}>
          {/* Child nodes would be fetched here in real implementation */}
        </div>
      ))}
    </div>
  );
};

interface DetailPanelProps {
  node: TreeNode | null;
  onGrade: (delta: number) => void;
  onAddNote: (text: string) => void;
}

const DetailPanel: React.FC<DetailPanelProps> = ({ node, onGrade, onAddNote }) => {
  const [noteText, setNoteText] = useState('');
  
  if (!node) {
    return <div style={{ padding: '16px', color: '#999' }}>Select a pattern to view details</div>;
  }
  
  return (
    <div style={{ padding: '16px', borderLeft: '1px solid #e5e7eb' }}>
      <h3>{node.name}</h3>
      
      <div style={{ marginTop: '16px' }}>
        <label style={{ fontSize: '12px', fontWeight: '600', color: '#666' }}>
          Confidence
        </label>
        <ConfidenceGauge value={node.confidence} />
      </div>
      
      <div style={{ marginTop: '16px' }}>
        <label style={{ fontSize: '12px', fontWeight: '600', color: '#666' }}>
          Quick Grade
        </label>
        <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
          <button onClick={() => onGrade(-1.0)} style={{ padding: '4px 12px' }}>
            👎 Failed
          </button>
          <button onClick={() => onGrade(0)} style={{ padding: '4px 12px' }}>
            😐 Neutral
          </button>
          <button onClick={() => onGrade(+1.0)} style={{ padding: '4px 12px' }}>
            👍 Good
          </button>
        </div>
      </div>
      
      <div style={{ marginTop: '16px' }}>
        <label style={{ fontSize: '12px', fontWeight: '600', color: '#666' }}>
          Operator Notes
        </label>
        <textarea
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
          placeholder="Add a note..."
          style={{
            width: '100%',
            minHeight: '60px',
            padding: '8px',
            fontSize: '12px',
            marginTop: '8px'
          }}
        />
        <button
          onClick={() => { onAddNote(noteText); setNoteText(''); }}
          style={{ marginTop: '8px', padding: '4px 12px' }}
        >
          Save Note
        </button>
      </div>
      
      <div style={{ marginTop: '16px', fontSize: '12px', color: '#666' }}>
        <p>Production calls: {node.calls_in_production}</p>
        {node.adr_link && <p>ADR: <a href={`#${node.adr_link}`}>{node.adr_link}</a></p>}
      </div>
    </div>
  );
};

export const LearningDashboard: React.FC = () => {
  const [selectedNode, setSelectedNode] = useState<TreeNode | null>(null);
  const [nodes, setNodes] = useState<TreeNode[]>([]);
  
  useEffect(() => {
    // Fetch nodes from backend (TODO: implement API endpoint)
    const mockRootNode: TreeNode = {
      id: 'framework_voice',
      level: 'framework',
      name: 'Voice Synthesis Framework',
      confidence: 0.78,
      children: ['method_tts_fallback'],
      calls_in_production: 150,
      operator_notes: []
    };
    setNodes([mockRootNode]);
  }, []);
  
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '300px 1fr',
      gap: '16px',
      padding: '16px',
      height: '100%'
    }}>
      <div style={{ borderRight: '1px solid #e5e7eb', paddingRight: '16px' }}>
        <h2>TreeOfThoughts</h2>
        <div style={{ fontSize: '12px', color: '#666', marginBottom: '16px' }}>
          Patterns, Methods, Frameworks
        </div>
        {nodes.map((node) => (
          <TreeView
            key={node.id}
            node={node}
            onSelect={setSelectedNode}
            selectedId={selectedNode?.id}
          />
        ))}
      </div>
      
      <DetailPanel
        node={selectedNode}
        onGrade={(delta) => {
          console.log(`Grade ${selectedNode?.id} with ${delta}`);
          // TODO: wire to backend
        }}
        onAddNote={(text) => {
          console.log(`Add note to ${selectedNode?.id}: ${text}`);
          // TODO: wire to backend
        }}
      />
    </div>
  );
};

export default LearningDashboard;
