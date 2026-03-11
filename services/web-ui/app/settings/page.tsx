"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  fetchAgentPrompts,
  fetchAgentRollout,
  fetchConnectorSettings,
  fetchLlmRoutes,
  fetchMcpServerTools,
  fetchMcpServers,
  testConnector,
  testMcpServer,
  upsertAgentPrompt,
  upsertAgentRollout,
  upsertConnector,
  upsertLlmRoute,
  upsertMcpServer,
} from "@/lib/api";
import {
  AgentPromptProfile,
  AgentRolloutConfig,
  ConnectorCredentialView,
  LlmRoute,
  McpServerConfig,
  McpToolDescriptor,
  WorkflowStageId,
} from "@/lib/types";

const STAGE_OPTIONS: WorkflowStageId[] = ["resolve_service_identity", "build_investigation_plan"];

export default function SettingsPage() {
  const [connectors, setConnectors] = useState<ConnectorCredentialView[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerConfig[]>([]);
  const [mcpToolPreview, setMcpToolPreview] = useState<Record<string, McpToolDescriptor[]>>({});
  const [agentPrompts, setAgentPrompts] = useState<AgentPromptProfile[]>([]);
  const [rollout, setRollout] = useState<AgentRolloutConfig>({
    tenant: "default",
    environment: "prod",
    mode: "compare",
    updated_at: new Date().toISOString(),
    updated_by: "web-ui",
  });
  const [llmRoute, setLlmRoute] = useState<LlmRoute>({
    tenant: "default",
    environment: "prod",
    primary_model: "codex",
    fallback_model: "claude",
    key_ref: "llm-provider-secret"
  });
  const [selectedPromptStage, setSelectedPromptStage] = useState<WorkflowStageId>("resolve_service_identity");
  const [promptDraft, setPromptDraft] = useState({
    system_prompt: "",
    objective_template: "",
    max_turns: 4,
    max_tool_calls: 6,
    tool_allowlist_csv: "",
  });
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [connectorData, llmData, mcpData, promptData, rolloutData] = await Promise.all([
          fetchConnectorSettings("prod"),
          fetchLlmRoutes("prod"),
          fetchMcpServers("prod"),
          fetchAgentPrompts("prod"),
          fetchAgentRollout("prod"),
        ]);
        setConnectors(connectorData);
        if (llmData[0]) {
          setLlmRoute(llmData[0]);
        }
        setMcpServers(mcpData);
        setAgentPrompts(promptData);
        setRollout(rolloutData);
        const defaultPrompt = promptData.find((item) => item.stage_id === "resolve_service_identity") ?? promptData[0];
        if (defaultPrompt) {
          setSelectedPromptStage(defaultPrompt.stage_id);
          setPromptDraft({
            system_prompt: defaultPrompt.system_prompt,
            objective_template: defaultPrompt.objective_template,
            max_turns: defaultPrompt.max_turns,
            max_tool_calls: defaultPrompt.max_tool_calls,
            tool_allowlist_csv: defaultPrompt.tool_allowlist.join(","),
          });
        }
      } catch (error) {
        setFeedback(error instanceof Error ? error.message : "Failed to load settings");
      }
    }

    void load();
  }, []);

  async function onConnectorSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const provider = String(form.get("provider"));
    const mode = String(form.get("mode"));

    try {
      if (mode === "raw_key") {
        const raw_key = String(form.get("raw_key") || "");
        await upsertConnector(provider, {
          tenant: "default",
          environment: "prod",
          mode,
          raw_key
        });
      } else {
        await upsertConnector(provider, {
          tenant: "default",
          environment: "prod",
          mode,
          secret_ref_name: String(form.get("secret_ref_name") || ""),
          secret_ref_key: String(form.get("secret_ref_key") || "")
        });
      }

      const updated = await fetchConnectorSettings("prod");
      setConnectors(updated);
      setFeedback(`Saved connector settings for ${provider}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save connector");
    }
  }

  async function onTestConnector(provider: string) {
    try {
      const result = await testConnector(provider, "prod");
      setFeedback(`${provider}: ${result.detail}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Connection test failed");
    }
  }

  async function onLlmRouteSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await upsertLlmRoute(llmRoute);
      setFeedback("Saved LLM routing settings");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save LLM routes");
    }
  }

  async function onMcpSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const serverId = String(form.get("server_id") || "").trim();
    if (!serverId) {
      setFeedback("server_id is required");
      return;
    }
    try {
      await upsertMcpServer(serverId, {
        tenant: "default",
        environment: "prod",
        transport: "http_sse",
        base_url: String(form.get("base_url") || ""),
        secret_ref_name: String(form.get("secret_ref_name") || ""),
        secret_ref_key: String(form.get("secret_ref_key") || ""),
        timeout_seconds: Number(form.get("timeout_seconds") || 8),
        enabled: String(form.get("enabled") || "true") === "true",
      });
      const updated = await fetchMcpServers("prod");
      setMcpServers(updated);
      setFeedback(`Saved MCP server ${serverId}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save MCP server");
    }
  }

  async function onTestMcp(serverId: string) {
    try {
      const result = await testMcpServer(serverId, "prod");
      setFeedback(`${serverId}: ${result.detail}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "MCP test failed");
    }
  }

  async function onLoadMcpTools(serverId: string) {
    try {
      const tools = await fetchMcpServerTools(serverId, "prod");
      setMcpToolPreview((current) => ({ ...current, [serverId]: tools }));
      setFeedback(`${serverId}: loaded ${tools.length} tool(s)`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to load MCP tools");
    }
  }

  async function onPromptSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await upsertAgentPrompt(selectedPromptStage, {
        tenant: "default",
        environment: "prod",
        system_prompt: promptDraft.system_prompt,
        objective_template: promptDraft.objective_template,
        max_turns: promptDraft.max_turns,
        max_tool_calls: promptDraft.max_tool_calls,
        tool_allowlist: promptDraft.tool_allowlist_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      const updated = await fetchAgentPrompts("prod");
      setAgentPrompts(updated);
      setFeedback(`Saved prompt profile for ${selectedPromptStage}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save prompt profile");
    }
  }

  async function onRolloutSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const updated = await upsertAgentRollout({
        tenant: rollout.tenant,
        environment: rollout.environment,
        mode: rollout.mode,
      });
      setRollout(updated);
      setFeedback("Saved agent rollout mode");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save rollout mode");
    }
  }

  function onPromptStageChange(stageId: WorkflowStageId) {
    setSelectedPromptStage(stageId);
    const profile = agentPrompts.find((item) => item.stage_id === stageId);
    if (!profile) {
      return;
    }
    setPromptDraft({
      system_prompt: profile.system_prompt,
      objective_template: profile.objective_template,
      max_turns: profile.max_turns,
      max_tool_calls: profile.max_tool_calls,
      tool_allowlist_csv: profile.tool_allowlist.join(","),
    });
  }

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h2 className="text-xl font-semibold">Settings</h2>
        <p className="text-sm text-slate-600">Manage connectors, model routing, MCP servers, and agent prompt/rollout controls.</p>
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <form onSubmit={onConnectorSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-700">Connector Credentials</h3>
          <div className="space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-slate-600">Provider</span>
              <select name="provider" className="w-full rounded-md border border-slate-300 px-3 py-2">
                <option value="newrelic">New Relic</option>
                <option value="azure">Azure</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Mode</span>
              <select name="mode" className="w-full rounded-md border border-slate-300 px-3 py-2">
                <option value="secret_ref">Secret Reference</option>
                <option value="raw_key">Raw Key (masked)</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Secret Ref Name</span>
              <input name="secret_ref_name" placeholder="rca-connector-secret" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Secret Ref Key</span>
              <input name="secret_ref_key" placeholder="apiKey" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Raw Key</span>
              <input name="raw_key" type="password" placeholder="paste key for masked storage" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <button type="submit" className="rounded-md bg-ink px-4 py-2 font-semibold text-white">Save Connector</button>
          </div>
        </form>

        <form onSubmit={onLlmRouteSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-700">LLM Routing</h3>
          <div className="space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-slate-600">Tenant</span>
              <input value={llmRoute.tenant} onChange={(event) => setLlmRoute({ ...llmRoute, tenant: event.target.value })} className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Environment</span>
              <input value={llmRoute.environment} onChange={(event) => setLlmRoute({ ...llmRoute, environment: event.target.value })} className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Primary Model</span>
              <input value={llmRoute.primary_model} onChange={(event) => setLlmRoute({ ...llmRoute, primary_model: event.target.value })} className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Fallback Model</span>
              <input value={llmRoute.fallback_model} onChange={(event) => setLlmRoute({ ...llmRoute, fallback_model: event.target.value })} className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Key Reference</span>
              <input value={llmRoute.key_ref} onChange={(event) => setLlmRoute({ ...llmRoute, key_ref: event.target.value })} className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <button type="submit" className="rounded-md bg-ink px-4 py-2 font-semibold text-white">Save LLM Route</button>
          </div>
        </form>
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <form onSubmit={onMcpSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-700">MCP Server Registry</h3>
          <div className="space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-slate-600">Server ID</span>
              <input name="server_id" placeholder="grafana-mcp" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Base URL</span>
              <input name="base_url" placeholder="https://mcp.example.com" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Secret Ref Name</span>
              <input name="secret_ref_name" placeholder="mcp-auth-secret" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Secret Ref Key</span>
              <input name="secret_ref_key" placeholder="NEW_RELIC_API_KEY or GRAFANA_MCP_API_KEY" className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Timeout Seconds</span>
              <input name="timeout_seconds" type="number" min={1} max={60} defaultValue={8} className="w-full rounded-md border border-slate-300 px-3 py-2" />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Enabled</span>
              <select name="enabled" defaultValue="true" className="w-full rounded-md border border-slate-300 px-3 py-2">
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </label>
            <button type="submit" className="rounded-md bg-ink px-4 py-2 font-semibold text-white">Save MCP Server</button>
          </div>
        </form>

        <form onSubmit={onRolloutSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-700">Agent Rollout</h3>
          <div className="space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-slate-600">Mode</span>
              <select
                value={rollout.mode}
                onChange={(event) => setRollout({ ...rollout, mode: event.target.value as "compare" | "active" })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="compare">compare</option>
                <option value="active">active</option>
              </select>
            </label>
            <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              Compare mode keeps deterministic resolver/planner active while scoring agent output in parallel.
            </p>
            <button type="submit" className="rounded-md bg-ink px-4 py-2 font-semibold text-white">Save Rollout Mode</button>
          </div>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Agent Prompt Profiles</h3>
        <form onSubmit={onPromptSubmit} className="grid gap-3 text-sm">
          <label className="block">
            <span className="mb-1 block text-slate-600">Stage</span>
            <select
              value={selectedPromptStage}
              onChange={(event) => onPromptStageChange(event.target.value as WorkflowStageId)}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            >
              {STAGE_OPTIONS.map((stage) => (
                <option key={stage} value={stage}>
                  {stage}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">System Prompt</span>
            <textarea
              rows={5}
              value={promptDraft.system_prompt}
              onChange={(event) => setPromptDraft({ ...promptDraft, system_prompt: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Objective Template</span>
            <textarea
              rows={4}
              value={promptDraft.objective_template}
              onChange={(event) => setPromptDraft({ ...promptDraft, objective_template: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-slate-600">Max Turns</span>
              <input
                type="number"
                min={1}
                max={20}
                value={promptDraft.max_turns}
                onChange={(event) => setPromptDraft({ ...promptDraft, max_turns: Number(event.target.value) })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Max Tool Calls</span>
              <input
                type="number"
                min={1}
                max={40}
                value={promptDraft.max_tool_calls}
                onChange={(event) => setPromptDraft({ ...promptDraft, max_tool_calls: Number(event.target.value) })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-slate-600">Tool Allowlist (comma-separated, optional)</span>
            <input
              value={promptDraft.tool_allowlist_csv}
              onChange={(event) => setPromptDraft({ ...promptDraft, tool_allowlist_csv: event.target.value })}
              placeholder="mcp.grafana.*,mcp.jaeger.*"
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <button type="submit" className="w-fit rounded-md bg-ink px-4 py-2 font-semibold text-white">Save Prompt Profile</button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Stored Connector Configurations</h3>
        <div className="space-y-2 text-sm">
          {connectors.map((connector) => (
            <div key={`${connector.provider}-${connector.environment}`} className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
              <strong className="capitalize">{connector.provider}</strong>
              <span>mode={connector.mode}</span>
              <span>key_last4={connector.key_last4 ?? "n/a"}</span>
              <button
                onClick={() => {
                  void onTestConnector(connector.provider);
                }}
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold"
              >
                Test Connection
              </button>
            </div>
          ))}
          {!connectors.length ? <p className="text-slate-500">No connector settings yet.</p> : null}
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">MCP Servers and Tool Preview</h3>
        <div className="space-y-3 text-sm">
          {mcpServers.map((server) => (
            <div key={`${server.server_id}-${server.environment}`} className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <strong>{server.server_id}</strong>
                <span>url={server.base_url}</span>
                <span>enabled={String(server.enabled)}</span>
                <button type="button" onClick={() => void onTestMcp(server.server_id)} className="rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold">
                  Test
                </button>
                <button type="button" onClick={() => void onLoadMcpTools(server.server_id)} className="rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold">
                  Load Tools
                </button>
              </div>
              {mcpToolPreview[server.server_id]?.length ? (
                <ul className="mt-2 space-y-1 text-xs text-slate-700">
                  {mcpToolPreview[server.server_id].map((tool) => (
                    <li key={`${tool.server_id}-${tool.tool_name}`} className="rounded border border-slate-200 bg-white px-2 py-1">
                      {tool.tool_name} · read_only={String(tool.read_only)} · light_probe={String(tool.light_probe)}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))}
          {!mcpServers.length ? <p className="text-slate-500">No MCP servers configured yet.</p> : null}
        </div>
      </section>

      {feedback ? <div className="rounded-md border border-cyan/40 bg-cyan/10 px-3 py-2 text-sm text-slate-700">{feedback}</div> : null}
    </div>
  );
}
