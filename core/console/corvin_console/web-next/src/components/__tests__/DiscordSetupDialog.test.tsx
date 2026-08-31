/**
 * E2E Tests for Discord Zero-Config Setup Dialog
 *
 * Tests:
 * 1. Input validation (empty token)
 * 2. Token validation call + response handling
 * 3. OAuth2 URL display + copy button
 * 4. Save token flow
 * 5. Error handling
 * 6. Success screen
 */

import React from 'react'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DiscordSetupDialog } from '../DiscordSetupDialog'

// Mock fetch
global.fetch = jest.fn()

// Mock navigator.clipboard
Object.assign(navigator, {
  clipboard: {
    writeText: jest.fn().mockResolvedValue(undefined),
  },
})

describe('DiscordSetupDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  test('1: Shows initial input state with token textarea', () => {
    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    expect(screen.getByText(/Discord Bot Aktivierung/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Paste your bot token/i)).toBeInTheDocument()
    expect(screen.getByText(/Validieren & Weiter/i)).toBeInTheDocument()
  })

  test('2: Prevents empty token submission', async () => {
    const user = userEvent.setup()
    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const button = screen.getByText(/Validieren & Weiter/i)
    await user.click(button)

    expect(screen.getByText(/Token erforderlich/i)).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  test('3: Validates token and displays OAuth2 URL', async () => {
    const user = userEvent.setup()
    const mockResponse = {
      valid: true,
      appId: '1234567890',
      appName: 'CorvinOS Bot',
      url: 'https://discord.com/api/oauth2/authorize?client_id=1234567890&scope=bot&permissions=68608',
      permissionsHuman: [
        'Read Messages/View Channels',
        'Send Messages',
        'Attach Files',
        'Read Message History',
      ],
    }

    ;(fetch as jest.Mock).mockResolvedValueOnce({
      json: async () => mockResponse,
    })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'test_token_123')

    const button = screen.getByText(/Validieren & Weiter/i)
    await user.click(button)

    await waitFor(() => {
      expect(screen.getByText(/Token validiert/i)).toBeInTheDocument()
      expect(screen.getByText(/CorvinOS Bot/i)).toBeInTheDocument()
      expect(screen.getByText(/1234567890/i)).toBeInTheDocument()
    })

    // Check permissions are displayed
    expect(screen.getByText(/Read Messages\/View Channels/i)).toBeInTheDocument()
    expect(screen.getByText(/Send Messages/i)).toBeInTheDocument()
  })

  test('4: Shows Discord authorization link with external icon', async () => {
    const user = userEvent.setup()
    const mockResponse = {
      valid: true,
      appId: '1234567890',
      appName: 'CorvinOS Bot',
      url: 'https://discord.com/api/oauth2/authorize?client_id=1234567890&scope=bot&permissions=68608',
      permissionsHuman: ['Read Messages', 'Send Messages'],
    }

    ;(fetch as jest.Mock).mockResolvedValueOnce({
      json: async () => mockResponse,
    })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'test_token_123')

    const button = screen.getByText(/Validieren & Weiter/i)
    await user.click(button)

    await waitFor(() => {
      const link = screen.getByText(/Öffne Discord Autorisierung/i)
      expect(link).toHaveAttribute('href', mockResponse.url)
      expect(link).toHaveAttribute('target', '_blank')
    })
  })

  test('5: Copy OAuth2 URL button works', async () => {
    const user = userEvent.setup()
    const mockResponse = {
      valid: true,
      appId: '1234567890',
      appName: 'CorvinOS Bot',
      url: 'https://discord.com/api/oauth2/authorize?client_id=1234567890&scope=bot&permissions=68608',
      permissionsHuman: ['Read Messages'],
    }

    ;(fetch as jest.Mock).mockResolvedValueOnce({
      json: async () => mockResponse,
    })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'test_token_123')

    const button = screen.getByText(/Validieren & Weiter/i)
    await user.click(button)

    await waitFor(() => {
      const copyButton = screen.getByRole('button', { name: /Copy URL/i })
      expect(copyButton).toBeInTheDocument()
    })

    const copyButton = screen.getByRole('button', { name: /Copy URL/i })
    await user.click(copyButton)

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(mockResponse.url)
  })

  test('6: Saves token and shows success screen', async () => {
    const user = userEvent.setup()
    const mockValidation = {
      valid: true,
      appId: '1234567890',
      appName: 'CorvinOS Bot',
      url: 'https://discord.com/api/oauth2/authorize?client_id=1234567890&scope=bot&permissions=68608',
      permissionsHuman: ['Read Messages'],
    }

    const mockSave = {
      success: true,
    }

    ;(fetch as jest.Mock)
      .mockResolvedValueOnce({
        json: async () => mockValidation,
      })
      .mockResolvedValueOnce({
        json: async () => mockSave,
      })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'test_token_123')

    const validateButton = screen.getByText(/Validieren & Weiter/i)
    await user.click(validateButton)

    await waitFor(() => {
      expect(screen.getByText(/Token speichern/i)).toBeInTheDocument()
    })

    const saveButton = screen.getByText(/Token speichern & Setup abschließen/i)
    await user.click(saveButton)

    await waitFor(() => {
      expect(screen.getByText(/Bot erfolgreich aktiviert/i)).toBeInTheDocument()
    })
  })

  test('7: Handles validation error', async () => {
    const user = userEvent.setup()
    const mockError = {
      valid: false,
      error: 'Invalid token (401 Unauthorized)',
    }

    ;(fetch as jest.Mock).mockResolvedValueOnce({
      json: async () => mockError,
    })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'invalid_token')

    const button = screen.getByText(/Validieren & Weiter/i)
    await user.click(button)

    await waitFor(() => {
      expect(screen.getByText(/Invalid token \(401 Unauthorized\)/i)).toBeInTheDocument()
    })
  })

  test('8: Handles save error', async () => {
    const user = userEvent.setup()
    const mockValidation = {
      valid: true,
      appId: '1234567890',
      appName: 'CorvinOS Bot',
      url: 'https://discord.com/api/oauth2/authorize?client_id=1234567890&scope=bot&permissions=68608',
      permissionsHuman: ['Read Messages'],
    }

    const mockSaveError = {
      success: false,
      error: 'Failed to write settings file',
    }

    ;(fetch as jest.Mock)
      .mockResolvedValueOnce({
        json: async () => mockValidation,
      })
      .mockResolvedValueOnce({
        json: async () => mockSaveError,
      })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'test_token_123')

    const validateButton = screen.getByText(/Validieren & Weiter/i)
    await user.click(validateButton)

    await waitFor(() => {
      expect(screen.getByText(/Token speichern/i)).toBeInTheDocument()
    })

    const saveButton = screen.getByText(/Token speichern & Setup abschließen/i)
    await user.click(saveButton)

    await waitFor(() => {
      expect(screen.getByText(/Failed to write settings file/i)).toBeInTheDocument()
    })
  })

  test('9: Reset button returns to input state', async () => {
    const user = userEvent.setup()
    const mockError = {
      valid: false,
      error: 'Invalid token',
    }

    ;(fetch as jest.Mock).mockResolvedValueOnce({
      json: async () => mockError,
    })

    render(<DiscordSetupDialog csrf="test-csrf" onClose={() => {}} />)

    const textarea = screen.getByPlaceholderText(/Paste your bot token/i)
    await user.type(textarea, 'invalid_token')

    const button = screen.getByText(/Validieren & Weiter/i)
    await user.click(button)

    await waitFor(() => {
      expect(screen.getByText(/Invalid token/i)).toBeInTheDocument()
    })

    const retryButton = screen.getByText(/Nochmal probieren/i)
    await user.click(retryButton)

    // Should be back to input state
    expect(screen.getByPlaceholderText(/Paste your bot token/i)).toBeInTheDocument()
    expect(screen.getByDisplayValue('')).toBeInTheDocument()
  })
})
