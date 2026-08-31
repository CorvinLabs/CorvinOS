/**
 * Unit tests for CustomRepositoryCard — ADR-0454 Week 2
 * Tests rendering, status display, action buttons
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { CustomRepositoryCard } from '@/components/CustomRepositoryCard'

describe('CustomRepositoryCard', () => {
  const defaultProps = {
    repoUrl: 'https://github.com/owner/repo',
    status: 'healthy' as const,
    extensionCount: 5,
    lastChecked: new Date().toISOString(),
    enabled: true
  }

  it('renders repository URL and extension count', () => {
    render(<CustomRepositoryCard {...defaultProps} />)

    expect(screen.getByText('owner/repo')).toBeInTheDocument()
    // The count sits in its own <strong>, so the string spans two nodes.
    expect(screen.getByText((_, el) => el?.textContent === '5 extensions'))
      .toBeInTheDocument()
    expect(screen.getByRole('link', { name: /owner\/repo/i })).toHaveAttribute(
      'href',
      'https://github.com/owner/repo'
    )
  })

  it('displays status indicator based on status prop', () => {
    const { rerender } = render(<CustomRepositoryCard {...defaultProps} />)

    // Healthy status
    expect(screen.getByLabelText('Healthy')).toBeInTheDocument()

    // Loading status
    rerender(<CustomRepositoryCard {...defaultProps} status="loading" />)
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()

    // Error status
    rerender(<CustomRepositoryCard {...defaultProps} status="error" errorMessage="Connection failed" />)
    expect(screen.getByLabelText('Error')).toBeInTheDocument()
    expect(screen.getByText('Connection failed')).toBeInTheDocument()
  })

  it('shows disabled badge when enabled=false', () => {
    render(<CustomRepositoryCard {...defaultProps} enabled={false} />)

    expect(screen.getByText('Disabled')).toBeInTheDocument()
  })

  it('displays error message when status is error', () => {
    render(
      <CustomRepositoryCard
        {...defaultProps}
        status="error"
        errorMessage="GitHub API rate limited"
      />
    )

    expect(screen.getByText('GitHub API rate limited')).toBeInTheDocument()
  })

  it('calls onRefresh when Refresh button is clicked', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)

    render(
      <CustomRepositoryCard
        {...defaultProps}
        onRefresh={onRefresh}
      />
    )

    const refreshBtn = screen.getByRole('button', { name: /refresh/i })
    fireEvent.click(refreshBtn)

    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('calls onToggle when Disable/Enable button is clicked', async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined)

    render(
      <CustomRepositoryCard
        {...defaultProps}
        enabled={true}
        onToggle={onToggle}
      />
    )

    const toggleBtn = screen.getByRole('button', { name: /disable/i })
    fireEvent.click(toggleBtn)

    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('calls onRemove when Remove button is clicked', async () => {
    const onRemove = vi.fn().mockResolvedValue(undefined)

    render(
      <CustomRepositoryCard
        {...defaultProps}
        onRemove={onRemove}
      />
    )

    const removeBtn = screen.getByRole('button', { name: /remove/i })
    fireEvent.click(removeBtn)

    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  it('displays last checked timestamp', () => {
    const date = new Date('2026-08-30T10:00:00Z')
    render(
      <CustomRepositoryCard
        {...defaultProps}
        lastChecked={date.toISOString()}
      />
    )

    expect(screen.getByText(/last checked/i)).toBeInTheDocument()
  })

  it('renders as article with proper accessibility attributes', () => {
    const { container } = render(<CustomRepositoryCard {...defaultProps} />)

    const article = container.querySelector('article')
    expect(article).toBeInTheDocument()
    expect(article).toHaveAttribute('aria-label', expect.stringContaining('Repository:'))
  })
})
