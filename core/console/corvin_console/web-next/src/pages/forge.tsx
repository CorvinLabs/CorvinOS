import React, { useState, useEffect } from 'react';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Loader2, Search } from 'lucide-react';
import ToolsTab from '@/components/forge/ToolsTab';
import SkillsTab from '@/components/forge/SkillsTab';
import OSSkillsTab from '@/components/forge/OSSkillsTab';
import GraphTab from '@/components/forge/GraphTab';
import AuditTab from '@/components/forge/AuditTab';
import {
  ForgeTool,
  ForgeSkill,
  ForgeOSSkill,
  ForgeDependency,
} from '@/types/forge';

export default function ForgePage() {
  const [activeTab, setActiveTab] = useState('tools');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [filterType, setFilterType] = useState<'all' | 'tool' | 'skill' | 'os-skill'>('all');

  const [tools, setTools] = useState<ForgeTool[]>([]);
  const [skills, setSkills] = useState<ForgeSkill[]>([]);
  const [osSkills, setOSSkills] = useState<ForgeOSSkill[]>([]);
  const [dependencies, setDependencies] = useState<ForgeDependency[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch all data on mount
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const [toolsRes, skillsRes, osSkillsRes, graphRes] = await Promise.all([
          fetch('/v1/console/forge/tools'),
          fetch('/v1/console/forge/skills'),
          fetch('/v1/console/forge/os-skills'),
          fetch('/v1/console/forge/graph'),
        ]);

        if (!toolsRes.ok || !skillsRes.ok || !osSkillsRes.ok || !graphRes.ok) {
          throw new Error('Failed to fetch forge data');
        }

        const toolsData = await toolsRes.json();
        const skillsData = await skillsRes.json();
        const osSkillsData = await osSkillsRes.json();
        const graphData = await graphRes.json();

        setTools(toolsData.tools || []);
        setSkills(skillsData.skills || []);
        setOSSkills(osSkillsData.os_skills || []);
        setDependencies(graphData.edges || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="w-full h-full flex flex-col p-6 bg-background">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold mb-4">Forge Management</h1>
        <p className="text-muted-foreground mb-4">
          Unified management for Tools, Skills, and OS-Skills
        </p>

        {/* Search & Filters */}
        <div className="flex gap-4 items-center flex-wrap">
          <Input
            placeholder="Search across all forge items..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 min-w-64"
          />
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value as any)}
            className="flex h-10 w-32 rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="all">All</option>
            <option value="enabled">Enabled</option>
            <option value="disabled">Disabled</option>
          </select>
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value as any)}
            className="flex h-10 w-40 rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="all">All Types</option>
            <option value="tool">Tools</option>
            <option value="skill">Skills</option>
            <option value="os-skill">OS-Skills</option>
          </select>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="mb-4 p-4 bg-destructive/10 text-destructive rounded-md">
          {error}
        </div>
      )}

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col">
        <TabsList className="grid w-full grid-cols-5 mb-4">
          <TabsTrigger value="tools">
            Tools
            <span className="ml-2 text-xs bg-secondary px-2 py-1 rounded">
              {tools.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="skills">
            Skills
            <span className="ml-2 text-xs bg-secondary px-2 py-1 rounded">
              {skills.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="os-skills">
            OS-Skills
            <span className="ml-2 text-xs bg-secondary px-2 py-1 rounded">
              {osSkills.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="audit">Audit</TabsTrigger>
        </TabsList>

        <TabsContent value="tools" className="flex-1 overflow-y-auto">
          <ToolsTab
            tools={tools}
            setTools={setTools}
            searchQuery={searchQuery}
            filterStatus={filterStatus}
          />
        </TabsContent>

        <TabsContent value="skills" className="flex-1 overflow-y-auto">
          <SkillsTab
            skills={skills}
            setSkills={setSkills}
            searchQuery={searchQuery}
            filterStatus={filterStatus}
          />
        </TabsContent>

        <TabsContent value="os-skills" className="flex-1 overflow-y-auto">
          <OSSkillsTab
            osSkills={osSkills}
            setOSSkills={setOSSkills}
            searchQuery={searchQuery}
            filterStatus={filterStatus}
          />
        </TabsContent>

        <TabsContent value="graph" className="flex-1 overflow-y-auto">
          <GraphTab
            tools={tools}
            skills={skills}
            osSkills={osSkills}
            dependencies={dependencies}
          />
        </TabsContent>

        <TabsContent value="audit" className="flex-1 overflow-y-auto">
          <AuditTab searchQuery={searchQuery} filterType={filterType} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export { ForgePage };
