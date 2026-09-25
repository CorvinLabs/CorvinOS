/**
 * A2A Peer Discovery Panel — Console UI for discovering and managing paired peers.
 *
 * Features:
 * - Live peer list with status indicators
 * - Pagination + filtering
 * - Detail view for each peer
 * - Dark/light mode support
 * - Mobile-responsive layout
 * - Accessibility (ARIA labels, keyboard navigation)
 *
 * API: GET /v1/console/discovery/peers
 */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Globe,
  Signal,
  SignalLow,
  MapPin,
  Clock,
  Plus,
  Search,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { HelpTooltip } from "@/components/ui/help-tooltip";
import { listDiscoveryPeers, type DiscoveryPeersResponse, type DiscoveryPeerInfo } from "@/lib/api/a2a";

// ── Components ────────────────────────────────────────────────────────

// ── Type Aliases for Readability ──────────────────────────────────────

type PeerInfo = DiscoveryPeerInfo;
type PeerListResponse = DiscoveryPeersResponse;

// ── Components ────────────────────────────────────────────────────────

/**
 * Status badge component.
 */
const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const isOnline = status === "online";
  return (
    <div className="flex items-center gap-2">
      {isOnline ? (
        <>
          <Signal className="h-4 w-4 text-green-600 dark:text-green-400" />
          <Badge variant="outline" className="bg-green-50 dark:bg-green-950">
            Online
          </Badge>
        </>
      ) : (
        <>
          <SignalLow className="h-4 w-4 text-slate-400 dark:text-slate-600" />
          <Badge variant="outline" className="bg-slate-50 dark:bg-slate-900">
            {status.charAt(0).toUpperCase() + status.slice(1)}
          </Badge>
        </>
      )}
    </div>
  );
};

/**
 * Peer list table.
 */
interface PeerListProps {
  peers: PeerInfo[];
  isLoading: boolean;
  onSelectPeer: (peer: PeerInfo) => void;
}

