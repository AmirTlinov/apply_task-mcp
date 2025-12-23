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

### Suggestions (executable actions)

`AIResponse.suggestions[]` are server-emitted, tool-ready actions that are intended to be copy/pasted and executed as-is.

Each suggestion has a stable shape:

```json
{
  "action": "patch",
  "target": "tasks_patch",
  "reason": "…",
  "priority": "high",
  "validated": true,
  "params": { "…" : "…" }
}
```

Notes:
- `validated=true` means the server validated `params` against the MCP tool input schema for that intent (schema-correct + executable).
- When a suggestion targets a mutating intent on the current focus, the server may auto-fill safety fields (`strict_targeting`, `expected_target_id`, `expected_kind`, and `expected_revision` when known).

## Optimistic concurrency (revision / expected_revision)

Every stored `PLAN-###` / `TASK-###` file has a **monotonic integer** `revision` (etag-like). It is:

- persisted in task file metadata
- surfaced in `resume`, `radar` focus payloads, and task/plan serialization
- incremented on every successful write (any mutating intent that saves the file)

To prevent lost updates, mutating intents accept an optional precondition:

```json
{"expected_revision": 7}
```

Notes:
- `expected_version` is accepted as an alias for `expected_revision`.
- When `expected_revision` is present and stale, the operation fails with `error.code = "REVISION_MISMATCH"` and includes `result.current_revision` plus recovery suggestions (resume/radar → retry).
- Read-only intents ignore `expected_revision` (but if provided it must be a valid integer).

## Safe targeting (expected_target_id / strict_targeting)

Focus is **convenience**, never magic: `explicit > focus`. To eliminate “silent mis-target” on writes, mutating intents accept an optional guard:

```json
{"expected_target_id":"TASK-001","expected_kind":"task","strict_targeting":true}
```

Rules:
- When `expected_target_id` is present, the resolved target id **must match** (otherwise fails with `error.code = "EXPECTED_TARGET_MISMATCH"`).
- When `strict_targeting=true` and the operation is focus-based (no explicit `task`/`plan`), `expected_target_id` is required (otherwise fails with `error.code = "STRICT_TARGETING_REQUIRES_EXPECTED_TARGET_ID"`).
- Mutating responses include `context.target_resolution` (`explicit|focus|focus_task_parent|missing|focus_incompatible`) so you can trace how the target was chosen.
- Aliases: `expected_target` → `expected_target_id`, `strict_writes` → `strict_targeting`.
- Auto strict: if there are multiple ACTIVE targets and a focus-based write omits strict flags, `strict_targeting` is enabled automatically and `context.strict_writes_auto=true` with `context.strict_writes_active_count`.
- Explicit addressing (`task`/`plan`) bypasses auto strict because the target is unambiguous.

## Preview mode (dry_run/apply=false) and audit stream

By default, previews leave **no trace** in the operational log:

- Any mutating intent with `dry_run=true` is treated as a **preview** (no write, no ops history/delta entry).
- `close_task` uses `apply=false` (default) as its **preview** mode.

If you need a trace of previews for debugging/analytics, pass `audit=true` on the preview call:

- The server records the preview call into a separate **audit** stream (non-undoable, does not affect `ops` undo/redo).
- Preview responses may include `meta.audit_operation_id` for `delta(stream="audit", since=...)` chaining.

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

Compact “Radar View” snapshot for the current work (1 screen → 1 truth):

- **Now**: active step / current plan checklist item
- **Why**: contract / goal summary
- **Verify**: commands + missing checkpoints + evidence summary
- **Next**: exactly one best next action (“recipe”), state-aware and executable
- **Blockers/Deps**: blockers + dependency state

```json
{"intent":"radar","task":"TASK-001","limit":3,"max_chars":12000}
```

