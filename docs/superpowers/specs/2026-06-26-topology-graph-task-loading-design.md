# Topology Graph Task Loading Design

Date: 2026-06-26

## Goal

The topology graph should show useful UI within 1-3 seconds after the user starts a request. The first render may be incomplete: it can show the center node, task progress, and later the initial LLM graph. Search enrichment, quotes, and source-verified relations can arrive after the first render.

The current `POST /api/topology/graph` request can block for a long time because `quote_mode=llm_initial` does more than produce an initial graph. It can also synchronously infer stale source relations before returning, so the frontend cannot render anything until slow LLM work finishes.

## Chosen Approach

Use a task-based topology graph loading flow.

The frontend creates a graph task and immediately receives a `task_id` plus a center-node skeleton graph. The backend stores the task in an in-process registry and advances it in background stages. The frontend polls the task endpoint and applies the newest full graph snapshot whenever one is available.

This approach is preferred over Server-Sent Events for the first implementation because it is easier to add to the current FastAPI and React code, works well with the existing polling style, and avoids connection lifecycle complexity.

## API Contract

### Create Task

`POST /api/topology/graph/tasks`

Request body:

```json
{
  "code": "NVDA",
  "market": "US",
  "depth": 3,
  "center_name": "NVIDIA",
  "quote_mode": "llm_initial"
}
```

Response body:

```json
{
  "ok": true,
  "data": {
    "task_id": "topo_...",
    "stage": "queued",
    "progress_pct": 5,
    "graph": {
      "center": {
        "id": "US:NVDA",
        "code": "NVDA",
        "market": "US",
        "name": "NVIDIA",
        "data_stage": "skeleton"
      },
      "nodes": [
        {
          "id": "US:NVDA",
          "code": "NVDA",
          "market": "US",
          "name": "NVIDIA",
          "data_stage": "skeleton",
          "is_center": true
        }
      ],
      "edges": [],
      "stats": {
        "relation_status": "pending",
        "data_stage": "skeleton"
      }
    },
    "message": "Task created",
    "warnings": [],
    "updated_at": "2026-06-26T00:00:00Z"
  }
}
```

The create endpoint should return after building the skeleton. It must not wait for the initial LLM graph.

### Read Task

`GET /api/topology/graph/tasks/{task_id}`

Response body:

```json
{
  "ok": true,
  "data": {
    "task_id": "topo_...",
    "stage": "initial_graph_ready",
    "progress_pct": 45,
    "graph": {
      "center": {},
      "nodes": [],
      "edges": [],
      "stats": {}
    },
    "message": "Initial graph ready",
    "warnings": [],
    "error": null,
    "updated_at": "2026-06-26T00:00:03Z"
  }
}
```

The task endpoint returns the full latest graph snapshot. It does not return graph patches in the first version. This lets the frontend reuse the current `applyGraph` merge path.

Supported stages:

- `queued`
- `initial_graph_running`
- `initial_graph_ready`
- `enriching`
- `source_polling`
- `done`
- `partial`
- `failed`
- `cancelled`
- `expired`

### Cancel Task

`DELETE /api/topology/graph/tasks/{task_id}`

Cancellation is best-effort. The registry marks the task as `cancelled`, and background processing checks that state between stages.

## Backend Design

Add an in-process topology task registry under the topology web layer. Each task stores:

- request parameters: code, market, depth, center name, quote mode
- latest stage
- latest progress percentage
- latest full graph snapshot
- warnings
- optional structured error
- created, updated, and expiry timestamps
- cancellation flag

The first version uses memory storage with a time-to-live of 10-30 minutes. This matches the current local development flow and avoids adding a database or Redis dependency. If the topology feature later runs behind multiple workers, the registry should move to shared storage.

The create endpoint resolves enough center identity to build a skeleton graph. If center resolve fails, it falls back to the request `code`, `market`, and `center_name`.

Background execution then advances the task:

1. `initial_graph_running`: build the initial LLM graph.
2. `initial_graph_ready`: store the initial nodes and edges so the frontend can render a useful graph.
3. `enriching`: call the existing `search_enrich` path and merge fields into the current graph.
4. `source_polling`: use the existing source relation generation and polling behavior to move from LLM initial data toward source-verified data.
5. `done` or `partial`: finish when enrichment, quotes, and relation polling complete or time out.

The existing `TopologyService.build_graph(... quote_mode="llm_initial")` should not be used unchanged for the initial stage if it still synchronously infers stale sources. The service needs a narrowly scoped initial-only path or option that builds the LLM initial snapshot and assembles the first graph without synchronously expanding stale sources.

## Frontend Design

Add task APIs to `topologyApi.ts`:

- `createGraphTask(...)`
- `getGraphTask(taskId)`
- `cancelGraphTask(taskId)`

Update `IndustryTopologyPanel.startTopology`:

1. Increment the existing local task guard.
2. Call `createGraphTask`.
3. Immediately call `applyGraph` with the returned skeleton graph.
4. Set stage/progress from the task response.
5. Start polling `getGraphTask` every 1-2 seconds.
6. For each response, ignore stale results using the existing task guard.
7. If the response includes a graph, call `applyGraph`.
8. Stop polling on `done`, `partial`, `failed`, `cancelled`, or `expired`.

When the user switches symbols or starts a new topology request, the frontend should invalidate the old local task guard and best-effort cancel the old backend task.

The existing quote polling and relation polling code can either stay as separate loops in the first implementation or gradually move behind the backend task. The preferred first implementation is to keep graph-task polling responsible for topology graph snapshots and keep quote refresh behavior close to the current frontend flow unless backend task ownership is simpler during implementation.

## Error Handling

Task responses expose a structured error:

```json
{
  "code": "initial_graph_failed",
  "message": "LLM initial graph generation failed"
}
```

Rules:

- Center resolve failure does not fail the task. The skeleton uses request data.
- Initial graph failure sets `stage=failed`; the frontend keeps the center skeleton visible and offers retry through the existing start action.
- Search enrichment failure adds a warning and does not fail the task.
- Quote failures use existing quote status and timeout behavior.
- Source relation timeout sets `stage=partial`; the LLM initial graph remains visible.
- Missing or expired task returns a clear terminal state or 404 response, and the frontend stops polling.
- Cancelled tasks stop updating the UI.

## Testing Plan

Backend tests:

- Creating a graph task returns a `task_id` and center skeleton without waiting for LLM completion.
- A successful fake-LLM task eventually reaches `initial_graph_ready` or `done` with nodes and edges.
- Initial graph failure moves the task to `failed` and preserves the skeleton graph.
- Search enrichment failure adds warnings but preserves the latest graph.
- Missing or expired tasks return a clear error response.
- TTL cleanup does not remove running tasks.
- Cancellation marks the task and prevents later stages from overwriting it.

Frontend tests:

- `startTopology` renders the skeleton immediately after task creation.
- Polling applies the initial graph when the task reaches `initial_graph_ready`.
- Terminal task stages stop polling.
- Starting a new topology request prevents old task responses from mutating the new graph.
- Failed, partial, expired, and cancelled states update stage/progress clearly.

## Scope Boundaries

This design does not add SSE streaming, Redis, database-backed task persistence, multi-worker coordination, or graph patch protocols. Those can be added later if polling snapshots are not enough.

The implementation should preserve the existing `/api/topology/graph` endpoint for compatibility while the task-based flow is introduced.
