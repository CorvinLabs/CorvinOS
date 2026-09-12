// Unified Forge API Type Definitions (Components + Tab Models)

// Simplified component-level types
export interface ForgeTool {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  version?: string;
  runtime?: string;
  scope_source?: string;
  actions?: string[];
  param_count?: number;
  call_count?: number;
  created_at?: string;
  sha256?: string;
  promoted?: boolean;
  registry_path?: string;
}

export interface ForgeSkill {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  version?: string;
  type?: string;
  scope_source?: string;
  created_at?: string;
  updated_at?: string;
  sha256?: string;
  versions?: string[];
  promoted?: boolean;
  skill_dir?: string;
  learning_state?: {
    confidence: number;
    feedback_count?: number;
    last_updated?: string;
  };
  grade_count?: number;
  mean_score?: number;
  injectable?: boolean;
}

export interface ForgeOSSkill {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  version?: string;
  layer?: string;
  boot_layer?: string;
  is_meta_skill?: boolean;
  config?: Record<string, any>;
  dependencies?: string[];
  created_at?: string;
  updated_at?: string;
  sha256?: string;
  scope_source?: string;
}

export interface ForgeDependency {
  source: string;
  target: string;
  type?: string;
  description?: string;
}

export interface ForgeAuditEvent {
  id: string;
  timestamp: string;
  type: string;
  resource_type: 'tool' | 'skill' | 'os_skill';
  resource_id: string;
  resource_name: string;
  action: string;
  user: string;
  details?: Record<string, any>;
  hash: string;
  prev_hash: string;
}

export interface ForgeSearchResult {
  type: 'tool' | 'skill' | 'os_skill';
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
}
