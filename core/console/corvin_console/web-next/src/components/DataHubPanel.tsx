/**
 * DataHub Phase 3: Console Panel Component
 * 
 * Features:
 * - Create new artifacts from data sources
 * - View artifact list with pagination
 * - Retrieve and preview artifact details
 * - Delete artifacts (soft delete)
 */

import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, AlertCircle, CheckCircle2, Trash2 } from 'lucide-react';

// API types (mirror from datahub_api.py)
interface ArtifactMetadata {
  artifact_id: string;
  name: string;
  description: string;
  creation_type: string;
  created_at: string;
  status: 'pending' | 'completed' | 'failed' | 'deleted';
  test_count: number;
  validation_errors: string[];
}

interface ArtifactCreateRequest {
  name: string;
  description: string;
  creation_type: 'skill' | 'tool' | 'dataset' | 'pipeline';
  data_source: 'json' | 'csv' | 'sql' | 'api' | 'parquet';
  data_path: string;
  sample_rows: number;
  complexity: 'low' | 'medium' | 'high';
}

interface ArtifactCreateResponse {
  artifact_id: string;
  status: string;
  message: string;
  metadata?: ArtifactMetadata;
}

interface ArtifactListResponse {
  items: ArtifactMetadata[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * DataHubPanel — Main component
 */
export const DataHubPanel: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'create' | 'list'>('list');
  const [artifacts, setArtifacts] = useState<ArtifactMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [pagination, setPagination] = useState({ limit: 50, offset: 0, total: 0 });

  // Form state
  const [formData, setFormData] = useState<ArtifactCreateRequest>({
    name: '',
    description: '',
    creation_type: 'skill',
    data_source: 'json',
    data_path: '',
    sample_rows: 100,
    complexity: 'medium',
  });

  /**
   * Fetch artifacts list
   */
  const fetchArtifacts = async (limit: number = 50, offset: number = 0) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/v1/console/datahub/list?limit=${limit}&offset=${offset}`,
        { method: 'GET' }
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      
      const data: ArtifactListResponse = await response.json();
      setArtifacts(data.items);
      setPagination({ limit: data.limit, offset: data.offset, total: data.total });
    } catch (err) {
      setError(`Failed to fetch artifacts: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Create new artifact
   */
  const handleCreateArtifact = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await fetch('/v1/console/datahub/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || `HTTP ${response.status}`);
      }

      const data: ArtifactCreateResponse = await response.json();
      setSuccess(`Artifact '${formData.name}' created successfully (ID: ${data.artifact_id})`);
      
      // Reset form
      setFormData({
        name: '',
        description: '',
        creation_type: 'skill',
        data_source: 'json',
        data_path: '',
        sample_rows: 100,
        complexity: 'medium',
      });

      // Refresh list
      fetchArtifacts();
      setActiveTab('list');
    } catch (err) {
      setError(`Failed to create artifact: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Delete artifact (soft delete)
   */
  const handleDeleteArtifact = async (artifactId: string) => {
    if (!confirm('Are you sure? This cannot be undone.')) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/v1/console/datahub/${artifactId}`, {
        method: 'DELETE',
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      setSuccess(`Artifact deleted successfully`);
      fetchArtifacts();
    } catch (err) {
      setError(`Failed to delete artifact: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Load artifacts on mount
   */
  useEffect(() => {
    if (activeTab === 'list') {
      fetchArtifacts();
    }
  }, [activeTab]);

  // ─────────────────────────────────────────────────────────
  // Render: Tab Navigation
  // ─────────────────────────────────────────────────────────

  return (
    <div className="w-full max-w-6xl mx-auto p-6 space-y-6">
      <div className="flex gap-4 border-b">
        <button
          onClick={() => setActiveTab('list')}
          className={`pb-2 px-4 font-medium ${
            activeTab === 'list'
              ? 'border-b-2 border-blue-500 text-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Artifacts ({pagination.total})
        </button>
        <button
          onClick={() => setActiveTab('create')}
          className={`pb-2 px-4 font-medium ${
            activeTab === 'create'
              ? 'border-b-2 border-blue-500 text-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Create New
        </button>
      </div>

      {/* Alerts */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {success && (
        <Alert className="border-green-200 bg-green-50">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          <AlertDescription className="text-green-800">{success}</AlertDescription>
        </Alert>
      )}

      {/* Create Tab */}
      {activeTab === 'create' && (
        <Card>
          <CardHeader>
            <CardTitle>Create New Artifact</CardTitle>
            <CardDescription>
              Generate a new Skill, Tool, Dataset, or Pipeline from your data
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreateArtifact} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Artifact Name*</label>
                  <Input
                    required
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="e.g., user_profiler_skill"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Type*</label>
                  <select
                    value={formData.creation_type}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        creation_type: e.target.value as ArtifactCreateRequest['creation_type'],
                      })
                    }
                    className="w-full px-3 py-2 border rounded-md"
                  >
                    <option value="skill">Skill</option>
                    <option value="tool">Tool</option>
                    <option value="dataset">Dataset</option>
                    <option value="pipeline">Pipeline</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium mb-1">Description*</label>
                <textarea
                  required
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="Describe what this artifact does"
                  rows={3}
                  className="w-full px-3 py-2 border rounded-md"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Data Source*</label>
                  <select
                    value={formData.data_source}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        data_source: e.target.value as ArtifactCreateRequest['data_source'],
                      })
                    }
                    className="w-full px-3 py-2 border rounded-md"
                  >
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                    <option value="sql">SQL</option>
                    <option value="api">API</option>
                    <option value="parquet">Parquet</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Data Path*</label>
                  <Input
                    required
                    value={formData.data_path}
                    onChange={(e) => setFormData({ ...formData, data_path: e.target.value })}
                    placeholder="/path/to/data.json"
                  />
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Sample Rows</label>
                  <Input
                    type="number"
                    min="1"
                    max="10000"
                    value={formData.sample_rows}
                    onChange={(e) =>
                      setFormData({ ...formData, sample_rows: parseInt(e.target.value) })
                    }
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Complexity</label>
                  <select
                    value={formData.complexity}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        complexity: e.target.value as ArtifactCreateRequest['complexity'],
                      })
                    }
                    className="w-full px-3 py-2 border rounded-md"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
              </div>

              <Button type="submit" disabled={loading} className="w-full">
                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {loading ? 'Creating...' : 'Create Artifact'}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {/* List Tab */}
      {activeTab === 'list' && (
        <div className="space-y-4">
          {loading && !artifacts.length && (
            <div className="flex justify-center items-center h-32">
              <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
            </div>
          )}

          {!loading && artifacts.length === 0 && (
            <Card>
              <CardContent className="pt-12 text-center">
                <p className="text-gray-500 mb-4">No artifacts created yet</p>
                <Button onClick={() => setActiveTab('create')}>Create First Artifact</Button>
              </CardContent>
            </Card>
          )}

          {artifacts.map((artifact) => (
            <Card key={artifact.artifact_id}>
              <CardHeader>
                <div className="flex justify-between items-start">
                  <div>
                    <CardTitle className="text-lg">{artifact.name}</CardTitle>
                    <CardDescription>{artifact.description}</CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <span className={`px-2 py-1 rounded text-sm font-medium ${
                      artifact.status === 'completed' ? 'bg-green-100 text-green-800' :
                      artifact.status === 'failed' ? 'bg-red-100 text-red-800' :
                      'bg-gray-100 text-gray-800'
                    }`}>
                      {artifact.status}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDeleteArtifact(artifact.artifact_id)}
                      disabled={loading}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-3 gap-4 text-sm">
                  <div>
                    <span className="font-medium">Type:</span> {artifact.creation_type}
                  </div>
                  <div>
                    <span className="font-medium">Tests:</span> {artifact.test_count}
                  </div>
                  <div>
                    <span className="font-medium">Created:</span>{' '}
                    {new Date(artifact.created_at).toLocaleDateString()}
                  </div>
                </div>
                {artifact.validation_errors.length > 0 && (
                  <Alert variant="destructive" className="mt-4">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>
                      <strong>Validation Errors:</strong>
                      <ul className="list-disc ml-4 mt-2">
                        {artifact.validation_errors.map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                      </ul>
                    </AlertDescription>
                  </Alert>
                )}
              </CardContent>
            </Card>
          ))}

          {/* Pagination */}
          {pagination.total > pagination.limit && (
            <div className="flex gap-2 justify-center">
              <Button
                variant="outline"
                onClick={() => fetchArtifacts(pagination.limit, Math.max(0, pagination.offset - pagination.limit))}
                disabled={pagination.offset === 0}
              >
                Previous
              </Button>
              <span className="py-2 px-4 text-sm text-gray-600">
                {pagination.offset + 1}-{Math.min(pagination.offset + pagination.limit, pagination.total)} of {pagination.total}
              </span>
              <Button
                variant="outline"
                onClick={() => fetchArtifacts(pagination.limit, pagination.offset + pagination.limit)}
                disabled={pagination.offset + pagination.limit >= pagination.total}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default DataHubPanel;
