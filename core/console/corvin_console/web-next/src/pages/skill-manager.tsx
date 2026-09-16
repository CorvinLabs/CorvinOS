/**
 * Skill Manager Page — ADR-0681 Phase 5 Console UI
 *
 * Provides three main panels:
 * 1. Generator: Create skills via LLM
 * 2. Manager: Upload and manage installed skills
 * 3. Dashboard: View skill stats and learning progress
 */

import React, { useState, useEffect } from 'react';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import {
  Loader2,
  Zap,
  Download,
  Upload,
  Trash2,
  Play,
  Pause,
  Code2,
  TrendingUp,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';

interface GenerationJob {
  job_id: string;
  status: 'pending' | 'generating' | 'testing' | 'packaging' | 'complete' | 'error';
  prompt: string;
  created_at: string;
  updated_at: string;
  progress: number;
  logs: string[];
  result?: {
    zip_path: string;
    size_bytes: number;
    generated_code: string;
  };
  error?: string;
}

interface InstalledSkill {
  skill_id: string;
  name: string;
  version: string;
  title: string;
  description: string;
  scope: 'console' | 'engine' | 'global';
  installed_at: string;
  enabled: boolean;
  usage_count: number;
  confidence_score: number;
}

const SkillManagerPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'generator' | 'manager' | 'dashboard'>('generator');

  // Generator state
  const [generatorPrompt, setGeneratorPrompt] = useState('');
  const [skillType, setSkillType] = useState<'tool' | 'workflow' | 'optimizer'>('tool');
  const [skillScope, setSkillScope] = useState<'console' | 'engine' | 'global'>('console');
  const [generatingJobId, setGeneratingJobId] = useState<string | null>(null);
  const [generationJob, setGenerationJob] = useState<GenerationJob | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);

  // Manager state
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [loadingSkills, setLoadingSkills] = useState(true);
  const [managerError, setManagerError] = useState<string | null>(null);

  // UI state
  const [isGenerating, setIsGenerating] = useState(false);

  // Load installed skills on mount
  useEffect(() => {
    loadInstalledSkills();
  }, []);

  // Poll generation job status
  useEffect(() => {
    if (!generatingJobId) return;

    const pollInterval = setInterval(async () => {
      try {
        const response = await fetch(`/v1/console/skills/generate/${generatingJobId}`);
        if (!response.ok) throw new Error('Failed to fetch job status');

        const job = await response.json();
        setGenerationJob(job);

        if (['complete', 'error'].includes(job.status)) {
          setGeneratingJobId(null);
          setIsGenerating(false);
        }
      } catch (err) {
        console.error('Error polling generation job:', err);
      }
    }, 2000); // Poll every 2 seconds

    return () => clearInterval(pollInterval);
  }, [generatingJobId]);

  const loadInstalledSkills = async () => {
    try {
      setLoadingSkills(true);
      setManagerError(null);
      const response = await fetch('/v1/console/skills/installed');
      if (!response.ok) throw new Error('Failed to load installed skills');

      const data = await response.json();
      setInstalledSkills(data.skills || []);
    } catch (err) {
      setManagerError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoadingSkills(false);
    }
  };

  const handleStartGeneration = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!generatorPrompt.trim()) {
      setGenerationError('Please enter a skill description');
      return;
    }

    try {
      setIsGenerating(true);
      setGenerationError(null);
      const response = await fetch('/v1/console/skills/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: generatorPrompt,
          skill_type: skillType,
          scope: skillScope,
        }),
      });

      if (!response.ok) throw new Error('Failed to start generation');

      const job = await response.json();
      setGeneratingJobId(job.job_id);
      setGenerationJob(job);
      setGeneratorPrompt('');
    } catch (err) {
      setGenerationError(err instanceof Error ? err.message : 'Unknown error');
      setIsGenerating(false);
    }
  };

  const handleDownloadSkill = async () => {
    if (!generationJob?.result?.zip_path) return;

    try {
      const response = await fetch(generationJob.result.zip_path);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `skill_${generatingJobId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Error downloading skill:', err);
    }
  };

  const handleToggleSkill = async (skillId: string, enabled: boolean) => {
    try {
      const endpoint = enabled ? 'enable' : 'disable';
      const response = await fetch(`/v1/console/skills/${skillId}/${endpoint}`, {
        method: 'POST',
      });

      if (!response.ok) throw new Error('Failed to toggle skill');

      await loadInstalledSkills();
    } catch (err) {
      setManagerError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  const handleDeleteSkill = async (skillId: string) => {
    try {
      const response = await fetch(`/v1/console/skills/${skillId}`, {
        method: 'DELETE',
      });

      if (!response.ok) throw new Error('Failed to delete skill');

      await loadInstalledSkills();
    } catch (err) {
      setManagerError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'complete':
        return <CheckCircle2 className="w-4 h-4 text-green-500" />;
      case 'error':
        return <AlertCircle className="w-4 h-4 text-red-500" />;
      default:
        return <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />;
    }
  };

  const getProgressPercentage = (status: string) => {
    const statusMap: Record<string, number> = {
      pending: 0,
      generating: 33,
      testing: 66,
      packaging: 85,
      complete: 100,
      error: 100,
    };
    return statusMap[status] || 0;
  };

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-6xl mx-auto p-6">
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">Skill Manager</h1>
          <p className="text-gray-600">Create, install, and manage Skill Forge skills</p>
        </div>

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as any)} className="w-full">
          <TabsList className="grid w-full grid-cols-3 mb-6">
            <TabsTrigger value="generator" className="flex items-center gap-2">
              <Zap className="w-4 h-4" />
              Generator
            </TabsTrigger>
            <TabsTrigger value="manager" className="flex items-center gap-2">
              <Upload className="w-4 h-4" />
              Manager
            </TabsTrigger>
            <TabsTrigger value="dashboard" className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4" />
              Dashboard
            </TabsTrigger>
          </TabsList>

          {/* Generator Tab */}
          <TabsContent value="generator" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Generate New Skill</CardTitle>
                <CardDescription>
                  Describe what your skill should do, and we'll generate the code for you
                </CardDescription>
              </CardHeader>
              <CardContent>
                <form onSubmit={handleStartGeneration} className="space-y-4">
                  <div className="space-y-2">
                    <label className="block text-sm font-medium">Skill Description</label>
                    <Textarea
                      placeholder="e.g., 'Create a skill that routes requests by confidence score'"
                      value={generatorPrompt}
                      onChange={(e) => setGeneratorPrompt(e.target.value)}
                      disabled={isGenerating}
                      className="h-32"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <label className="block text-sm font-medium">Skill Type</label>
                      <select
                        value={skillType}
                        onChange={(e) => setSkillType(e.target.value as any)}
                        disabled={isGenerating}
                        className="w-full px-3 py-2 border rounded-md"
                      >
                        <option value="tool">Tool (Deterministic)</option>
                        <option value="workflow">Workflow (Multi-step)</option>
                        <option value="optimizer">Optimizer (Learning-enabled)</option>
                      </select>
                    </div>

                    <div className="space-y-2">
                      <label className="block text-sm font-medium">Scope</label>
                      <select
                        value={skillScope}
                        onChange={(e) => setSkillScope(e.target.value as any)}
                        disabled={isGenerating}
                        className="w-full px-3 py-2 border rounded-md"
                      >
                        <option value="console">Console Only</option>
                        <option value="engine">Engine-wide</option>
                        <option value="global">Global (All Tenants)</option>
                      </select>
                    </div>
                  </div>

                  {generationError && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-600">
                      {generationError}
                    </div>
                  )}

                  <Button
                    type="submit"
                    disabled={isGenerating || !generatorPrompt.trim()}
                    className="w-full"
                  >
                    {isGenerating ? (
                      <>
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        Generating...
                      </>
                    ) : (
                      <>
                        <Zap className="w-4 h-4 mr-2" />
                        Generate Skill
                      </>
                    )}
                  </Button>
                </form>
              </CardContent>
            </Card>

            {/* Generation Progress */}
            {generationJob && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    {getStatusIcon(generationJob.status)}
                    Generation Progress
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span>{generationJob.status.charAt(0).toUpperCase() + generationJob.status.slice(1)}</span>
                      <span>{Math.round(generationJob.progress * 100)}%</span>
                    </div>
                    <Progress value={getProgressPercentage(generationJob.status)} />
                  </div>

                  <div className="bg-gray-50 rounded p-4 max-h-64 overflow-y-auto font-mono text-xs">
                    {generationJob.logs.map((log, i) => (
                      <div key={i} className="text-gray-700">{log}</div>
                    ))}
                  </div>

                  {generationJob.status === 'complete' && generationJob.result && (
                    <div className="p-4 bg-green-50 border border-green-200 rounded space-y-3">
                      <p className="text-sm font-medium text-green-900">Skill Generated Successfully!</p>
                      <div className="text-xs text-gray-600">
                        <p>Size: {(generationJob.result.size_bytes / 1024).toFixed(2)} KB</p>
                      </div>
                      <Button
                        onClick={handleDownloadSkill}
                        className="w-full bg-green-600 hover:bg-green-700"
                      >
                        <Download className="w-4 h-4 mr-2" />
                        Download Skill ZIP
                      </Button>
                    </div>
                  )}

                  {generationJob.status === 'error' && (
                    <div className="p-4 bg-red-50 border border-red-200 rounded">
                      <p className="text-sm font-medium text-red-900">Generation Error</p>
                      <p className="text-xs text-red-700 mt-1">{generationJob.error}</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* Manager Tab */}
          <TabsContent value="manager" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Upload Skill</CardTitle>
                <CardDescription>
                  Upload a skill ZIP file to install it
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:bg-gray-50">
                  <Upload className="w-12 h-12 mx-auto mb-2 text-gray-400" />
                  <p className="font-medium mb-1">Click to upload or drag and drop</p>
                  <p className="text-xs text-gray-500">ZIP files up to 10MB</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Installed Skills</CardTitle>
                <CardDescription>
                  {installedSkills.length} skill{installedSkills.length !== 1 ? 's' : ''} installed
                </CardDescription>
              </CardHeader>
              <CardContent>
                {managerError && (
                  <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-600 mb-4">
                    {managerError}
                  </div>
                )}

                {loadingSkills ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
                  </div>
                ) : installedSkills.length === 0 ? (
                  <p className="text-center text-gray-500 py-8">No skills installed yet</p>
                ) : (
                  <div className="space-y-3">
                    {installedSkills.map((skill) => (
                      <div
                        key={skill.skill_id}
                        className="flex items-start justify-between p-4 border rounded-lg hover:bg-gray-50"
                      >
                        <div className="flex-1">
                          <h3 className="font-medium">{skill.name}</h3>
                          <p className="text-sm text-gray-600">{skill.description}</p>
                          <div className="flex items-center gap-2 mt-2">
                            <Badge variant="outline">{skill.version}</Badge>
                            <Badge variant="outline">{skill.scope}</Badge>
                            {skill.enabled && (
                              <Badge className="bg-green-100 text-green-800">Active</Badge>
                            )}
                          </div>
                          <p className="text-xs text-gray-500 mt-2">
                            Used {skill.usage_count} times • Confidence: {(skill.confidence_score * 100).toFixed(0)}%
                          </p>
                        </div>
                        <div className="flex items-center gap-2 ml-4">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleToggleSkill(skill.skill_id, !skill.enabled)}
                          >
                            {skill.enabled ? (
                              <Pause className="w-4 h-4" />
                            ) : (
                              <Play className="w-4 h-4" />
                            )}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-gray-400 hover:text-gray-600"
                          >
                            <Code2 className="w-4 h-4" />
                          </Button>
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-red-400 hover:text-red-600"
                              >
                                <Trash2 className="w-4 h-4" />
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogTitle>Delete Skill</AlertDialogTitle>
                              <AlertDialogDescription>
                                Are you sure you want to delete "{skill.name}"? This action cannot be undone.
                              </AlertDialogDescription>
                              <div className="flex gap-4 justify-end">
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction
                                  onClick={() => handleDeleteSkill(skill.skill_id)}
                                  className="bg-red-600 hover:bg-red-700"
                                >
                                  Delete
                                </AlertDialogAction>
                              </div>
                            </AlertDialogContent>
                          </AlertDialog>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Dashboard Tab */}
          <TabsContent value="dashboard" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Skill Learning Dashboard</CardTitle>
                <CardDescription>
                  Monitor skill performance and learning progress
                </CardDescription>
              </CardHeader>
              <CardContent>
                {loadingSkills ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
                  </div>
                ) : installedSkills.length === 0 ? (
                  <p className="text-center text-gray-500 py-8">No skills to display</p>
                ) : (
                  <div className="grid grid-cols-2 gap-4">
                    {installedSkills.map((skill) => (
                      <Card key={skill.skill_id}>
                        <CardContent className="pt-6">
                          <h3 className="font-medium mb-4">{skill.name}</h3>
                          <div className="space-y-3 text-sm">
                            <div className="flex justify-between">
                              <span className="text-gray-600">Confidence</span>
                              <span className="font-medium">{(skill.confidence_score * 100).toFixed(0)}%</span>
                            </div>
                            <Progress value={skill.confidence_score * 100} />
                            <div className="flex justify-between pt-2">
                              <span className="text-gray-600">Usage</span>
                              <span className="font-medium">{skill.usage_count}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-gray-600">Installed</span>
                              <span className="font-medium text-xs">
                                {new Date(skill.installed_at).toLocaleDateString()}
                              </span>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
};

export default SkillManagerPage;
