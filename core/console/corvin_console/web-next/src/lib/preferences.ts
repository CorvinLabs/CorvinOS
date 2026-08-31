/**
 * Tiny defensive wrapper around localStorage for UI preferences.
 *
 * Why a wrapper:
 *   - localStorage throws in private-browsing tabs and inside some
 *     restricted iframes. A naïve `localStorage.getItem(...)` call
 *     can crash an entire React tree on render. Every read/write here
 *     is try/wrapped so a hostile environment degrades to "no
 *     persistence" instead of a white-screen.
 *   - One typed surface for the whole app, instead of scattered string
 *     keys. Adding a new preference goes through this file → one place
 *     to audit, one place to deprecate.
 *
 * Storage layout is namespaced under `corvin.` so this never collides
 * with whatever else might share the origin (e.g. legacy console SPA).
 */

export const PREF_KEYS = {
  theme: "corvin.theme",
  voiceOut: "corvin.chat.voiceOut",
  lastChatSid: "corvin.chat.lastSid",
  lastVisitedRoute: "corvin.lastVisitedRoute",
  voiceLang: "corvin.chat.voiceLang",
} as const;

export type PrefKey = (typeof PREF_KEYS)[keyof typeof PREF_KEYS];

/* ───────── primitive get/set with defensive try ───────── */

function read(key: PrefKey): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: PrefKey, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* private-mode: silently drop */
  }
}

/* ───────── typed accessors ───────── */

export function getBool(key: PrefKey, fallback: boolean): boolean {
  const raw = read(key);
  if (raw === "true") return true;
  if (raw === "false") return false;
  return fallback;
}

export function setBool(key: PrefKey, value: boolean): void {
  write(key, value ? "true" : "false");
}

export function getString(key: PrefKey, fallback = ""): string {
  const raw = read(key);
  return raw ?? fallback;
}

export function setString(key: PrefKey, value: string | null): void {
  write(key, value);
}

/* ───────── React hook: state + persistence in one line ───────── */

import * as React from "react";

/**
 * `usePersistedBool("corvin.chat.voiceOut", true)` behaves like
 * `useState(true)` but the value is loaded from localStorage on
 * mount and written back on every change.
 */
export function usePersistedBool(
  key: PrefKey,
  fallback: boolean,
): [boolean, (v: boolean) => void] {
  const [value, setValue] = React.useState<boolean>(() => getBool(key, fallback));
  const set = React.useCallback(
    (v: boolean) => {
      setValue(v);
      setBool(key, v);
    },
    [key],
  );
  return [value, set];
}

export function usePersistedString(
  key: PrefKey,
  fallback = "",
): [string, (v: string | null) => void] {
  const [value, setValue] = React.useState<string>(() => getString(key, fallback));
  const set = React.useCallback(
    (v: string | null) => {
      setValue(v ?? fallback);
      setString(key, v);
    },
    [key, fallback],
  );
  return [value, set];
}
