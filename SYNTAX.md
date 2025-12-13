# apply_task syntax

Deterministic CLI surface: one operation → one command. All non-interactive commands emit JSON.

## Output contract

```json
{
  "command": "show",
  "status": "OK",
  "message": "Task detail",
  "timestamp": "...",
  "payload": { "task": { ... } }
}
```

Errors return `status: "ERROR"` with the same schema and an explanatory message. Interactive modes (`tui`, `guided`) keep textual output, but they still read/write the same files.

## Core commands

```bash
apply_task "Fix memory leak #bug #critical"
apply_task "Add OAuth @TASK-015 #feature"
apply_task "Refactor parser #refactoring"
```

- `#tag` becomes a label.
- `@TASK-XXX` becomes a dependency.
- Subtasks are either added via CLI (`apply_task subtask TASK --add ...`) or supplied as JSON with `--subtasks`.

### Creating a fully-specified task

```bash
apply_task create "Task Title #tag" \
  --parent TASK-001 \
  --description "Concrete scope" \
  --tests "pytest -q;coverage xml" \
  --risks "risk1;risk2" \
  --subtasks @payload/subtasks.json \
  --depends-on TASK-002,TASK-003
```

Flags `--parent`, `--description`, `--tests`, `--risks`, `--subtasks` are mandatory for flagship quality. Subtasks payload must contain ≥3 detailed items.

**Optional flags:**
- `--depends-on` — comma-separated task IDs for dependencies
- `--validate-only` — dry run validation without creating task

### Smart create (auto-parses tags & dependencies)

```bash
apply_task task "Add auth #feature #security @TASK-010" \
  --parent ROOT \
  --description "..." \
  --tests "..." \
  --risks "..." \
  --subtasks @subtasks.json
```

Automatically extracts:
- `#tag` → adds to tags
- `@TASK-xxx` → adds to dependencies

### Subtasks input helpers

- `--subtasks '<JSON>'` – inline string.
- `--subtasks @file.json` – load from a file.
- `--subtasks -` – read from STDIN.
- `apply_task template subtasks --count N` – emit a JSON skeleton for editing.

### Viewing/listing

```bash
apply_task show           # last task from .last
apply_task show 001       # TASK-001
apply_task list           # backlog summary
apply_task list --status ACTIVE   # filter by task status
apply_task list --tag feature     # filter by tag
apply_task list --blocked         # only tasks blocked by dependencies
apply_task list --stale 7         # inactive for 7+ days
apply_task list --progress        # show completion progress
apply_task analyze TASK-001       # deep task analysis
apply_task suggest                # AI recommendations
apply_task quick                  # top-3 quick overview
```

List output (TUI/CLI) shows status glyph, title, status code, and percentage. Details include tags, description, subtasks, dependencies, blockers.

### Editing tasks

```bash
apply_task edit TASK-001 \
  --description "New description" \
  --context "Additional context" \
  --tags "backend,api" \
  --priority HIGH \
  --phase sprint-2 \
  --component auth \
  --depends-on TASK-002,TASK-003  # replace all dependencies
  --add-dep TASK-004              # add single dependency
  --remove-dep TASK-002           # remove single dependency
  --new-domain "core/api"         # move to subdirectory
```

### Status updates

```bash
apply_task update [TASK] ACTIVE # TODO → ACTIVE (start work)
apply_task update [TASK] DONE   # ACTIVE → DONE (complete, requires all subtasks done)
apply_task update [TASK] TODO   # → TODO (reopen)

# Alternative argument order
apply_task update DONE [TASK]   # Status first, then task
```

### Navigation

```bash
apply_task next           # show the next 3 priority tasks and focus the first
```

### TUI

```bash
apply_task tui            # launch full-screen interface
apply_task tui --theme dark-contrast
```

### Checkpoint macros

```bash
# All-in-one completion (single subtask)
apply_task ok TASK-001 0 \
  --criteria-note "evidence" \
  --tests-note "output" \
  --blockers-note "resolution"

# Batch completion (multiple subtasks)
apply_task ok TASK-001 0,1,2        # specific indices
apply_task ok TASK-001 --all        # all incomplete subtasks

# Nested subtasks (path notation)
apply_task ok TASK-001 --path 0.1.2 # path instead of index

# Add note to checkpoint
apply_task note TASK-001 0 --checkpoint tests --note "pytest output"
apply_task note TASK-001 0 --checkpoint criteria --undo  # reset confirmation

# Alias: sub ok (same as ok)
apply_task sub ok TASK-001 0 --criteria-note "..." --tests-note "..."

# Batch from JSON
apply_task bulk --task TASK-001 --input checkpoints.json
```

### Checkpoint wizard

```bash
# Interactive step-by-step wizard
apply_task checkpoint TASK-001 --subtask 0

# Auto-confirm all checkpoints
apply_task checkpoint TASK-001 --auto

# With default note
apply_task checkpoint . --subtask 0 --note "Verified in code review"
```

**Bulk JSON payload example:**

```json
[
  {
    "task": "TASK-123",
    "index": 0,
    "criteria": {"done": true, "note": "metrics logged"},
    "tests": {"done": true, "note": "pytest -q"},
    "blockers": {"done": true, "note": "resolved"},
    "complete": true
  }
]
```

### Subtask management

