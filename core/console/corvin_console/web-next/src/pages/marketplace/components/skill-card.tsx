/**
 * Skill Card Component (Phase 1 Session 1).
 *
 * Displays a single skill in the marketplace grid with:
 * - Title, author, category
 * - Rating + review count
 * - Install/enable/details buttons
 * - Tags and compatibility info
 */

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Star, Download, Eye } from "lucide-react";
import type { SearchResult } from "../discovery-api";

interface SkillCardProps {
  skill: SearchResult;
  onInstall?: () => void;
  onEnable?: () => void;
  onViewDetails?: () => void;
}

export function SkillCard({ skill, onInstall, onEnable, onViewDetails }: SkillCardProps) {
  const ratingColor = skill.rating
    ? skill.rating >= 4
      ? "text-green-600"
      : skill.rating >= 3
        ? "text-yellow-600"
        : "text-red-600"
    : "text-gray-400";

  const tierBadgeColor = skill.tier === "buildin" ? "bg-blue-100 text-blue-800" : "bg-purple-100 text-purple-800";

  return (
    <Card className="w-full h-full flex flex-col hover:shadow-md transition-shadow">
      <CardHeader className="pb-3">
        <div className="flex justify-between items-start gap-2">
          <div className="flex-1">
            <h3 className="font-semibold text-base line-clamp-2">{skill.name}</h3>
            <p className="text-xs text-gray-500 mt-1">by {skill.author}</p>
          </div>
          <Badge className={tierBadgeColor} variant="secondary">
            {skill.tier === "buildin" ? "Built-in" : "Community"}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="flex-1 flex flex-col gap-3">
        {/* Description */}
        <p className="text-sm text-gray-600 line-clamp-3">{skill.description}</p>

        {/* Metrics */}
        {skill.rating !== undefined && (
          <div className="flex items-center gap-3 text-sm">
            <div className="flex items-center gap-1">
              <Star className={`w-4 h-4 ${ratingColor}`} />
              <span className="font-medium">{skill.rating.toFixed(1)}</span>
            </div>
            {skill.download_count !== undefined && (
              <div className="flex items-center gap-1 text-gray-500">
                <Download className="w-4 h-4" />
                <span>{skill.download_count.toLocaleString()}</span>
              </div>
            )}
          </div>
        )}

        {/* Category + Tags */}
        <div className="flex gap-1 flex-wrap">
          <Badge variant="outline" className="text-xs">
            {skill.category}
          </Badge>
          {skill.tags?.slice(0, 2).map((tag) => (
            <Badge key={tag} variant="outline" className="text-xs">
              {tag}
            </Badge>
          ))}
        </div>

        {/* Version */}
        <div className="text-xs text-gray-500">v{skill.version}</div>

        {/* Actions */}
        <div className="flex gap-2 mt-auto pt-3">
          {skill.installed ? (
            skill.enabled ? (
              <Button size="sm" variant="outline" disabled className="flex-1">
                Enabled
              </Button>
            ) : (
              <Button size="sm" onClick={onEnable} className="flex-1">
                Enable
              </Button>
            )
          ) : skill.installable ? (
            <Button size="sm" onClick={onInstall} className="flex-1">
              Install
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled className="flex-1">
              Unavailable
            </Button>
          )}

          <Button size="sm" variant="ghost" onClick={onViewDetails}>
            <Eye className="w-4 h-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
