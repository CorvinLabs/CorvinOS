/**
 * Marketplace Hub — Search & Filter UI (ADR-0691)
 *
 * Components:
 * - SearchInput: text query field
 * - FilterPanel: type & status filters
 * - ResultsTable: sortable results with filtering
 *
 * Session 4 Milestone C
 */

import React, { useState, useMemo } from 'react';
import {
  TextInput,
  MultiSelect,
  Group,
  Button,
  Table,
  Stack,
  ActionIcon,
  Badge,
  Container,
  Drawer,
  Checkbox,
  Select,
} from '@mantine/core';
import {
  IconSearch,
  IconX,
  IconChevronUp,
  IconChevronDown,
  IconFilter,
} from '@tabler/icons-react';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

export type ContentType = 'plugin' | 'skill' | 'dataset' | 'service' | 'template';
export type SortOrder = 'asc' | 'desc';
export type SortField = 'name' | 'type' | 'updated' | 'rating';

export interface SearchResult {
  id: string;
  name: string;
  type: ContentType;
  description?: string;
  updated?: string;
  rating?: number;
  status?: 'active' | 'inactive';
}

export interface SearchFilters {
  types: ContentType[];
  statuses: ('active' | 'inactive')[];
  sortField: SortField;
  sortOrder: SortOrder;
}

export interface MarketplaceSearchProps {
  data: SearchResult[];
  onResultSelect: (result: SearchResult) => void;
}

// ============================================================================
// COMPONENT: SEARCH INPUT
// ============================================================================

export const SearchInput: React.FC<{
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
}> = ({ value, onChange, onClear }) => (
  <Group gap="xs" mb="md">
    <TextInput
      data-testid="search-query"
      placeholder="Find plugins, skills, datasets, services, templates…"
      value={value}
      onChange={(e) => onChange(e.currentTarget.value)}
      leftSection={<IconSearch size={16} />}
      rightSection={
        value && (
          <ActionIcon size="xs" color="gray" radius="xl" variant="transparent" onClick={onClear}>
            <IconX size={14} />
          </ActionIcon>
        )
      }
      style={{ flex: 1 }}
    />
  </Group>
);

// ============================================================================
// COMPONENT: FILTER PANEL
// ============================================================================

export const FilterPanel: React.FC<{
  filters: SearchFilters;
  onFilterChange: (filters: SearchFilters) => void;
  onOpen?: () => void;
  isDrawer?: boolean;
}> = ({ filters, onFilterChange, onOpen, isDrawer = false }) => {
  const typeOptions = [
    { label: 'Plugin', value: 'plugin' },
    { label: 'Skill', value: 'skill' },
    { label: 'Dataset', value: 'dataset' },
    { label: 'Service', value: 'service' },
    { label: 'Template', value: 'template' },
  ];

  const statusOptions = [
    { value: 'active', label: 'Active' },
    { value: 'inactive', label: 'Inactive' },
  ];

  const sortOptions = [
    { value: 'name', label: 'Name' },
    { value: 'type', label: 'Type' },
    { value: 'updated', label: 'Recently Updated' },
    { value: 'rating', label: 'Rating' },
  ];

  const content = (
    <Stack gap="md">
      <div>
        <label style={{ fontSize: '0.875rem', fontWeight: 500, marginBottom: '0.5rem', display: 'block' }}>
          Content Type
        </label>
        <Group gap="xs">
          {typeOptions.map((option) => (
            <Checkbox
              key={option.value}
              data-testid={`filter-type-${option.value}`}
              label={option.label}
              checked={filters.types.includes(option.value as ContentType)}
              onChange={(e) => {
                const newTypes = e.currentTarget.checked
                  ? [...filters.types, option.value as ContentType]
                  : filters.types.filter((t) => t !== option.value);
                onFilterChange({ ...filters, types: newTypes });
              }}
            />
          ))}
        </Group>
      </div>

      <div>
        <label style={{ fontSize: '0.875rem', fontWeight: 500, marginBottom: '0.5rem', display: 'block' }}>
          Status
        </label>
        <Group gap="xs">
          {statusOptions.map((option) => (
            <Checkbox
              key={option.value}
              label={option.label}
              checked={filters.statuses.includes(option.value as any)}
              onChange={(e) => {
                const newStatuses = e.currentTarget.checked
                  ? [...filters.statuses, option.value as any]
                  : filters.statuses.filter((s) => s !== option.value);
                onFilterChange({ ...filters, statuses: newStatuses });
              }}
            />
          ))}
        </Group>
      </div>

      <div>
        <label style={{ fontSize: '0.875rem', fontWeight: 500, marginBottom: '0.5rem', display: 'block' }}>
          Sort By
        </label>
        <Select
          data={sortOptions}
          value={filters.sortField}
          onChange={(value) => onFilterChange({ ...filters, sortField: value as SortField })}
          clearable={false}
        />
      </div>

      <Group justify="space-between">
        <Button
          variant="light"
          size="sm"
          onClick={() => {
            onFilterChange({
              types: [],
              statuses: [],
              sortField: 'name',
              sortOrder: 'asc',
            });
          }}
        >
          Clear Filters
        </Button>
      </Group>
    </Stack>
  );

  if (isDrawer) {
    return content;
  }

  return <div style={{ borderRight: '1px solid #e9ecef', paddingRight: '1rem', minWidth: '250px' }}>{content}</div>;
};

