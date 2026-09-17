/**
 * Marketplace Hub — Card Component Library (ADR-0691)
 *
 * 5 Discoverable Card Types:
 * 1. Plugin Card — extensions, integrations
 * 2. Skill Card — AI skills with confidence scores
 * 3. Dataset Card — data collections
 * 4. Service Card — connected services
 * 5. Template Card — workflow templates
 *
 * Session 4 Milestone C
 *
 * NOTE: Uses Tailwind CSS + lucide-react (consistent with project design system).
 * Refactored from Mantine/Tabler to reduce dependencies.
 */

import React from 'react';
import { Download, Plus, Check, Clock } from 'lucide-react';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

export interface BaseCardProps {
  icon?: string;
  description?: string;
}

export interface PluginCardProps extends BaseCardProps {
  id: string;
  name: string;
  version: string;
  installCount?: number;
  onInstall: () => void;
}

export interface SkillCardProps extends BaseCardProps {
  id: string;
  name: string;
  confidenceScore: number; // 0-100
  onInstall: () => void;
}

export interface DatasetCardProps extends BaseCardProps {
  id: string;
  name: string;
  rowCount: number;
  lastUpdated?: string;
  onDownload: () => void;
}

export interface ServiceCardProps extends BaseCardProps {
  id: string;
  name: string;
  endpointUrl?: string;
  healthStatus: 'active' | 'inactive' | 'degraded';
  uptime?: number;
}

export interface TemplateCardProps extends BaseCardProps {
  id: string;
  name: string;
  category?: string;
  useCount?: number;
  onUse: () => void;
}

// ============================================================================
// SHARED CARD WRAPPER
// ============================================================================

const CardWrapper: React.FC<{ children: React.ReactNode; testId: string }> = ({
  children,
  testId,
}) => (
  <div
    data-testid={testId}
    className="border border-gray-200 rounded-lg p-4 shadow-sm hover:shadow-md transition-shadow bg-white"
  >
    {children}
  </div>
);

// ============================================================================
// BADGE COMPONENT
// ============================================================================

const Badge: React.FC<{ children: React.ReactNode; color?: string; size?: 'xs' | 'sm' | 'lg' }> = ({
  children,
  color = 'blue',
  size = 'sm',
}) => {
  const colorClasses = {
    blue: 'bg-blue-100 text-blue-800',
    green: 'bg-green-100 text-green-800',
    yellow: 'bg-yellow-100 text-yellow-800',
    red: 'bg-red-100 text-red-800',
  }[color] || 'bg-gray-100 text-gray-800';

  const sizeClasses = {
    xs: 'px-2 py-1 text-xs',
    sm: 'px-3 py-1.5 text-sm',
    lg: 'px-4 py-2 text-base',
  }[size];

  return <span className={`inline-block rounded-full font-medium ${colorClasses} ${sizeClasses}`}>{children}</span>;
};

// ============================================================================
// COMPONENT 1: PLUGIN CARD
// ============================================================================

export const PluginCard: React.FC<PluginCardProps> = ({
  id,
  name,
  version,
  icon,
  description,
  installCount = 0,
  onInstall,
}) => (
  <CardWrapper testId="plugin-card">
    {icon && <img src={icon} alt={name} className="w-12 h-12 mb-3 rounded" />}

    <div className="space-y-3">
      <div>
        <h3 className="font-semibold text-base text-gray-900">{name}</h3>
        <p className="text-xs text-gray-500">v{version}</p>
      </div>

      {description && <p className="text-sm text-gray-600">{description.substring(0, 80)}...</p>}

      <div className="flex items-center justify-between pt-2">
        <Badge size="lg" color="blue">
          {installCount} installs
        </Badge>
        <button
          onClick={onInstall}
          className="inline-flex items-center gap-1 px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded hover:bg-blue-700 transition"
        >
          <Plus size={14} />
          Install
        </button>
      </div>
    </div>
  </CardWrapper>
);

// ============================================================================
// COMPONENT 2: SKILL CARD
// ============================================================================

