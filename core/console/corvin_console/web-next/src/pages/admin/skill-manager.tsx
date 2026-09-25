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

export function SkillManager() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Skill Manager</h1>
        <p className="text-gray-600 mt-2">Manage installed skills and versions</p>
      </div>

      <button onClick={fetchSkills} disabled={loading}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
        {loading ? 'Loading...' : 'Refresh'}
      </button>

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

      <div className="grid gap-4">
        {skills.map((skill) => (
          <Card key={`${skill.skill_id}-${skill.version}`}>
            <CardHeader>
              <div className="flex justify-between items-center">
                <div>
                  <CardTitle>{skill.skill_id}</CardTitle>
                  <p className="text-sm text-gray-500">v{skill.version}</p>
                </div>
                <Badge>{skill.boot_layer}</Badge>
              </div>
            </CardHeader>
            <CardContent>
              {skill.verified && <p className="text-xs text-green-600">✅ Verified</p>}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

export default SkillManager;
