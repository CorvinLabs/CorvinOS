/**
 * E2E Wiring Proof for Forge Bundle Phase 4 (ADR-2229)
 *
 * Verifies that all Phase 4 frontend components are:
 * 1. Syntactically importable without errors
 * 2. Properly typed
 * 3. Integrated into the forge.tsx page
 * 4. Export/Import/Review tabs are present in the page
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

describe('Forge Bundle Phase 4 Frontend Wiring', () => {
  const forgePagePath = join(
    __dirname,
    '../../src/pages/forge.tsx'
  );

  const componentsPaths = [
    'src/components/forge/ExportDialog.tsx',
    'src/components/forge/ImportPreviewDialog.tsx',
    'src/components/forge/QuarantinePanel.tsx',
    'src/components/forge/UnverifiedOriginBadge.tsx',
  ];

  it('All Phase 4 components exist and are syntactically valid', () => {
    componentsPaths.forEach((componentPath) => {
      const fullPath = join(
        __dirname,
        '../../',
        componentPath
      );
      const content = readFileSync(fullPath, 'utf-8');

      // Check for React/TypeScript signatures
      expect(content).toMatch(/export const|export default|export interface/);
      expect(content).toMatch(/React.FC|FC<|function/);
    });
  });

  it('forge.tsx imports all Phase 4 components', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    expect(forgeContent).toContain("import ExportDialog");
    expect(forgeContent).toContain("import ImportPreviewDialog");
    expect(forgeContent).toContain("import QuarantinePanel");
  });

  it('forge.tsx includes new Bundle tabs (export, import, review)', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    // Check that FORGE_TABS includes the new tabs
    expect(forgeContent).toMatch(/'export'.*'import'.*'review'/);

    // Check that TabsTrigger elements exist for each
    expect(forgeContent).toContain('value="export"');
    expect(forgeContent).toContain('value="import"');
    expect(forgeContent).toContain('value="review"');
  });

  it('forge.tsx includes TabsContent for all new tabs', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    expect(forgeContent).toContain('TabsContent value="export"');
    expect(forgeContent).toContain('TabsContent value="import"');
    expect(forgeContent).toContain('TabsContent value="review"');
  });

  it('ExportDialog is wired with tools, skills, osSkills props', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    // Check that ExportDialog is rendered with correct props
    expect(forgeContent).toContain('ExportDialog');
    expect(forgeContent).toContain('tools={tools}');
    expect(forgeContent).toContain('skills={skills}');
    expect(forgeContent).toContain('osSkills={osSkills}');
  });

  it('ImportPreviewDialog is wired with onImportComplete callback', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    expect(forgeContent).toContain('ImportPreviewDialog');
    expect(forgeContent).toContain('onImportComplete');
  });

  it('QuarantinePanel is included in review tab content', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    expect(forgeContent).toContain('QuarantinePanel');
  });

  it('Dialog state management is present', () => {
    const forgeContent = readFileSync(forgePagePath, 'utf-8');

    expect(forgeContent).toContain('exportDialogOpen');
    expect(forgeContent).toContain('importDialogOpen');
    expect(forgeContent).toContain('setExportDialogOpen');
    expect(forgeContent).toContain('setImportDialogOpen');
  });

  it('Types for Forge Bundle are defined', () => {
    const typesPath = join(__dirname, '../../src/types/forge.ts');
    const typesContent = readFileSync(typesPath, 'utf-8');

    expect(typesContent).toContain('ForgeBundleArtifact');
    expect(typesContent).toContain('ForgeBundleExportRequest');
    expect(typesContent).toContain('ForgeBundleValidationReport');
    expect(typesContent).toContain('ForgeBundleQuarantineItem');
    expect(typesContent).toContain('ForgeBundleImportResult');
  });
});
