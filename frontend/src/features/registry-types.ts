export type Skill = {
  name: string; description: string; enabled: boolean; editable: boolean;
  source: string; command?: { name: string }; required_tools?: string[];
};

export type McpServer = {
  name: string; endpoint: string; state: string; transport: string;
  enabled: boolean; required: boolean; protocol_version?: string;
  registered_tools?: string[]; discovered_tools?: unknown[];
  cache_hit?: boolean; last_error_code?: string; retry_count?: number;
};

export type ProviderConnection = {
  provider: string; enabled: boolean; status: string; credential_configured: boolean;
  credential_error?: string; model_count?: number;
};

export type RegisteredModel = {
  id: string; display_name: string; provider: string; model: string;
  status: string; enabled: boolean; auto_eligible: boolean;
  context_window_tokens: number; max_output_tokens: number; configured_latency_ms?: number;
  routing_metadata?: { latency_source?: string; routing_latency_ms?: number; quality_tier?: string; cost_tier?: string };
  telemetry?: {
    sample_count?: number; total_latency_p95_ms?: number; updated_at?: string; last_error?: string;
    probe?: { last_failure_at?: string; last_success_at?: string; last_error?: string;
      success_count?: number; sample_count?: number; last_latency_ms?: number;
      latency_p50_ms?: number; updated_at?: string };
  };
};
