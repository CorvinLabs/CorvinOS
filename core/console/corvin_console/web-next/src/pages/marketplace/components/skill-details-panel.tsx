/**
 * Skill Details Panel (Phase 1 Session 1).
 *
 * Full-screen modal showing comprehensive skill information:
 * - Full description and long description
 * - Version history
 * - Dependencies and requirements
 * - Reviews and ratings
 * - Metrics (downloads, success rate, latency)
 * - Install/enable actions
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Award, Clock, Download, ExternalLink, FileText, Loader2, Star, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getSkillDetails, type SkillDetailsResponse } from "../discovery-api";

interface SkillDetailsPanelProps {
  pluginId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstall?: () => void;
  onEnable?: () => void;
}

export function SkillDetailsPanel({
  pluginId,
  open,
  onOpenChange,
  onInstall,
  onEnable,
}: SkillDetailsPanelProps) {
  const { data: details, isLoading, error } = useQuery({
    queryKey: ["skill-details", pluginId],
    queryFn: () => getSkillDetails(pluginId),
    enabled: open,
  });

  if (!open) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center h-64">
            <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-64 gap-2">
            <AlertCircle className="w-8 h-8 text-red-600" />
            <span className="text-red-600">Failed to load skill details</span>
          </div>
        ) : details ? (
          <>
            <DialogHeader>
              <div className="flex justify-between items-start">
                <div>
                  <DialogTitle className="text-2xl">{details.name}</DialogTitle>
                  <p className="text-sm text-gray-500 mt-2">by {details.author}</p>
                </div>
                <Badge
                  variant="secondary"
                  className={
                    details.tier === "buildin"
                      ? "bg-blue-100 text-blue-800"
                      : "bg-purple-100 text-purple-800"
                  }
                >
                  {details.tier === "buildin" ? "Built-in" : "Community"}
                </Badge>
              </div>
            </DialogHeader>

            <Tabs defaultValue="overview" className="w-full">
              <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="versions">Versions</TabsTrigger>
                <TabsTrigger value="reviews">Reviews</TabsTrigger>
                <TabsTrigger value="technical">Technical</TabsTrigger>
              </TabsList>

              {/* Overview Tab */}
              <TabsContent value="overview" className="space-y-4 mt-4">
                <div>
                  <h3 className="font-semibold mb-2">Description</h3>
                  <p className="text-sm text-gray-700">{details.description}</p>
                  {details.long_description && (
                    <p className="text-sm text-gray-600 mt-3">{details.long_description}</p>
                  )}
                </div>

                {/* Metrics */}
                <div className="grid grid-cols-2 gap-4">
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm flex items-center gap-2">
                        <Star className="w-4 h-4" />
                        Rating
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">
                        {details.metrics.rating.toFixed(1)}/5.0
                      </div>
                      <p className="text-xs text-gray-500">
                        {details.metrics.review_count} reviews
                      </p>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm flex items-center gap-2">
                        <Download className="w-4 h-4" />
                        Downloads
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">
                        {details.metrics.downloads.toLocaleString()}
                      </div>
                      <p className="text-xs text-gray-500">
                        Success rate: {(details.metrics.success_rate * 100).toFixed(0)}%
                      </p>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm flex items-center gap-2">
                        <Clock className="w-4 h-4" />
                        Latency
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">
                        {details.metrics.avg_latency_ms.toFixed(0)}ms
                      </div>
                      <p className="text-xs text-gray-500">average execution</p>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm flex items-center gap-2">
                        <Award className="w-4 h-4" />
                        Installs
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">
                        {details.metrics.installs.toLocaleString()}
                      </div>
                      <p className="text-xs text-gray-500">total installations</p>
                    </CardContent>
                  </Card>
                </div>

                {/* Metadata */}
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <p className="font-semibold text-gray-700">License</p>
                    <p className="text-gray-600">{details.license}</p>
                  </div>
                  <div>
                    <p className="font-semibold text-gray-700">Category</p>
                    <p className="text-gray-600">{details.category}</p>
                  </div>
                </div>

                {/* Tags */}
                {details.tags.length > 0 && (
                  <div>
                    <p className="font-semibold text-gray-700 mb-2">Tags</p>
                    <div className="flex flex-wrap gap-2">
                      {details.tags.map((tag) => (
                        <Badge key={tag} variant="outline">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                {/* Links */}
                <div className="flex gap-2 pt-4 flex-wrap">
                  {details.readme_url && (
                    <Button
                      size="sm"
                      variant="outline"
                      asChild
                    >
                      <a href={details.readme_url} target="_blank" rel="noopener noreferrer">
                        <FileText className="w-4 h-4 mr-2" />
                        README
                      </a>
                    </Button>
                  )}
                  {details.documentation_url && (
                    <Button
                      size="sm"
                      variant="outline"
                      asChild
                    >
                      <a href={details.documentation_url} target="_blank" rel="noopener noreferrer">
                        <FileText className="w-4 h-4 mr-2" />
                        Documentation
                      </a>
                    </Button>
                  )}
                  {details.source_url && (
                    <Button
                      size="sm"
                      variant="outline"
                      asChild
                    >
                      <a href={details.source_url} target="_blank" rel="noopener noreferrer">
                        <ExternalLink className="w-4 h-4 mr-2" />
                        Source Code
                      </a>
                    </Button>
                  )}
                </div>
              </TabsContent>

              {/* Versions Tab */}
              <TabsContent value="versions" className="space-y-2 mt-4">
                {details.versions.length > 0 ? (
                  details.versions.map((v) => (
                    <Card key={v.version}>
                      <CardHeader className="pb-2">
                        <div className="flex justify-between">
                          <CardTitle className="text-sm">v{v.version}</CardTitle>
                          <span className="text-xs text-gray-500">{v.release_date}</span>
                        </div>
                      </CardHeader>
                      <CardContent>
                        {v.release_notes && (
                          <p className="text-xs text-gray-600 mb-2">{v.release_notes}</p>
                        )}
                        <p className="text-xs text-gray-500">
                          {v.downloads.toLocaleString()} downloads
                        </p>
                      </CardContent>
                    </Card>
                  ))
                ) : (
                  <p className="text-sm text-gray-500">No version history available</p>
                )}
              </TabsContent>

              {/* Reviews Tab */}
              <TabsContent value="reviews" className="space-y-3 mt-4">
                {details.reviews.length > 0 ? (
                  details.reviews.map((review, i) => (
                    <Card key={i}>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm">
                          {String(review.author || "Anonymous")}
                        </CardTitle>
                        {/*
                          A review is typed Record<string, unknown>, so every
                          field has to be narrowed before it can be rendered -
                          same as the author/text fields above. `??` not `||`:
                          a rating of 0 is a real rating, not a missing one.
                        */}
                        <CardDescription className="text-xs">
                          {String(review.date ?? "")} · {String(review.rating ?? "-")} stars
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <p className="text-sm text-gray-700">{String(review.text || "")}</p>
                      </CardContent>
                    </Card>
                  ))
                ) : (
                  <p className="text-sm text-gray-500">No reviews yet</p>
                )}
              </TabsContent>

              {/* Technical Tab */}
              <TabsContent value="technical" className="space-y-3 mt-4">
                {details.dependencies.length > 0 && (
                  <div>
                    <h4 className="font-semibold text-sm mb-2">Dependencies</h4>
                    <div className="space-y-1">
                      {details.dependencies.map((dep) => (
                        <Badge key={dep} variant="outline" className="mr-2">
                          {dep}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                {details.requires_version && (
                  <div>
                    <p className="text-sm font-semibold">Requires</p>
                    <p className="text-sm text-gray-600">CorvinOS {details.requires_version}</p>
                  </div>
                )}

                {details.boot_layer && (
                  <div>
                    <p className="text-sm font-semibold">Boot Layer</p>
                    <p className="text-sm text-gray-600">{details.boot_layer}</p>
                  </div>
                )}

                {details.sla_level && (
                  <div>
                    <p className="text-sm font-semibold">SLA Level</p>
                    <p className="text-sm text-gray-600">{details.sla_level}</p>
                  </div>
                )}
              </TabsContent>
            </Tabs>

            {/* Action Buttons */}
            <div className="flex gap-2 mt-6 pt-4 border-t">
              <Button onClick={onInstall} className="flex-1">
                Install
              </Button>
              <Button onClick={onEnable} variant="outline" className="flex-1">
                Enable
              </Button>
            </div>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