// ============================================================================
// COMPONENT: RESULTS TABLE
// ============================================================================

export const ResultsTable: React.FC<{
  data: SearchResult[];
  onRowClick: (result: SearchResult) => void;
}> = ({ data, onRowClick }) => {
  const rows = data.map((item) => (
    <Table.Tr key={item.id} onClick={() => onRowClick(item)} style={{ cursor: 'pointer' }}>
      <Table.Td>
        <strong>{item.name}</strong>
      </Table.Td>
      <Table.Td>
        <Badge size="sm" variant="light">
          {item.type}
        </Badge>
      </Table.Td>
      <Table.Td>{item.updated || '—'}</Table.Td>
      <Table.Td>{item.rating ? `${item.rating}★` : '—'}</Table.Td>
      <Table.Td>
        {item.status && (
          <Badge size="sm" color={item.status === 'active' ? 'green' : 'red'}>
            {item.status}
          </Badge>
        )}
      </Table.Td>
    </Table.Tr>
  ));

  return (
    <Table data-testid="results-table" striped highlightOnHover>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Name</Table.Th>
          <Table.Th>Type</Table.Th>
          <Table.Th>Updated</Table.Th>
          <Table.Th>Rating</Table.Th>
          <Table.Th>Status</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>{rows}</Table.Tbody>
    </Table>
  );
};

// ============================================================================
// COMPONENT: MARKETPLACE SEARCH (FULL PAGE)
// ============================================================================

export const MarketplaceSearch: React.FC<MarketplaceSearchProps> = ({ data, onResultSelect }) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [filters, setFilters] = useState<SearchFilters>({
    types: [],
    statuses: [],
    sortField: 'name',
    sortOrder: 'asc',
  });
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Filter & sort results
  const filteredResults = useMemo(() => {
    let results = data;

    // Text search
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      results = results.filter((item) => item.name.toLowerCase().includes(query));
    }

    // Type filter
    if (filters.types.length > 0) {
      results = results.filter((item) => filters.types.includes(item.type));
    }

    // Status filter
    if (filters.statuses.length > 0 && results[0]?.status) {
      results = results.filter((item) => filters.statuses.includes(item.status as any));
    }

    // Sort
    results.sort((a, b) => {
      let aVal = a[filters.sortField] ?? '';
      let bVal = b[filters.sortField] ?? '';

      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }

      const comparison = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return filters.sortOrder === 'asc' ? comparison : -comparison;
    });

    return results;
  }, [data, searchQuery, filters]);

  return (
    <Container fluid data-testid="marketplace-search">
      <Stack gap="md">
        {/* Search Input */}
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          onClear={() => setSearchQuery('')}
        />

        {/* Desktop Layout: Filters + Results */}
        <Group align="flex-start" gap="md">
          {/* Desktop Filters (hidden on mobile) */}
          <div style={{ display: 'none', '@media (min-width: 768px)': { display: 'block' } }}>
            <FilterPanel filters={filters} onFilterChange={setFilters} />
          </div>

          {/* Results */}
          <div style={{ flex: 1 }} data-testid="card-grid">
            <Group justify="space-between" mb="md">
              <Badge>{filteredResults.length} results</Badge>
              <Button
                leftSection={<IconFilter size={14} />}
                variant="light"
                onClick={() => setDrawerOpen(true)}
              >
                Filters
              </Button>
            </Group>

            {filteredResults.length > 0 ? (
              <ResultsTable data={filteredResults} onRowClick={onResultSelect} />
            ) : (
              <div style={{ textAlign: 'center', padding: '2rem' }}>
                <p>No results found</p>
              </div>
            )}
          </div>
        </Group>

        {/* Mobile Filter Drawer */}
        <Drawer
          opened={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          title="Filters"
          padding="md"
        >
          <FilterPanel filters={filters} onFilterChange={setFilters} isDrawer={true} />
        </Drawer>
      </Stack>
    </Container>
  );
};

export default MarketplaceSearch;
