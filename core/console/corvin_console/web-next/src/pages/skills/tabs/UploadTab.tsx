/**
 * UploadTab — Staged Plugin Management
 *
 * Lists pending uploads, shows validation status, approve/reject.
 * Polls GET /v1/skills/uploads every 2 seconds.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, AlertCircle, Trash2, Upload } from 'lucide-react';
import { ConsolePluginUploadModal } from '../../../components/ConsolePluginUploadModal';

interface StagedUpload {
  upload_id: string;
  file_name: string;
  file_size: number;
  upload_timestamp: string;
  status: 'pending_approval' | 'approved' | 'rejected';
  validation_errors: string[];
}

export const UploadTab: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [uploads, setUploads] = useState<StagedUpload[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [approvingId, setApprovingId] = useState<string | null>(null);

  const fetchUploads = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch('/v1/skills/uploads');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setUploads(data.uploads || []);
    } catch (err) {
      console.error('Failed to fetch uploads:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUploads();
    const interval = setInterval(fetchUploads, 2000);
    return () => clearInterval(interval);
  }, [fetchUploads]);

  const handleApprove = useCallback(async (uploadId: string): Promise<void> => {
    setApprovingId(uploadId);
    try {
      const response = await fetch(`/v1/skills/uploads/${uploadId}/approve`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setUploads((prev) => prev.filter((u) => u.upload_id !== uploadId));
    } catch (err) {
      console.error('Approval failed:', err);
    } finally {
      setApprovingId(null);
    }
  }, []);

  const handleReject = useCallback(async (uploadId: string): Promise<void> => {
    try {
      const response = await fetch(`/v1/skills/uploads/${uploadId}/reject`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setUploads((prev) => prev.filter((u) => u.upload_id !== uploadId));
    } catch (err) {
      console.error('Reject failed:', err);
    }
  }, []);

  const handleUploadComplete = useCallback((): void => {
    setIsModalOpen(false);
    fetchUploads();
  }, [fetchUploads]);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Upload Management</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Manage pending plugin uploads and staging approvals
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="px-4 py-2 bg-primary text-primary-foreground rounded hover:opacity-90 flex items-center gap-2"
        >
          <Upload className="h-4 w-4" />
          Upload New Plugin
        </button>
      </div>

      {/* Uploads list */}
      {loading ? (
        <div className="text-center py-8 text-muted-foreground">Loading uploads…</div>
      ) : uploads.length === 0 ? (
        <div className="text-center py-8 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No pending uploads</p>
        </div>
      ) : (
        <div className="space-y-3">
          {uploads.map((upload) => (
            <div
              key={upload.upload_id}
              className="border border-border rounded-lg p-4 hover:bg-muted/30 transition-colors"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{upload.file_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {(upload.file_size / 1024 / 1024).toFixed(2)} MB · {upload.upload_timestamp}
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-2">
                  {upload.validation_errors.length === 0 ? (
                    <CheckCircle2 className="h-5 w-5 text-green-600" />
                  ) : (
                    <AlertCircle className="h-5 w-5 text-amber-600" />
                  )}
                  <span className="text-xs font-medium">{upload.status}</span>
                </div>
              </div>

              {/* Validation errors */}
              {upload.validation_errors.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded p-2 mb-3">
                  <ul className="text-xs text-amber-900 list-disc list-inside">
                    {upload.validation_errors.map((err, idx) => (
                      <li key={idx}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => handleReject(upload.upload_id)}
                  className="px-3 py-1.5 text-xs border border-destructive text-destructive rounded hover:bg-destructive/10 transition-colors"
                >
                  <Trash2 className="h-3.5 w-3.5 inline mr-1" />
                  Reject
                </button>
                <button
                  onClick={() => handleApprove(upload.upload_id)}
                  disabled={
                    upload.validation_errors.length > 0 || approvingId === upload.upload_id
                  }
                  className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
                >
                  {approvingId === upload.upload_id ? 'Approving…' : 'Approve'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal */}
      <ConsolePluginUploadModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onUploadComplete={handleUploadComplete}
      />
    </div>
  );
};
