/**
 * Control Plane — Plugin Management Panel (ADR-2029 Stream 1)
 * Install, enable, disable, uninstall plugins
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Trash2, Power, PowerOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { toast } from "@/hooks/use-toast";

const API_BASE = "/v1/console/control-plane/plugins";

interface Plugin {
  plugin_id: string;
  name: string;
  version: string;
  boot_layer: string;
  enabled: boolean;
}

export default function ControlPlanePluginsPage() {
  const queryClient = useQueryClient();
  const [searchTerm, setSearchTerm] = React.useState("");

  // Fetch plugins
  const { data: plugins, isLoading } = useQuery<Plugin[]>({
    queryKey: ["control-plane-plugins"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}`);
      if (!res.ok) throw new Error("Failed to load plugins");
      return res.json();
    },
  });

  // There is no install here. A plugin is installed through the ONE marketplace
  // install route (POST /api/v1/marketplace/plugins/{id}/install, ADR-0892): it
  // resolves real source from the marketplace checkout, runs the manifest and
  // licence gates and writes the tenant registry. The PUT /install this page used
  // to call recorded an operator-typed id/name/version and installed no code.

  // Enable/Disable plugin
  const toggleMutation = useMutation({
    mutationFn: async ({
      pluginId,
      enable,
    }: {
      pluginId: string;
      enable: boolean;
    }) => {
      const endpoint = enable ? "enable" : "disable";
      const res = await fetch(`${API_BASE}/${pluginId}/${endpoint}`, {
        method: "PATCH",
      });
      if (!res.ok) throw new Error(`Failed to ${endpoint} plugin`);
      return res.json();
    },
    onSuccess: (_, { enable }) => {
      queryClient.invalidateQueries({ queryKey: ["control-plane-plugins"] });
      toast({
        title: enable
          ? "Plugin enabled successfully"
          : "Plugin disabled successfully",
      });
    },
    onError: () => {
      toast({ title: "Operation failed", variant: "destructive" });
    },
  });

  // Uninstall plugin
  const uninstallMutation = useMutation({
    mutationFn: async (pluginId: string) => {
      const res = await fetch(`${API_BASE}/${pluginId}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Failed to uninstall plugin");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-plane-plugins"] });
      toast({ title: "Plugin uninstalled successfully" });
    },
    onError: () => {
      toast({ title: "Failed to uninstall plugin", variant: "destructive" });
    },
  });

  const filteredPlugins = (plugins || []).filter(
    (p) =>
      p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      p.plugin_id.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="container mx-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Plugins</h1>
          <p className="mt-1 text-sm text-gray-600">
            Manage CorvinOS plugins and integrations
          </p>
        </div>
        <Button asChild>
          <Link to="/app/marketplace?tab=browse">
            <Plus className="mr-2 h-4 w-4" /> Install from Marketplace
          </Link>
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <Input
          placeholder="Search plugins..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      {/* Plugin List */}
      <div className="space-y-3">
        {isLoading ? (
          <>
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </>
        ) : filteredPlugins.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <p className="text-gray-600">No plugins found</p>
          </div>
        ) : (
          filteredPlugins.map((plugin) => (
            <div
              key={plugin.plugin_id}
              className="flex items-center justify-between rounded-lg border p-4"
            >
              <div className="flex-1">
                <h3 className="font-semibold">{plugin.name}</h3>
                <div className="mt-1 flex gap-2">
                  <Badge variant="outline">{plugin.version}</Badge>
                  <Badge
                    variant={
                      plugin.boot_layer === "bundled" ? "default" : "secondary"
                    }
                  >
                    {plugin.boot_layer}
                  </Badge>
                  <Badge
                    variant={plugin.enabled ? "default" : "secondary"}
                    className={
                      plugin.enabled ? "bg-green-600" : "bg-gray-400"
                    }
                  >
                    {plugin.enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </div>
              </div>
              <div className="flex gap-2">
                {plugin.enabled ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      toggleMutation.mutate({
                        pluginId: plugin.plugin_id,
                        enable: false,
                      })
                    }
                    disabled={toggleMutation.isPending}
                  >
                    <PowerOff className="mr-2 h-4 w-4" /> Disable
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={() =>
                      toggleMutation.mutate({
                        pluginId: plugin.plugin_id,
                        enable: true,
                      })
                    }
                    disabled={toggleMutation.isPending}
                  >
                    <Power className="mr-2 h-4 w-4" /> Enable
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => uninstallMutation.mutate(plugin.plugin_id)}
                  disabled={uninstallMutation.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

    </div>
  );
}
