# Apply Task AI Intents Reference

Complete reference for the canonical intent API exposed via MCP tools (`tasks_<intent>`).

## Overview

The intent API is a deterministic JSON surface designed for AI agents and automation.

In MCP, intents are exposed as tools named `tasks_<intent>` and map 1:1 to the intent payloads described below.

### Control tower rules (MCP ergonomics)

These are strict-by-construction conventions intended to make the MCP surface “one screen → one truth”:

- **Explicit > focus**: if an intent accepts an explicit `task`/`plan`/`step_id`/`task_node_id`/`path`, it always wins.
- **Focus is convenience, never magic**: the only supported implicit target is the stored focus (`.last`), and it is managed explicitly via `focus_set` / `focus_get` / `focus_clear`.
- **Errors are actionable**: missing-id errors should return a recovery hint and suggestions (`set_focus …` or candidate ids).

### Canonical model

- **Plan**: `PLAN-###` (`TaskDetail.kind = "plan"`) stores:
  - `contract` (text)
  - plan checklist: `plan_doc`, `plan_steps[]`, `plan_current`
- **Task**: `TASK-###` (`TaskDetail.kind = "task"`, `parent = PLAN-###`) stores:
  - nested **steps** tree (`TaskDetail.steps[]`)
- **Step**: node inside a task; each step can hold a **Plan** with **Tasks**, and each task holds **Steps**.
  - checkpoints: `criteria` (explicit), `tests` (explicit OR auto-confirmed when empty at creation)
  - `blockers[]` are **data only** (not a checkpoint)

### Path format

Paths are typed segments:
- **Step path**: `"s:0"` or `"s:0.t:1.s:2"`
- **Task path** (inside a step plan): `"s:0.t:1"`
Stable node ids:
- **step_id**: `STEP-XXXXXXXX`
- **task_node_id**: `NODE-XXXXXXXX`

## Response format

Every intent returns `AIResponse`:

```json
{
  "success": true,
  "intent": "context",
  "result": { "..." : "..." },
  "warnings": [],
  "suggestions": [],
  "context": {},
  "error": null,
  "timestamp": "2025-12-18T21:33:41.489384+00:00"
}
```

On failure: `success=false` and `error={code,message,recovery?}`.

## Intents

### focus_get

Get current focus (the `.last` pointer).

```json
{"intent":"focus_get"}
```

### focus_set

Set focus (writes `.last`).

```json
{"intent":"focus_set","task":"TASK-001","domain":"alpha/api"}
```

### focus_clear

Clear focus (removes `.last` if present).

```json
{"intent":"focus_clear"}
```

### radar

Compact “Radar View” snapshot for the current work:

- **Now**: active step / current plan checklist item
- **Why**: contract / goal summary
- **How to verify**: checks/tests (and missing checkpoints)
- **Next**: top 1–3 actions/suggestions
- **Blockers/Deps**: blockers + dependency state

```json
{"intent":"radar","task":"TASK-001","limit":3}
```

### context

Get global context snapshot.

```json
{"intent":"context","include_all":true,"compact":true}
```

Result includes `counts`, `by_status`, and (when `include_all=true`) `plans[]` and `tasks[]`.

### resume

Load a specific `plan`/`task` (or `.last` fallback) with optional timeline.

```json
{"intent":"resume","task":"TASK-001","events_limit":20}
```

Returns either `result.plan` or `result.task`. For tasks also returns `result.checkpoint_status`.

### create

Create a plan or a task.

Create a plan:
```json
{"intent":"create","kind":"plan","title":"Release v1","contract":"..."}
```

Optional structured contract (stored in metadata as `contract_data` and versioned):
```json
{
  "intent":"create",
  "kind":"plan",
  "title":"Release v1",
  "contract_data":{
    "goal":"Ship v1 safely",
    "constraints":["No data loss"],
    "assumptions":["CI is available"],
    "non_goals":["Rewrite UI"],
    "done":["pytest -q green"],
    "risks":["Migration risk"],
    "checks":["pytest -q"]
  }
}
```

Create a task under a plan:
```json
{
  "intent":"create",
  "kind":"task",
  "parent":"PLAN-001",
  "title":"Ship OAuth",
  "description":"...",
  "steps":[
    {"title":"Wire login flow","success_criteria":["..."],"tests":["pytest -q"],"blockers":["..."]}
  ]
}
```

Notes:
- If `kind` is omitted: defaults to `"plan"` unless `parent` is set (then `"task"`).
- `dry_run=true` validates without writing files.

### decompose

Append nested steps to an existing task.

```json
{"intent":"decompose","task":"TASK-001","parent":"s:0.t:1","steps":[{"title":"...","success_criteria":["..."]}]}
```

### define

