/**
 * Skill Forge v2.0 Generator — Console UI Panel
 * Generate skills via template or LLM
 */

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Card } from "@/components/ui/card";

type SkillType = "learned-experience" | "reasoning" | "reference" | "automation";
type SkillScope = "task" | "session" | "project" | "user";

export default function SkillForgeGenerator() {
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [skillType, setSkillType] = useState<SkillType>("learned-experience");
  const [scope, setScope] = useState<SkillScope>("task");
  const [useLLM, setUseLLM] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");

  const handleGenerate = async () => {
    setLoading(true);
    setError("");

    try {
      const response = await fetch("/v1/skill-forge/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          title: title || name.replace(/_/g, " ").toUpperCase(),
          description,
          skill_type: skillType,
          scope,
          use_llm: useLLM,
        }),
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);
      const data = await response.json();
      setResult(data.manifest);
      setError("");
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-3xl font-bold">Skill Forge Generator</h1>

      <Card className="p-6 space-y-4">
        <h2 className="text-xl font-semibold">Generate New Skill</h2>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-2">Skill Name</label>
            <Input
              placeholder="my_skill (snake_case)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={loading}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Title</label>
            <Input
              placeholder="My Skill"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={loading}
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Description</label>
          <textarea
            className="w-full p-2 border rounded font-mono text-sm"
            placeholder="What does this skill do?"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={loading}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-2">Type</label>
            <Select
              value={skillType}
              onChange={(e) => setSkillType(e.target.value as SkillType)}
              disabled={loading}
            >
              <option value="learned-experience">Learned Experience</option>
              <option value="reasoning">Reasoning</option>
              <option value="reference">Reference</option>
              <option value="automation">Automation</option>
            </Select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Scope</label>
            <Select
              value={scope}
              onChange={(e) => setScope(e.target.value as SkillScope)}
              disabled={loading}
            >
              <option value="task">Task</option>
              <option value="session">Session</option>
              <option value="project">Project</option>
              <option value="user">User</option>
            </Select>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="use_llm"
            checked={useLLM}
            onChange={(e) => setUseLLM(e.target.checked)}
            disabled={loading}
          />
          <label htmlFor="use_llm" className="text-sm">
            Use LLM enhancement (requires API key)
          </label>
        </div>

        <Button
          onClick={handleGenerate}
          disabled={!name || !description || loading}
          className="w-full"
        >
          {loading ? "Generating..." : "Generate Skill"}
        </Button>

        {error && (
          <div className="p-3 bg-red-100 border border-red-400 rounded text-red-700 text-sm">
            {error}
          </div>
        )}
      </Card>

      {result && (
        <Card className="p-6 space-y-4">
          <h2 className="text-xl font-semibold">Generated Skill</h2>

          <div className="bg-gray-50 p-4 rounded font-mono text-sm overflow-auto max-h-96">
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={() => {
                navigator.clipboard.writeText(JSON.stringify(result, null, 2));
              }}
              variant="outline"
            >
              Copy JSON
            </Button>
            <Button
              onClick={() => {
                const link = document.createElement("a");
                link.href = `data:text/plain,${encodeURIComponent(JSON.stringify(result, null, 2))}`;
                link.download = `${result.name}-manifest.json`;
                link.click();
              }}
              variant="outline"
            >
              Download
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