export const SkillCard: React.FC<SkillCardProps> = ({
  id,
  name,
  confidenceScore,
  icon,
  description,
  onInstall,
}) => {
  const confidenceLevel =
    confidenceScore >= 80 ? 'high' : confidenceScore >= 50 ? 'medium' : 'low';
  const confidenceColor = {
    high: 'green',
    medium: 'yellow',
    low: 'red',
  }[confidenceLevel];

  return (
    <CardWrapper testId="skill-card">
      {icon && <img src={icon} alt={name} className="w-12 h-12 mb-3 rounded" />}

      <div className="space-y-3">
        <div>
          <h3 className="font-semibold text-base text-gray-900">{name}</h3>
          <div className="mt-2">
            <Badge size="lg" color={confidenceColor}>
              {confidenceScore}% confidence
            </Badge>
          </div>
        </div>

        {description && <p className="text-sm text-gray-600">{description.substring(0, 80)}...</p>}

        <button
          onClick={onInstall}
          className="w-full inline-flex items-center justify-center gap-1 px-3 py-2 bg-blue-600 text-white text-xs font-medium rounded hover:bg-blue-700 transition"
        >
          <Plus size={14} />
          Install Skill
        </button>
      </div>
    </CardWrapper>
  );
};

// ============================================================================
// COMPONENT 3: DATASET CARD
// ============================================================================

export const DatasetCard: React.FC<DatasetCardProps> = ({
  id,
  name,
  rowCount,
  icon,
  description,
  lastUpdated,
  onDownload,
}) => (
  <CardWrapper testId="dataset-card">
    {icon && <img src={icon} alt={name} className="w-12 h-12 mb-3 rounded" />}

    <div className="space-y-3">
      <div>
        <h3 className="font-semibold text-base text-gray-900">{name}</h3>
        <p className="text-xs text-gray-500">{rowCount.toLocaleString()} rows</p>
      </div>

      {description && <p className="text-sm text-gray-600">{description.substring(0, 80)}...</p>}

      <div className="flex items-center justify-between pt-2">
        {lastUpdated && (
          <div className="inline-flex items-center gap-1 text-xs text-gray-500">
            <Clock size={12} />
            Updated: {lastUpdated}
          </div>
        )}
        <button
          onClick={onDownload}
          className="inline-flex items-center gap-1 px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded hover:bg-blue-700 transition"
        >
          <Download size={14} />
          Download
        </button>
      </div>
    </div>
  </CardWrapper>
);

// ============================================================================
// COMPONENT 4: SERVICE CARD
// ============================================================================

export const ServiceCard: React.FC<ServiceCardProps> = ({
  id,
  name,
  endpointUrl,
  healthStatus,
  icon,
  description,
  uptime,
}) => {
  const statusColors = {
    active: 'green',
    inactive: 'red',
    degraded: 'yellow',
  };

  return (
    <CardWrapper testId="service-card">
      {icon && <img src={icon} alt={name} className="w-12 h-12 mb-3 rounded" />}

      <div className="space-y-3">
        <div>
          <h3 className="font-semibold text-base text-gray-900">{name}</h3>
          <div className="mt-2">
            <Badge size="lg" color={statusColors[healthStatus]}>
              <Check size={12} className="inline mr-1" />
              {healthStatus}
            </Badge>
          </div>
        </div>

        {endpointUrl && (
          <p className="text-xs text-gray-500 truncate">{endpointUrl}</p>
        )}

        {description && <p className="text-sm text-gray-600">{description.substring(0, 80)}...</p>}

        {uptime !== undefined && (
          <p className="text-xs text-gray-500">Uptime: {uptime.toFixed(2)}%</p>
        )}
      </div>
    </CardWrapper>
  );
};

// ============================================================================
// COMPONENT 5: TEMPLATE CARD
// ============================================================================

export const TemplateCard: React.FC<TemplateCardProps> = ({
  id,
  name,
  category,
  icon,
  description,
  useCount = 0,
  onUse,
}) => (
  <CardWrapper testId="template-card">
    {icon && <img src={icon} alt={name} className="w-12 h-12 mb-3 rounded" />}

    <div className="space-y-3">
      <div>
        <h3 className="font-semibold text-base text-gray-900">{name}</h3>
        {category && (
          <div className="mt-1">
            <Badge size="xs">{category}</Badge>
          </div>
        )}
      </div>

      {description && <p className="text-sm text-gray-600">{description.substring(0, 80)}...</p>}

      <div className="flex items-center justify-between pt-2">
        <span className="text-xs text-gray-500">{useCount} uses</span>
        <button
          onClick={onUse}
          className="inline-flex items-center gap-1 px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded hover:bg-blue-700 transition"
        >
          <Plus size={14} />
          Use Template
        </button>
      </div>
    </div>
  </CardWrapper>
);

// ============================================================================
// EXPORT COLLECTION
// ============================================================================

export const MarketplaceCardComponents = {
  PluginCard,
  SkillCard,
  DatasetCard,
  ServiceCard,
  TemplateCard,
};

export default MarketplaceCardComponents;
