/**
 * API utilities for handling console routing.
 * The app is served from /console/ base path.
 * API requests go to /api/console/* which Vite proxies to the gateway.
 */

export async function fetchConsoleApi(
  path: string,
  options?: RequestInit
): Promise<Response> {
  // Always use absolute URLs to avoid base path issues
  // In dev: http://127.0.0.1:5173/api/console/* → proxied to gateway via Vite
  // In prod: /api/console/* is served from gateway's /v1/console/* via rewrite
  const url = window.location.origin + path
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
