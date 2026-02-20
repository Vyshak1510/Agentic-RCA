import {
  AgentPromptProfile,
  AgentRolloutConfig,
  ConnectorCredentialView,
  InvestigationListResponse,
  InvestigationRecord,
  LlmRoute,
  McpServerConfig,
  McpToolDescriptor,
  UserContext,
  WorkflowLayoutState,
  WorkflowLayoutNode,
  WorkflowStageId,
  WorkflowViewport,
  WorkflowRunDetail,
  WorkflowRunSummary,
} from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY;
const DEFAULT_TENANT = process.env.NEXT_PUBLIC_DEFAULT_TENANT ?? "default";
const DEFAULT_ROLE = process.env.NEXT_PUBLIC_DEFAULT_ROLE ?? "admin";
const DEFAULT_USER = process.env.NEXT_PUBLIC_DEFAULT_USER ?? "web-ui";

function buildHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra ?? {});
  headers.set("x-user-role", DEFAULT_ROLE);
  headers.set("x-user-id", DEFAULT_USER);
  headers.set("x-tenant-id", DEFAULT_TENANT);
  if (API_KEY) {
    headers.set("x-api-key", API_KEY);
  }
  headers.set("content-type", headers.get("content-type") ?? "application/json");
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: buildHeaders(init?.headers),
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`API request failed (${response.status}): ${path}`);
  }

  return (await response.json()) as T;
}

export async function fetchMe(): Promise<UserContext> {
  return request<UserContext>("/v1/me");
}

export async function fetchInvestigations(params: Record<string, string | number | undefined>): Promise<InvestigationListResponse> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return request<InvestigationListResponse>(`/v1/investigations${query ? `?${query}` : ""}`);
}

export async function fetchInvestigation(id: string): Promise<InvestigationRecord> {
  return request<InvestigationRecord>(`/v1/investigations/${id}`);
}

export async function fetchInvestigationRuns(id: string): Promise<WorkflowRunSummary[]> {
  const payload = await request<{ items: WorkflowRunSummary[] }>(`/v1/investigations/${id}/runs`);
  return payload.items;
}

export async function fetchInvestigationRun(id: string, runId: string): Promise<WorkflowRunDetail> {
  return request<WorkflowRunDetail>(`/v1/investigations/${id}/runs/${runId}`);
}

export async function startInvestigationRun(id: string, publishOutputs = true): Promise<{ investigation_id: string; run_id: string; workflow_id: string | null; status: string }> {
  return request<{ investigation_id: string; run_id: string; workflow_id: string | null; status: string }>(`/v1/investigations/${id}/runs`, {
    method: "POST",
    body: JSON.stringify({ publish_outputs: publishOutputs })
  });
}

export async function rerunInvestigation(id: string): Promise<{ investigation_id: string; status: string; run_id: string; workflow_id: string }> {
  return request<{ investigation_id: string; status: string; run_id: string; workflow_id: string }>(`/v1/investigations/${id}/rerun`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function getInvestigationEventsUrl(id: string): string {
  const url = new URL(`${API_BASE}/v1/investigations/${id}/events`);
  if (API_KEY) {
    url.searchParams.set("apiKey", API_KEY);
  }
  return url.toString();
}

export function getRunEventsUrl(investigationId: string, runId: string): string {
  const url = new URL(`${API_BASE}/v1/investigations/${investigationId}/runs/${runId}/events`);
  if (API_KEY) {
    url.searchParams.set("apiKey", API_KEY);
  }
  return url.toString();
}

export async function fetchConnectorSettings(environment = "prod"): Promise<ConnectorCredentialView[]> {
  const payload = await request<{ items: ConnectorCredentialView[] }>(`/v1/settings/connectors?environment=${environment}`);
  return payload.items;
}

export async function upsertConnector(provider: string, payload: Record<string, string>): Promise<ConnectorCredentialView> {
  return request<ConnectorCredentialView>(`/v1/settings/connectors/${provider}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function testConnector(provider: string, environment = "prod"): Promise<{ success: boolean; detail: string }> {
  return request<{ success: boolean; detail: string }>(`/v1/settings/connectors/${provider}/test?environment=${environment}`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function fetchLlmRoutes(environment = "prod"): Promise<LlmRoute[]> {
  const payload = await request<{ items: LlmRoute[] }>(`/v1/settings/llm-routes?environment=${environment}`);
  return payload.items;
}

export async function upsertLlmRoute(route: LlmRoute): Promise<{ status: string }> {
  return request<{ status: string }>("/v1/settings/llm-routes", {
    method: "PUT",
    body: JSON.stringify(route)
  });
}

export async function fetchMcpServers(environment = "prod"): Promise<McpServerConfig[]> {
  const payload = await request<{ items: McpServerConfig[] }>(`/v1/settings/mcp-servers?environment=${environment}`);
  return payload.items;
}

export async function upsertMcpServer(
  serverId: string,
  payload: {
    tenant: string;
    environment: string;
    transport: "http_sse";
    base_url: string;
    secret_ref_name?: string;
    secret_ref_key?: string;
    timeout_seconds: number;
    enabled: boolean;
  }
): Promise<McpServerConfig> {
  return request<McpServerConfig>(`/v1/settings/mcp-servers/${serverId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function testMcpServer(serverId: string, environment = "prod"): Promise<{ success: boolean; detail: string }> {
  return request<{ success: boolean; detail: string }>(`/v1/settings/mcp-servers/${serverId}/test?environment=${environment}`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function fetchMcpServerTools(serverId: string, environment = "prod"): Promise<McpToolDescriptor[]> {
  const payload = await request<{ items: McpToolDescriptor[] }>(
    `/v1/settings/mcp-servers/${serverId}/tools?environment=${environment}`
  );
  return payload.items;
}

export async function fetchAgentPrompts(environment = "prod"): Promise<AgentPromptProfile[]> {
  const payload = await request<{ items: AgentPromptProfile[] }>(`/v1/settings/agent-prompts?environment=${environment}`);
  return payload.items;
}

export async function upsertAgentPrompt(
  stageId: WorkflowStageId,
  payload: {
    tenant: string;
    environment: string;
    system_prompt: string;
    objective_template: string;
    max_turns: number;
    max_tool_calls: number;
    tool_allowlist: string[];
  }
): Promise<AgentPromptProfile> {
  return request<AgentPromptProfile>(`/v1/settings/agent-prompts/${stageId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function fetchAgentRollout(environment = "prod"): Promise<AgentRolloutConfig> {
  return request<AgentRolloutConfig>(`/v1/settings/agent-rollout?environment=${environment}`);
}

export async function upsertAgentRollout(payload: {
  tenant: string;
  environment: string;
  mode: "compare" | "active";
}): Promise<AgentRolloutConfig> {
  return request<AgentRolloutConfig>("/v1/settings/agent-rollout", {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function fetchWorkflowLayout(workflowKey: string): Promise<WorkflowLayoutState | null> {
  const payload = await request<WorkflowLayoutState | { status: "not_found" }>(`/v1/ui/workflow-layouts/${workflowKey}`);
  if ("status" in payload && payload.status === "not_found") {
    return null;
  }
  return payload as WorkflowLayoutState;
}

export async function upsertWorkflowLayout(
  workflowKey: string,
  payload: { nodes: WorkflowLayoutNode[]; viewport: WorkflowViewport }
): Promise<WorkflowLayoutState> {
  return request<WorkflowLayoutState>(`/v1/ui/workflow-layouts/${workflowKey}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}
