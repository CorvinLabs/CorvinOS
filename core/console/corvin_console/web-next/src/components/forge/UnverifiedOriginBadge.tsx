import React from 'react';
import { AlertCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

/**
 * UnverifiedOriginBadge — reusable badge for externally-imported artifacts
 *
 * Shows a warning that an artifact was imported from an external bundle
 * and has not been cryptographically verified. Always visible in import
 * preview and quarantine panels.
 *
 * ADR-2229 Phase 4: All imported artifacts carry this badge until reviewed.
 */
export const UnverifiedOriginBadge: React.FC = () => {
  return (
    <Badge
      variant="outline"
      className="bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-700 gap-1"
    >
      <AlertCircle className="w-3 h-3" />
      Unverified origin
    </Badge>
  );
};

export default UnverifiedOriginBadge;
