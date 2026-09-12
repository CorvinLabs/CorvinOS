import React, { useState, useMemo } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AlertTriangle, Network } from 'lucide-react';
import { ForgeTool, ForgeSkill, ForgeOSSkill, ForgeDependency } from '@/types/forge';

interface GraphTabProps {
  tools: ForgeTool[];
  skills: ForgeSkill[];
  osSkills: ForgeOSSkill[];
  dependencies: ForgeDependency[];
}

type GraphNode = {
  id: string;
  name: string;
  type: 'tool' | 'skill' | 'os-skill';
  enabled: boolean;
};

export default function GraphTab({
  tools,
  skills,
  osSkills,
  dependencies,
}: GraphTabProps) {
  const [highlightedNode, setHighlightedNode] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<'all' | 'tool' | 'skill' | 'os-skill'>('all');

  const nodes: GraphNode[] = useMemo(() => {
    return [
      ...osSkills.map((s) => ({ id: s.id, name: s.name, type: 'os-skill' as const, enabled: s.enabled })),
      ...skills.map((s) => ({ id: s.id, name: s.name, type: 'skill' as const, enabled: s.enabled })),
      ...tools.map((t) => ({ id: t.id, name: t.name, type: 'tool' as const, enabled: t.enabled })),
    ];
  }, [osSkills, skills, tools]);

  // Detect cycles
  const cycles = useMemo(() => {
    const cycles: string[][] = [];
    const visited = new Set<string>();
    const path = new Set<string>();

    const dfs = (nodeId: string, currentPath: string[]): void => {
      if (path.has(nodeId)) {
        cycles.push([...currentPath, nodeId]);
        return;
      }
      if (visited.has(nodeId)) return;

      visited.add(nodeId);
      path.add(nodeId);

      dependencies
        .filter((d) => d.source === nodeId)
        .forEach((d) => dfs(d.target, [...currentPath, nodeId]));

      path.delete(nodeId);
    };

    nodes.forEach((n) => dfs(n.id, []));
    return cycles;
  }, [nodes, dependencies]);

  const relatedNodes = useMemo(() => {
    if (!highlightedNode) return new Set<string>();

    const related = new Set<string>([highlightedNode]);
    const queue = [highlightedNode];

    while (queue.length > 0) {
      const current = queue.shift()!;
      dependencies
        .filter((d) => d.source === current || d.target === current)
        .forEach((d) => {
          const otherNode = d.source === current ? d.target : d.source;
          if (!related.has(otherNode)) {
            related.add(otherNode);
            queue.push(otherNode);
          }
        });
    }

    return related;
  }, [highlightedNode, dependencies]);

  const filteredDependencies = dependencies.filter((d) => {
    if (filterType === 'all') return true;
    const sourceNode = nodes.find((n) => n.id === d.source);
    const targetNode = nodes.find((n) => n.id === d.target);
    return sourceNode?.type === filterType || targetNode?.type === filterType;
  });

  return (
    <div className="space-y-4">
      <div className="flex gap-4 items-center">
        <div className="flex-1">
          <h2 className="text-xl font-semibold mb-2">Dependency Graph</h2>
          <p className="text-sm text-muted-foreground">
            Shows which Skills use which Tools, and OS-Skill dependencies.
          </p>
        </div>
        <div className="flex gap-2">
          {['all', 'os-skill', 'skill', 'tool'].map((type) => (
            <Button
              key={type}
              variant={filterType === type ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilterType(type as any)}
            >
              {type === 'all' ? 'All' : type.replace('-', ' ').toUpperCase()}
            </Button>
          ))}
        </div>
      </div>

      {/* Cycle Detection */}
      {cycles.length > 0 && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="py-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-destructive mt-0.5 shrink-0" />
              <div>
                <p className="font-semibold text-destructive text-sm">
                  {cycles.length} circular dependenc{cycles.length === 1 ? 'y' : 'ies'} detected
                </p>
                {cycles.map((cycle, i) => (
                  <p key={i} className="text-xs text-destructive/80 mt-1">
                    {cycle.map((id) => nodes.find((n) => n.id === id)?.name).join(' → ')}
                  </p>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Dependency List */}
      <div className="space-y-3">
        <div className="text-sm font-medium">
          {filteredDependencies.length} dependenc{filteredDependencies.length === 1 ? 'y' : 'ies'}
        </div>

        {filteredDependencies.length === 0 ? (
          <Card className="border-dashed">
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No dependencies found for this filter.
            </CardContent>
          </Card>
        ) : (
          filteredDependencies.map((dep, i) => {
            const sourceNode = nodes.find((n) => n.id === dep.source);
            const targetNode = nodes.find((n) => n.id === dep.target);
            const isHighlighted =
              relatedNodes.has(dep.source) || relatedNodes.has(dep.target);

            return (
              <Card
                key={`${dep.source}-${dep.target}-${i}`}
                className={`transition-all ${
                  isHighlighted ? 'border-accent/50' : 'opacity-50 hover:opacity-100'
                }`}
                onMouseEnter={() => setHighlightedNode(dep.source)}
                onMouseLeave={() => setHighlightedNode(null)}
              >
                <CardContent className="py-3">
                  <div className="flex items-center gap-3 justify-between">
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <Badge variant="outline" className="text-[10px] shrink-0">
                          {sourceNode?.type.replace('-', ' ')}
                        </Badge>
                        <span className="truncate font-mono text-sm">{sourceNode?.name}</span>
                      </div>
                      <span className="text-muted-foreground mx-2">→</span>
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <Badge variant="outline" className="text-[10px] shrink-0">
                          {targetNode?.type.replace('-', ' ')}
                        </Badge>
                        <span className="truncate font-mono text-sm">{targetNode?.name}</span>
                      </div>
                    </div>
                    {!sourceNode?.enabled || !targetNode?.enabled ? (
                      <Badge variant="secondary" className="text-[10px] shrink-0">
                        {!sourceNode?.enabled ? 'Source disabled' : 'Target disabled'}
                      </Badge>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            );
          })
        )}
      </div>

      {/* Stats */}
      <Card className="bg-muted/40">
        <CardContent className="py-4">
          <div className="grid grid-cols-4 gap-4 text-center text-sm">
            <div>
              <p className="font-semibold">{osSkills.length}</p>
              <p className="text-xs text-muted-foreground">OS-Skills</p>
            </div>
            <div>
              <p className="font-semibold">{skills.length}</p>
              <p className="text-xs text-muted-foreground">Skills</p>
            </div>
            <div>
              <p className="font-semibold">{tools.length}</p>
              <p className="text-xs text-muted-foreground">Tools</p>
            </div>
            <div>
              <p className="font-semibold">{filteredDependencies.length}</p>
              <p className="text-xs text-muted-foreground">Dependencies</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