Notes:
- Radar always returns stable keys: `now`, `why`, `verify`, `next`, `blockers`, `open_checkpoints`, `runway` (plus `focus`, `links`, `budget`).
- `result.focus.lifecycle_status` is the canonical lifecycle status (TODO|ACTIVE|DONE) of the focused item.
- `result.now.queue_status` is the queue/UI status of the “Now” item (not the lifecycle status).
- `max_chars` is a hard output budget (UTF-8 bytes). Result includes `result.budget` with `used_chars` and `truncated`.
- `result.why.contract` may include a compact summary from structured `contract_data` (goal/done/checks/constraints/risks).
- `result.links` contains small “expand” payloads (resume/mirror/context/history/handoff).
- For tasks, `result.verify.evidence` is the “black box” summary for the active step (counts + kinds + last observed timestamps).
- For tasks, `result.verify.evidence_task` aggregates evidence across **all** steps (task-level black box).
- `result.next[]` is always a single suggestion and mirrors `AIResponse.suggestions[0]` (same payload, `validated` flag, and safe-write defaults when applicable).
- `result.runway` is the “runway status” for safe closure:
  - `open: bool` — whether closing is allowed right now
  - `blocking.lint` — top lint errors (severity=error) + summary counters
  - `blocking.validation` — structural gating (e.g., task not complete / step not ready / plan checklist not finished)
  - `recipe` — one executable, schema-correct fix payload (usually `patch`, sometimes `batch`/`close_step` or `plan(advance=true)`).
  - When `runway.open=false`, `result.next[0]` prioritizes `runway.recipe` (fix first, closure later).

### handoff

Compact handoff/export snapshot for shift change (done/remaining/risks + radar core).

```json
{"intent":"handoff","task":"TASK-001","limit":3,"max_chars":12000}
```

Notes:
- Handoff returns the same stable radar keys plus `done`, `remaining`, and `risks`.
- `done`/`remaining` are capped lists (5 items) with counts + totals.
- `max_chars` is enforced with the same budget rules as radar.

### context_pack

Cold-start pack: Radar View + delta slice under a hard budget (one call = orientation).

```json
{"intent":"context_pack","task":"TASK-001","delta_limit":20,"max_chars":12000}
```

Notes:
- Returns the stable radar keys (`now/why/verify/next/blockers/open_checkpoints`) plus `delta`.
- Includes `runway` (same contract as `radar`) — enough to continue work without re-reading full context.
- `delta` is metadata-only by default; use `include_details` or `include_snapshot` explicitly.
- `since` mirrors `delta` semantics; invalid `since` returns `SINCE_NOT_FOUND`.
- `budget` is enforced for the whole pack.

### context

Get global context snapshot.

```json
{"intent":"context","include_all":true,"compact":true}
```

Result includes `counts`, `by_status`, and (when `include_all=true`) `plans[]` and `tasks[]`.

### resume

Load a specific `plan`/`task` (or `.last` fallback) with optional timeline.

```json
{"intent":"resume","task":"TASK-001","events_limit":20,"compact":true}
```

Notes:
- `compact=true` (default) returns a short summary payload; use `compact=false` for full snapshots.
- Returns either `result.plan` or `result.task`. For tasks also returns `result.checkpoint_status` (bounded in `compact=true`).

### lint

Read-only discipline lint (preflight checks) for a plan/task.

```json
{"intent":"lint","task":"TASK-001"}
```

```json
{"intent":"lint","plan":"PLAN-001"}
```

Result highlights:
- `result.summary`: counts of errors/warnings
- `result.issues[]`: structured issues with `code`, `severity`, `message`, and `target` (task/step/plan/deps)
- `result.links`: small “expand” payloads (radar/resume/mirror)

Common warning:
- `EVIDENCE_MISSING`: step is ready/completed but has no evidence (`verification_outcome`, `checks`, `attachments`). Attach evidence via `verify` (step only).

### templates_list

List built-in templates available for `scaffold`.

```json
{"intent":"templates_list"}
```

### scaffold

Create a plan/task from a template (safe default: `dry_run=true`).

Dry run (preview only):
```json
{"intent":"scaffold","template":"bugfix","kind":"task","title":"Fix login redirect","parent":"PLAN-001"}
```

Write (create files):
```json
{"intent":"scaffold","template":"bugfix","kind":"task","title":"Fix login redirect","parent":"PLAN-001","dry_run":false}
```

Notes:
- For `kind:"task"`, `parent` is required unless it can be inferred from focus (`focus_set` to a `PLAN-###` or a `TASK-###` with a parent).
- Successful writes return `meta.operation_id` (for `delta` chaining).
- On missing/invalid inputs, errors include `error.recovery` plus actionable `suggestions` (e.g. `templates_list`, `context(include_all=true)`, `focus_set`).

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

Confirm checkpoints (`criteria` / `tests` / `security` / `perf` / `docs`) for any checkpointable node.

```json
{"intent":"verify","task":"TASK-001","path":"s:0","checkpoints":{"criteria":{"confirmed":true,"note":"ok"}}}
```

