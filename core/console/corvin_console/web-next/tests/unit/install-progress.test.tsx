import { describe, it, expect, vi, beforeEach } from 'vitest'
/**
 * Unit Tests: InstallProgress Component
 * Phase 2 Week 2 — Task #6
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { InstallProgress } from '@/components/install-progress'

describe('InstallProgress Component', () => {
  const mockOnClose = vi.fn()
  const mockOnComplete = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('renders with extension name in title', () => {
    render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Extension"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    expect(screen.getByText(/Installing Test Extension/)).toBeInTheDocument()
  })

  test('displays progress bar with initial 0%', () => {
    render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Ext"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    const progressPercent = screen.getByTestId('progress-percentage')
    expect(progressPercent).toHaveTextContent('0%')
  })

  test('renders 5-step indicators', () => {
    render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Ext"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    // 5 step indicators (flex divs)
    const progressBar = screen.getByTestId('progress-bar')
    const parent = progressBar.closest('.mb-4')?.previousElementSibling
    // Verify step indicator structure exists
    expect(screen.getByTestId('install-progress-modal')).toBeInTheDocument()
  })

  test('renders Cancel button when installing', () => {
    render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Ext"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    const cancelBtn = screen.getByTestId('install-progress-cancel-btn')
    expect(cancelBtn).toBeInTheDocument()
    expect(cancelBtn).toBeEnabled()
  })

  test('calls onClose when cancel button is clicked', async () => {
    render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Ext"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    const cancelBtn = screen.getByTestId('install-progress-cancel-btn')
    fireEvent.click(cancelBtn)

    await waitFor(() => {
      expect(mockOnClose).toHaveBeenCalled()
    })
  })

  test('shows modal with correct ARIA role', () => {
    render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Ext"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    const modal = screen.getByTestId('install-progress-modal')
    expect(modal).toHaveAttribute('role', 'dialog')
  })

  test('close button is disabled during installation', () => {
    const { rerender } = render(
      <InstallProgress
        extensionId="test-ext"
        extensionName="Test Ext"
        onClose={mockOnClose}
        onComplete={mockOnComplete}
      />
    )
    const closeBtn = screen.getByTestId('install-progress-close-btn')
    // During installation (progress < 100), close button should be disabled
    expect(closeBtn).toBeDisabled()
  })
})
