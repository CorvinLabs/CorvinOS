import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { ExportDialog } from '@/components/forge/ExportDialog';
import { ForgeTool, ForgeSkill, ForgeOSSkill } from '@/types/forge';

const mockTools: ForgeTool[] = [
  { id: 'tool-1', name: 'Tool 1', enabled: true },
  { id: 'tool-2', name: 'Tool 2', enabled: true },
];

const mockSkills: ForgeSkill[] = [
  { id: 'skill-1', name: 'Skill 1', enabled: true },
];

const mockOSSkills: ForgeOSSkill[] = [
  { id: 'os-skill-1', name: 'OS Skill 1', enabled: true },
];

describe('ExportDialog', () => {
  it('renders dialog title', () => {
    render(
      <ExportDialog
        isOpen={true}
        onClose={() => {}}
        tools={mockTools}
        skills={mockSkills}
        osSkills={mockOSSkills}
      />
    );
    expect(screen.getByText('Export Forge Bundle')).toBeInTheDocument();
  });

  it('export button is disabled without valid form data', () => {
    render(
      <ExportDialog
        isOpen={true}
        onClose={() => {}}
        tools={mockTools}
        skills={mockSkills}
        osSkills={mockOSSkills}
      />
    );
    const exportButton = screen.getByRole('button', { name: /Export Bundle/i });
    expect(exportButton).toBeDisabled();
  });

  it('enables export button when form is valid', async () => {
    render(
      <ExportDialog
        isOpen={true}
        onClose={() => {}}
        tools={mockTools}
        skills={mockSkills}
        osSkills={mockOSSkills}
      />
    );

    // Fill form
    await userEvent.type(screen.getByPlaceholderText(/my-tools-v1/), 'test-bundle');
    await userEvent.type(screen.getByPlaceholderText(/Optional description/), 'Test bundle');

    // Select at least one artifact
    const toolCheckbox = screen.getByRole('checkbox', { name: /Tool 1/ });
    await userEvent.click(toolCheckbox);

    const exportButton = screen.getByRole('button', { name: /Export Bundle/i });
    await waitFor(() => expect(exportButton).not.toBeDisabled());
  });

  it('validates semantic version format', async () => {
    render(
      <ExportDialog
        isOpen={true}
        onClose={() => {}}
        tools={mockTools}
        skills={mockSkills}
        osSkills={mockOSSkills}
      />
    );

    const versionInput = screen.getByPlaceholderText('1.0.0');
    await userEvent.clear(versionInput);
    await userEvent.type(versionInput, 'invalid-version');

    const exportButton = screen.getByRole('button', { name: /Export Bundle/i });
    await waitFor(() => expect(exportButton).toBeDisabled());
  });

  it('shows artifact counts when artifacts are selected', async () => {
    render(
      <ExportDialog
        isOpen={true}
        onClose={() => {}}
        tools={mockTools}
        skills={mockSkills}
        osSkills={mockOSSkills}
      />
    );

    const toolCheckbox = screen.getByRole('checkbox', { name: /Tool 1/ });
    await userEvent.click(toolCheckbox);

    expect(screen.getByText(/Tools \(1\/2\)/)).toBeInTheDocument();
  });

  it('calls onClose when cancel is clicked', async () => {
    const onClose = vi.fn();
    render(
      <ExportDialog
        isOpen={true}
        onClose={onClose}
        tools={mockTools}
        skills={mockSkills}
        osSkills={mockOSSkills}
      />
    );

    const cancelButton = screen.getByRole('button', { name: /Cancel/i });
    await userEvent.click(cancelButton);

    expect(onClose).toHaveBeenCalled();
  });
});
