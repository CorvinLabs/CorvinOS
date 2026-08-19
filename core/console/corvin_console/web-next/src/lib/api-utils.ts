/**
 * API utilities for handling console routing.
 * The app is served from /console/ base path.
 * In dev: Vite proxies /api/console/* to gateway :8765
 * In prod: Direct /v1/console/* routing on gateway
 */

export async function fetchConsoleApi(
  path: string,
  options?: RequestInit
): Promise<Response> {
  // Detect if running in dev mode (Vite dev server)
  const isDev = window.location.port === '5173' || window.location.port === '5174' || window.location.port === '5175'

  let url: string
  if (isDev) {
    // Dev mode: use /api/console/* (Vite proxy will rewrite to /v1/console/*)
    url = window.location.origin + path
  } else {
    // Production: direct /v1/console/* routing
    // Replace /api/console/ with /v1/console/
    const actualPath = path.replace(/^\/api\/console\//, '/v1/console/')
    url = window.location.origin + actualPath
  }

  // Debug logging
  const method = options?.method || 'GET'
  console.log(`[API] ${method} ${url}`, { isDev, options })

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
