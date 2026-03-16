# Agentic RCA Learnings

This document captures the key engineering learnings from building and testing the local agentic RCA workflow against the OpenTelemetry demo with MCP-backed tooling.

## What Worked

- Anchor-first alias resolution materially improved service scoping.
  - Using `entity_ids` and explicit service fields before alert summary text prevented drift from `recommendationservice` to unrelated services like `ad`.
- MCP discovery plus artifact state made the workflow more inspectable.
  - Resolver and planner now expose `artifact_state`, `resolved_aliases`, `blocked_tools`, and `invocable_tools`, which made stage debugging much easier.
- Mission-driven team boundaries improved reasoning quality.
  - Splitting evidence collection into `app` and `infra` teams clarified what each team was responsible for proving.
- Negative-proof infra missions were the right model.
  - When resource metrics were missing, infra now ended as `unknown_not_available` instead of falsely claiming the platform was healthy.
- Targeted reruns are useful when they are bounded and explicit.
  - Planner and synthesis were able to request another evidence pass when scope or corroboration was missing.

## What We Learned The Hard Way

### 1. Resolver correctness is necessary, but not sufficient

Fixing alias resolution removed the worst scope drift, but that alone did not produce a strong RCA. The workflow still failed when later stages did not preserve that resolved service or use it deeply enough.

### 2. Planner correctness does not matter if evidence execution ignores the plan

The planner emitted service-correct steps including Jaeger and Prometheus actions, but `collect_evidence` still selected shallow or redundant tools instead of consuming the planned path. In practice this meant:

- the app team repeated `get_services`, `get_operations`, and `search_traces`,
- the app team often did not progress to `get_trace`,
- the infra team did not execute the Prometheus checks the planner had requested.

This is the main architectural gap right now.

### 3. Natural-language Prometheus queries are not acceptable

Planning a Prometheus step with a free-text query like "Recommendation service latency and resource usage are abnormal" is not a real metric query. Prometheus usage must become label-driven and artifact-driven:

- discover metric labels,
- resolve service labels,
- build valid query templates,
- then execute range and instant queries.

### 4. Infra needs explicit proof, not generic observability activity

Grafana metadata and annotation calls are useful, but they are not enough to conclude infra health. For resource and latency anomalies, the system must prove:

- local service metrics checked,
- global/shared metrics checked,
- change or annotation context checked.

Without those, infra should remain `unknown_not_available`.

### 5. Reruns only help if they change behavior

The workflow successfully triggered a rerun when synthesis detected missing infra corroboration, but the rerun repeated the same shallow evidence path. That means rerun control exists, but rerun execution is not yet adaptive enough.

### 6. Synthesis still overcommits under incomplete evidence

Even when infra evidence was incomplete, synthesis still produced a reasonably confident dependency-level RCA. That is too aggressive. When required evidence classes are missing, synthesis should:

- lower confidence,
- preserve ambiguity,
- and abstain from strong causal blame where needed.

### 7. Eval needs to grade execution quality, not just produce output metadata

The system currently records useful stage-level eval signals, but final RCA grading is still too permissive. A run should not be treated as a clean pass when:

- only 2 hypotheses are generated instead of 3,
- infra proof is incomplete,
- or a rerun was requested because the first pass lacked required evidence.

### 8. Context packs help, but they should not predetermine the answer

Architecture context was useful for service relationships and mission framing, but the RCA still has to come from live evidence. Context should bias search, not replace diagnosis.

## Current Known Gaps

- `collect_evidence` does not reliably honor planner-selected Prometheus steps.
- The infra team can still miss Prometheus completely even when its mission requires metric evidence.
- The app team still tends to repeat shallow Jaeger discovery instead of drilling into traces.
- Synthesis still returns only 2 hypotheses in some runs.
- Final RCA confidence remains too high when evidence classes are missing.
- Local development still stores investigations and run history in memory, so run history is lost across API restarts.

## Near-Term Priorities

1. Make `collect_evidence` consume planner-selected steps directly, per team.
2. Restore Prometheus as a first-class invocable tool for the infra team.
3. Replace free-text Prometheus arguments with metric/label-driven query generation.
4. Force synthesis to reduce confidence or abstain when infra completeness is not proven.
5. Tighten eval so incomplete RCA outputs do not grade as clean passes.

## Practical Product Takeaway

The system is now materially better at staying on the right service and explaining what it did. The next quality jump will not come from another prompt tweak. It will come from making evidence execution follow the planned diagnostic path and from requiring the final report to respect missing evidence instead of papering over it.
