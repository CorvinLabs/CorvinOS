import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import ToolsTab from '@/components/forge/ToolsTab';
import SkillsTab from '@/components/forge/SkillsTab';
import OSSkillsTab from '@/components/forge/OSSkillsTab';
import LayersTab, { ForgeLayerPanel } from '@/components/forge/LayersTab';
import GraphTab from '@/components/forge/GraphTab';
import AuditTab from '@/components/forge/AuditTab';
import AutonomousForgePanel from '@/components/forge/AutonomousForgePanel';
import ForgeCreatorPanel from '@/components/forge/ForgeCreatorPanel';
import { SkillForgePanel } from '@/components/SkillForgePanel';
import ForgeBundlesPanel from '@/components/forge/ForgeBundlesPanel';
import {
  ForgeTool,
  ForgeSkill,
  ForgeOSSkill,
  ForgeDependency } from '@/types/forge';

/** Tab ids, in tab-bar order. Also the accepted `?tab=` values.
 *
 *  Generator leads: creating something (a skill, a tool or a plugin) is what
 *  an operator opens this page to do, and the tools/skills lists are
 *  reference material next to it. The FIRST tab is also the default — a tab
 *  bar whose leftmost entry is not the one that opens reads as a bug — so
 *  `?tab=` is omitted for it and present for every other.
 *
 *  Skill Forge, Tool Forge and Plugin Forge used to be three separate top-
 *  level tabs; they are now one "Generator" tab with the three as sub-tabs
 *  (operator request, 2026-10-05) — the engine run/poll/phase protocol is the
 *  one Skill Forge uses for all three (ADR-2217), so one door to it reads
 *  better than three. See GENERATOR_SUBTABS below.
 *
 *  'layers' (Layer Forge, ADR-2222) was folded in the same way on 2026-10-06
 *  (operator request): it used to be its own top-level nav entry
 *  (/app/layer-forge) with its own page; it's now a tab here, placed next to
 *  tools/skills/os-skills (the other "what has this operator's Forge
 *  generated/registered" surfaces) rather than next to graph/audit (which
 *  are cross-cutting views over all of them). Old links redirect — see
 *  App.tsx's `layer-forge` <Route>. It still has its own List+Detail UI and
 *  its own backend (/v1/console/layer-forge/*, untouched by this move) — it
 *  does NOT share Generator's run/poll/phase protocol (ADR-2217/ADR-0672),
 *  so it stays a sibling top-level tab for BROWSING, same as Tools/Skills/
 *  OS-Skills. CREATING one, though, followed those three into Generator the
 *  same day (see GENERATOR_SUBTABS below) — "the one place to create
 *  something" applies to the action regardless of whether its backend
 *  protocol matches; forging a layer is a single synchronous plan→create
 *  call, not a polled run, and needs none of the engine machinery the other
 *  three sub-tabs share — it only lives in the same tab group as them. */
const FORGE_TABS = ['generator', 'autonomous-forge', 'tools', 'skills', 'os-skills', 'layers', 'graph', 'audit', 'bundles'] as const;
type ForgeTab = (typeof FORGE_TABS)[number];

const DEFAULT_TAB: ForgeTab = 'generator';

/** Retired `?tab=` values, still honoured so existing links keep landing on
 *  the surface they named. `creator` was this tab's id until 2026-09-20, and
 *  /app/skill-forge-generator's redirect pointed at it; `skill-forge`,
 *  `tool-forge` and `plugin-forge` were each a top-level tab id until they
 *  were folded into Generator's sub-tabs (2026-10-05) — see
 *  GENERATOR_SUB_ALIASES, which also reads these same three values to pick
 *  the right sub-tab. */
const TAB_ALIASES: Record<string, ForgeTab> = {
  creator: 'generator',
  'skill-forge': 'generator',
  'tool-forge': 'generator',
  'plugin-forge': 'generator',
};

