import React, { useState, useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ShoppingBag, Package, Lightbulb, Wrench, Plug, Shield } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';

export interface MarketplaceStatus {
  plugins: { installed: number; available: number; status: 'active' | 'inactive' | 'pending' };
  skills: { active: number; available: number; status: 'active' | 'inactive' | 'pending' };
  tools: { registered: number; available: number; status: 'active' | 'inactive' | 'pending' };
  connectors: { configured: number; available: number; status: 'active' | 'inactive' | 'pending' };
  layers: { builtin: number; immutable: boolean; status: 'active' | 'inactive' | 'pending' };
}

interface TypeCardProps {
  type: 'plugins' | 'skills' | 'tools' | 'connectors' | 'layers';
  installed: number;
  available: number;
  status: 'active' | 'inactive' | 'pending';
  tier: string;
  license: string;
  link?: string;
  readOnly?: boolean;
}

const getTypeIcon = (type: string) => {
  const icons: Record<string, React.ReactNode> = {
    plugins: <Package className="w-6 h-6" />,
    skills: <Lightbulb className="w-6 h-6" />,
    tools: <Wrench className="w-6 h-6" />,
    connectors: <Plug className="w-6 h-6" />,
    layers: <Shield className="w-6 h-6" />,
  };
  return icons[type] || <ShoppingBag className="w-6 h-6" />;
};

const getStatusColor = (status: string) => {
  switch (status) {
    case 'active':
      return 'bg-green-100 text-green-800';
    case 'inactive':
      return 'bg-gray-100 text-gray-800';
    case 'pending':
      return 'bg-yellow-100 text-yellow-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
};

const TypeCard: React.FC<TypeCardProps> = ({
  type,
  installed,
  available,
  status,
  tier,
  license,
  link,
  readOnly,
}) => {
  const displayName = type.charAt(0).toUpperCase() + type.slice(1);

  return (
    <div className="border rounded-lg p-6 hover:shadow-lg transition-shadow bg-white dark:bg-slate-900 dark:border-slate-700">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="text-slate-600 dark:text-slate-400">{getTypeIcon(type)}</div>
          <h2 className="text-xl font-semibold text-slate-900 dark:text-white">{displayName}</h2>
        </div>
        <Badge className={getStatusColor(status)}>{status}</Badge>
      </div>

      <div className="space-y-2 mb-6 text-sm text-slate-600 dark:text-slate-400">
        <div className="flex justify-between">
          <span>📊 Installed/Available:</span>
          <span className="font-medium text-slate-900 dark:text-white">
            {installed}/{available}
          </span>
        </div>
        <div className="flex justify-between">
          <span>📋 Tier:</span>
          <span className="font-medium text-slate-900 dark:text-white">{tier}</span>
        </div>
        <div className="flex justify-between">
          <span>⚖️ License:</span>
          <span className="font-medium text-slate-900 dark:text-white">{license}</span>
        </div>
      </div>

      {readOnly ? (
        <div className="text-xs text-slate-500 italic">Read-only (compliance-critical)</div>
      ) : link ? (
        <a
          href={link}
          className="text-blue-600 hover:text-blue-700 hover:underline font-semibold text-sm"
        >
          Go to {displayName} Panel →
        </a>
      ) : null}
    </div>
  );
};

const MarketplaceHub: React.FC = () => {
  const [search, setSearch] = useState('');
  const [visibleCards, setVisibleCards] = useState<string[]>([
    'plugins',
    'skills',
    'tools',
    'connectors',
    'layers',
  ]);

  const { data: status, isLoading, isError } = useQuery<MarketplaceStatus>({
    queryKey: ['marketplace-status'],
    queryFn: async () => {
      const res = await fetch('/api/v1/marketplace/status');
      if (!res.ok) throw new Error('Failed to fetch marketplace status');
      return res.json();
    },
    staleTime: 30000, // 30 seconds
    retry: 2,
  });

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
  }, []);

  const filteredCards = useMemo(() => {
    if (!search) return visibleCards;
    const query = search.toLowerCase();
    return visibleCards.filter((card) => card.toLowerCase().includes(query));
  }, [search, visibleCards]);

  if (isLoading) {
    return (
      <div className="p-8 flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <p className="text-gray-600 dark:text-gray-400">Loading Marketplace...</p>
        </div>
      </div>
    );
  }

  if (isError || !status) {
    return (
      <div className="p-8 flex items-center justify-center min-h-screen">
        <div className="text-center text-red-600 dark:text-red-400">
          <p className="text-lg font-semibold mb-2">Error Loading Marketplace</p>
          <p className="text-sm">Please try refreshing the page.</p>
        </div>
      </div>
    );
  }

  const typeCards: TypeCardProps[] = [
    {
      type: 'plugins',
      installed: status.plugins.installed,
      available: status.plugins.available,
      status: status.plugins.status,
      tier: 'buildin',
      license: 'Apache-2.0 + CLA',
      link: '/console/plugin-center',
    },
    {
      type: 'skills',
      installed: status.skills.active,
      available: status.skills.available,
      status: status.skills.status,
      tier: 'mixed',
      license: 'Apache-2.0',
      link: '/console/skills',
    },
    {
      type: 'tools',
      installed: status.tools.registered,
      available: status.tools.available,
      status: status.tools.status,
      tier: 'mixed',
      license: 'Varies',
      link: '/console/mcp-tools',
    },
    {
      type: 'connectors',
      installed: status.connectors.configured,
      available: status.connectors.available,
      status: status.connectors.status,
      tier: 'beta',
      license: 'TBD',
      link: '/console/connectors',
    },
    {
      type: 'layers',
      installed: 0,
      available: status.layers.builtin,
      status: status.layers.status,
      tier: 'buildin',
      license: 'Apache-2.0',
      readOnly: true,
    },
  ];

  return (
    <div className="marketplace-hub min-h-screen bg-gray-50 dark:bg-slate-950">
      <div className="p-8 max-w-7xl mx-auto">
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <ShoppingBag className="w-8 h-8 text-blue-600" />
            <h1 className="text-4xl font-bold text-slate-900 dark:text-white">Marketplace</h1>
          </div>
          <p className="text-slate-600 dark:text-slate-400">
            Unified discovery hub for Plugins, Skills, Tools, Connectors, and Layers
          </p>
        </div>

        {/* Search Bar */}
        <div className="mb-8">
          <Input
            placeholder="Search by type (plugins, skills, tools, connectors, layers)..."
            value={search}
            onChange={handleSearchChange}
            className="max-w-md"
          />
        </div>

        {/* TypeCards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredCards.length > 0 ? (
            filteredCards.map((cardType) => {
              const card = typeCards.find((c) => c.type === cardType);
              return card ? <TypeCard key={cardType} {...card} /> : null;
            })
          ) : (
            <div className="col-span-full text-center py-12">
              <p className="text-slate-500 dark:text-slate-400">No results found for "{search}"</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MarketplaceHub;
