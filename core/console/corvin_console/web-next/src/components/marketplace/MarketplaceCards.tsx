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
 */

import React from 'react';
import { Badge, Button, Card, Image, Text, Stack, Group } from '@mantine/core';
import { IconDownload, IconPlus, IconCheck, IconClock } from '@tabler/icons-react';
import styles from './MarketplaceCards.module.css';

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
  <Card data-testid="plugin-card" className={styles.card} shadow="sm" padding="lg" radius="md" withBorder>
    {icon && <Image src={icon} alt={name} height={48} mb="md" />}

    <Stack gap="xs">
      <div>
        <Text fw={600} size="md">
          {name}
        </Text>
        <Text size="xs" c="dimmed">
          v{version}
        </Text>
      </div>

      {description && (
        <Text size="sm" c="gray">
          {description.substring(0, 80)}...
        </Text>
      )}

      <Group justify="space-between">
        <Badge size="lg" variant="light" color="blue">
          {installCount} installs
        </Badge>
        <Button size="xs" onClick={onInstall} leftSection={<IconPlus size={14} />}>
          Install
        </Button>
      </Group>
    </Stack>
  </Card>
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
    <Card data-testid="skill-card" className={styles.card} shadow="sm" padding="lg" radius="md" withBorder>
      {icon && <Image src={icon} alt={name} height={48} mb="md" />}

      <Stack gap="xs">
        <div>
          <Text fw={600} size="md">
            {name}
          </Text>
          <Badge size="lg" color={confidenceColor} variant="light" mt="xs">
            {confidenceScore}% confidence
          </Badge>
        </div>

        {description && (
          <Text size="sm" c="gray">
            {description.substring(0, 80)}...
          </Text>
        )}

        <Button size="xs" onClick={onInstall} leftSection={<IconPlus size={14} />} fullWidth>
          Install Skill
        </Button>
      </Stack>
    </Card>
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
  <Card data-testid="dataset-card" className={styles.card} shadow="sm" padding="lg" radius="md" withBorder>
    {icon && <Image src={icon} alt={name} height={48} mb="md" />}

    <Stack gap="xs">
      <div>
        <Text fw={600} size="md">
          {name}
        </Text>
        <Text size="xs" c="dimmed">
          {rowCount.toLocaleString()} rows
        </Text>
      </div>

      {description && (
        <Text size="sm" c="gray">
          {description.substring(0, 80)}...
        </Text>
      )}

      <Group justify="space-between" align="center">
        {lastUpdated && (
          <Text size="xs" c="dimmed" leftSection={<IconClock size={12} />}>
            Updated: {lastUpdated}
          </Text>
        )}
        <Button size="xs" onClick={onDownload} leftSection={<IconDownload size={14} />}>
          Download
        </Button>
      </Group>
    </Stack>
  </Card>
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
    <Card data-testid="service-card" className={styles.card} shadow="sm" padding="lg" radius="md" withBorder>
      {icon && <Image src={icon} alt={name} height={48} mb="md" />}

      <Stack gap="xs">
        <div>
          <Text fw={600} size="md">
            {name}
          </Text>
          <Badge
            size="lg"
            color={statusColors[healthStatus]}
            variant="light"
            mt="xs"
            leftSection={<IconCheck size={12} />}
          >
            {healthStatus}
          </Badge>
        </div>

        {endpointUrl && (
          <Text size="xs" c="dimmed" truncate>
            {endpointUrl}
          </Text>
        )}

        {description && (
          <Text size="sm" c="gray">
            {description.substring(0, 80)}...
          </Text>
        )}

        {uptime !== undefined && (
          <Text size="xs" c="dimmed">
            Uptime: {uptime.toFixed(2)}%
          </Text>
        )}
      </Stack>
    </Card>
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
  <Card data-testid="template-card" className={styles.card} shadow="sm" padding="lg" radius="md" withBorder>
    {icon && <Image src={icon} alt={name} height={48} mb="md" />}

    <Stack gap="xs">
      <div>
        <Text fw={600} size="md">
          {name}
        </Text>
        {category && (
          <Badge size="xs" variant="light">
            {category}
          </Badge>
        )}
      </div>

      {description && (
        <Text size="sm" c="gray">
          {description.substring(0, 80)}...
        </Text>
      )}

      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          {useCount} uses
        </Text>
        <Button size="xs" onClick={onUse} leftSection={<IconPlus size={14} />}>
          Use Template
        </Button>
      </Group>
    </Stack>
  </Card>
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
