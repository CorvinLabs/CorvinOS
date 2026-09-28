import React, { useState, useEffect } from 'react';
import { CheckCircle2, Loader2, Package, RefreshCw, ShieldCheck, Trash2, Upload } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { getCurrentCsrf } from '@/lib/csrf-fetch';
import { errorMessage } from '@/pages/skills/endpoints';

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
  const [confirmUninstall, setConfirmUninstall] = useState<SkillInfo | null>(null);

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

      // No `throw` in here: this listener runs long after the surrounding
      // try/catch returned, so a throw was an unhandled rejection that left
      // `uploading` true forever. A 403 (not owner/admin, missing CSRF) or a
      // 400 (bad ZIP, refused package) is an ordinary answer — show the
      // backend's `detail` and re-enable the form.
      xhr.addEventListener('load', async () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          const msg = await errorMessage(
            new Response(xhr.responseText || null, { status: xhr.status }),
          );
          setError(msg === `HTTP ${xhr.status}` ? `Upload failed: ${msg}` : msg);
          setUpload(prev => ({ ...prev, uploading: false, progress: 0 }));
          return;
        }
        let data: { message?: string } = {};
        try { data = JSON.parse(xhr.responseText); } catch { /* empty body */ }
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
      });

      xhr.addEventListener('error', () => {
        setError('Upload failed');
        setUpload(prev => ({ ...prev, uploading: false }));
      });

      xhr.open('POST', '/v1/console/skills-manager/skills/install');
      // XHR bypasses the window.fetch CSRF wrapper (lib/csrf-fetch.ts), so the
      // token must be attached here or a CSRF-guarded install answers 403.
      const csrf = getCurrentCsrf();
      if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
      xhr.send(formData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload error');
      setUpload(prev => ({ ...prev, uploading: false }));
    }
  };

  const uninstall = (skill: SkillInfo) => {
    fetch(
      `/v1/console/skills-manager/skills/uninstall/${encodeURIComponent(skill.skill_id)}/${encodeURIComponent(skill.version)}`,
      { method: 'DELETE' }
    )
      .then(async r => {
        // A refusal carries `detail`, not `message` —
        // reading data.message showed an empty error.
        if (!r.ok) throw new Error(await errorMessage(r));
        return r.json();
      })
      .then(data => {
        if (data.success) {
          setUpload(prev => ({ ...prev, success: data.message }));
          setTimeout(() => {
            fetchSkills();
            setUpload(prev => ({ ...prev, success: null }));
          }, 1500);
        } else {
          setError(data.message || 'Uninstall failed');
        }
      })
      .catch((err: unknown) =>
        setError(err instanceof Error && err.message ? err.message : 'Uninstall failed'));
  };

  return (
    <div className="space-y-6 p-6 max-w-5xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Skill Manager</h1>
          <p className="text-muted-foreground mt-2">
            Install skill packages and manage the versions that are loaded.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchSkills} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Upload Section */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Upload className="w-4 h-4 text-accent" /> Install a skill package
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="sm-skill-id">Skill ID</Label>
              <Input
                id="sm-skill-id"
                value={upload.skillId}
                onChange={(e) => setUpload(prev => ({ ...prev, skillId: e.target.value }))}
                placeholder="e.g. my-awesome-skill"
                disabled={upload.uploading}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sm-version">Version</Label>
              <Input
                id="sm-version"
                value={upload.version}
                onChange={(e) => setUpload(prev => ({ ...prev, version: e.target.value }))}
                placeholder="e.g. 1.0.0"
                disabled={upload.uploading}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sm-file">ZIP file</Label>
              <Input
                id="sm-file"
                type="file"
                accept=".zip"
                onChange={handleFileChange}
                disabled={upload.uploading}
                className="file:mr-3 file:rounded file:border-0 file:bg-muted file:px-2 file:py-1 file:text-xs file:text-foreground"
              />
            </div>
          </div>

          {upload.file && (
            <p className="text-sm text-muted-foreground">
              Selected: <span className="font-mono text-foreground">{upload.file.name}</span>
            </p>
          )}

          {upload.uploading && <Progress value={upload.progress} />}

          <Button
            variant="accent"
            className="w-full"
            onClick={handleUpload}
            disabled={upload.uploading || !upload.file || !upload.skillId || !upload.version}
          >
            {upload.uploading ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Uploading {Math.round(upload.progress)}%</>
            ) : (
              <><Upload className="w-4 h-4 mr-2" /> Install skill</>
            )}
          </Button>

          {upload.success && (
            <Alert>
              <AlertDescription className="flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-accent shrink-0" /> {upload.success}
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-3">
        <h2 className="text-lg font-semibold">
          Installed skills
          {!loading && <span className="ml-2 text-sm font-normal text-muted-foreground">{skills.length}</span>}
        </h2>

        {loading && skills.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading installed skills…
          </div>
        ) : skills.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              <Package className="w-6 h-6 mx-auto mb-2 opacity-60" />
              No skill packages installed yet.
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0">
              <ul className="divide-y">
                {skills.map((skill) => (
                  <li key={`${skill.skill_id}-${skill.version}`}
                      className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <Package className="w-4 h-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-sm truncate">{skill.skill_id}</div>
                      <div className="text-xs text-muted-foreground">v{skill.version}</div>
                    </div>
                    {skill.verified && (
                      <Badge variant="ok" className="gap-1">
                        <ShieldCheck className="w-3 h-3" /> Verified
                      </Badge>
                    )}
                    <Badge variant="outline">{skill.boot_layer}</Badge>
                    <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive"
                            onClick={() => setConfirmUninstall(skill)}>
                      <Trash2 className="w-4 h-4 mr-1" /> Uninstall
                    </Button>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>

      <Dialog open={confirmUninstall !== null} onOpenChange={(o) => !o && setConfirmUninstall(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Uninstall this skill?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            <span className="font-mono text-foreground">
              {confirmUninstall?.skill_id}@{confirmUninstall?.version}
            </span>{' '}
            is removed from this install.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmUninstall(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => {
              const target = confirmUninstall;
              setConfirmUninstall(null);
              if (target) uninstall(target);
            }}>
              Uninstall
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default SkillManager;
