/**
 * ExportDialog — Forge Bundle Export UI
 *
 * Modal dialog for exporting selected artifacts (tools, skills, layers, plugins)
 * into a reusable Forge Bundle (.zip file).
 *
 * ADR-2229 Phase 4: Frontend export component
 */
import React, { useState, useMemo, useCallback } from 'react';
import { Download, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent } from '@/components/ui/card';
import { ForgeBundleArtifact, ForgeTool, ForgeSkill, ForgeOSSkill } from '@/types/forge';

interface ExportDialogProps {
  isOpen: boolean;
  onClose: () => void;
  tools: ForgeTool[];
  skills: ForgeSkill[];
  osSkills: ForgeOSSkill[];
}

interface SelectedArtifacts {
  tools: Set<string>;
  skills: Set<string>;
  osSkills: Set<string>;
}

interface ExportState {
  status: 'idle' | 'loading' | 'success' | 'error';
  error?: string;
}

/**
 * Validates semantic versioning format (e.g. "1.0.0")
 */
function isSemverValid(version: string): boolean {
  return /^\d+\.\d+\.\d+/.test(version);
}

export const ExportDialog: React.FC<ExportDialogProps> = ({
  isOpen,
  onClose,
  tools,
  skills,
  osSkills,
}) => {
  const [bundleId, setBundleId] = useState('');
  const [bundleVersion, setBundleVersion] = useState('1.0.0');
  const [description, setDescription] = useState('');
  const [selected, setSelected] = useState<SelectedArtifacts>({
    tools: new Set(),
    skills: new Set(),
    osSkills: new Set(),
  });
  const [exportState, setExportState] = useState<ExportState>({ status: 'idle' });

  // Validation
  const isFormValid = useMemo(() => {
    const hasArtifacts =
      selected.tools.size > 0 || selected.skills.size > 0 || selected.osSkills.size > 0;
    const isIdValid = bundleId.length > 0 && bundleId.length <= 255;
    const isVersionValid = isSemverValid(bundleVersion);
    const isDescValid = description.length <= 2000;
    return hasArtifacts && isIdValid && isVersionValid && isDescValid;
  }, [bundleId, bundleVersion, description, selected]);

  // Toggle selections
  const toggleTool = useCallback((id: string) => {
    setSelected((prev) => {
      const newSelected = { ...prev };
      if (newSelected.tools.has(id)) {
        newSelected.tools.delete(id);
      } else {
        newSelected.tools.add(id);
      }
      return newSelected;
    });
  }, []);

  const toggleSkill = useCallback((id: string) => {
    setSelected((prev) => {
      const newSelected = { ...prev };
      if (newSelected.skills.has(id)) {
        newSelected.skills.delete(id);
      } else {
        newSelected.skills.add(id);
      }
      return newSelected;
    });
  }, []);

  const toggleOSSkill = useCallback((id: string) => {
    setSelected((prev) => {
      const newSelected = { ...prev };
      if (newSelected.osSkills.has(id)) {
        newSelected.osSkills.delete(id);
      } else {
        newSelected.osSkills.add(id);
      }
      return newSelected;
    });
  }, []);

  // Handle export
  const handleExport = useCallback(async () => {
    setExportState({ status: 'loading' });

    try {
      const artifacts: ForgeBundleArtifact[] = [
        ...Array.from(selected.tools).map((id) => ({
          type: 'tool' as const,
          id,
          version: bundleVersion,
          name: tools.find((t) => t.id === id)?.name || id,
        })),
        ...Array.from(selected.skills).map((id) => ({
          type: 'skill' as const,
          id,
          version: bundleVersion,
          name: skills.find((s) => s.id === id)?.name || id,
        })),
        ...Array.from(selected.osSkills).map((id) => ({
          type: 'plugin' as const,
          id,
          version: bundleVersion,
          name: osSkills.find((os) => os.id === id)?.name || id,
        })),
      ];

      const formData = new FormData();
      formData.append('bundle_id', bundleId);
      formData.append('bundle_version', bundleVersion);
      formData.append('description', description);
      formData.append('artifacts', JSON.stringify(artifacts));

      const response = await fetch('/v1/console/forge-bundles/export', {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
      });

      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }

      // Trigger download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${bundleId}-${bundleVersion}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);

      setExportState({ status: 'success' });
      setTimeout(() => {
        onClose();
        setExportState({ status: 'idle' });
      }, 1500);
    } catch (error) {
      setExportState({
        status: 'error',
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }, [bundleId, bundleVersion, description, selected, tools, skills, osSkills, onClose]);

  // Reset on close
  const handleClose = () => {
    if (exportState.status !== 'loading') {
      setBundleId('');
      setBundleVersion('1.0.0');
      setDescription('');
      setSelected({ tools: new Set(), skills: new Set(), osSkills: new Set() });
      setExportState({ status: 'idle' });
      onClose();
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-screen overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Export Forge Bundle</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* Metadata Section */}
          <div className="space-y-2">
            <Label htmlFor="bundle-id">Bundle ID *</Label>
            <Input
              id="bundle-id"
              placeholder="e.g., my-tools-v1"
              value={bundleId}
              onChange={(e) => setBundleId(e.target.value)}
              disabled={exportState.status === 'loading'}
              maxLength={255}
            />
            <p className="text-xs text-muted-foreground">{bundleId.length}/255 characters</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="bundle-version">Bundle Version *</Label>
            <Input
              id="bundle-version"
              placeholder="1.0.0"
              value={bundleVersion}
              onChange={(e) => setBundleVersion(e.target.value)}
              disabled={exportState.status === 'loading'}
            />
            {!isSemverValid(bundleVersion) && bundleVersion.length > 0 && (
              <p className="text-xs text-destructive">Must follow semantic versioning (e.g., 1.0.0)</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="bundle-desc">Description</Label>
            <textarea
              id="bundle-desc"
              placeholder="Optional description of what's in this bundle..."
              className="w-full h-20 px-3 py-2 border rounded-md text-sm"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={exportState.status === 'loading'}
              maxLength={2000}
            />
            <p className="text-xs text-muted-foreground">{description.length}/2000 characters</p>
          </div>

          {/* Artifact Selection */}
          <div className="space-y-3">
            <h3 className="font-semibold text-sm">Select Artifacts to Export</h3>

            {/* Tools */}
            {tools.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <h4 className="font-medium text-sm mb-3">
                    Tools ({selected.tools.size}/{tools.length})
                  </h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {tools.map((tool) => (
                      <label key={tool.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selected.tools.has(tool.id)}
                          onChange={() => toggleTool(tool.id)}
                          disabled={exportState.status === 'loading'}
                        />
                        <span>{tool.name}</span>
                      </label>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Skills */}
            {skills.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <h4 className="font-medium text-sm mb-3">
                    Skills ({selected.skills.size}/{skills.length})
                  </h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {skills.map((skill) => (
                      <label key={skill.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selected.skills.has(skill.id)}
                          onChange={() => toggleSkill(skill.id)}
                          disabled={exportState.status === 'loading'}
                        />
                        <span>{skill.name}</span>
                      </label>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* OS-Skills */}
            {osSkills.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <h4 className="font-medium text-sm mb-3">
                    OS-Skills ({selected.osSkills.size}/{osSkills.length})
                  </h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {osSkills.map((osSkill) => (
                      <label key={osSkill.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selected.osSkills.has(osSkill.id)}
                          onChange={() => toggleOSSkill(osSkill.id)}
                          disabled={exportState.status === 'loading'}
                        />
                        <span>{osSkill.name}</span>
                      </label>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Status Messages */}
          {exportState.status === 'success' && (
            <div className="flex gap-2 items-center p-3 bg-green-50 text-green-700 rounded-md">
              <CheckCircle2 className="w-4 h-4" />
              <span className="text-sm">Bundle exported successfully!</span>
            </div>
          )}

          {exportState.status === 'error' && (
            <div className="flex gap-2 items-center p-3 bg-destructive/10 text-destructive rounded-md">
              <AlertCircle className="w-4 h-4" />
              <span className="text-sm">{exportState.error}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose} disabled={exportState.status === 'loading'}>
            Cancel
          </Button>
          <Button
            onClick={handleExport}
            disabled={!isFormValid || exportState.status === 'loading'}
          >
            {exportState.status === 'loading' ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Exporting...
              </>
            ) : (
              <>
                <Download className="w-4 h-4 mr-2" />
                Export Bundle
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ExportDialog;
