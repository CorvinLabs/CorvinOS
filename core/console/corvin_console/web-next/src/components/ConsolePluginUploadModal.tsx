/**
 * ConsolePluginUploadModal — Plugin Upload Dialog
 *
 * Drag-drop ZIP upload for plugin distribution (Layer 1-4).
 * Validates file + size, calls POST /v1/skills/upload endpoint.
 *
 * Props: isOpen (bool), onClose (fn), onUploadComplete (fn)
 */
import React, { useCallback, useRef, useState } from 'react';
import { Upload, X, AlertCircle } from 'lucide-react';

export interface ConsolePluginUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUploadComplete: (uploadId: string) => void;
}

export const ConsolePluginUploadModal: React.FC<ConsolePluginUploadModalProps> = ({
  isOpen,
  onClose,
  onUploadComplete,
}) => {
  const [dragActive, setDragActive] = useState<boolean>(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateFile = useCallback((file: File): boolean => {
    if (!file.name.endsWith('.zip')) {
      setError('File must be a .zip archive');
      return false;
    }
    const maxSize = 100 * 1024 * 1024; // 100 MB
    if (file.size > maxSize) {
      setError(`File too large (max ${maxSize / 1024 / 1024}MB)`);
      return false;
    }
    setError(null);
    return true;
  }, []);

  const handleDrag = useCallback((e: React.DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
      }
    }
  }, [validateFile]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>): void => {
    const files = e.currentTarget.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
      }
    }
  }, [validateFile]);

  const handleUpload = useCallback(async (): Promise<void> => {
    if (!selectedFile) {
      setError('No file selected');
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const response = await fetch('/v1/skills/upload', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || `HTTP ${response.status}`);
      }

      const data = await response.json();
      const uploadId = data.upload_id || data.id || '';
      setSelectedFile(null);
      onUploadComplete(uploadId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }, [selectedFile, onUploadComplete, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-background border border-border rounded-lg shadow-lg w-full max-w-md p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Upload Plugin</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-muted rounded-md transition-colors"
            aria-label="Close modal"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Drop zone */}
        <div
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
          className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors mb-4 ${
            dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/50'
          }`}
        >
          <Upload className="h-10 w-10 mx-auto mb-2 text-muted-foreground" />
          <p className="text-sm font-medium mb-1">Drag ZIP file here</p>
          <p className="text-xs text-muted-foreground mb-3">or</p>
          <label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              onChange={handleFileSelect}
              className="hidden"
            />
            <span className="inline-block px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm hover:opacity-90 cursor-pointer">
              Browse
            </span>
          </label>
        </div>

        {/* Selected file */}
        {selectedFile && (
          <div className="bg-muted/50 border border-muted rounded p-3 mb-4">
            <p className="text-sm font-medium truncate">{selectedFile.name}</p>
            <p className="text-xs text-muted-foreground">
              {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="bg-destructive/10 border border-destructive rounded p-3 mb-4 flex gap-2">
            <AlertCircle className="h-4 w-4 text-destructive flex-shrink-0 mt-0.5" />
            <p className="text-xs text-destructive">{error}</p>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-border rounded hover:bg-muted transition-colors text-sm"
          >
            Cancel
          </button>
          <button
            onClick={handleUpload}
            disabled={!selectedFile || uploading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
          >
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </div>
    </div>
  );
};