const PeerList: React.FC<PeerListProps> = ({
  peers,
  isLoading,
  onSelectPeer,
}) => {
  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (peers.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="pt-6">
          <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <Globe className="h-8 w-8 text-slate-300 dark:text-slate-700" />
            <div>
              <p className="font-medium text-slate-900 dark:text-slate-100">
                No Peers Discovered
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Generate an invite code to connect with other instances.
              </p>
            </div>
            <Button size="sm" className="mt-2" disabled>
              <Plus className="mr-2 h-4 w-4" />
              New Pairing
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Region</TableHead>
            <TableHead>Last Seen</TableHead>
            <TableHead className="w-[50px]"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {peers.map((peer) => (
            <TableRow
              key={peer.peer_id}
              onClick={() => onSelectPeer(peer)}
              className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-900"
            >
              <TableCell className="font-medium">{peer.name}</TableCell>
              <TableCell>
                <StatusBadge status={peer.status} />
              </TableCell>
              <TableCell className="text-sm text-slate-600 dark:text-slate-400">
                {peer.region ? (
                  <div className="flex items-center gap-1">
                    <MapPin className="h-4 w-4" />
                    {peer.region}
                  </div>
                ) : (
                  "—"
                )}
              </TableCell>
              <TableCell className="text-sm text-slate-600 dark:text-slate-400">
                {peer.last_seen ? (
                  <div className="flex items-center gap-1">
                    <Clock className="h-4 w-4" />
                    {new Date(peer.last_seen).toLocaleDateString()}
                  </div>
                ) : (
                  "—"
                )}
              </TableCell>
              <TableCell className="text-right">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectPeer(peer);
                  }}
                  className="text-blue-600 hover:underline dark:text-blue-400"
                  aria-label={`View details for ${peer.name}`}
                >
                  Details
                </button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
};

/**
 * Peer detail card.
 */
interface PeerDetailProps {
  peer: PeerInfo | null;
  onClose: () => void;
}

const PeerDetail: React.FC<PeerDetailProps> = ({ peer, onClose }) => {
  if (!peer) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 dark:bg-black/70">
      <Card className="w-full max-w-2xl mx-4">
        <CardHeader>
          <div className="flex items-start justify-between">
            <div>
              <CardTitle>{peer.name}</CardTitle>
              <CardDescription className="text-xs font-mono mt-1">
                {peer.peer_id}
              </CardDescription>
            </div>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              aria-label="Close detail view"
            >
              ✕
            </button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                Status
              </p>
              <div className="mt-1">
                <StatusBadge status={peer.status} />
              </div>
            </div>
            <div>
              <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                Instance ID
              </p>
              <p className="mt-1 text-sm font-mono">{peer.instance_id || "—"}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                Region
              </p>
              <p className="mt-1 text-sm">{peer.region || "—"}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                Endpoint
              </p>
              <p className="mt-1 text-sm font-mono break-all">{peer.endpoint || "—"}</p>
            </div>
            <div className="col-span-2">
              <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                Last Seen
              </p>
              <p className="mt-1 text-sm">
                {peer.last_seen
                  ? new Date(peer.last_seen).toLocaleString()
                  : "Never"}
              </p>
            </div>
          </div>

          <div className="flex gap-2 pt-4">
            <Button variant="outline" onClick={onClose}>
              Close
            </Button>
            <Button disabled>Connect (Coming Soon)</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

/**
 * Discovery Panel — main component.
 */
export const DiscoveryPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const [searchTerm, setSearchTerm] = React.useState("");
  const [selectedPeer, setSelectedPeer] = React.useState<PeerInfo | null>(null);

  const { data, isLoading, isError, error, refetch } =
    useQuery<PeerListResponse>({
      queryKey: ["discovery-peers"],
      queryFn: async ({ signal }) => listDiscoveryPeers(signal),
      refetchInterval: 60000, // Poll every 60 seconds
      retry: 2,
    });

  // Filter peers by search term
  const filteredPeers = React.useMemo(() => {
    if (!data?.peers) return [];
    return data.peers.filter(
      (p) =>
        p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        p.peer_id.toLowerCase().includes(searchTerm.toLowerCase())
    );
  }, [data?.peers, searchTerm]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <h1 className="text-3xl font-bold text-slate-900 dark:text-slate-100">
          Peer Discovery
        </h1>
        <p className="text-slate-600 dark:text-slate-400">
          Discover and connect with other CorvinOS instances via A2A pairing.
        </p>
      </div>

      {/* Error State */}
      {isError && (
        <Card className="border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950">
          <CardContent className="pt-6">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="font-medium text-red-900 dark:text-red-100">
                  Failed to Load Peers
                </p>
                <p className="text-sm text-red-800 dark:text-red-200 mt-1">
                  {error instanceof Error ? error.message : "Unknown error"}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => refetch()}
                  className="mt-3"
                >
                  <RefreshCw className="mr-2 h-4 w-4" />
                  Retry
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Controls */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
          <Input
            placeholder="Search peers by name or ID..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9"
            aria-label="Search peers"
          />
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isLoading}
            aria-label="Refresh peer list"
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
          </Button>
          <Button disabled size="sm">
            <Plus className="mr-2 h-4 w-4" />
            Generate Invite
          </Button>
        </div>
      </div>

      {/* Peer List Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Discovered Peers</CardTitle>
              <CardDescription>
                {data?.total === 0
                  ? "No peers connected"
                  : `${data?.total || 0} peer${data?.total === 1 ? "" : "s"} discovered`}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <PeerList
            peers={filteredPeers}
            isLoading={isLoading}
            onSelectPeer={setSelectedPeer}
          />
        </CardContent>
      </Card>

      {/* Peer Detail Modal */}
      <PeerDetail peer={selectedPeer} onClose={() => setSelectedPeer(null)} />
    </div>
  );
};

export default DiscoveryPanel;
