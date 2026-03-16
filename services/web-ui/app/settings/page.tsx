"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  activateContextPack,
  createContextPack,
  fetchAgentPrompts,
  fetchAgentRollout,
  fetchConnectorSettings,
  fetchContextPacks,
  fetchInvestigationTeams,
  fetchLlmRoutes,
  fetchMcpServerTools,
  fetchMcpServers,
  fetchStageMission,
  fetchTeamMission,
  testConnector,
  testMcpServer,
  upsertAgentPrompt,
  upsertAgentRollout,
  upsertConnector,
  upsertInvestigationTeam,
  upsertLlmRoute,
  upsertMcpServer,
  upsertStageMission,
  upsertTeamMission,
  uploadContextArtifact,
} from "@/lib/api";
import {
  AgentPromptProfile,
  AgentRolloutConfig,
  ConnectorCredentialView,
  ContextPack,
  InvestigationTeamProfile,
  LlmRoute,
  McpServerConfig,
  McpToolDescriptor,
  StageMissionProfile,
  TeamMissionProfile,
  WorkflowStageId,
} from "@/lib/types";

const STAGE_OPTIONS: WorkflowStageId[] = [
  "resolve_service_identity",
  "build_investigation_plan",
  "collect_evidence",
  "synthesize_rca_report",
  "emit_eval_event",
];

