/**
 * Skill Manager Context — shared install/uninstall state for the tabs.
 *
 * NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
 * nothing imports pages/skills/**; the mounted skills manager is
 * pages/admin/skill-manager.tsx.
 *
 * Install and uninstall are single synchronous requests against
 * routes/skill_manager.py (see ./endpoints.ts). There is no task id and no
 * progress endpoint: the state is "installing" until the response arrives,
 * then "complete" or "error" with the server's message — never a made-up
 * percentage. (This used to POST JSON to /v1/skills/{upload,install}, which
 * no router serves, and the Uninstall button called the INSTALL path.)
 */

import { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import { SKILLS_INSTALL, errorMessage, skillUninstallPath } from './endpoints';

export interface InstallationState {
  status: 'idle' | 'installing' | 'uninstalling' | 'complete' | 'error';
  skillId: string;
  message: string | null;
}

export interface SkillManagerContextType {
  installation: InstallationState;
  /** Bumped after every successful install/uninstall so lists refetch. */
  revision: number;
  installZip: (file: File, skillId: string, version: string) => Promise<boolean>;
  uninstall: (skillId: string, version: string) => Promise<boolean>;
  clearInstallation: () => void;
}

export const SkillManagerContext = createContext<SkillManagerContextType | undefined>(undefined);

const IDLE: InstallationState = { status: 'idle', skillId: '', message: null };

export function SkillManagerProvider({ children }: { children: ReactNode }) {
  const [installation, setInstallation] = useState<InstallationState>(IDLE);
  const [revision, setRevision] = useState(0);

  const run = useCallback(
    async (
      skillId: string,
      pending: 'installing' | 'uninstalling',
      request: () => Promise<Response>,
    ): Promise<boolean> => {
      setInstallation({ status: pending, skillId, message: null });
      try {
        const res = await request();
        if (!res.ok) {
          setInstallation({ status: 'error', skillId, message: await errorMessage(res) });
          return false;
        }
        const body = (await res.json().catch(() => ({}))) as { message?: string };
        setInstallation({ status: 'complete', skillId, message: body.message ?? null });
        setRevision((r) => r + 1);
        return true;
      } catch (err) {
        setInstallation({ status: 'error', skillId, message: err instanceof Error ? err.message : String(err) });
        return false;
      }
    },
    [],
  );

  const installZip = useCallback(
    (file: File, skillId: string, version: string) =>
      run(skillId, 'installing', () => {
        const form = new FormData();
        form.append('file', file);
        form.append('skill_id', skillId);
        form.append('version', version);
        // window.fetch carries X-CSRF-Token via lib/csrf-fetch.ts.
        return fetch(SKILLS_INSTALL, { method: 'POST', body: form, credentials: 'same-origin' });
      }),
    [run],
  );

  const uninstall = useCallback(
    (skillId: string, version: string) =>
      run(skillId, 'uninstalling', () =>
        fetch(skillUninstallPath(skillId, version), { method: 'DELETE', credentials: 'same-origin' }),
      ),
    [run],
  );

  const clearInstallation = useCallback(() => setInstallation(IDLE), []);

  return (
    <SkillManagerContext.Provider value={{ installation, revision, installZip, uninstall, clearInstallation }}>
      {children}
    </SkillManagerContext.Provider>
  );
}

export function useSkillManager() {
  const ctx = useContext(SkillManagerContext);
  if (!ctx) {
    throw new Error('useSkillManager must be used inside SkillManagerProvider');
  }
  return ctx;
}
