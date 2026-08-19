/**
 * API utilities for handling console routing under /console subpath
 */

export function getConsoleApiUrl(path: string): string {
  const pathname = new URL(window.location.href).pathname
  const baseUrl = pathname.includes('/console') ? '/console' : ''
  return `${baseUrl}${path}`
}

/**
 * Global fetch override that automatically adds /console prefix if needed
 */
const originalFetch = window.fetch
window.fetch = function(resource: any, init?: RequestInit) {
  if (typeof resource === 'string' && resource.startsWith('/api/')) {
    const correctedUrl = getConsoleApiUrl(resource)
    return originalFetch.call(window, correctedUrl, init)
  }
  return originalFetch.call(window, resource, init)
} as any

export async function fetchConsoleApi(
  path: string,
  options?: RequestInit
): Promise<Response> {
  const url = getConsoleApiUrl(path)
  return fetch(url, options)
}

export async function fetchConsoleJson<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetchConsoleApi(path, options)
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}