export default function SettingsPage() {
  const [connectors, setConnectors] = useState<ConnectorCredentialView[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerConfig[]>([]);
  const [mcpToolPreview, setMcpToolPreview] = useState<Record<string, McpToolDescriptor[]>>({});
  const [investigationTeams, setInvestigationTeams] = useState<InvestigationTeamProfile[]>([]);
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
  const [selectedTeamId, setSelectedTeamId] = useState<string>("app");
  const [teamDraft, setTeamDraft] = useState({
    enabled: true,
    objective_prompt: "",
    tool_allowlist_csv: "",
    max_tool_calls: 6,
    max_parallel_calls: 3,
    timeout_seconds: 30,
  });
  const [stageMission, setStageMission] = useState<StageMissionProfile | null>(null);
  const [stageMissionDraft, setStageMissionDraft] = useState({
    mission_objective: "",
    required_checks_csv: "",
    allowed_tools_csv: "",
    completion_criteria_csv: "",
    unknown_rules_csv: "",
    relevance_weights_json: "{\"alert\":1.0}",
  });
  const [teamMission, setTeamMission] = useState<TeamMissionProfile | null>(null);
  const [teamMissionDraft, setTeamMissionDraft] = useState({
    mission_objective: "",
    required_checks_csv: "",
    allowed_tools_csv: "",
    completion_criteria_csv: "",
    unknown_rules_csv: "",
    relevance_weights_json: "{\"service_scoped\":1.0}",
  });
  const [contextPacks, setContextPacks] = useState<ContextPack[]>([]);
  const [activeContextPack, setActiveContextPack] = useState<ContextPack | null>(null);
  const [contextPackDraft, setContextPackDraft] = useState({
    pack_id: "",
    name: "",
    description: "",
    stage_bindings_csv: "",
    team_bindings_csv: "",
    service_tags_csv: "",
    infra_components_csv: "",
    dependencies_csv: "",
  });
  const [artifactDraft, setArtifactDraft] = useState({
    pack_id: "",
    filename: "",
    artifact_type: "markdown",
    media_type: "text/markdown",
    content: "",
    operator_notes: "",
  });

  useEffect(() => {
    async function load() {
      try {
        const [connectorData, llmData, mcpData, teamData, promptData, rolloutData, initialStageMission, contextPackState] = await Promise.all([
          fetchConnectorSettings("prod"),
          fetchLlmRoutes("prod"),
          fetchMcpServers("prod"),
          fetchInvestigationTeams("prod"),
          fetchAgentPrompts("prod"),
          fetchAgentRollout("prod"),
          fetchStageMission("resolve_service_identity", "prod"),
          fetchContextPacks("prod"),
        ]);
        setConnectors(connectorData);
        if (llmData[0]) {
          setLlmRoute(llmData[0]);
        }
        setMcpServers(mcpData);
        setInvestigationTeams(teamData);
        setAgentPrompts(promptData);
        setRollout(rolloutData);
        setStageMission(initialStageMission);
        setStageMissionDraft({
          mission_objective: initialStageMission.mission_objective,
          required_checks_csv: initialStageMission.required_checks.join(","),
          allowed_tools_csv: initialStageMission.allowed_tools.join(","),
          completion_criteria_csv: initialStageMission.completion_criteria.join(","),
          unknown_rules_csv: initialStageMission.unknown_not_available_rules.join(","),
          relevance_weights_json: JSON.stringify(initialStageMission.relevance_weights || {}, null, 2),
        });
        setContextPacks(contextPackState.items);
        setActiveContextPack(contextPackState.active);
        if (contextPackState.active) {
          setArtifactDraft((current) => ({ ...current, pack_id: contextPackState.active?.pack_id ?? "" }));
        }
        const defaultTeam = teamData.find((item) => item.team_id === "app") ?? teamData[0];
        if (defaultTeam) {
          setSelectedTeamId(defaultTeam.team_id);
          setTeamDraft({
            enabled: defaultTeam.enabled,
            objective_prompt: defaultTeam.objective_prompt,
            tool_allowlist_csv: defaultTeam.tool_allowlist.join(","),
            max_tool_calls: defaultTeam.max_tool_calls,
            max_parallel_calls: defaultTeam.max_parallel_calls,
            timeout_seconds: defaultTeam.timeout_seconds,
          });
          const defaultTeamMission = await fetchTeamMission(defaultTeam.team_id, "prod");
          setTeamMission(defaultTeamMission);
          setTeamMissionDraft({
            mission_objective: defaultTeamMission.mission_objective,
            required_checks_csv: defaultTeamMission.required_checks.join(","),
            allowed_tools_csv: defaultTeamMission.allowed_tools.join(","),
            completion_criteria_csv: defaultTeamMission.completion_criteria.join(","),
            unknown_rules_csv: defaultTeamMission.unknown_not_available_rules.join(","),
            relevance_weights_json: JSON.stringify(defaultTeamMission.relevance_weights || {}, null, 2),
          });
        }
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

  async function onPromptStageChange(stageId: WorkflowStageId) {
    setSelectedPromptStage(stageId);
    const profile = agentPrompts.find((item) => item.stage_id === stageId);
    if (!profile) {
      // continue to mission fetch even if prompt profile isn't loaded yet
    } else {
      setPromptDraft({
        system_prompt: profile.system_prompt,
        objective_template: profile.objective_template,
        max_turns: profile.max_turns,
        max_tool_calls: profile.max_tool_calls,
        tool_allowlist_csv: profile.tool_allowlist.join(","),
      });
    }
    try {
      const mission = await fetchStageMission(stageId, "prod");
      setStageMission(mission);
      setStageMissionDraft({
        mission_objective: mission.mission_objective,
        required_checks_csv: mission.required_checks.join(","),
        allowed_tools_csv: mission.allowed_tools.join(","),
        completion_criteria_csv: mission.completion_criteria.join(","),
        unknown_rules_csv: mission.unknown_not_available_rules.join(","),
        relevance_weights_json: JSON.stringify(mission.relevance_weights || {}, null, 2),
      });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to load stage mission");
    }
  }

  async function onTeamSelection(teamId: string) {
    setSelectedTeamId(teamId);
    const profile = investigationTeams.find((item) => item.team_id === teamId);
    if (!profile) {
      // keep going and still load mission defaults
    } else {
      setTeamDraft({
        enabled: profile.enabled,
        objective_prompt: profile.objective_prompt,
        tool_allowlist_csv: profile.tool_allowlist.join(","),
        max_tool_calls: profile.max_tool_calls,
        max_parallel_calls: profile.max_parallel_calls,
        timeout_seconds: profile.timeout_seconds,
      });
    }
    try {
      const mission = await fetchTeamMission(teamId, "prod");
      setTeamMission(mission);
      setTeamMissionDraft({
        mission_objective: mission.mission_objective,
        required_checks_csv: mission.required_checks.join(","),
        allowed_tools_csv: mission.allowed_tools.join(","),
        completion_criteria_csv: mission.completion_criteria.join(","),
        unknown_rules_csv: mission.unknown_not_available_rules.join(","),
        relevance_weights_json: JSON.stringify(mission.relevance_weights || {}, null, 2),
      });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to load team mission");
    }
  }

  async function onTeamSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await upsertInvestigationTeam(selectedTeamId, {
        tenant: "default",
        environment: "prod",
        enabled: teamDraft.enabled,
        objective_prompt: teamDraft.objective_prompt,
        tool_allowlist: teamDraft.tool_allowlist_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        max_tool_calls: teamDraft.max_tool_calls,
        max_parallel_calls: teamDraft.max_parallel_calls,
        timeout_seconds: teamDraft.timeout_seconds,
      });
      const updated = await fetchInvestigationTeams("prod");
      setInvestigationTeams(updated);
      setFeedback(`Saved investigation team profile for ${selectedTeamId}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save investigation team profile");
    }
  }

  async function onStageMissionSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const parsedWeights = JSON.parse(stageMissionDraft.relevance_weights_json || "{}") as Record<string, number>;
      const mission = await upsertStageMission(selectedPromptStage, {
        tenant: "default",
        environment: "prod",
        mission_objective: stageMissionDraft.mission_objective,
        required_checks: stageMissionDraft.required_checks_csv.split(",").map((item) => item.trim()).filter(Boolean),
        allowed_tools: stageMissionDraft.allowed_tools_csv.split(",").map((item) => item.trim()).filter(Boolean),
        completion_criteria: stageMissionDraft.completion_criteria_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        unknown_not_available_rules: stageMissionDraft.unknown_rules_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        relevance_weights: parsedWeights,
      });
      setStageMission(mission);
      setFeedback(`Saved stage mission for ${selectedPromptStage}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save stage mission");
    }
  }

  async function onTeamMissionSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const parsedWeights = JSON.parse(teamMissionDraft.relevance_weights_json || "{}") as Record<string, number>;
      const mission = await upsertTeamMission(selectedTeamId, {
        tenant: "default",
        environment: "prod",
        mission_objective: teamMissionDraft.mission_objective,
        required_checks: teamMissionDraft.required_checks_csv.split(",").map((item) => item.trim()).filter(Boolean),
        allowed_tools: teamMissionDraft.allowed_tools_csv.split(",").map((item) => item.trim()).filter(Boolean),
        completion_criteria: teamMissionDraft.completion_criteria_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        unknown_not_available_rules: teamMissionDraft.unknown_rules_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        relevance_weights: parsedWeights,
      });
      setTeamMission(mission);
      setFeedback(`Saved team mission for ${selectedTeamId}`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to save team mission");
    }
  }

  async function onContextPackCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const pack = await createContextPack({
        tenant: "default",
        environment: "prod",
        pack_id: contextPackDraft.pack_id.trim(),
        name: contextPackDraft.name.trim(),
        description: contextPackDraft.description.trim() || undefined,
        stage_bindings: contextPackDraft.stage_bindings_csv
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean) as WorkflowStageId[],
        team_bindings: contextPackDraft.team_bindings_csv.split(",").map((item) => item.trim()).filter(Boolean),
        service_tags: contextPackDraft.service_tags_csv.split(",").map((item) => item.trim()).filter(Boolean),
        infra_components: contextPackDraft.infra_components_csv.split(",").map((item) => item.trim()).filter(Boolean),
        dependencies: contextPackDraft.dependencies_csv.split(",").map((item) => item.trim()).filter(Boolean),
      });
      const updated = await fetchContextPacks("prod");
      setContextPacks(updated.items);
      setActiveContextPack(updated.active);
      setArtifactDraft((current) => ({ ...current, pack_id: pack.pack_id }));
      setFeedback(`Created context pack ${pack.pack_id} (v${pack.version})`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to create context pack");
    }
  }

  async function onContextArtifactUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const pack = await uploadContextArtifact(artifactDraft.pack_id, {
        tenant: "default",
        environment: "prod",
        filename: artifactDraft.filename.trim(),
        artifact_type: artifactDraft.artifact_type.trim(),
        media_type: artifactDraft.media_type.trim() || undefined,
        content: artifactDraft.content,
        operator_notes: artifactDraft.operator_notes.trim() || undefined,
        metadata: {},
      });
      const updated = await fetchContextPacks("prod");
      setContextPacks(updated.items);
      setActiveContextPack(updated.active);
      setFeedback(`Uploaded artifact to ${pack.pack_id} (v${pack.version})`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to upload context artifact");
    }
  }

  async function onActivateContextPack(packId: string) {
    try {
      const activated = await activateContextPack(packId, {
        tenant: "default",
        environment: "prod",
      });
      const updated = await fetchContextPacks("prod");
      setContextPacks(updated.items);
      setActiveContextPack(updated.active ?? activated);
      setFeedback(`Activated context pack ${activated.pack_id} (v${activated.version})`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Failed to activate context pack");
    }
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
              onChange={(event) => {
                void onPromptStageChange(event.target.value as WorkflowStageId);
              }}
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
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Stage Mission Policy</h3>
        <form onSubmit={onStageMissionSubmit} className="grid gap-3 text-sm">
          <label className="block">
            <span className="mb-1 block text-slate-600">Mission Objective</span>
            <textarea
              rows={3}
              value={stageMissionDraft.mission_objective}
              onChange={(event) => setStageMissionDraft({ ...stageMissionDraft, mission_objective: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Required Checks (comma-separated)</span>
            <input
              value={stageMissionDraft.required_checks_csv}
              onChange={(event) => setStageMissionDraft({ ...stageMissionDraft, required_checks_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Allowed Tools (comma-separated)</span>
            <input
              value={stageMissionDraft.allowed_tools_csv}
              onChange={(event) => setStageMissionDraft({ ...stageMissionDraft, allowed_tools_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Completion Criteria</span>
            <input
              value={stageMissionDraft.completion_criteria_csv}
              onChange={(event) => setStageMissionDraft({ ...stageMissionDraft, completion_criteria_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Unknown/Unavailable Rules</span>
            <input
              value={stageMissionDraft.unknown_rules_csv}
              onChange={(event) => setStageMissionDraft({ ...stageMissionDraft, unknown_rules_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Relevance Weights (JSON)</span>
            <textarea
              rows={4}
              value={stageMissionDraft.relevance_weights_json}
              onChange={(event) => setStageMissionDraft({ ...stageMissionDraft, relevance_weights_json: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
            />
          </label>
          <button type="submit" className="w-fit rounded-md bg-ink px-4 py-2 font-semibold text-white">Save Stage Mission</button>
          {stageMission ? (
            <p className="text-xs text-slate-500">
              current mission: {stageMission.stage_id} updated by {stageMission.updated_by}
            </p>
          ) : null}
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Investigation Team Profiles</h3>
        <form onSubmit={onTeamSubmit} className="grid gap-3 text-sm">
          <label className="block">
            <span className="mb-1 block text-slate-600">Team</span>
            <select
              value={selectedTeamId}
              onChange={(event) => {
                void onTeamSelection(event.target.value);
              }}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            >
              {investigationTeams.map((team) => (
                <option key={team.team_id} value={team.team_id}>
                  {team.team_id}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Enabled</span>
            <select
              value={String(teamDraft.enabled)}
              onChange={(event) => setTeamDraft({ ...teamDraft, enabled: event.target.value === "true" })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Objective Prompt</span>
            <textarea
              rows={4}
              value={teamDraft.objective_prompt}
              onChange={(event) => setTeamDraft({ ...teamDraft, objective_prompt: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Tool Allowlist (comma-separated)</span>
            <input
              value={teamDraft.tool_allowlist_csv}
              onChange={(event) => setTeamDraft({ ...teamDraft, tool_allowlist_csv: event.target.value })}
              placeholder="mcp.jaeger.*,mcp.grafana.find_slow_requests"
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="block">
              <span className="mb-1 block text-slate-600">Max Tool Calls</span>
              <input
                type="number"
                min={1}
                max={40}
                value={teamDraft.max_tool_calls}
                onChange={(event) => setTeamDraft({ ...teamDraft, max_tool_calls: Number(event.target.value) })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Max Parallel Calls</span>
              <input
                type="number"
                min={1}
                max={20}
                value={teamDraft.max_parallel_calls}
                onChange={(event) => setTeamDraft({ ...teamDraft, max_parallel_calls: Number(event.target.value) })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Timeout Seconds</span>
              <input
                type="number"
                min={1}
                max={180}
                value={teamDraft.timeout_seconds}
                onChange={(event) => setTeamDraft({ ...teamDraft, timeout_seconds: Number(event.target.value) })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
          </div>
          <button type="submit" className="w-fit rounded-md bg-ink px-4 py-2 font-semibold text-white">Save Team Profile</button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Team Mission Policy</h3>
        <form onSubmit={onTeamMissionSubmit} className="grid gap-3 text-sm">
          <label className="block">
            <span className="mb-1 block text-slate-600">Mission Objective</span>
            <textarea
              rows={3}
              value={teamMissionDraft.mission_objective}
              onChange={(event) => setTeamMissionDraft({ ...teamMissionDraft, mission_objective: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Required Checks (comma-separated)</span>
            <input
              value={teamMissionDraft.required_checks_csv}
              onChange={(event) => setTeamMissionDraft({ ...teamMissionDraft, required_checks_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Allowed Tools (comma-separated)</span>
            <input
              value={teamMissionDraft.allowed_tools_csv}
              onChange={(event) => setTeamMissionDraft({ ...teamMissionDraft, allowed_tools_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Completion Criteria</span>
            <input
              value={teamMissionDraft.completion_criteria_csv}
              onChange={(event) => setTeamMissionDraft({ ...teamMissionDraft, completion_criteria_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Unknown/Unavailable Rules</span>
            <input
              value={teamMissionDraft.unknown_rules_csv}
              onChange={(event) => setTeamMissionDraft({ ...teamMissionDraft, unknown_rules_csv: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-600">Relevance Weights (JSON)</span>
            <textarea
              rows={4}
              value={teamMissionDraft.relevance_weights_json}
              onChange={(event) => setTeamMissionDraft({ ...teamMissionDraft, relevance_weights_json: event.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
            />
          </label>
          <button type="submit" className="w-fit rounded-md bg-ink px-4 py-2 font-semibold text-white">Save Team Mission</button>
          {teamMission ? (
            <p className="text-xs text-slate-500">
              current mission: {teamMission.team_id} updated by {teamMission.updated_by}
            </p>
          ) : null}
        </form>
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <form onSubmit={onContextPackCreate} className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Context Packs</h3>
          <div className="space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-slate-600">Pack ID</span>
              <input
                value={contextPackDraft.pack_id}
                onChange={(event) => setContextPackDraft({ ...contextPackDraft, pack_id: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Name</span>
              <input
                value={contextPackDraft.name}
                onChange={(event) => setContextPackDraft({ ...contextPackDraft, name: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Description</span>
              <textarea
                rows={3}
                value={contextPackDraft.description}
                onChange={(event) => setContextPackDraft({ ...contextPackDraft, description: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Stage Bindings (comma-separated)</span>
              <input
                value={contextPackDraft.stage_bindings_csv}
                onChange={(event) => setContextPackDraft({ ...contextPackDraft, stage_bindings_csv: event.target.value })}
                placeholder="resolve_service_identity,collect_evidence"
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Team Bindings (comma-separated)</span>
              <input
                value={contextPackDraft.team_bindings_csv}
                onChange={(event) => setContextPackDraft({ ...contextPackDraft, team_bindings_csv: event.target.value })}
                placeholder="app,infra"
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <button type="submit" className="rounded-md bg-ink px-4 py-2 font-semibold text-white">Create Context Pack</button>
          </div>
        </form>

        <form onSubmit={onContextArtifactUpload} className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Context Artifact Upload</h3>
          <div className="space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-slate-600">Pack ID</span>
              <input
                value={artifactDraft.pack_id}
                onChange={(event) => setArtifactDraft({ ...artifactDraft, pack_id: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Filename</span>
              <input
                value={artifactDraft.filename}
                onChange={(event) => setArtifactDraft({ ...artifactDraft, filename: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Artifact Type</span>
              <select
                value={artifactDraft.artifact_type}
                onChange={(event) => setArtifactDraft({ ...artifactDraft, artifact_type: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="markdown">markdown</option>
                <option value="text">text</option>
                <option value="json">json</option>
                <option value="yaml">yaml</option>
                <option value="architecture_diagram">architecture_diagram</option>
                <option value="operator_notes">operator_notes</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-600">Content</span>
              <textarea
                rows={8}
                value={artifactDraft.content}
                onChange={(event) => setArtifactDraft({ ...artifactDraft, content: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
              />
            </label>
            <button type="submit" className="rounded-md bg-ink px-4 py-2 font-semibold text-white">Upload Artifact</button>
          </div>
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
                      {tool.tool_name} · phase={tool.phase} · scope={tool.scope_kind} · read_only={String(tool.read_only)} · light_probe={String(tool.light_probe)}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))}
          {!mcpServers.length ? <p className="text-slate-500">No MCP servers configured yet.</p> : null}
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Context Pack Registry</h3>
        <div className="space-y-2 text-sm">
          {contextPacks.map((pack) => (
            <div key={`${pack.pack_id}-v${pack.version}`} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <strong>{pack.pack_id}</strong>
                <span>v{pack.version}</span>
                <span>name={pack.name}</span>
                <span>artifacts={pack.artifacts.length}</span>
                <span>active={String(Boolean(activeContextPack && activeContextPack.pack_id === pack.pack_id && activeContextPack.version === pack.version))}</span>
                <button
                  type="button"
                  onClick={() => {
                    void onActivateContextPack(pack.pack_id);
                  }}
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold"
                >
                  Activate
                </button>
              </div>
            </div>
          ))}
          {!contextPacks.length ? <p className="text-slate-500">No context packs yet.</p> : null}
        </div>
      </section>

      {feedback ? <div className="rounded-md border border-cyan/40 bg-cyan/10 px-3 py-2 text-sm text-slate-700">{feedback}</div> : null}
    </div>
  );
}
