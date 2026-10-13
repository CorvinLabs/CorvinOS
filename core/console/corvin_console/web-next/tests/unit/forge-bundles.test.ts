import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  extractFilenameFromHeader,
  buildExportUrl,
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
        '/v1/console/forge-bundles/export/tool/my-tool'
      );
    });

    it('builds single-item export URL for skills', () => {
      expect(buildExportUrl('skills', 'my-skill')).toBe(
        '/v1/console/forge-bundles/export/skill/my-skill'
      );
    });

    it('URL-encodes special characters in ID', () => {
      expect(buildExportUrl('tools', 'my tool')).toBe(
        '/v1/console/forge-bundles/export/tool/my%20tool'
      );
    });

    it('encodes slashes and other special chars', () => {
      expect(buildExportUrl('skills', 'org/skill@v1')).toBe(
        '/v1/console/forge-bundles/export/skill/org%2Fskill%40v1'
      );
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
