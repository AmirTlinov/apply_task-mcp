# Task Tracker — single-file CLI + TUI

Task Tracker (`apply_task`) is a single self-contained CLI/TUI that keeps your backlog deterministic and AI-friendly. Every non-interactive command returns structured JSON, while the TUI gives instant visibility into objectives, subtasks, tests, and blockers.

**Start here**
- Rules & aliases: [AGENTS.md](AGENTS.md)
- Domain layout: [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md)
- Syntax reference: [SYNTAX.md](SYNTAX.md)

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Install into PATH (one-time; isolated)
pipx install .
# or: python -m pip install --user .

# Launch the TUI (auto-opens current project; project picker is still available via ←)
apply_task tui
```

## ASCII screen previews

### Board view (list of tasks)

```
+------+----------------------------------------------+-----+-----+
| ● OK | TASK-022 · Core-Lab · Mixed policies runtime |100% |3/3  |
| ● IP | TASK-023 · Core-Lab · Prompt blending engine | 65% |2/3  |
| ○ BL | TASK-024 · Tools · Streaming tracer          | 20% |0/3  |
+------+----------------------------------------------+-----+-----+
Legend: ● Done   ● In Progress   ○ Backlog | % progress | Σ subtasks
Mouse: wheel scrolls viewport, click selects, double-click opens details.
```

### Detail view with back button

```
+====================================================================+
| [← Back]                                                           |
+--------------------------------------------------------------------+
| SUBTASK 2   ● IN PROGRESS                                          |
+--------------------------------------------------------------------+
| Title: Wire policy mixer across runtime injections                 |
| [• • ·]  Criteria/Test/Blockers checkpoints                        |
|  > 1. Collect policy graph stats                                   |
|    [• • ·]                                                         |
|  > 2. Simulate mixer latency                                       |
|                                                                   |
| Notes:                                                             |
|  - pytest -k mixer_latency                                         |
|  - attach tracing IDs                                              |
+====================================================================+
```

## Why this tool

- **Single file** — copy `tasks.py` into any repo, no external service.
- **Git-aware** — works from any subdirectory, always anchors to the project root.
- **TUI + CLI** — human-friendly interface, deterministic JSON for automation.
- **Keyboard & mouse parity** — dual-language hotkeys plus wheel + click navigation.
- **Nested subtasks tree** — detail pane renders recursive subtasks with `--path` prefixes (e.g., `0.1.2`), all actions honor the tree; use `←/→` in detail view to collapse/expand.
- **Domain discipline** — tasks live in domain folders inside `.tasks/` (see [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md)).
- **Guided quality gates** — criteria/tests/blockers must be proven before OK status.
- **Templates & validators** — `apply_task template subtasks --count N` generates JSON stubs, flagship validation guarantees ≥3 detailed subtasks, ≥85% coverage.

## Deterministic command surface

Every non-interactive command prints structured JSON:

```json
{
  "command": "list",
  "status": "OK",
  "message": "Backlog",
  "timestamp": "2025-11-19T12:34:56.789Z",
  "summary": "5 tasks",
  "payload": {
    "tasks": [
      {
        "id": "TASK-022",
        "title": "Mixed policies runtime",
        "status": "WARN",
        "progress": 65,
        "subtasks": [
          {
            "title": "Collect policy graph stats",
            "criteria_confirmed": true,
            "tests_confirmed": false,
            "blockers_resolved": false
          }
        ]
      }
    ]
  }
}
```

Use `apply_task help` for the complete command reference. Highlights:

```bash
# Create task with full specification
apply_task create "Ship vector index #feature" --parent ROOT \
  --description "Implement vector search" --tests "pytest -q" \
  --risks "perf spike;quota" --subtasks @specs/subtasks.json \
  --depends-on TASK-001,TASK-002

# Smart create (auto-parses #tags and @dependencies from title)
apply_task task "Add OAuth #feature #security @TASK-015" \
  --parent ROOT --description "..." --tests "..." --risks "..." \
  --subtasks @subtasks.json

