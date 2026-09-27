/**
 * Skill Manager Context — Shared Installation State (Phase 5 K=3)
 *
 * Provides InstallationState and control methods to all skill manager tabs.
 * Polling logic lives in InstallationProgress component.
 */

import { createContext, useContext, useState, useCallback, ReactNode } from 'react';

export interface InstallationState {
  taskId: string | null;
  skillName: string;
  skillId: string;
  status: 'idle' | 'pending' | 'downloading' | 'extracting' | 'validating' | 'installing' | 'complete' | 'error';
  progress: number; // 0-100
  errorMessage: string | null;
}

export interface SkillManagerContextType {
  installation: InstallationState;
  startInstall: (skillId: string, skillName: string, source: 'marketplace' | 'upload') => Promise<void>;
  cancelInstall: () => void;
  clearInstallation: () => void;
}

export const SkillManagerContext = createContext<SkillManagerContextType | undefined>(undefined);

export function SkillManagerProvider({ children }: { children: ReactNode }) {
  const [installation, setInstallation] = useState<InstallationState>({
    taskId: null,
    skillName: '',
    skillId: '',
    status: 'idle',
    progress: 0,
    errorMessage: null,
  });

  const startInstall = useCallback(
    async (skillId: string, skillName: string, source: 'marketplace' | 'upload') => {
      try {
        const endpoint = source === 'upload' ? '/v1/skills/upload' : '/v1/skills/install';
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill_id: skillId, source }),
        });
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        setInstallation({
          taskId: data.task_id,
          skillId,
          skillName,
          status: 'pending',
          progress: 0,
          errorMessage: null,
        });
      } catch (err) {
        setInstallation((prev) => ({
          ...prev,
          status: 'error',
          errorMessage: String(err),
        }));
      }
    },
    [],
  );

  const cancelInstall = useCallback(() => {
    setInstallation((prev) => ({
      ...prev,
      status: 'error',
      errorMessage: 'Installation cancelled by user',
    }));
  }, []);

  const clearInstallation = useCallback(() => {
    setInstallation({
      taskId: null,
      skillName: '',
      skillId: '',
      status: 'idle',
      progress: 0,
      errorMessage: null,
    });
  }, []);

  return (
    <SkillManagerContext.Provider value={{ installation, startInstall, cancelInstall, clearInstallation }}>
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
