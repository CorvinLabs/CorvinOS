import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Hammer, Plus, Search } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { ForgeTool } from '@/types/forge';

interface ToolsTabProps {
  tools: ForgeTool[];
  setTools: (tools: ForgeTool[]) => void;
  searchQuery: string;
  filterStatus: 'all' | 'enabled' | 'disabled';
}

export default function ToolsTab({
  tools,
  setTools,
  searchQuery,
  filterStatus,
}: ToolsTabProps) {
  const [selectedTool, setSelectedTool] = useState<string | null>(null);

  const filtered = React.useMemo(() => {
    return tools.filter((tool) => {
      const matchesSearch =
        searchQuery === '' ||
        tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (tool.description?.toLowerCase().includes(searchQuery.toLowerCase()) ?? false);

      const matchesStatus =
        filterStatus === 'all' ||
        (filterStatus === 'enabled' && tool.enabled) ||
        (filterStatus === 'disabled' && !tool.enabled);

      return matchesSearch && matchesStatus;
    });
  }, [tools, searchQuery, filterStatus]);

  const handleToggle = async (toolId: string) => {
    const tool = tools.find((t) => t.id === toolId);
    if (!tool) return;

    try {
      const endpoint = tool.enabled
        ? `/v1/console/forge/tools/${toolId}/disable`
        : `/v1/console/forge/tools/${toolId}/enable`;

      const res = await fetch(endpoint, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to toggle tool');

      setTools(
        tools.map((t) => (t.id === toolId ? { ...t, enabled: !t.enabled } : t))
      );
    } catch (err) {
      console.error('Error toggling tool:', err);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">MCP Tools</h2>
        <Button size="sm">
          <Plus className="w-4 h-4 mr-2" />
          New Tool
        </Button>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {tools.length === 0
              ? 'No tools yet. Create one to get started.'
              : 'No tools match your search.'}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((tool) => (
            <Card
              key={tool.id}
              className="cursor-pointer hover:border-accent/50 transition-colors"
              onClick={() => setSelectedTool(tool.id)}
            >
              <CardContent className="py-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <Hammer className="w-4 h-4 text-accent" />
                      <h3 className="font-semibold">{tool.name}</h3>
                      <Badge variant={tool.enabled ? 'ok' : 'secondary'} className="text-[10px]">
                        {tool.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                      {tool.version && (
                        <Badge variant="outline" className="text-[10px]">
                          v{tool.version}
                        </Badge>
                      )}
                    </div>
                    {tool.description && (
                      <p className="text-sm text-muted-foreground line-clamp-2">
                        {tool.description}
                      </p>
                    )}
                    {tool.actions && (
                      <p className="text-xs text-muted-foreground mt-2">
                        {tool.actions.length} action{tool.actions.length === 1 ? '' : 's'}
                      </p>
                    )}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggle(tool.id);
                    }}
                  >
                    {tool.enabled ? 'Disable' : 'Enable'}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
