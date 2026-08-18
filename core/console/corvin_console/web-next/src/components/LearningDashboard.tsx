/**
 * TreeOfThoughts Learning Dashboard (Weg A — self-earned confidence).
 *
 * The tree is NOT hand-built any more. It renders the nodes fetched from
 * GET /v1/console/learning/nodes, which projects the CEL stage-grade store — confidence
 * CorvinOS EARNS ITSELF from real turns via the outcome-feedback loop (G4), refined by
 * operator overrides (G3). So the operator's job here is oversight + the occasional
 * correction, not data entry. A grade is an OPERATOR OVERRIDE that writes straight to the
 * CEL store (POST /vibe-engineering/grades/{stage}), so it lands in the same place the
 * confidence is computed from and shows up on the next refresh.
 */
import React, { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface Evidence { auto_earned: number; operator: number }
interface TreeNode {
  id: string;
  level: 'pattern' | 'method' | 'framework';
  name: string;
  confidence: number;
  children: string[];
  calls_in_production: number;
  operator_notes: Array<[string, string, string]>;
  adr_link?: string;
  when?: string[];
  evidence?: Evidence;
}

const ConfidenceGauge: React.FC<{ value: number }> = ({ value }) => {
  const color = value < 0.3 ? '#ef4444' : value < 0.7 ? '#f59e0b' : '#10b981';
  const percentage = Math.round(value * 100);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <div style={{ width: '120px', height: '8px', background: 'rgba(127,127,127,0.25)', borderRadius: '4px', overflow: 'hidden' }}>
        <div style={{ width: `${percentage}%`, height: '100%', backgroundColor: color, transition: 'width 0.3s ease' }} />
      </div>
      <span style={{ fontSize: '14px', fontWeight: 500 }}>{percentage}%</span>
    </div>
  );
};

