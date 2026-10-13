import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  extractFilenameFromHeader,
  buildExportUrl,
  buildBatchExportRequest,
  isValidZipFile,
} from '@/lib/forge-bundles';

describe('forge-bundles helpers', () => {
  describe('extractFilenameFromHeader', () => {
    it('extracts quoted filename', () => {
      const header = 'attachment; filename="tools-export-2026-10-13.zip"';
      expect(extractFilenameFromHeader(header)).toBe('tools-export-2026-10-13.zip');
    });

    it('extracts unquoted filename', () => {
      const header = 'attachment; filename=skills-export.zip';
      expect(extractFilenameFromHeader(header)).toBe('skills-export.zip');
    });

    it('handles RFC 5987 filename*', () => {
      const header = "attachment; filename*=UTF-8''skills-2026.zip";
      expect(extractFilenameFromHeader(header)).toBe('skills-2026.zip');
    });

    it('returns fallback for empty header', () => {
      expect(extractFilenameFromHeader('')).toBe('export.zip');
    });

    it('returns fallback for missing filename', () => {
      expect(extractFilenameFromHeader('attachment')).toBe('export.zip');
    });

    it('handles whitespace around filename', () => {
      const header = 'attachment; filename= "tools-export.zip" ';
      expect(extractFilenameFromHeader(header)).toBe('tools-export.zip');
    });
  });

  describe('buildExportUrl', () => {
    it('builds single-item export URL for tools', () => {
      expect(buildExportUrl('tools', 'my-tool')).toBe(
        '/v1/console/forge-bundles/export/tools/my-tool'
      );
    });

    it('builds single-item export URL for skills', () => {
      expect(buildExportUrl('skills', 'my-skill')).toBe(
        '/v1/console/forge-bundles/export/skills/my-skill'
      );
    });

    it('URL-encodes special characters in ID', () => {
      expect(buildExportUrl('tools', 'my tool')).toBe(
        '/v1/console/forge-bundles/export/tools/my%20tool'
      );
    });

    it('encodes slashes and other special chars', () => {
      expect(buildExportUrl('skills', 'org/skill@v1')).toBe(
        '/v1/console/forge-bundles/export/skills/org%2Fskill%40v1'
      );
    });
  });

  describe('buildBatchExportRequest', () => {
    it('builds batch export request with correct headers', () => {
      const req = buildBatchExportRequest('tools', ['tool-1', 'tool-2']);

      expect(req.url).toBe('/v1/console/forge-bundles/export');
      expect(req.init.method).toBe('POST');
      expect(req.init.headers).toEqual({
        'Content-Type': 'application/json',
      });
    });

    it('includes type and ids in body', () => {
      const req = buildBatchExportRequest('skills', ['skill-a', 'skill-b']);
      const body = JSON.parse(req.init.body as string);

      expect(body.type).toBe('skills');
      expect(body.ids).toEqual(['skill-a', 'skill-b']);
    });

    it('handles empty array', () => {
      const req = buildBatchExportRequest('plugins', []);
      const body = JSON.parse(req.init.body as string);

      expect(body.ids).toEqual([]);
    });
  });

  describe('isValidZipFile', () => {
    it('accepts .zip extension', () => {
      expect(isValidZipFile('export.zip')).toBe(true);
    });

    it('accepts uppercase .ZIP', () => {
      expect(isValidZipFile('export.ZIP')).toBe(true);
    });

    it('accepts mixed case', () => {
      expect(isValidZipFile('export.Zip')).toBe(true);
    });

    it('rejects non-zip files', () => {
      expect(isValidZipFile('export.tar.gz')).toBe(false);
      expect(isValidZipFile('export.json')).toBe(false);
      expect(isValidZipFile('export')).toBe(false);
    });

    it('handles filenames with multiple dots', () => {
      expect(isValidZipFile('skills-2026.10.13.zip')).toBe(true);
      expect(isValidZipFile('skills.2026.10.13.tar')).toBe(false);
    });
  });
});