# View and navigate
apply_task show               # show the last task
apply_task list --blocked     # tasks blocked by dependencies
apply_task list --stale 7     # inactive for 7+ days
apply_task list --progress    # show completion progress
apply_task analyze TASK-001   # deep task analysis

# Edit task properties
apply_task edit TASK-001 --description "New scope" --priority HIGH \
  --depends-on TASK-002 --phase sprint-2

# Status updates
apply_task update TASK-001 WARN   # start work (FAIL → WARN)
apply_task update TASK-001 OK     # complete (WARN → OK)

# Checkpoints
apply_task ok TASK-001 0 --criteria-note "..." --tests-note "..."
apply_task ok TASK-001 0,1,2      # batch complete multiple subtasks
apply_task ok TASK-001 --all      # complete all incomplete subtasks
apply_task ok TASK-001 --path 0.1.2  # nested subtask by path

# Checkpoint wizard
apply_task checkpoint TASK-001 --subtask 0  # interactive step-by-step
apply_task checkpoint TASK-001 --auto       # auto-confirm all

# Bulk operations
apply_task bulk --input plan.json   # batch checkpoints from JSON

# TUI
apply_task tui --theme dark-contrast   # TUI with alternative palette
```

## Keyboard & mouse quick reference

| Action                       | Keys / Mouse                                  |
|------------------------------|-----------------------------------------------|
| Exit                         | `q`, `й`, `Ctrl+Z`                             |
| Reload                       | `r`, `к`                                       |
| Enter / open                 | `Enter` or double-click                       |
| Back                         | `Esc` or click `[← Back]`                     |
| Navigate                     | `↑↓`, `j`/`о`, `k`/`л`, mouse wheel           |
| Horizontal scroll            | `Shift + wheel`                               |
| Filters                      | `1` In Progress, `2` Backlog, `3` Done, `0` All|
| Subtask toggle               | `d`, `в` or mouse click on checkbox           |

## AI interface (JSON API)

For AI agents and automation, `apply_task ai` provides a structured JSON API:

```bash
# Get current context
apply_task ai '{"intent": "context"}'
apply_task ai '{"intent": "context", "compact": true}'
apply_task ai '{"intent": "context", "format": "markdown"}'

# Resume session after context loss
apply_task ai '{"intent": "resume"}'
apply_task ai '{"intent": "resume", "task": "TASK-001"}'

# View operation history
apply_task ai '{"intent": "history"}'
apply_task ai '{"intent": "history", "task": "TASK-001", "format": "markdown"}'

# Create task programmatically
apply_task ai '{"intent": "create", "title": "Task", "parent": "ROOT", ...}'

# Batch operations (atomic)
apply_task ai '{"intent": "batch", "task": "TASK-001", "atomic": true, "operations": [...]}'
```

**Available intents:** `context`, `resume`, `create`, `decompose`, `define`, `verify`, `done`, `progress`, `delete`, `complete`, `batch`, `undo`, `redo`, `history`, `storage`, `migrate`.

All responses follow a consistent structure:

```json
{
  "success": true,
  "intent": "context",
  "result": { ... },
  "suggestions": ["next action 1", "next action 2"]
}
```

## MCP server

For Claude Code and other AI assistants:

```bash
apply_task mcp  # Start MCP stdio server
```

Available tools: `tasks_list`, `tasks_show`, `tasks_context`, `tasks_create`, `tasks_done`, `tasks_macro_ok`, `tasks_macro_note`, `tasks_macro_bulk`.

Configure in Claude Desktop:
```json
{"mcpServers": {"tasks": {"command": "apply_task", "args": ["mcp"]}}}
```

## Data layout / storage

- All tasks for a git project live in the global directory `~/.tasks/<namespace>`, where `namespace` is derived from the git remote (or folder name if no remote). The tool ignores any local `.tasks` inside the repo.
- `todo.machine.md` — human overview (`- [x] Title | OK | note >> .tasks/TASK-001.task`).
- `.tasks/TASK-###.task` — YAML front matter + Markdown body (description, subtasks, risks, tests, blockers, notes).
- `.last` — stores the last `TASK@domain` context for shorthand commands.

