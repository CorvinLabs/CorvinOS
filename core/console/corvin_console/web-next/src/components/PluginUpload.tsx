/**
 * Plugin Installation Flow UI Component
 * Stages: Upload → Verify → Audit → Install → Enable → Health Check
 *
 * Features:
 * - File input + drag-and-drop
 * - SHA256 checksum verification (optional)
 * - Trust level display
 * - Progress tracking
 * - Error handling (fail-closed)
 */

import React, { useState, useRef } from 'react';
import { Upload, Loader, CheckCircle, AlertCircle, FileArchive } from 'lucide-react';

interface UploadProgress {
  stage: 'idle' | 'uploading' | 'verifying' | 'installing' | 'enabling' | 'health_check' | 'complete' | 'error';
  percentage: number;
  message: string;
}

interface UploadResponse {
  plugin_id: string;
  version: string;
  status: 'installed' | 'installed_pending_enable' | 'error';
  message: string;
  trust_verdict?: 'vetted' | 'community' | 'forged' | null;
  requires_consent: boolean;
  health_check_passed?: boolean | null;
}

export interface PluginUploadProps {
  onSuccess?: (response: UploadResponse) => void;
  onError?: (error: string) => void;
  autoEnable?: boolean;
  className?: string;
}

export const PluginUpload: React.FC<PluginUploadProps> = ({
  onSuccess,
  onError,
  autoEnable = false,
  className = '',
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [progress, setProgress] = useState<UploadProgress>({
    stage: 'idle',
    percentage: 0,
    message: '',
  });
  const [checksum, setChecksum] = useState('');
  const [showChecksumInput, setShowChecksumInput] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const stageNames: Record<UploadProgress['stage'], string> = {
    idle: 'Ready',
    uploading: 'Uploading',
    verifying: 'Verifying',
    installing: 'Installing',
    enabling: 'Enabling',
    health_check: 'Health Check',
    complete: 'Complete',
    error: 'Error',
  };

  const stagePercentages: Record<UploadProgress['stage'], number> = {
    idle: 0,
    uploading: 10,
    verifying: 30,
    installing: 60,
    enabling: 80,
    health_check: 90,
    complete: 100,
    error: 100,
  };

  const handleFileSelect = async (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const file = files[0];

    if (!file.name.endsWith('.tar.gz') && !file.name.endsWith('.tgz')) {
      const error = 'Plugin must be a .tar.gz archive';
      setProgress({ stage: 'error', percentage: 100, message: error });
      onError?.(error);
      return;
    }

    setSelectedFile(file);
    await performUpload(file);
  };

  const performUpload = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);

    if (checksum) {
      formData.append('checksum', checksum);
    }
    if (autoEnable) {
      formData.append('auto_enable', 'true');
    }

    try {
      setProgress({ stage: 'uploading', percentage: 10, message: 'Uploading file...' });

      const response = await fetch('/v1/console/plugins/upload', {
        method: 'POST',
        body: formData,
        headers: {
          'X-CSRF-Token': (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement)?.content || '',
        },
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || `Upload failed (${response.status})`);
      }

      setProgress({ stage: 'verifying', percentage: 30, message: 'Verifying manifest...' });

      const data: UploadResponse = await response.json();

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      setProgress({ stage: 'installing', percentage: 60, message: 'Installing plugin...' });
      setProgress({ stage: 'enabling', percentage: 80, message: 'Enabling plugin...' });

      if (data.health_check_passed) {
        setProgress({ stage: 'health_check', percentage: 90, message: 'Running health check...' });
      }

      setProgress({
        stage: 'complete',
        percentage: 100,
        message: data.message,
      });

      onSuccess?.(data);
      setSelectedFile(null);
      setChecksum('');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Upload failed';
      setProgress({
        stage: 'error',
        percentage: 100,
        message: errorMessage,
      });
      onError?.(errorMessage);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (progress.stage !== 'idle' && progress.stage !== 'error') {
      return; // Upload already in progress
    }

    handleFileSelect(e.dataTransfer.files);
  };

  const getTrustIcon = (verdict?: string | null) => {
    switch (verdict) {
      case 'vetted':
        return <CheckCircle className="w-5 h-5 text-green-600" />;
      case 'community':
        return <AlertCircle className="w-5 h-5 text-yellow-600" />;
      case 'forged':
        return <AlertCircle className="w-5 h-5 text-red-600" />;
      default:
        return null;
    }
  };

  const isUploading = ['uploading', 'verifying', 'installing', 'enabling', 'health_check'].includes(progress.stage);

  return (
    <div className={`space-y-4 ${className}`}>
      {/* File Input + Drag Drop Area */}
      {progress.stage === 'idle' || progress.stage === 'error' ? (
        <div
          className={`
            relative border-2 border-dashed rounded-lg p-8 text-center transition-colors
            ${isDragging
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
              : 'border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900/20'
            }
            ${progress.stage === 'error' ? 'border-red-300 dark:border-red-600 bg-red-50 dark:bg-red-900/20' : ''}
          `}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".tar.gz,.tgz"
            onChange={(e) => handleFileSelect(e.target.files)}
            disabled={isUploading}
            className="hidden"
          />

          <div className="flex flex-col items-center gap-3">
            {selectedFile && progress.stage === 'idle' ? (
              <>
                <FileArchive className="w-12 h-12 text-blue-500" />
                <div>
                  <p className="font-semibold text-gray-900 dark:text-white">
                    {selectedFile.name}
                  </p>
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                </div>
                <div className="flex gap-2 mt-4">
                  <button
                    onClick={() => performUpload(selectedFile)}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                  >
                    Install
                  </button>
                  <button
                    onClick={() => {
                      setSelectedFile(null);
                      if (fileInputRef.current) fileInputRef.current.value = '';
                    }}
                    className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <>
                <Upload className="w-12 h-12 text-gray-400" />
                <div>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploading}
                    className="font-semibold text-blue-600 hover:text-blue-700 disabled:text-gray-400"
                  >
                    Click to upload
                  </button>
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    or drag and drop a .tar.gz plugin
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}

      {/* Checksum Input (Optional) */}
      {!isUploading && (
        <div className="space-y-2">
          <button
            onClick={() => setShowChecksumInput(!showChecksumInput)}
            className="text-sm text-blue-600 hover:text-blue-700"
          >
            {showChecksumInput ? 'Hide' : 'Add'} SHA256 checksum
          </button>

          {showChecksumInput && (
            <input
              type="text"
              placeholder="SHA256 checksum (optional)"
              value={checksum}
              onChange={(e) => setChecksum(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded text-sm"
              disabled={isUploading}
            />
          )}
        </div>
      )}

      {/* Progress Display */}
      {progress.stage !== 'idle' && (
        <div className="space-y-3 p-4 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
          {/* Progress Bar */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-900 dark:text-white">
                {stageNames[progress.stage]}
              </span>
              <span className="text-sm text-gray-600 dark:text-gray-400">
                {progress.percentage}%
              </span>
            </div>
            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all duration-300 ${
                  progress.stage === 'error'
                    ? 'bg-red-500'
                    : progress.stage === 'complete'
                      ? 'bg-green-500'
                      : 'bg-blue-500'
                }`}
                style={{ width: `${progress.percentage}%` }}
              />
            </div>
          </div>

          {/* Status Message */}
          <div className="flex items-start gap-2">
            {progress.stage === 'error' ? (
              <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            ) : progress.stage === 'complete' ? (
              <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
            ) : (
              <Loader className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5 animate-spin" />
            )}
            <p
              className={`text-sm ${
                progress.stage === 'error'
                  ? 'text-red-700 dark:text-red-400'
                  : progress.stage === 'complete'
                    ? 'text-green-700 dark:text-green-400'
                    : 'text-gray-700 dark:text-gray-300'
              }`}
            >
              {progress.message}
            </p>
          </div>
        </div>
      )}

      {/* Trust Verdict Display */}
      {progress.stage === 'complete' && (
        <div className="space-y-3 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900 dark:text-white">
              Trust Status:
            </span>
            {getTrustIcon(progress.message)}
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {progress.message === 'community'
                ? 'Community Plugin (Unreviewed)'
                : progress.message === 'vetted'
                  ? 'Verified Plugin'
                  : 'Unknown'}
            </span>
          </div>
          {progress.message === 'community' && (
            <p className="text-xs text-gray-600 dark:text-gray-400">
              This plugin is unreviewed third-party code. Review its permissions before enabling.
            </p>
          )}
        </div>
      )}

      {/* Auto-Enable Toggle */}
      {!isUploading && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={autoEnable}
            onChange={(e) => {
              // Would update parent state in actual implementation
            }}
            disabled={isUploading}
            className="rounded"
          />
          <span className="text-gray-700 dark:text-gray-300">
            Automatically enable after installation
          </span>
        </label>
      )}
    </div>
  );
};

export default PluginUpload;
