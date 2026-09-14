/**DataHub Unified Generator — Console UI*/
import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Card } from "@/components/ui/card";

type SourceType = "json" | "csv" | "sql" | "api" | "parquet";
type CreationType = "skill" | "tool" | "dataset" | "pipeline";

export default function DataHubUnified() {
  const [stage, setStage] = useState<"ingestion" | "creation">("ingestion");
  const [sourceType, setSourceType] = useState<SourceType>("json");
  const [sourcePath, setSourcePath] = useState("");
  const [sampleRows, setSampleRows] = useState(100);
  const [creationType, setCreationType] = useState<CreationType>("skill");
  const [artifactName, setArtifactName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");

  const handleIngest = async () => {
    setLoading(true);
    setError("");

    try {
      const response = await fetch("/v1/datahub/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_type: sourceType,
          source_path: sourcePath,
          sample_rows: sampleRows,
        }),
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);
      const data = await response.json();
      setResult(data);
      setStage("creation");
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!result) return;
    setLoading(true);
    setError("");

    try {
      const response = await fetch("/v1/datahub/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_type: sourceType,
          source_path: sourcePath,
          sample_rows: sampleRows,
          creation_type: creationType,
          name: artifactName,
          description,
        }),
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);
      const data = await response.json();
      setResult(data);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-3xl font-bold">DataHub Unified</h1>

      {stage === "ingestion" && (
        <Card className="p-6 space-y-4">
          <h2 className="text-xl font-semibold">Phase 1: Ingest Data</h2>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-2">Source Type</label>
              <Select value={sourceType} onChange={(e) => setSourceType(e.target.value as SourceType)}>
                <option value="json">JSON</option>
                <option value="csv">CSV</option>
                <option value="sql">SQL</option>
                <option value="api">API</option>
                <option value="parquet">Parquet</option>
              </Select>
            </div>

            <div>
              <label className="block text-sm font-medium mb-2">Sample Rows</label>
              <Input
                type="number"
                value={sampleRows}
                onChange={(e) => setSampleRows(Number(e.target.value))}
                min={1}
                disabled={loading}
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Source Path</label>
            <Input
              placeholder="data.json or s3://bucket/data.csv"
              value={sourcePath}
              onChange={(e) => setSourcePath(e.target.value)}
              disabled={loading}
            />
          </div>

          <Button onClick={handleIngest} disabled={!sourcePath || loading} className="w-full">
            {loading ? "Ingesting..." : "Ingest Data"}
          </Button>

          {error && <div className="p-3 bg-red-100 rounded text-red-700 text-sm">{error}</div>}
        </Card>
      )}

      {stage === "creation" && result && (
        <Card className="p-6 space-y-4">
          <h2 className="text-xl font-semibold">Phase 2: Create Artifact</h2>

          <div className="p-4 bg-blue-50 rounded">
            <p className="text-sm">
              Ingested: <strong>{result.analysis?.row_count}</strong> rows
            </p>
            <p className="text-sm">
              Completeness: <strong>{(result.analysis?.completeness * 100).toFixed(0)}%</strong>
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-2">Artifact Type</label>
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
            <textarea
              className="w-full p-2 border rounded font-mono text-sm"
              placeholder="What is this artifact for?"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={loading}
            />
          </div>

          <Button onClick={handleCreate} disabled={!artifactName || loading} className="w-full">
            {loading ? "Creating..." : "Create Artifact"}
          </Button>

          {error && <div className="p-3 bg-red-100 rounded text-red-700 text-sm">{error}</div>}
        </Card>
      )}

      {result?.artifact_name && (
        <Card className="p-6 space-y-4">
          <h2 className="text-xl font-semibold">Result</h2>
          <div className="space-y-2 text-sm">
            <p>
              <strong>Name:</strong> {result.artifact_name}
            </p>
            <p>
              <strong>Type:</strong> {result.artifact_type || result.artifact_type}
            </p>
            <p>
              <strong>Tests:</strong> {result.test_count || 0}
            </p>
            {result.validation_errors?.length > 0 && (
              <p className="text-red-600">
                <strong>Errors:</strong> {result.validation_errors.join(", ")}
              </p>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