const TreeView: React.FC<{ node: TreeNode; onSelect: (n: TreeNode) => void; selectedId?: string }> = ({ node, onSelect, selectedId }) => {
  const isSelected = selectedId === node.id;
  return (
    <div
      onClick={() => onSelect(node)}
      style={{
        padding: '8px', marginLeft: node.level === 'framework' ? 0 : 16,
        background: isSelected ? 'rgba(2,132,199,0.15)' : 'transparent',
        borderLeft: isSelected ? '3px solid #0284c7' : '3px solid transparent',
        cursor: 'pointer', borderRadius: 4,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span style={{ fontWeight: 500 }}>{node.name}</span>
        <span style={{ fontSize: 12, opacity: 0.6 }}>[{node.level}]</span>
      </div>
      <ConfidenceGauge value={node.confidence} />
    </div>
  );
};

const btnStyle: React.CSSProperties = {
  padding: '4px 12px', border: '1px solid rgba(127,127,127,0.4)', background: 'transparent',
  color: 'inherit', borderRadius: 6, cursor: 'pointer', fontSize: 16,
};

const DetailPanel: React.FC<{ node: TreeNode | null; onGrade: (score: number) => void; busy: boolean; error: string | null }> = ({ node, onGrade, busy, error }) => {
  if (!node) return <div style={{ padding: 16, opacity: 0.6 }}>Wähle links einen Knoten, um Details zu sehen.</div>;
  const isStage = node.id.startsWith('stage-');
  const ev = node.evidence;
  return (
    <div style={{ padding: 16, borderLeft: '1px solid rgba(127,127,127,0.25)' }}>
      <h3 style={{ margin: 0 }}>{node.name}</h3>
      {node.when?.[0] && <p style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>{node.when[0]}</p>}

      <div style={{ marginTop: 16 }}>
        <label style={{ fontSize: 12, fontWeight: 600, opacity: 0.7 }}>Verdiente Confidence</label>
        <ConfidenceGauge value={node.confidence} />
      </div>

      <div style={{ marginTop: 16, fontSize: 12, opacity: 0.8 }}>
        <p style={{ margin: '2px 0' }}>Bewertungen gesamt: {node.calls_in_production}</p>
        {ev && <p style={{ margin: '2px 0' }}>↳ automatisch (System: Loop + Seed): {ev.auto_earned} · Operator: {ev.operator}</p>}
        {node.adr_link && <p style={{ margin: '2px 0' }}>ADR: {node.adr_link}</p>}
      </div>

      {isStage ? (
        <div style={{ marginTop: 16 }}>
          <label style={{ fontSize: 12, fontWeight: 600, opacity: 0.7 }}>Operator-Override</label>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
            <button disabled={busy} aria-label="Schlecht bewerten" onClick={() => onGrade(0.0)} style={{ ...btnStyle, cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}>👎</button>
            <button disabled={busy} aria-label="Neutral bewerten" onClick={() => onGrade(0.5)} style={{ ...btnStyle, cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}>😐</button>
            <button disabled={busy} aria-label="Gut bewerten" onClick={() => onGrade(1.0)} style={{ ...btnStyle, cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}>👍</button>
            {busy && <span style={{ fontSize: 12, opacity: 0.7 }}>speichere…</span>}
          </div>
          {error && <p style={{ fontSize: 12, color: '#ef4444', marginTop: 8 }}>{error}</p>}
          <p style={{ fontSize: 11, opacity: 0.6, marginTop: 8 }}>
            Nur nötig als Korrektur — die Confidence verdient sich sonst automatisch aus echten Turns.
          </p>
        </div>
      ) : (
        <p style={{ fontSize: 11, opacity: 0.6, marginTop: 16 }}>
          Aggregat-Knoten — bewerte die einzelnen Stages darunter.
        </p>
      )}
    </div>
  );
};

export const LearningDashboard: React.FC<{ nodes?: any[] }> = ({ nodes = [] }) => {
  // Store the selected ID, not the node object: after an override the query refetches
  // and `nodes` changes, so deriving the node from the fresh list keeps the detail panel
  // in sync instead of showing pre-grade numbers (review M2).
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const tree = nodes as TreeNode[];
  const selectedNode = tree.find((n) => n.id === selectedId) ?? null;

  const grade = async (score: number) => {
    if (!selectedNode || !selectedNode.id.startsWith('stage-')) return;
    const stage = selectedNode.id.replace(/^stage-/, '');
    setBusy(true);
    setError(null);
    try {
      const whoRes = await fetch('/v1/console/auth/whoami', { credentials: 'include' });
      if (!whoRes.ok) throw new Error('Sitzung abgelaufen — bitte neu laden.');
      const who = await whoRes.json();
      const r = await fetch(`/v1/console/vibe-engineering/grades/${encodeURIComponent(stage)}`, {
        method: 'POST', credentials: 'include',
        headers: { 'content-type': 'application/json', 'x-csrf-token': who.csrf_token || '' },
        body: JSON.stringify({ score, notes: 'operator override (learning dashboard)' }),
      });
      if (!r.ok) throw new Error(`Bewertung fehlgeschlagen (HTTP ${r.status}).`);
      await qc.invalidateQueries({ queryKey: ['learning-nodes'] });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bewertung fehlgeschlagen.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>TreeOfThoughts — verdientes Vertrauen</h2>
        <p style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
          Die Confidence verdient sich CorvinOS selbst aus echten Turns (Outcome-Loop). Du greifst nur
          korrigierend ein.
        </p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 16 }}>
        <div style={{ borderRight: '1px solid rgba(127,127,127,0.25)', paddingRight: 16 }}>
          {tree.length === 0 && <div style={{ fontSize: 12, opacity: 0.6 }}>Noch keine Daten.</div>}
          {tree.map((node) => (
            <TreeView key={node.id} node={node} onSelect={(n) => { setSelectedId(n.id); setError(null); }} selectedId={selectedNode?.id} />
          ))}
        </div>
        <DetailPanel node={selectedNode} onGrade={grade} busy={busy} error={error} />
      </div>
    </div>
  );
};

export default LearningDashboard;
