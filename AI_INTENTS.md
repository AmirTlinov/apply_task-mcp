# Apply Task AI Intents Reference

Complete reference for the JSON-based AI interface (`apply_task ai`).

## Overview

The AI interface provides a structured JSON API for AI agents and automation tools. All requests follow the pattern:

```bash
apply_task ai '{"intent": "...", ...}'
```

## Response Format

All responses are JSON with consistent structure:

```json
{
  "success": true|false,
  "intent": "context",
  "result": { ... },
  "suggestions": ["next action 1", "next action 2"],
  "recovery_hint": { ... }  // on errors
}
```

## Intent Reference

### context

Get current working context for AI session initialization.

**Request:**
```json
{
  "intent": "context",
  "task": "TASK-001",       // optional: specific task (default: last task)
  "include_all": true,      // optional: include all tasks
  "compact": true,          // optional: minimal output
  "format": "markdown"      // optional: "json" or "markdown"
}
```

**Response includes:**
- Current task details with subtask status
- Checkpoint completion state
- Blocked dependencies
- Suggested next actions

**Use case:** Start of AI session, getting current state.

---

### resume

Restore AI session context with timeline and dependencies. Designed for AI agents resuming work after context loss.

**Request:**
```json
{
  "intent": "resume",
  "task": "TASK-001",       // optional: specific task
  "events_limit": 20        // optional: limit timeline events
}
```

**Response:**
```json
{
  "success": true,
  "intent": "resume",
  "result": {
    "task": { ... },        // full task with subtasks
    "timeline": [ ... ],    // recent events
    "dependencies": {
      "depends_on": ["TASK-001", "TASK-002"],
      "blocked_by": ["TASK-001"],  // incomplete deps
      "blocking": ["TASK-005"]     // tasks waiting for this
    },
    "checkpoint_status": {
      "pending": ["0", "1.0"],     // subtask paths needing checkpoints
      "ready": ["2"]              // subtasks ready for completion
    }
  },
  "suggestions": [ ... ]
}
```

**Use case:** Resume work after break, context window refresh.

---

### create

Create a new task programmatically.

**Request:**
```json
{
  "intent": "create",
  "title": "Task title #tag",
  "parent": "ROOT",
  "description": "Detailed description",
  "tests": ["pytest tests/", "coverage >= 85%"],
  "risks": ["risk1", "risk2"],
  "subtasks": [
    {
      "title": "Subtask one",
      "criteria": ["criterion"],
      "tests": ["test command"],
      "blockers": ["blocker"]
    }
  ],
  "domain": "core/api",           // optional
  "phase": "sprint-1",            // optional
  "component": "auth",            // optional
  "idempotency_key": "unique-123", // optional: prevent duplicates
  "dry_run": true                  // optional: validate only
}
```

**Use case:** Automated task creation, decomposition results.

---

### decompose

Add subtasks to an existing task.

**Request:**
```json
{
  "intent": "decompose",
  "task": "TASK-001",
  "subtasks": [
    {
      "title": "New subtask",
      "criteria": ["criterion"],
      "tests": ["test"],
      "blockers": ["blocker"]
    }
  ]
}
```

**Use case:** Breaking down tasks, adding work items.

---

### define

Update task properties.

**Request:**
```json
{
  "intent": "define",
  "task": "TASK-001",
  "description": "Updated description",
  "tests": ["new tests"],
  "risks": ["new risks"],
  "dependencies": ["TASK-002"],
  "next_steps": ["step 1"]
}
```

**Use case:** Refining task details, adding context.

---

### verify

Verify checkpoint completion on a subtask.

**Request:**
```json
{
  "intent": "verify",
  "task": "TASK-001",
  "path": "0",              // subtask index or path (e.g., "0.1")
  "checkpoints": {
    "criteria": {"done": true, "note": "Verified criteria"},
    "tests": {"done": true, "note": "Tests passed"},
    "blockers": {"done": true, "note": "Blockers resolved"}
  }
}
```

**Use case:** Recording evidence of checkpoint completion.

---

### done

Mark subtask as complete.

**Request:**
```json
{
  "intent": "done",
  "task": "TASK-001",
  "path": "0",
  "note": "Completion notes",   // optional
  "force": true                  // optional: skip verification
}
```

**Requirements:** All checkpoints (criteria, tests, blockers) must be verified unless `force: true`.

**Use case:** Completing subtasks after all checkpoints verified.

---

### progress

Toggle subtask completion status.

**Request:**
```json
{
  "intent": "progress",
  "task": "TASK-001",
  "path": "0",
  "completed": true
}
```

**Use case:** Quick status toggle, undo completion.

---

### delete

Delete a task or subtask.

**Request:**
```json
{
  "intent": "delete",
  "task": "TASK-001",       // delete entire task
  "path": "0"               // optional: delete specific subtask
}
```

**Use case:** Cleanup, removing obsolete items.

---

### complete

Complete an entire task (all subtasks done → task status OK).