## Copying into another repository

```bash
cp tasks.py requirements.txt /path/to/repo/
cd /path/to/repo
# Tasks will still be stored in ~/.tasks/<namespace> for this git project
apply_task tui
```

## Additional docs

- [SYNTAX.md](SYNTAX.md) — CLI/JSON formats, required fields.
- [AI_INTENTS.md](AI_INTENTS.md) — complete AI JSON API reference.
- [AGENTS.md](AGENTS.md) — playbook for AI agents.
- [CHANGES.md](CHANGES.md) — latest UX/feature notes.
- [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md) — domain/layer layout.
- [SCROLLING.md](SCROLLING.md) — TUI navigation & scrolling design.
- [UI_UX_IMPROVEMENTS.md](UI_UX_IMPROVEMENTS.md) — rationale behind the responsive interface.
- [GIT_PROJECT.md](GIT_PROJECT.md) — git-aware workflow details.
- `automation` shortcuts: `apply_task automation --help` (templates, auto-create, checkpoint, health, projects-health).

## GitHub Projects v2 sync

`apply_task` can mirror every task into a GitHub Projects v2 board:

1. Save your GitHub PAT once (either run `apply_task projects-auth --token <PAT>` or click `[⚙ Настройки]` next to `[← Назад]` inside the TUI detail pane). The token lives in `~/.apply_task_config.yaml` and is reused across every repository.
2. Copy `apply_task_projects.example.yaml` to `.apply_task_projects.yaml` and edit it:
   ```yaml
   project:
     type: repository
     owner: AmirTlinov
     repo: apply_task
     number: 1
   fields:
     status:
       name: Status
       options:
         OK: Done
         WARN: "In Progress"
         FAIL: Backlog
     progress:
       name: Progress
     domain:
       name: Domain
     subtasks:
       name: Subtasks
   ```
3. `APPLY_TASK_GITHUB_TOKEN` / `GITHUB_TOKEN` override the stored PAT (useful for CI runners); otherwise the saved token is used automatically.
4. Any `apply_task` save automatically creates/updates the corresponding Project draft item, including status, percentage, domain text, and a Markdown checklist of subtasks.
5. Optional reverse sync: expose the webhook endpoint (or rely on the bundled GitHub Action) and every board edit updates the `.task` metadata.

If the config or token is missing, the sync layer silently disables itself. Existing tasks will update as soon as they are touched; for older ones just run `apply_task show TASK-ID` → edit/save to trigger a sync.

Sample config lives in `apply_task_projects.example.yaml`.

### Webhooks (remote → local)

GitHub Projects v2 emits `projects_v2_item` webhooks whenever a field changes. Two helper commands let you apply those changes locally:

```bash
# One-shot handler (reads payload from file or STDIN)
apply_task projects-webhook --payload payload.json --signature "$X_HUB_SIG" --secret "$HOOK_SECRET"

# Long-running HTTP server (default 0.0.0.0:8787)
apply_task projects-webhook-serve --secret "$HOOK_SECRET"
```

Point your GitHub webhook to the server URL, set the same secret, and only `projects_v2_item` events are required. When a single-select column such as “Status” is edited on the board, the corresponding `.task` front matter (`status`, `progress`, `domain`) is updated automatically. Signature validation follows `X-Hub-Signature-256` semantics; omit `--secret` to accept unsigned traffic in trusted networks.

Prefer zero servers? Keep `.github/workflows/projects-sync.yml` enabled. GitHub delivers the same `projects_v2_item` payload to Actions, which runs `apply_task projects-webhook --payload @$GITHUB_EVENT_PATH` and commits YAML updates automatically. No daemons or manual commands—the board state and `.tasks/` stay mirrored entirely via CI.

Stay in sync with `apply_task` for every change and let GitHub Projects v2 mirror the exact state of your backlog.
