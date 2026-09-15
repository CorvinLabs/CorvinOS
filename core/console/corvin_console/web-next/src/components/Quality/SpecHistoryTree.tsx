/**
 * SpecHistoryTree — Specification version history browser
 * Shows constraints evolution across iterations
 */

import React, { useState } from "react";
import { SpecConstraint } from "./types";

interface Props {
  constraints: SpecConstraint[];
  specVersion: number;
}

export default function SpecHistoryTree({ constraints, specVersion }: Props) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    critical_invariant: true,
    domain_fact: true,
    success_criterion: true,
  });

  const grouped = groupConstraints(constraints);

  const toggleGroup = (type: string) => {
    setExpandedGroups((prev) => ({ ...prev, [type]: !prev[type] }));
  };

  return (
    <div className="bg-white border rounded-lg p-6 shadow-sm">
      <div className="mb-4">
        <p className="text-sm text-gray-600">Current Specification Version</p>
        <p className="text-2xl font-bold">v{specVersion}</p>
        <p className="text-xs text-gray-500 mt-1">
          {constraints.length} constraint{constraints.length !== 1 ? "s" : ""}
        </p>
      </div>

      <div className="space-y-4">
        {Object.entries(grouped).map(([type, items]) => (
          <div key={type} className="border rounded">
            <button
              onClick={() => toggleGroup(type)}
              className="w-full px-4 py-2 flex justify-between items-center hover:bg-gray-50"
            >
              <span className="font-semibold text-sm capitalize">
                {type.replace("_", " ")}
              </span>
              <span className="text-xs text-gray-600 px-2 py-1 bg-gray-100 rounded">
                {items.length}
              </span>
            </button>

            {expandedGroups[type] && (
              <div className="border-t divide-y">
                {items.map((constraint) => (
                  <div key={constraint.name} className="px-4 py-3 text-sm">
                    <div className="flex justify-between items-start mb-1">
                      <code className="font-mono text-xs bg-gray-100 px-2 py-1 rounded">
                        {constraint.name}
                      </code>
                      <span className="text-xs text-gray-600">
                        weight: {constraint.weight.toFixed(2)}
                      </span>
                    </div>
                    <p className="text-gray-700 mb-2">{constraint.description}</p>
                    <div className="flex gap-2">
                      <span className="inline-block px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded">
                        v{constraint.version}
                      </span>
                      <span className="inline-block px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded">
                        {constraint.source}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {constraints.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          No constraints in specification
        </div>
      )}
    </div>
  );
}

function groupConstraints(
  constraints: SpecConstraint[]
): Record<string, SpecConstraint[]> {
  return {
    critical_invariant: constraints.filter((c) => c.type === "critical_invariant"),
    domain_fact: constraints.filter((c) => c.type === "domain_fact"),
    success_criterion: constraints.filter((c) => c.type === "success_criterion"),
  };
}
