/** DataHub — analyze a JSON/CSV file, then create an artifact from it. */
import React, { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";

const BASE = "/v1/console/datahub";

type SourceType = "json" | "csv";
type CreationType = "skill" | "tool" | "dataset" | "pipeline";

interface Analysis {
  row_count: number;
  completeness: number;
  schema: Record<string, string> | null;
  security: { secret: number; pii: number; injection: number };
}

interface Artifact {
  artifact_id: string;
  name: string;
  description: string;
  creation_type: string;
  created_at: string;
  status: string;
  row_count: number;
  validation_errors: string[];
  body?: string;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return data as T;
}

export default function DataHubUnified() {
  const [sourceType, setSourceType] = useState<SourceType>("json");
  const [sourcePath, setSourcePath] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [creationType, setCreationType] = useState<CreationType>("skill");
  const [artifactName, setArtifactName] = useState("");
  const [description, setDescription] = useState("");
  const [created, setCreated] = useState<Artifact | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const source = { data_source: sourceType, data_path: sourcePath };

  const refresh = useCallback(async () => {
    try {
      const data = await call<{ items: Artifact[] }>(`${BASE}/list?limit=50`);
      setArtifacts(data.items);
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (fn: () => Promise<void>) => {
    setLoading(true);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoading(false);
    }
  };

  const handleAnalyze = () =>
    run(async () => {
      setCreated(null);
      const data = await call<{ analysis: Analysis }>(`${BASE}/analyze`, {
        method: "POST",
        body: JSON.stringify(source),
      });
      setAnalysis(data.analysis);
    });

  const handleCreate = () =>
    run(async () => {
      const res = await call<{ artifact_id: string }>(`${BASE}/create`, {
        method: "POST",
        body: JSON.stringify({ ...source, creation_type: creationType, name: artifactName, description }),
      });
      setCreated(await call<Artifact>(`${BASE}/${res.artifact_id}`));
      await refresh();
    });

  const handleDelete = (id: string) =>
    run(async () => {
      await call(`${BASE}/${id}`, { method: "DELETE" });
      if (created?.artifact_id === id) setCreated(null);
      await refresh();
    });

  const blocked = (analysis?.security.secret ?? 0) > 0;

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-3xl font-bold">DataHub</h1>

      <Card className="p-6 space-y-4">
        <h2 className="text-xl font-semibold">1. Analyze a source file</h2>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium mb-2">Format</label>
            <Select value={sourceType} onChange={(e) => setSourceType(e.target.value as SourceType)}>
              <option value="json">JSON</option>
              <option value="csv">CSV</option>
            </Select>
          </div>
          <div className="col-span-2">
            <label className="block text-sm font-medium mb-2">File path on this machine</label>
            <Input
              placeholder="/home/me/data/orders.json"
              value={sourcePath}
              onChange={(e) => {
                setSourcePath(e.target.value);
                setAnalysis(null);
              }}
              disabled={loading}
            />
          </div>
        </div>
        <Button onClick={handleAnalyze} disabled={!sourcePath || loading} className="w-full">
          {loading && !analysis ? "Analyzing..." : "Analyze"}
        </Button>

        {analysis && (
          <div className="p-4 rounded border text-sm space-y-1" data-testid="datahub-analysis">
            <p>
              Rows: <strong>{analysis.row_count}</strong> · Completeness:{" "}
              <strong>{(analysis.completeness * 100).toFixed(0)}%</strong>
            </p>
            <p>
              Fields: {analysis.schema && Object.keys(analysis.schema).length > 0
                ? Object.entries(analysis.schema).map(([k, v]) => `${k} (${v})`).join(", ")
                : "none detected"}
            </p>
            <p>
              Security scan: {analysis.security.secret} secret(s), {analysis.security.pii} PII
              match(es), {analysis.security.injection} prompt-injection pattern(s)
            </p>
            {blocked && (
              <p className="text-red-600">This file contains secrets. Remove them before creating an artifact.</p>
            )}
          </div>
        )}
      </Card>

      {analysis && !blocked && (
        <Card className="p-6 space-y-4">
          <h2 className="text-xl font-semibold">2. Create an artifact</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-2">Artifact type</label>
              <Select value={creationType} onChange={(e) => setCreationType(e.target.value as CreationType)}>
                <option value="skill">Skill</option>
                <option value="tool">Tool</option>
                <option value="dataset">Dataset</option>
                <option value="pipeline">Pipeline</option>
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-2">Name</label>
              <Input
                placeholder="artifact_name"
                value={artifactName}
                onChange={(e) => setArtifactName(e.target.value)}
                disabled={loading}
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Description</label>
            <Textarea
              placeholder="What is this artifact for?"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={loading}
            />
          </div>
          <Button onClick={handleCreate} disabled={!artifactName || !description || loading} className="w-full">
            {loading ? "Creating..." : "Create artifact"}
          </Button>
        </Card>
      )}

      {error && <div className="p-3 bg-red-100 rounded text-red-700 text-sm">{error}</div>}

      {created && (
        <Card className="p-6 space-y-3" data-testid="datahub-created">
          <h2 className="text-xl font-semibold">{created.name}</h2>
          <p className="text-sm">
            {created.creation_type} · {created.status} · from {created.row_count} row(s)
          </p>
          {created.validation_errors.length > 0 && (
            <p className="text-sm text-red-600">{created.validation_errors.join(", ")}</p>
          )}
          <pre className="p-3 border rounded text-xs whitespace-pre-wrap">{created.body}</pre>
        </Card>
      )}

      <Card className="p-6 space-y-3">
        <h2 className="text-xl font-semibold">Artifacts</h2>
        {artifacts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No artifacts yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {artifacts.map((a) => (
              <li key={a.artifact_id} className="flex items-center justify-between gap-4">
                <button
                  type="button"
                  className="text-left underline-offset-2 hover:underline"
                  onClick={() => run(async () => setCreated(await call<Artifact>(`${BASE}/${a.artifact_id}`)))}
                >
                  <strong>{a.name}</strong> · {a.creation_type} · {a.status} · {a.row_count} row(s)
                </button>
                <Button variant="outline" onClick={() => handleDelete(a.artifact_id)} disabled={loading}>
                  Delete
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
