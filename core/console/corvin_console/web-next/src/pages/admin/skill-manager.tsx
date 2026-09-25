import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';

interface SkillInfo {
  skill_id: string;
  version: string;
  boot_layer: string;
  verified: boolean;
}

interface UploadState {
  file: File | null;
  skillId: string;
  version: string;
  uploading: boolean;
  progress: number;
  success: string | null;
}

export function SkillManager() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadState>({
    file: null,
    skillId: '',
    version: '',
    uploading: false,
    progress: 0,
    success: null,
  });

  useEffect(() => {
    fetchSkills();
  }, []);

  const fetchSkills = async () => {
    try {
      setLoading(true);
      const response = await fetch('/v1/console/skills-manager/skills/installed');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setSkills(data.skills || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error fetching skills');
      setSkills([]);
    } finally {
      setLoading(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setUpload(prev => ({ ...prev, file }));
  };

  const handleUpload = async () => {
    if (!upload.file || !upload.skillId || !upload.version) {
      setError('Please select a file and enter skill_id and version');
      return;
    }

    try {
      setUpload(prev => ({ ...prev, uploading: true, progress: 0 }));

      const formData = new FormData();
      formData.append('file', upload.file);
      formData.append('skill_id', upload.skillId);
      formData.append('version', upload.version);

      const xhr = new XMLHttpRequest();

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const percent = (e.loaded / e.total) * 100;
          setUpload(prev => ({ ...prev, progress: percent }));
        }
      });

      xhr.addEventListener('load', async () => {
        if (xhr.status === 200) {
          const data = JSON.parse(xhr.responseText);
          setUpload(prev => ({
            ...prev,
            uploading: false,
            progress: 100,
            success: data.message || 'Skill installed successfully!',
            file: null,
            skillId: '',
            version: '',
          }));
          setError(null);
          // Auto-refresh after success
          setTimeout(() => {
            fetchSkills();
            setUpload(prev => ({ ...prev, success: null }));
          }, 2000);
        } else {
          throw new Error('Upload failed');
        }
      });

      xhr.addEventListener('error', () => {
        setError('Upload failed');
        setUpload(prev => ({ ...prev, uploading: false }));
      });

      xhr.open('POST', '/v1/console/skills-manager/skills/install');
      xhr.send(formData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload error');
      setUpload(prev => ({ ...prev, uploading: false }));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Skill Manager</h1>
        <p className="text-gray-600 mt-2">Manage installed skills and versions</p>
      </div>

      <div className="flex gap-2">
        <button onClick={fetchSkills} disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
          {loading ? 'Loading...' : 'Refresh'}
        </button>
      </div>

      {/* Upload Section */}
      <Card>
        <CardHeader>
          <CardTitle>Install New Skill</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="block text-sm font-medium mb-1">Skill ID</label>
              <input
                type="text"
                value={upload.skillId}
                onChange={(e) => setUpload(prev => ({ ...prev, skillId: e.target.value }))}
                placeholder="e.g., my-awesome-skill"
                className="w-full px-3 py-2 border rounded"
                disabled={upload.uploading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Version</label>
              <input
                type="text"
                value={upload.version}
                onChange={(e) => setUpload(prev => ({ ...prev, version: e.target.value }))}
                placeholder="e.g., 1.0.0"
                className="w-full px-3 py-2 border rounded"
                disabled={upload.uploading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">ZIP File</label>
              <input
                type="file"
                accept=".zip"
                onChange={handleFileChange}
                className="w-full px-3 py-2 border rounded"
                disabled={upload.uploading}
              />
            </div>
          </div>

          {upload.file && (
            <p className="text-sm text-gray-600">
              Selected: <span className="font-mono">{upload.file.name}</span>
            </p>
          )}

          {upload.uploading && (
            <div className="w-full bg-gray-200 rounded h-2">
              <div
                className="bg-green-600 h-2 rounded transition-all"
                style={{ width: `${upload.progress}%` }}
              />
            </div>
          )}

          <button
            onClick={handleUpload}
            disabled={upload.uploading || !upload.file || !upload.skillId || !upload.version}
            className="w-full px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {upload.uploading ? `Uploading ${Math.round(upload.progress)}%` : 'Install Skill'}
          </button>

          {upload.success && (
            <Alert variant="default" className="bg-green-100 border-green-300">
              <AlertDescription className="text-green-800">✅ {upload.success}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!loading && skills.length === 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-gray-600">No skills installed yet.</p>
          </CardContent>
        </Card>
      )}

      <div>
        <h2 className="text-xl font-bold mb-4">Installed Skills</h2>
        <div className="grid gap-4">
          {skills.map((skill) => (
            <Card key={`${skill.skill_id}-${skill.version}`}>
              <CardHeader>
                <div className="flex justify-between items-center">
                  <div>
                    <CardTitle>{skill.skill_id}</CardTitle>
                    <p className="text-sm text-gray-500">v{skill.version}</p>
                  </div>
                  <div className="flex gap-2 items-center">
                    <Badge>{skill.boot_layer}</Badge>
                    <button
                      onClick={() => {
                        if (confirm(`Uninstall ${skill.skill_id}@${skill.version}?`)) {
                          fetch(
                            `/v1/console/skills-manager/skills/uninstall/${skill.skill_id}/${skill.version}`,
                            { method: 'DELETE' }
                          )
                            .then(r => r.json())
                            .then(data => {
                              if (data.success) {
                                setUpload(prev => ({ ...prev, success: data.message }));
                                setTimeout(() => {
                                  fetchSkills();
                                  setUpload(prev => ({ ...prev, success: null }));
                                }, 1500);
                              } else {
                                setError(data.message);
                              }
                            })
                            .catch(() => setError('Uninstall failed'));
                        }
                      }}
                      className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700"
                    >
                      Uninstall
                    </button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {skill.verified && <p className="text-xs text-green-600">✅ Verified</p>}
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}

export default SkillManager;
