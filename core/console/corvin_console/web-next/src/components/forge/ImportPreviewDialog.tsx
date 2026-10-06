/**
 * ImportPreviewDialog — Forge Bundle Import Preview & Validation
 *
 * Handles file upload (drag-drop + file input), validates bundle, and shows
 * validation report before importing. All imported artifacts carry the
 * UnverifiedOriginBadge until reviewed in the Quarantine panel.
 *
 * ADR-2229 Phase 4: Frontend import component
 */
import React, { useState, useRef, useCallback } from 'react';
import { Upload, AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { UnverifiedOriginBadge } from './UnverifiedOriginBadge';
import { ForgeBundleValidationReport } from '@/types/forge';

interface ImportPreviewDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete?: () => void;
}

interface ImportState {
  status: 'idle' | 'validating' | 'importing' | 'success' | 'error';
  error?: string;
  report?: ForgeBundleValidationReport;
}

export const ImportPreviewDialog: React.FC<ImportPreviewDialogProps> = ({
  isOpen,
  onClose,
  onImportComplete,
}) => {
  const [dragActive, setDragActive] = useState(false);
  const [bundleFile, setBundleFile] = useState<File | null>(null);
  const [importState, setImportState] = useState<ImportState>({ status: 'idle' });
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Validation
  const validateFile = useCallback((file: File): boolean => {
    if (!file.name.endsWith('.zip')) {
      setImportState({
        status: 'error',
        error: 'File must be a .zip archive',
      });
      return false;
    }
    const maxSize = 50 * 1024 * 1024; // 50 MB
    if (file.size > maxSize) {
      setImportState({
        status: 'error',
        error: `File too large (max ${maxSize / 1024 / 1024}MB)`,
      });
      return false;
    }
    return true;
  }, []);

  // Drag handlers
  const handleDrag = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setBundleFile(file);
        void handleValidate(file);
      }
    }
  }, [validateFile]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setBundleFile(file);
        void handleValidate(file);
      }
    }
  }, [validateFile]);

  // Validation
  const handleValidate = useCallback(async (file: File) => {
    setImportState({ status: 'validating' });

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch('/v1/console/forge-bundles/validate', {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
      });

      if (!response.ok) {
        throw new Error(`Validation failed: ${response.statusText}`);
      }

      const report: ForgeBundleValidationReport = await response.json();
      setImportState({ status: 'idle', report });
    } catch (error) {
      setImportState({
        status: 'error',
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }, []);

  // Import
  const handleImport = useCallback(async () => {
    if (!bundleFile) return;

    setImportState((prev) => ({ ...prev, status: 'importing' }));

    try {
      const formData = new FormData();
      formData.append('file', bundleFile);

      const response = await fetch('/v1/console/forge-bundles/import', {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
      });

      if (!response.ok) {
        throw new Error(`Import failed: ${response.statusText}`);
      }

      setImportState({ status: 'success' });
      setTimeout(() => {
        onImportComplete?.();
        onClose();
        setBundleFile(null);
        setImportState({ status: 'idle' });
      }, 1500);
    } catch (error) {
      setImportState({
        status: 'error',
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }, [bundleFile, onImportComplete, onClose]);

  // Reset on close
  const handleClose = () => {
    if (importState.status !== 'validating' && importState.status !== 'importing') {
      setBundleFile(null);
      setImportState({ status: 'idle' });
      onClose();
    }
  };

  const canImport =
    importState.report?.status === 'valid' &&
    importState.status !== 'importing' &&
    importState.status !== 'validating';

  return (
    <Dialog open={isOpen} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-screen overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Import Forge Bundle</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* File Drop Zone */}
          {!bundleFile || importState.status === 'error' ? (
            <>
              <div
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
                className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                  dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground'
                }`}
              >
                <Upload className="w-12 h-12 mx-auto mb-3 text-muted-foreground" />
                <p className="font-medium mb-1">Drag and drop your bundle here</p>
                <p className="text-sm text-muted-foreground mb-4">or click to browse</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip"
                  onChange={handleFileSelect}
                  className="hidden"
                  disabled={importState.status === 'validating'}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={importState.status === 'validating'}
                >
                  Choose File
                </Button>
              </div>

              {importState.status === 'validating' && (
                <div className="flex gap-2 items-center p-3 bg-blue-50 text-blue-700 rounded-md">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-sm">Validating bundle...</span>
                </div>
              )}

              {importState.status === 'error' && (
                <div className="flex gap-2 items-center p-3 bg-destructive/10 text-destructive rounded-md">
                  <AlertCircle className="w-4 h-4" />
                  <span className="text-sm">{importState.error}</span>
                </div>
              )}
            </>
          ) : (
            <>
              {/* Selected File */}
              <div className="flex items-center justify-between p-3 bg-muted rounded-md">
                <span className="text-sm font-medium">{bundleFile.name}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setBundleFile(null);
                    setImportState({ status: 'idle' });
                  }}
                  disabled={importState.status === 'importing'}
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>

              {/* Validation Report */}
              {importState.report && (
                <div className="space-y-3">
                  {/* Overall Status */}
                  <div className="flex gap-2 items-start p-3 rounded-md bg-muted">
                    {importState.report.status === 'valid' ? (
                      <>
                        <CheckCircle2 className="w-4 h-4 text-green-600 mt-0.5" />
                        <div>
                          <p className="text-sm font-medium">Bundle is valid</p>
                          <p className="text-xs text-muted-foreground">
                            {importState.report.artifacts.length} artifact(s) found
                          </p>
                        </div>
                      </>
                    ) : (
                      <>
                        <AlertCircle className="w-4 h-4 text-destructive mt-0.5" />
                        <div>
                          <p className="text-sm font-medium">Bundle validation failed</p>
                          {importState.report.errors && importState.report.errors.length > 0 && (
                            <ul className="text-xs text-destructive mt-1 list-disc list-inside">
                              {importState.report.errors.map((err, i) => (
                                <li key={i}>{err}</li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </>
                    )}
                  </div>

                  {/* Artifacts */}
                  {importState.report.artifacts.length > 0 && (
                    <Card>
                      <CardContent className="pt-4">
                        <h4 className="font-medium text-sm mb-3">Artifacts</h4>
                        <div className="space-y-2 max-h-48 overflow-y-auto">
                          {importState.report.artifacts.map((artifact, i) => (
                            <div key={i} className="flex items-start justify-between gap-2 text-sm">
                              <div className="flex-1">
                                <div className="font-medium">{artifact.name}</div>
                                <div className="text-xs text-muted-foreground">{artifact.type}</div>
                              </div>
                              <div className="flex gap-2 items-center">
                                <UnverifiedOriginBadge />
                                {artifact.status === 'accepted' && (
                                  <Badge variant="outline" className="bg-green-50 text-green-700 border-green-300">
                                    Ready
                                  </Badge>
                                )}
                                {artifact.status === 'rejected' && (
                                  <Badge variant="outline" className="bg-destructive/10 text-destructive">
                                    Rejected
                                  </Badge>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* Warnings */}
                  {(importState.report.unchecked_refs?.length || 0) > 0 && (
                    <div className="text-xs p-2 bg-amber-50 text-amber-700 rounded-md">
                      <p className="font-medium mb-1">Unchecked references:</p>
                      <p>{importState.report.unchecked_refs?.join(', ')}</p>
                    </div>
                  )}

                  {(importState.report.unscanned_files?.length || 0) > 0 && (
                    <div className="text-xs p-2 bg-amber-50 text-amber-700 rounded-md">
                      <p className="font-medium mb-1">Unscanned files (informational):</p>
                      <p>{importState.report.unscanned_files?.join(', ')}</p>
                    </div>
                  )}
                </div>
              )}

              {importState.status === 'importing' && (
                <div className="flex gap-2 items-center p-3 bg-blue-50 text-blue-700 rounded-md">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-sm">Importing bundle...</span>
                </div>
              )}

              {importState.status === 'success' && (
                <div className="flex gap-2 items-center p-3 bg-green-50 text-green-700 rounded-md">
                  <CheckCircle2 className="w-4 h-4" />
                  <span className="text-sm">Bundle imported successfully!</span>
                </div>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose} disabled={importState.status === 'importing'}>
            Cancel
          </Button>
          {bundleFile && (
            <Button onClick={handleImport} disabled={!canImport || importState.status === 'importing'}>
              {importState.status === 'importing' ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Importing...
                </>
              ) : (
                <>
                  <Upload className="w-4 h-4 mr-2" />
                  Import Bundle
                </>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ImportPreviewDialog;
