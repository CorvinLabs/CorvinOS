/**
 * Skill Upload Tab — local ZIP install.
 *
 * POSTs multipart {file, skill_id, version} to
 * /v1/console/skills-manager/skills/install (routes/skill_manager.py), which
 * requires all three fields. It used to POST only the file to /v1/skills/upload
 * (served by no router) and then read a `skill_id` the response never had.
 */

import { useCallback, useState } from 'react';
import { Upload, FileCheck } from 'lucide-react';
import { useSkillManager } from '../SkillManagerContext';

export function SkillUploadTab() {
  const { installZip } = useSkillManager();
  const [skillId, setSkillId] = useState('');
  const [version, setVersion] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const validateFile = (file: File): boolean => {
    if (!file.name.endsWith('.zip')) {
      setError('File must be a .zip archive');
      return false;
    }
    if (file.size > 100 * 1024 * 1024) {
      // 100 MB limit
      setError('File is too large (max 100 MB)');
      return false;
    }
    setError(null);
    return true;
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
      }
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
      }
    }
  };

  const handleUpload = useCallback(async () => {
    if (!selectedFile) {
      setError('No file selected');
      return;
    }
    if (!skillId.trim() || !version.trim()) {
      setError('Enter the skill id and version');
      return;
    }

    setUploading(true);
    setError(null);
    try {
      if (await installZip(selectedFile, skillId.trim(), version.trim())) {
        setSelectedFile(null);
        setSkillId('');
        setVersion('');
      }
    } finally {
      setUploading(false);
    }
  }, [selectedFile, skillId, version, installZip]);

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-4">Upload Custom Skill</h2>
        <p className="text-sm text-muted-foreground mb-6">
          Upload a .zip file containing your skill package, with its skill id and version. The package is validated and installed.
        </p>
      </div>

      {/* Drop zone */}
      <div
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer ${
          dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/50 hover:border-primary/50'
        }`}
      >
        <Upload className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
        <p className="font-medium mb-2">Drag and drop your skill .zip file here</p>
        <p className="text-sm text-muted-foreground mb-4">or</p>
        <label>
          <input
            type="file"
            accept=".zip"
            onChange={handleFileSelect}
            className="hidden"
          />
          <span className="px-4 py-2 bg-primary text-primary-foreground rounded hover:opacity-90 cursor-pointer inline-block">
            Browse files
          </span>
        </label>
      </div>

      {/* Selected file info */}
      {selectedFile && (
        <div className="bg-muted/50 border border-muted rounded p-4 space-y-2">
          <div className="flex items-center gap-2">
            <FileCheck className="h-5 w-5 text-primary" />
            <div>
              <p className="font-medium">{selectedFile.name}</p>
              <p className="text-sm text-muted-foreground">
                {(selectedFile.size / 1024).toFixed(2)} KB
              </p>
            </div>
          </div>
          <button
            onClick={() => setSelectedFile(null)}
            className="text-sm text-destructive hover:underline"
          >
            Clear
          </button>
        </div>
      )}

      {/* Skill identity — required by the install route */}
      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm space-y-1">
          <span className="font-medium">Skill id</span>
          <input
            value={skillId}
            onChange={(e) => setSkillId(e.target.value)}
            className="w-full border rounded px-2 py-1 bg-background"
            placeholder="my-skill"
          />
        </label>
        <label className="text-sm space-y-1">
          <span className="font-medium">Version</span>
          <input
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            className="w-full border rounded px-2 py-1 bg-background"
            placeholder="1.0.0"
          />
        </label>
      </div>

      {/* Error message */}
      {error && (
        <div className="bg-destructive/10 border border-destructive rounded p-4 text-destructive">
          <p className="font-medium">Error: {error}</p>
        </div>
      )}

      {/* Upload button */}
      <button
        onClick={handleUpload}
        disabled={!selectedFile || !skillId.trim() || !version.trim() || uploading}
        className="w-full px-4 py-2 bg-primary text-primary-foreground rounded font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90"
      >
        {uploading ? 'Uploading…' : 'Upload & Install'}
      </button>
    </div>
  );
}