Update a step at `path` (title / success_criteria / tests / blockers).

```json
{"intent":"define","task":"TASK-001","path":"s:0.t:1.s:2","tests":["pytest -q"]}
```

### verify

Confirm checkpoints (`criteria` and/or `tests`) for any checkpointable node.

```json
{"intent":"verify","task":"TASK-001","path":"s:0","checkpoints":{"criteria":{"confirmed":true,"note":"ok"}}}
```

Target kinds:
- `kind: "step"` (default) → step at `path` or `step_id`
- `kind: "task"` → task node at `path` or `task_node_id`
- `kind: "plan"` → nested plan owned by step at `path` or `step_id`
- `kind: "task_detail"` → root task/plan (no path)

Optional evidence (step only):
- `checks[]` / `attachments[]` / `verification_outcome`

Auto evidence (step only, best-effort):
- When a checkpoint is confirmed, apply_task may append `checks` of kind `ci` (GitHub Actions) and/or `git` (HEAD state), deduped by `digest`.

### progress

Set step completion (respects checkpoints unless `force=true`).

```json
{"intent":"progress","task":"TASK-001","path":"s:0","completed":true,"force":false}
```

### done

Unified “verify + done” style completion (optional `note` is saved as a progress note first).

```json
{"intent":"done","task":"TASK-001","path":"s:0","force":false,"note":"done"}
```

### note

Add a progress note to a step (does not complete it).

```json
{"intent":"note","task":"TASK-001","path":"s:0.t:1.s:2","note":"Implemented parsing"}
```

### block

Toggle step `blocked` state (separate from `blockers[]` list).

```json
{"intent":"block","task":"TASK-001","path":"s:0","blocked":true,"reason":"Waiting for access"}
```

### task_add

Add a task node inside a step plan:

```json
{"intent":"task_add","task":"TASK-001","parent_step":"s:0","title":"Split integration work"}
```

### task_define

Update a task node inside a step plan:

```json
{"intent":"task_define","task":"TASK-001","path":"s:0.t:1","status":"ACTIVE","priority":"HIGH"}
```

### task_delete

Delete a task node inside a step plan:

```json
{"intent":"task_delete","task":"TASK-001","path":"s:0.t:1"}
```

### edit

Edit task/plan notes/meta fields (no step mutations).

```json
{"intent":"edit","task":"TASK-001","context":"...","tags":["a","b"],"depends_on":["TASK-002"]}
```

### contract

Set/clear a plan contract.

```json
{"intent":"contract","plan":"PLAN-001","current":"..."}
```

Optional structured contract data:
```json
{"intent":"contract","plan":"PLAN-001","contract_data":{"goal":"...","done":["..."],"checks":["pytest -q"]}}
```

### plan

Update plan checklist (`doc`, `steps`, `current`) and/or `advance=true`.

```json
{"intent":"plan","plan":"PLAN-001","steps":["Design","Implement","Verify"],"current":1}
```

### mirror

Export a compact plan slice for a plan/task (exactly one `in_progress` item).

```json
{"intent":"mirror","task":"TASK-001","limit":7}
```

Optional subtree targeting:
```json
{"intent":"mirror","task":"TASK-001","path":"s:0","kind":"step"}
```

Result fields:
- `scope`: `{task_id, kind, path?}`
- `items[]`: `{kind, path?, id?, task_id?, title, status, progress, children_done, children_total}`
- `summary`: `{total, completed, in_progress, pending}`

### complete

Set plan/task status (`TODO|ACTIVE|DONE`). For plans: requires checklist completion unless `force=true`.

```json
{"intent":"complete","task":"TASK-001","status":"DONE","force":false}
```

### delete

Delete a whole item:
```json
{"intent":"delete","task":"TASK-001"}
```

Delete a step node:
```json
{"intent":"delete","task":"TASK-001","path":"s:0.t:1.s:2"}
```

### batch

Execute multiple intents in order (optionally `atomic=true`).

```json
{
  "intent":"batch",
  "atomic":true,
  "task":"TASK-001",
  "operations":[
    {"intent":"note","path":"s:0","note":"..."},
    {"intent":"verify","path":"s:0","checkpoints":{"criteria":{"confirmed":true}}}
  ]
}
```

### undo / redo

Undo/redo last reversible operation (when available).

```json
{"intent":"undo"}
{"intent":"redo"}
```

### history

Return recent operation history (undo/redo metadata).

```json
{"intent":"history","limit":20}
```

### delta

Return operation log entries strictly after a given operation id (agent-friendly delta updates).

```json
{"intent":"delta","since":"<operation_id>","limit":50}
```

### storage

Return resolved storage info (global/local/current + namespaces).

```json
{"intent":"storage"}
```