Target kinds:
- `kind: "auto"` → if `task=PLAN-###` targets the root plan; otherwise defaults to `step`
- `kind: "step"` (default for `TASK-###`) → step at `path` or `step_id`
- `kind: "task"` → task node at `path` or `task_node_id`
- `kind: "plan"`:
  - for `task=PLAN-###` → root plan checkpoints (no path)
  - for `task=TASK-###` → nested plan owned by a step at `path` or `step_id`
- `kind: "task_detail"` → root task/plan (no path, legacy-compatible)

Strict confirmation-only rule:
- Every provided `checkpoints.<name>` must include `confirmed:true`. Missing/false confirmations are rejected with `error.code="VERIFY_NOOP"` and do not mutate state.

Optional evidence (step only):
- `checks[]` / `verification_outcome` (step only)
- `attachments[]` (any checkpoint target)

Shorthand:
- `checks[]` accepts strings (`"pytest -q"`) → `{kind:"command", spec:"...", outcome:"info"}`
- `attachments[]` accepts string paths or `{file_path:"..."}` → `{kind:"file", path:"..."}`

Golden path (evidence-first):
1) `evidence_capture` attaches artifacts/checks/outcome to the step.
2) `verify` confirms checkpoints and links *all existing evidence* (captured + auto checks) to those checkpoints.
3) `close_step` finalizes the step (atomic verify → done).

Auto evidence (step only, best-effort):
- When a checkpoint is confirmed, apply_task may append `checks` of kind `ci` (GitHub Actions) and/or `git` (HEAD state), deduped by `digest`.

### evidence_capture

Capture evidence for a step without confirming checkpoints.

This is the canonical way to attach:
- artifacts: `cmd_output` / `diff` / `url` (stored under `<tasks_dir>/.artifacts/` when needed, referenced via `attachment.uri`)
- plain `attachments[]` and/or `checks[]` (same shapes as in `verify`)
- optional `verification_outcome`

```json
{"intent":"evidence_capture","task":"TASK-001","path":"s:0","artifacts":[{"kind":"cmd_output","command":"pytest -q","stdout":"..."}],"verification_outcome":"pass"}
```

### progress

Set step completion (respects checkpoints unless `force=true`).

```json
{"intent":"progress","task":"TASK-001","path":"s:0","completed":true,"force":false}
```

### done

Close a step (optional `note` is saved as a progress note first).

If `auto_verify=true`, this becomes atomic `verify(step)` → `done(step)` in a single call (requires `checkpoints.*.confirmed=true`).
Supported checkpoints: `criteria` / `tests` / `security` / `perf` / `docs`.

```json
{"intent":"done","task":"TASK-001","path":"s:0","force":false,"note":"done"}
```

Atomic close example:

```json
{"intent":"done","task":"TASK-001","path":"s:0","auto_verify":true,"checkpoints":{"criteria":{"confirmed":true},"tests":{"confirmed":true}}}
```

### close_step

Atomic `verify(step)` → `done(step)` (strict checkpoints + explicit gating errors). Equivalent to `done(auto_verify=true)`.
Supported checkpoints: `criteria` / `tests` / `security` / `perf` / `docs`.