function resolveTab(requested: string | null): ForgeTab {
  if (!requested) return DEFAULT_TAB;
  if (FORGE_TABS.includes(requested as ForgeTab)) return requested as ForgeTab;
  return TAB_ALIASES[requested] ?? DEFAULT_TAB;
}

/** Generator's own sub-tabs — Skill Forge leads here for the same reason it
 *  used to lead the whole page. 'layer' joined on 2026-10-06 (operator
 *  request) — see the FORGE_TABS comment above for why it's grouped here
 *  despite not sharing skill/tool/plugin's run/poll engine protocol. */
const GENERATOR_SUBTABS = ['skill', 'tool', 'plugin', 'layer'] as const;
type GeneratorSubTab = (typeof GENERATOR_SUBTABS)[number];
const DEFAULT_GENERATOR_SUB: GeneratorSubTab = 'skill';

/** Old top-level tab ids (and the even older `creator` alias) map onto the
 *  sub-tab they used to be, so a bookmark or in-app link minted before
 *  2026-10-05 still lands on the right composer inside Generator. */
const GENERATOR_SUB_ALIASES: Record<string, GeneratorSubTab> = {
  creator: 'skill',
  'skill-forge': 'skill',
  'tool-forge': 'tool',
  'plugin-forge': 'plugin',
};

function resolveGeneratorSub(requested: string | null): GeneratorSubTab {
  if (!requested) return DEFAULT_GENERATOR_SUB;
  if (GENERATOR_SUBTABS.includes(requested as GeneratorSubTab)) return requested as GeneratorSubTab;
  return GENERATOR_SUB_ALIASES[requested] ?? DEFAULT_GENERATOR_SUB;
}