```bash
# Add subtask with full specification
apply_task subtask TASK-001 --add "Subtask title" \
  --criteria "criterion1;criterion2" \
  --tests "test1;test2" \
  --blockers "blocker1;blocker2"

# Confirm individual checkpoints
apply_task subtask TASK-001 --criteria-done 0 --note "evidence"
apply_task subtask TASK-001 --tests-done 0 --note "output"
apply_task subtask TASK-001 --blockers-done 0 --note "resolution"
apply_task subtask TASK-001 --done 0

# Add dependency
apply_task add-dep TASK-001 TASK-002  # TASK-001 depends on TASK-002
```

### GitHub Projects helpers

```bash
apply_task projects-webhook --payload payload.json --signature "$X_HUB_SIG" --secret "$WEBHOOK_SECRET"
apply_task projects-webhook-serve --host 0.0.0.0 --port 8787 --secret "$WEBHOOK_SECRET"
```

The first form applies a single webhook payload (useful for testing or piping from another server). The `projects-webhook-serve` command runs a minimal HTTP server that accepts GitHub `projects_v2_item` events and keeps `.task` metadata in sync when board fields change.

## AI interface (JSON API)

For AI agents and automation, `apply_task ai` provides a structured JSON API:

```bash
# Basic usage
apply_task ai '{"intent": "context"}'
apply_task ai '{"intent": "context", "compact": true}'
echo '{"intent": "resume"}' | apply_task ai
apply_task ai @request.json
```

### Available intents

| Intent | Description | Example |
|--------|-------------|---------|
| `context` | Get working context | `{"intent": "context", "format": "markdown"}` |
| `resume` | Restore AI session | `{"intent": "resume", "task": "TASK-001"}` |
| `create` | Create new task | `{"intent": "create", "title": "...", "parent": "ROOT"}` |
| `decompose` | Add subtasks | `{"intent": "decompose", "task": "T-1", "subtasks": [...]}` |
| `define` | Set subtask fields | `{"intent": "define", "task": "T-1", "path": "0", "criteria": [...], "tests": [...], "blockers": [...]}` |
| `verify` | Verify checkpoints | `{"intent": "verify", "task": "T-1", "path": "0", ...}` |
| `done` | Complete subtask | `{"intent": "done", "task": "T-1", "path": "0"}` |
| `progress` | Toggle completion | `{"intent": "progress", "task": "T-1", "path": "0", "completed": true}` |
| `plan` | Set/advance AI plan | `{"intent": "plan", "task": "T-1", "steps": [...], "current": 0}` |
| `delete` | Delete task/subtask | `{"intent": "delete", "task": "T-1"}` |
| `complete` | Complete task | `{"intent": "complete", "task": "T-1"}` |
| `batch` | Multiple ops | `{"intent": "batch", "task": "T-1", "atomic": true, "operations": [...]}` |
| `undo` | Undo last op | `{"intent": "undo"}` |
| `redo` | Redo undone op | `{"intent": "redo"}` |
| `history` | View history | `{"intent": "history", "task": "T-1", "format": "markdown"}` |
| `storage` | Storage info | `{"intent": "storage"}` |
| `migrate` | Migrate local→global | `{"intent": "migrate"}` |

### Response format

All responses follow a consistent structure:

```json
{
  "success": true,
  "intent": "context",
  "result": { ... },
  "suggestions": ["next action 1", "next action 2"],
  "recovery_hint": { ... }  // on errors
}
```

## MCP server

For Claude Code and other AI assistants:

```bash
apply_task mcp  # Start MCP stdio server
```

Available tools (core): `tasks_context`, `tasks_list`, `tasks_show`, `tasks_create`, `tasks_decompose`, `tasks_define`, `tasks_verify`, `tasks_done`, `tasks_progress`, `tasks_delete`, `tasks_complete`, `tasks_batch`, `tasks_history`, `tasks_storage`, `tasks_next`.

AI transparency/tools: `tasks_ai_status`, `tasks_plan`, `tasks_user_signal`, `tasks_send_signal`, `tasks_template_subtasks`.

Macros/automation: `tasks_macro_ok`, `tasks_macro_note`, `tasks_macro_bulk`, `tasks_macro_update`, `tasks_macro_suggest`, `tasks_macro_quick`, `tasks_automation_task_template`, `tasks_automation_health`, `tasks_automation_projects_health`.

Configure in Claude Desktop:
```json
{"mcpServers": {"tasks": {"command": "apply_task", "args": ["mcp"]}}}
```

## Direct tasks.py commands (for scripts)

```bash
./tasks.py tui
./tasks.py list --status ACTIVE
./tasks.py show TASK-001
./tasks.py create "Task" --description "..." --tags "tag1,tag2" --subtasks @file.json
./tasks.py task "Task #tag"          # smart parser (tags/deps from title)
./tasks.py add-subtask TASK "..." --criteria "metric>=85%" --tests "pytest -q" --blockers "DB access"
./tasks.py add-dependency TASK "TASK-002"
./tasks.py edit TASK --description "..."
```

## ID formats

`001`, `1`, `TASK-001` — everything is normalized to `TASK-001`.

## Last-task context

`.last` keeps the last `TASK@domain`, so `apply_task show`, `start`, `done`, `fail` can operate without retyping IDs.

## Git awareness

Search order for `tasks.py`:
1. git root (`git rev-parse --show-toplevel`)
2. current directory
3. parents up to the root
4. directory containing the CLI script

This makes `apply_task` usable from any nested folder in the repo.