```json
{"intent":"close_step","task":"TASK-001","path":"s:0","checkpoints":{"criteria":{"confirmed":true},"tests":{"confirmed":true}}}
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

### patch

Diff-oriented safe patch (field allowlist) for `task_detail` / `step` / `task` (task node).

Operations:
- `set` / `unset` for scalar fields
- `set` / `unset` / `append` / `remove` for list fields
- `compact=true` (default) returns compact snapshots; use `compact=false` for full snapshots.

Patch a root task/plan (`kind` inferred when omitted):
```json
{
  "intent":"patch",
  "task":"TASK-001",
  "expected_revision": 3,
  "ops":[{"op":"set","field":"description","value":"Updated"}]
}
```

Patch structured contract data (plan/task detail only, allowlisted keys):
```json
{
  "intent":"patch",
  "task":"PLAN-001",
  "kind":"task_detail",
  "ops":[
    {"op":"set","field":"contract_data.goal","value":"Ship v1 safely"},
    {"op":"append","field":"contract_data.checks","value":"pytest -q"}
  ]
}
```

Patch a step:
```json
{"intent":"patch","task":"TASK-001","kind":"step","path":"s:0","ops":[{"op":"append","field":"blockers","value":"Waiting for access"}]}
```

Per-step gating policy (defaults to `["criteria","tests"]` when empty):
```json
{"intent":"patch","task":"TASK-001","kind":"step","path":"s:0","ops":[{"op":"set","field":"required_checkpoints","value":["criteria","tests","security"]}]}
```

Patch a task node inside a step plan:
```json
{"intent":"patch","task":"TASK-001","kind":"task","path":"s:0.t:1","ops":[{"op":"set","field":"status","value":"DONE"}]}
```

Dry-run preview (no writes; does not pollute ops history/delta):
```json
{"intent":"patch","task":"TASK-001","dry_run":true,"compact":true,"ops":[{"op":"append","field":"success_criteria","value":"<done>"}]}
```

Dry-run response is explicit-by-shape:
- `result.current` — current snapshot
- `result.computed` — post-preview snapshot (in-memory)
- `result.diff.state` — status/progress/blocked diff (when changed)
- `result.diff.fields` — before/after for changed fields (trust-by-diff; works even in `compact=true`)
- `result.would_execute=false` when the patch is a true no-op (no side-effects)

No-op behavior (non-dry-run):
- If the patch results in no changes, it returns `result.no_op=true`, does not bump revision, and does not write an ops history entry.

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

Notes:
- Lint errors (`tasks_lint` severity=error) block `status=DONE` unless `force=true`.

### close_task

Golden task closure: `dry_run → diff → apply → complete` (atomic).

Dry-run preview (safe-by-default):
```json
{"intent":"close_task","task":"TASK-001"}
```

Preview with patches (simulate in-memory; no writes):
```json
{"intent":"close_task","task":"TASK-001","patches":[{"kind":"task_detail","ops":[{"op":"append","field":"success_criteria","value":"<definition of done>"}]}]}
```

Apply atomically (patches → complete DONE):
```json
{"intent":"close_task","task":"TASK-001","apply":true,"patches":[{"kind":"task_detail","ops":[{"op":"append","field":"success_criteria","value":"<definition of done>"}]}]}
```

Notes:
- When `apply=false` (default), `close_task` is always a dry-run (no history side-effects).
- To record the preview in the audit stream, pass `audit=true` and query `history(stream=\"audit\")` / `delta(stream=\"audit\")`.
- Returns `runway` + `diff` so you can see exactly what would change.
- If the runway is closed and `force=false`, `apply=true` fails with `RUNWAY_CLOSED` and includes the same `runway` + `diff` in the error payload.
- `patches[]` use the same shape as `patch` requests but omit the root `task` id (it’s implied by `close_task.task`).
- Status is explicit: a 100% complete task is not auto-flipped to `DONE` on save/patch; use `close_task(apply=true)` or `complete(status=\"DONE\")`.

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
  "expected_target_id":"TASK-001",
  "strict_targeting":true,
  "operations":[
    {"intent":"evidence_capture","path":"s:0","artifacts":[{"kind":"cmd_output","command":"pytest -q","stdout":"..."}]},
    {"intent":"close_step","path":"s:0","note":"...","checkpoints":{"criteria":{"confirmed":true},"tests":{"confirmed":true}}}
  ]
}
```

Batch result also includes:
- `result.latest_id`: latest operation id after the batch (for `delta` chaining)
- `result.operation_ids`: operation ids in execution order (one per successful nested mutation)
- Nested results may include `meta.operation_id` (when the nested intent is mutating)

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

Filters:
- `stream: "ops" | "audit"` (default `"ops"`)
- `task: "TASK-###" | "PLAN-###"` (optional)
- `intents: ["patch","complete",...]` (optional)
- `paths: ["s:0", "s:1.t:2", ...]` (optional; matches any `path` found in the operation payload)

Notes:
- `ops` is the canonical mutation stream (undoable where supported).
- `audit` is preview/trace-only and never affects undo/redo.

### delta

Return operation log entries strictly after a given operation id (agent-friendly delta updates).

```json
{"intent":"delta","since":"<operation_id>","task":"TASK-001","limit":50}
```

Notes:
- Use `meta.operation_id` from any mutating response as the `since` cursor.
- Use `meta.audit_operation_id` from preview calls (with `audit=true`) as the `since` cursor for `stream=\"audit\"`.
- `since` is exclusive (returns ops strictly after it).
- Delta is metadata-only by default: set `include_details=true` to return full `data/result` payloads.
- Set `include_snapshot=true` to include before/after snapshot content for each operation (heavier).
- Supports the same filters as `history` (`stream`, `task`, `intents`, `paths`).

### storage

Return resolved storage info (global/local/current + namespaces).

```json
{"intent":"storage"}
```