export default function ForgePage() {
  // ?tab= picks the opening tab, so /app/skills can redirect straight onto the
  // Skills tab instead of dropping the operator elsewhere and making them find
  // it (the standalone skills panel was folded in here on 2026-09-20). An
  // unknown value falls back to the default rather than rendering nothing.
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<string>(() => resolveTab(initialTabParam));
  // A deep link into an old top-level tab (e.g. ?tab=tool-forge) names both
  // WHICH sub-tab to open and that Generator is the top-level tab — ?sub=
  // wins when present (it is what THIS page writes), the old ?tab= value is
  // the fallback for links minted before 2026-10-05.
  const [generatorSub, setGeneratorSub] = useState<GeneratorSubTab>(() =>
    resolveGeneratorSub(searchParams.get('sub') ?? initialTabParam),
  );

  /** Switch top-level tab AND keep ?tab= in sync — the Skills tab hands skill
   *  creation to Generator's Skill Forge sub-tab through this, and a deep
   *  link has to survive a reload. A value that used to be its own top-level
   *  tab (skill-forge/tool-forge/plugin-forge/creator) now also picks the
   *  right Generator sub-tab instead of just landing on whichever one was
   *  last open. */
  const goToTab = (v: string) => {
    if (v === 'tools') void reloadToolsRef.current?.();
    const sub = GENERATOR_SUB_ALIASES[v];
    if (sub) {
      setGeneratorSub(sub);
      setActiveTab('generator');
      setSearchParams(sub === DEFAULT_GENERATOR_SUB ? {} : { tab: 'generator', sub }, { replace: true });
      return;
    }
    setActiveTab(v);
    setSearchParams(v === DEFAULT_TAB ? {} : { tab: v }, { replace: true });
  };

  /** Switch Generator's own sub-tab, keeping ?tab=generator&sub= in sync. */
  const goToGeneratorSub = (v: string) => {
    const sub = GENERATOR_SUBTABS.includes(v as GeneratorSubTab) ? (v as GeneratorSubTab) : DEFAULT_GENERATOR_SUB;
    setGeneratorSub(sub);
    setSearchParams(sub === DEFAULT_GENERATOR_SUB ? {} : { tab: 'generator', sub }, { replace: true });
  };
  const reloadToolsRef = React.useRef<(() => Promise<void>) | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [filterType, setFilterType] = useState<'all' | 'tool' | 'skill' | 'os-skill'>('all');

  const [tools, setTools] = useState<ForgeTool[]>([]);
  const [skills, setSkills] = useState<ForgeSkill[]>([]);
  const [osSkills, setOSSkills] = useState<ForgeOSSkill[]>([]);
  const [dependencies, setDependencies] = useState<ForgeDependency[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch all data on mount. Uses the shared `api()` client (not raw
  // `fetch`) so this page gets the same request timeout and 401-session-
  // renewal handling every other panel gets — see lib/api/client.ts. Raw
  // `fetch` here previously meant a hung backend left the page spinning
  // forever (no timeout to reject it) and a transient session hiccup any
  // other panel silently recovers from instead surfaced as an opaque
  // "Failed to fetch forge data" with no indication of which call failed
  // or why.
  // Re-read only the tools list — a finished Tool Forge run registers one,
  // possibly while another tab was open (the creator tab then is unmounted).
  const reloadTools = React.useCallback(async () => {
    try {
      const toolsData = await api<{ tools?: ForgeTool[] }>('/forge/tools');
      setTools(toolsData.tools || []);
    } catch {
      /* the tab keeps its last list; a reload shows the error */
    }
  }, []);
  reloadToolsRef.current = reloadTools;

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const [toolsData, skillsData, osSkillsData, graphData] = await Promise.all([
          api<{ tools?: ForgeTool[] }>('/forge/tools'),
          api<{ skills?: ForgeSkill[] }>('/forge/skills'),
          api<{ os_skills?: ForgeOSSkill[] }>('/forge/os-skills'),
          api<{ edges?: ForgeDependency[] }>('/forge/graph'),
        ]);

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
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 mb-4">
          {/* The ONE place to create something (2026-09-20, folded further on
              2026-10-05): Skill Forge, Tool Forge and Plugin Forge share the
              same describe → run → phases protocol (ADR-2217) over the
              shared generation-run store, so they live as sub-tabs of one
              Generator tab instead of three top-level tabs. Skill Forge's own
              two composers (ADR-0405 orchestration, and the template form
              rehomed from /app/skill-forge-generator) are inside its
              sub-tab, unchanged. */}
          <TabsTrigger value="generator">Generator</TabsTrigger>
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
          <TabsTrigger value="layers">Layers</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="audit">Audit</TabsTrigger>
          <TabsTrigger value="bundles">Bundles</TabsTrigger>
        </TabsList>

        <TabsContent value="generator" className="flex-1 overflow-y-auto">
          <Tabs value={generatorSub} onValueChange={goToGeneratorSub} className="flex-1 flex flex-col">
            <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 mb-4">
              <TabsTrigger value="skill">Skill Forge</TabsTrigger>
              <TabsTrigger value="tool">Tool Forge</TabsTrigger>
              <TabsTrigger value="plugin">Plugin Forge</TabsTrigger>
              <TabsTrigger value="layer">Layer Forge</TabsTrigger>
            </TabsList>

            <TabsContent value="skill" className="flex-1 overflow-y-auto">
              <SkillForgePanel />
            </TabsContent>

            <TabsContent value="tool" className="flex-1 overflow-y-auto">
              <ForgeCreatorPanel
                kind="tool"
                onCreated={reloadTools}
                onOpenTools={() => { void reloadTools(); goToTab('tools'); }}
              />
            </TabsContent>

            <TabsContent value="plugin" className="flex-1 overflow-y-auto">
              <ForgeCreatorPanel kind="plugin" />
            </TabsContent>

            <TabsContent value="layer" className="flex-1 overflow-y-auto">
              <ForgeLayerPanel onCreated={() => goToTab('layers')} />
            </TabsContent>
          </Tabs>
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

        <TabsContent value="layers" className="flex-1 overflow-y-auto">
          <LayersTab />
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

        <TabsContent value="bundles" className="flex-1 overflow-y-auto">
          <ForgeBundlesPanel />
        </TabsContent>
      </Tabs>

    </div>
  );
}

export { ForgePage };
