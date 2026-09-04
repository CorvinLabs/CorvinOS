/**
 * AUDIT_GRAPH_LAYOUT must name a layout cytoscape actually registers.
 *
 * 2026-09-04: the Graph View of /app/vibe-engineering rendered nothing on the
 * live console. The layout was named `breadthFirstSearch`; cytoscape's built-in
 * BFS layout is `breadthfirst`, and cytoscape() throws "No such layout ...
 * found" at construction — inside the mount effect, so React tore the graph
 * down before a single node was drawn. Every existing test mocked the
 * component or ran under jsdom (no canvas), so nothing caught it.
 *
 * Cytoscape runs headless in node, so this test drives the REAL library with
 * the REAL layout object: a typo in the name fails here, not in the browser.
 */
import { describe, it, expect } from 'vitest';
import cytoscape from 'cytoscape';
import { AUDIT_GRAPH_LAYOUT } from '@/pages/vibe-engineering/components/AuditChainGraph';

describe('AUDIT_GRAPH_LAYOUT', () => {
  it('is a layout cytoscape can construct and run', () => {
    const cy = cytoscape({
      headless: true,
      elements: [
        { data: { id: 'e0' } },
        { data: { id: 'e1' } },
        { data: { id: 'e2' } },
        { data: { id: 'e0_to_e1', source: 'e0', target: 'e1' } },
        { data: { id: 'e1_to_e2', source: 'e1', target: 'e2' } },
      ],
    });
    expect(() => cy.layout({ ...AUDIT_GRAPH_LAYOUT, roots: '#e2' } as cytoscape.LayoutOptions).run()).not.toThrow();
    // A layout that ran assigns distinct positions; the fallback pile is all (0,0).
    const ys = cy.nodes().map((n) => n.position('y'));
    expect(new Set(ys).size).toBeGreaterThan(1);
    cy.destroy();
  });

  it('rejects the name that shipped broken (guards the regression, not the fix)', () => {
    const cy = cytoscape({ headless: true, elements: [{ data: { id: 'a' } }] });
    expect(() => cy.layout({ name: 'breadthFirstSearch' } as unknown as cytoscape.LayoutOptions).run()).toThrow(/No such layout/);
    cy.destroy();
  });
});
