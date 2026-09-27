/**
 * Skill Card — Reusable Skill Display Component (Phase 5 K=3)
 *
 * Props-driven card for installed/available skills.
 * Variants for different action types.
 */

import { ReactNode } from 'react';

interface SkillCardProps {
  skill: {
    skill_id: string;
    name: string;
    version: string;
    author?: string;
    description?: string;
    boot_layer?: string;
    status?: string;
    installed_at?: string;
    category?: string;
  };
  action?: 'install' | 'uninstall' | 'none';
  onAction?: () => void;
  actionLabel?: string;
  actionIcon?: ReactNode;
  subtitle?: string;
}

export function SkillCard({
  skill,
  action = 'none',
  onAction,
  actionLabel,
  actionIcon,
  subtitle,
}: SkillCardProps) {
  const getActionColor = () => {
    switch (action) {
      case 'install':
        return 'bg-primary hover:opacity-90';
      case 'uninstall':
        return 'bg-destructive hover:opacity-90';
      default:
        return 'bg-muted text-muted-foreground cursor-not-allowed';
    }
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return '';
    try {
      return new Date(dateStr).toLocaleDateString();
    } catch {
      return dateStr;
    }
  };

  return (
    <div className="border rounded-lg p-4 bg-card hover:shadow-md transition-shadow space-y-3">
      {/* Header: Name + Version */}
      <div>
        <h3 className="font-semibold text-base">{skill.name}</h3>
        <p className="text-xs text-muted-foreground">v{skill.version}</p>
      </div>

      {/* Subtitle (ratings for marketplace skills) */}
      {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}

      {/* Description */}
      {skill.description && (
        <p className="text-sm text-muted-foreground line-clamp-2">{skill.description}</p>
      )}

      {/* Meta info */}
      <div className="space-y-1 text-xs text-muted-foreground">
        {skill.author && (
          <p>
            <span className="font-medium">Author:</span> {skill.author}
          </p>
        )}
        {skill.boot_layer && (
          <p>
            <span className="font-medium">Boot layer:</span> {skill.boot_layer}
          </p>
        )}
        {skill.category && (
          <p>
            <span className="font-medium">Category:</span>{' '}
            <span className="capitalize">{skill.category}</span>
          </p>
        )}
        {skill.installed_at && (
          <p>
            <span className="font-medium">Installed:</span> {formatDate(skill.installed_at)}
          </p>
        )}
        {skill.status && (
          <p>
            <span className="font-medium">Status:</span>{' '}
            <span
              className={skill.status === 'active' || skill.status === 'verified' ? 'text-green-600' : 'text-amber-600'}
            >
              {skill.status}
            </span>
          </p>
        )}
      </div>

      {/* Action button */}
      {action !== 'none' && (
        <button
          onClick={onAction}
          className={`w-full mt-4 px-3 py-2 rounded text-sm font-medium text-white transition-opacity flex items-center justify-center gap-2 ${getActionColor()}`}
        >
          {actionIcon}
          {actionLabel || (action === 'install' ? 'Install' : 'Uninstall')}
        </button>
      )}
    </div>
  );
}
