import * as React from "react";
import { ChevronDown } from "lucide-react";
import {
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { Command } from "@/lib/api";

interface CommandsHelpModalProps {
  commands: Record<string, Command[]>;
  tip: string;
}

export function CommandsHelpModal({
  commands,
  tip,
}: CommandsHelpModalProps) {
  const [searchQuery, setSearchQuery] = React.useState("");
  const [expandedCategories, setExpandedCategories] = React.useState<Set<string>>(
    new Set(Object.keys(commands))
  );

  // Filter categories and commands by search
  const filtered = React.useMemo(() => {
    const result: Record<string, Command[]> = {};
    const query = searchQuery.toLowerCase();

    Object.entries(commands).forEach(([category, cmds]) => {
      const matches = cmds.filter(
        (c) =>
          c.name.toLowerCase().includes(query) ||
          c.description.toLowerCase().includes(query) ||
          (c.details && c.details.toLowerCase().includes(query))
      );
      if (matches.length) {
        result[category] = matches;
      }
    });

    return result;
  }, [commands, searchQuery]);

  const toggleCategory = (category: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  };

  return (
    <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col">
      <DialogHeader className="shrink-0">
        <DialogTitle>Available Commands</DialogTitle>
        <DialogDescription>
          All commands work across all messaging channels (Discord, Telegram, Slack, WhatsApp, Email, Signal)
        </DialogDescription>
      </DialogHeader>

      {/* Search Input */}
      <div className="px-6 py-3 border-b shrink-0">
        <input
          type="text"
          placeholder="Search commands... (e.g., 'persona', 'audit', 'consent')"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full px-3 py-2 rounded-lg border border-border text-sm
                     focus:outline-none focus:ring-2 focus:ring-accent bg-background"
          autoFocus
        />
        {searchQuery && (
          <p className="text-xs text-muted-foreground mt-2">
            Found {Object.keys(filtered).length} categories, {Object.values(filtered).flat().length} commands
          </p>
        )}
      </div>

      {/* Commands List */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {Object.keys(filtered).length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <p className="text-sm">No commands match "{searchQuery}"</p>
            <p className="text-xs mt-2 text-muted-foreground/60">
              Try searching for: persona, audit, consent, engine, or other keywords
            </p>
          </div>
        ) : (
          <div className="space-y-5">
            {Object.entries(filtered).map(([category, cmds]) => (
              <CommandCategory
                key={category}
                category={category}
                commands={cmds}
                expanded={expandedCategories.has(category)}
                onToggle={() => toggleCategory(category)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer Tip */}
      <div className="px-6 py-3 border-t bg-muted/30 text-xs text-muted-foreground shrink-0">
        ℹ️ {tip}
      </div>
    </DialogContent>
  );
}

function CommandCategory({
  category,
  commands,
  expanded,
  onToggle,
}: {
  category: string;
  commands: Command[];
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="flex items-center gap-2 w-full text-sm font-semibold
                   text-foreground hover:text-accent transition-colors mb-3"
      >
        <ChevronDown
          className={cn(
            "h-4 w-4 transition-transform flex-shrink-0",
            expanded && "rotate-180"
          )}
        />
        <span>{category}</span>
      </button>

      {expanded && (
        <div className="space-y-3 ml-6 mb-5">
          {commands.map((cmd) => (
            <CommandItem key={cmd.name} command={cmd} />
          ))}
        </div>
      )}
    </div>
  );
}

function CommandItem({ command }: { command: Command }) {
  return (
    <div className="text-sm border-l-2 border-accent/30 pl-3">
      <div className="font-mono font-semibold text-accent text-xs">
        {command.name}
      </div>
      <div className="text-xs text-muted-foreground mb-1">
        {command.description}
      </div>
      {command.syntax && (
        <div className="text-xs bg-muted px-2 py-1 rounded font-mono text-foreground/80 mb-1 inline-block">
          {command.syntax}
        </div>
      )}
      {command.details && (
        <div className="text-xs text-muted-foreground leading-relaxed">
          {command.details}
        </div>
      )}
    </div>
  );
}