**Request:**
```json
{
  "intent": "complete",
  "task": "TASK-001",
  "status": "OK"            // optional: explicit status
}
```

**Requirements:** All subtasks must be completed.

**Use case:** Finalizing task after all work done.

---

### batch

Execute multiple operations atomically.

**Request:**
```json
{
  "intent": "batch",
  "task": "TASK-001",       // default task for operations
  "atomic": true,           // rollback all on any failure
  "operations": [
    {"intent": "decompose", "subtasks": [...]},
    {"intent": "verify", "path": "0", "checkpoints": {...}},
    {"intent": "progress", "path": "1", "completed": true}
  ]
}
```

**Response:**
```json
{
  "success": true,
  "intent": "batch",
  "result": {
    "results": [
      {"intent": "decompose", "success": true, ...},
      {"intent": "verify", "success": true, ...},
      {"intent": "progress", "success": true, ...}
    ],
    "all_succeeded": true
  }
}
```

**Use case:** Complex updates that must succeed or fail together.

---

### undo

Undo the last operation.

**Request:**
```json
{
  "intent": "undo"
}
```

**Response:**
```json
{
  "success": true,
  "intent": "undo",
  "result": {
    "undone": {
      "intent": "done",
      "task": "TASK-001",
      "path": "0"
    }
  }
}
```

**Use case:** Reverting mistakes, exploring changes.

---

### redo

Redo an undone operation.

**Request:**
```json
{
  "intent": "redo"
}
```

**Use case:** Re-applying undone changes.

---

### history

View operation history or task event timeline.

**Request (operation history):**
```json
{
  "intent": "history",
  "limit": 20
}
```

**Request (task timeline):**
```json
{
  "intent": "history",
  "task": "TASK-001",
  "format": "markdown"      // optional: "json" or "markdown"
}
```

**Use case:** Reviewing changes, debugging, audit trail.

---

### storage

Get storage configuration information.

**Request:**
```json
{
  "intent": "storage"
}
```

**Response:**
```json
{
  "success": true,
  "intent": "storage",
  "result": {
    "tasks_dir": "/home/user/.tasks/project",
    "namespace": "owner_repo",
    "source": "git_remote",
    "task_count": 15
  }
}
```

**Use case:** Debugging storage issues, configuration verification.

---

### migrate

Migrate local `.tasks/` to global `~/.tasks/<namespace>/` storage.

**Request:**
```json
{
  "intent": "migrate"
}
```

**Request with specific project:**
```json
{
  "intent": "migrate",
  "project_dir": "/path/to/project",
  "dry_run": true
}
```

**Response:**
```json
{
  "success": true,
  "intent": "migrate",
  "result": {
    "migrated": true,
    "message": "Successfully migrated tasks",
    "from": "/project/.tasks",
    "to": "/home/user/.tasks/owner_repo",
    "namespace": "owner_repo"
  }
}
```

**Use case:** Migrating existing projects from local to global storage.

---

## Common Patterns

### AI Session Start

```json
{"intent": "context", "compact": true}
```

### Resume After Break

```json
{"intent": "resume"}
```

### Create and Decompose

```json
{
  "intent": "batch",
  "atomic": true,
  "operations": [
    {
      "intent": "create",
      "title": "New feature",
      "parent": "ROOT",
      "description": "...",
      "tests": ["..."],
      "risks": ["..."]
    },
    {
      "intent": "decompose",
      "subtasks": [...]
    }
  ]
}
```

### Complete Subtask with Evidence

```json
{
  "intent": "batch",
  "task": "TASK-001",
  "atomic": true,
  "operations": [
    {
      "intent": "verify",
      "path": "0",
      "checkpoints": {
        "criteria": {"done": true, "note": "Metrics verified: 92%"},
        "tests": {"done": true, "note": "pytest: 45 passed"},
        "blockers": {"done": true, "note": "All resolved"}
      }
    },
    {
      "intent": "done",
      "path": "0"
    }
  ]
}
```

### Dry Run Validation

```json
{
  "intent": "create",
  "title": "Test task",
  "dry_run": true,
  ...
}
```

### Idempotent Create

```json
{
  "intent": "create",
  "title": "Task",
  "idempotency_key": "feature-auth-v1",
  ...
}
```

## Error Handling

Errors include recovery hints:

```json
{
  "success": false,
  "intent": "done",
  "error": {
    "code": "CHECKPOINT_NOT_VERIFIED",
    "message": "Criteria checkpoint not verified",
    "recoverable": true
  },
  "recovery_hint": {
    "intent": "verify",
    "path": "0",
    "checkpoints": {"criteria": {"done": true, "note": "..."}}
  }
}
```

Common error codes:
- `TASK_NOT_FOUND` — Task doesn't exist
- `SUBTASK_NOT_FOUND` — Invalid subtask path
- `CHECKPOINT_NOT_VERIFIED` — Missing checkpoint confirmation
- `VALIDATION_ERROR` — Invalid input data
- `INTERNAL_ERROR` — Unexpected error
