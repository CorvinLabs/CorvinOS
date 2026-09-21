import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Loader2 } from 'lucide-react';
import ToolsTab from '@/components/forge/ToolsTab';
import SkillsTab from '@/components/forge/SkillsTab';
import OSSkillsTab from '@/components/forge/OSSkillsTab';
import GraphTab from '@/components/forge/GraphTab';
import AuditTab from '@/components/forge/AuditTab';
import AutonomousForgePanel from '@/components/forge/AutonomousForgePanel';
import { SkillForgePanel } from '@/components/SkillForgePanel';
import {
  ForgeTool,
  ForgeSkill,
  ForgeOSSkill,
  ForgeDependency } from '@/types/forge';

/** Tab ids, in tab-bar order. Also the accepted `?tab=` values.
 *
 *  Skill Forge leads: creating a skill is what an operator opens this page to
 *  do, and the tools list is reference material next to it. The FIRST tab is
 *  also the default — a tab bar whose leftmost entry is not the one that
 *  opens reads as a bug — so `?tab=` is omitted for it and present for every
 *  other. */
const FORGE_TABS = ['skill-forge', 'autonomous-forge', 'tools', 'skills', 'os-skills', 'graph', 'audit'] as const;
type ForgeTab = (typeof FORGE_TABS)[number];

const DEFAULT_TAB: ForgeTab = 'skill-forge';

/** Retired `?tab=` values, still honoured so existing links keep landing on
 *  the surface they named. `creator` was this tab's id until 2026-09-20, and
 *  /app/skill-forge-generator's redirect pointed at it. */
const TAB_ALIASES: Record<string, ForgeTab> = { creator: 'skill-forge' };

function resolveTab(requested: string | null): ForgeTab {
  if (!requested) return DEFAULT_TAB;
  if (FORGE_TABS.includes(requested as ForgeTab)) return requested as ForgeTab;
  return TAB_ALIASES[requested] ?? DEFAULT_TAB;
}

export default function ForgePage() {
  // ?tab= picks the opening tab, so /app/skills can redirect straight onto the
  // Skills tab instead of dropping the operator elsewhere and making them find
  // it (the standalone skills panel was folded in here on 2026-09-20). An
  // unknown value falls back to the default rather than rendering nothing.
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState<string>(() =>
    resolveTab(searchParams.get('tab')),
  );

  /** Switch tab AND keep ?tab= in sync — the Skills tab hands skill creation
   *  to Skill Forge through this, and a deep link has to survive a reload. */
  const goToTab = (v: string) => {
    setActiveTab(v);
    setSearchParams(v === DEFAULT_TAB ? {} : { tab: v }, { replace: true });
  };
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
            onChange={(e) => setFilterStatus(e.target.value as 'all' | 'enabled' | 'disabled')}
            className="flex h-10 w-32 rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="all">All</option>
            <option value="enabled">Enabled</option>
            <option value="disabled">Disabled</option>
          </select>
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value as 'all' | 'tool' | 'skill' | 'os-skill')}
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
      <Tabs
        value={activeTab}
        onValueChange={goToTab}
        className="flex-1 flex flex-col"
      >
        <TabsList className="grid w-full grid-cols-7 mb-4">
          {/* The ONE place a skill is created (2026-09-20), and the tab this
              page opens on. Two composers over one registry: ADR-0405
              orchestration (describe → watch the phases → read, refine, keep
              or delete) and the template form rehomed from
              /app/skill-forge-generator, whose own backend
              (POST /v1/skill-forge/generate) was an unmounted Flask blueprint
              answering 404 — the page could never create anything. Its fields
              now write through POST /skills/manual, the same registry this
              panel's library reads. The Skills tab's create dialog was the
              third half-duplicate and now links here. */}
          <TabsTrigger value="skill-forge">Skill Forge</TabsTrigger>
          <TabsTrigger value="autonomous-forge">Autonomous</TabsTrigger>
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

        <TabsContent value="skill-forge" className="flex-1 overflow-y-auto">
          <SkillForgePanel />
        </TabsContent>

        <TabsContent value="autonomous-forge" className="flex-1 overflow-y-auto">
          <AutonomousForgePanel />
        </TabsContent>

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
            onCreateSkill={() => goToTab('skill-forge')}
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
