import { useState, useEffect } from "react";
import { Brain, Save, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface Model {
  id: string;
  name: string;
  provider: string;
  cost_per_1k: number;
  latency_ms: number;
  capabilities: string[];
}

interface Config {
  default_model: string;
  cost_threshold?: number;
  latency_threshold?: number;
}

export function ModelSelectionPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [costSlider, setCostSlider] = useState(0.5);
  const [changes, setChanges] = useState(false);

  useEffect(() => {
    fetchModels();
    fetchConfig();
  }, []);

  const fetchModels = async () => {
    try {
      const response = await fetch("/v1/models/available");
      const data = await response.json();
      setModels(data.models || []);
    } catch (error) {
      console.error("Failed to fetch models:", error);
      setModels([]);
    }
  };

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const response = await fetch("/v1/models/config");
      const data = await response.json();
      setConfig(data.config);
      setSelectedModel(data.config?.default_model || "");
    } catch (error) {
      console.error("Failed to fetch config:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      const response = await fetch("/v1/models/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          default_model: selectedModel,
          cost_threshold: costSlider,
        }),
      });
      if (response.ok) {
        setChanges(false);
        await fetchConfig();
      }
    } catch (error) {
      console.error("Failed to save config:", error);
    }
  };

  const handleModelChange = (modelId: string) => {
    setSelectedModel(modelId);
    setChanges(true);
  };

  const selectedModelData = models.find((m) => m.id === selectedModel);
  const sortedByLatency = [...models].sort((a, b) => a.latency_ms - b.latency_ms);
  const sortedByCost = [...models].sort((a, b) => a.cost_per_1k - b.cost_per_1k);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="h-6 w-6" />
          <h1 className="text-2xl font-bold">Model Selection</h1>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setSelectedModel(config?.default_model || "");
              setChanges(false);
            }}
            disabled={!changes}
          >
            <RotateCcw className="h-4 w-4 mr-2" />
            Reset
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!changes || !selectedModel}>
            <Save className="h-4 w-4 mr-2" />
            Save Configuration
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-8 text-muted-foreground">
          Loading models...
        </div>
      ) : (
        <>
          <Card className="p-4">
            <h3 className="text-lg font-semibold mb-4">Current Selection</h3>
            {selectedModelData ? (
              <div className="space-y-3">
                <div>
                  <div className="text-sm font-medium text-muted-foreground">
                    Model
                  </div>
                  <div className="text-xl font-bold">{selectedModelData.name}</div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-sm font-medium text-muted-foreground">
                      Cost per 1k tokens
                    </div>
                    <div className="text-lg font-semibold">
                      ${selectedModelData.cost_per_1k.toFixed(4)}
                    </div>
                  </div>
                  <div>
                    <div className="text-sm font-medium text-muted-foreground">
                      Latency
                    </div>
                    <div className="text-lg font-semibold">
                      {selectedModelData.latency_ms}ms
                    </div>
                  </div>
                </div>
                <div>
                  <div className="text-sm font-medium text-muted-foreground mb-2">
                    Capabilities
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {selectedModelData.capabilities.map((cap) => (
                      <Badge key={cap} variant="secondary">
                        {cap}
                      </Badge>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-muted-foreground">No model selected</div>
            )}
          </Card>

          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">Cost vs Latency Tradeoff</label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={costSlider}
                onChange={(e) => {
                  setCostSlider(parseFloat(e.target.value));
                  setChanges(true);
                }}
                className="w-full mt-2"
              />
              <div className="flex justify-between text-xs text-muted-foreground mt-1">
                <span>Cost optimized</span>
                <span>Latency optimized</span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card className="p-4">
              <h3 className="text-lg font-semibold mb-4">Fastest Models</h3>
              <div className="space-y-2">
                {sortedByLatency.slice(0, 3).map((model) => (
                  <button
                    key={model.id}
                    onClick={() => handleModelChange(model.id)}
                    className={`w-full text-left p-3 rounded-lg border-2 transition-all ${
                      selectedModel === model.id
                        ? "border-blue-500 bg-blue-50"
                        : "border-gray-200 hover:border-gray-300"
                    }`}
                  >
                    <div className="font-medium">{model.name}</div>
                    <div className="text-sm text-muted-foreground">
                      {model.latency_ms}ms latency
                    </div>
                  </button>
                ))}
              </div>
            </Card>

            <Card className="p-4">
              <h3 className="text-lg font-semibold mb-4">Most Cost-Effective</h3>
              <div className="space-y-2">
                {sortedByCost.slice(0, 3).map((model) => (
                  <button
                    key={model.id}
                    onClick={() => handleModelChange(model.id)}
                    className={`w-full text-left p-3 rounded-lg border-2 transition-all ${
                      selectedModel === model.id
                        ? "border-blue-500 bg-blue-50"
                        : "border-gray-200 hover:border-gray-300"
                    }`}
                  >
                    <div className="font-medium">{model.name}</div>
                    <div className="text-sm text-muted-foreground">
                      ${model.cost_per_1k.toFixed(4)}/1k tokens
                    </div>
                  </button>
                ))}
              </div>
            </Card>
          </div>

          <Card className="p-4">
            <h3 className="text-lg font-semibold mb-4">All Models</h3>
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {models.map((model) => (
                <button
                  key={model.id}
                  onClick={() => handleModelChange(model.id)}
                  className={`w-full text-left p-3 rounded-lg border-2 transition-all ${
                    selectedModel === model.id
                      ? "border-blue-500 bg-blue-50"
                      : "border-gray-200 hover:border-gray-300"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-medium">{model.name}</div>
                      <div className="text-sm text-muted-foreground">
                        {model.provider}
                      </div>
                    </div>
                    <div className="text-right text-sm">
                      <div>${model.cost_per_1k.toFixed(4)}/1k</div>
                      <div>{model.latency_ms}ms</div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
