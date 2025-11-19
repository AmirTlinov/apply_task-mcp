# Task Tracker — single-file CLI + TUI

Task Tracker (`apply_task`) is a single self-contained CLI/TUI that keeps your backlog deterministic and AI-friendly. Every non-interactive command returns structured JSON, while the TUI gives instant visibility into objectives, subtasks, tests, and blockers.

**For AI operators**: read [AI.md](AI.md) for the formal discipline, validation rules, and automation tips.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Install apply_task into PATH (one-time)
cp apply_task ~/.local/bin/ && chmod +x ~/.local/bin/apply_task

# Reload your shell (or `source ~/.zshrc`)

# Launch the TUI
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

Use `apply_task help` (also in English) for the complete command reference. Highlights:

```bash
apply_task "Ship vector index #feature" --parent ROOT --tests "pytest -q" \
  --risks "perf spike;quota" --subtasks @specs/subtasks.json
apply_task show               # show the last task
apply_task start|done|fail    # update status (WARN/OK/FAIL)
apply_task ok NOTEBOOK 0      # close criteria/tests/blockers for subtask 0
apply_task bulk --input plan.json   # batch checkpoints from JSON
apply_task checkpoint TASK-123 --auto  # guided wizard for criteria/tests/blockers
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

## Data layout

- `todo.machine.md` — human overview (`- [x] Title | OK | note >> .tasks/TASK-001.task`).
- `.tasks/TASK-###.task` — YAML front matter + Markdown body (description, subtasks, risks, tests, blockers, notes).
- `.last` — stores the last `TASK@domain` context for shorthand commands.

## Copying into another repository

```bash
cp tasks.py requirements.txt /path/to/repo/
cd /path/to/repo
mkdir -p .tasks && touch todo.machine.md
apply_task tui
```

## Additional docs

- [AI.md](AI.md) — flagship rules for AI operators and automation.
- [SYNTAX.md](SYNTAX.md) — JSON schema, CLI arg formats.
- [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md) — required hexagonal folder layout.
- [SCROLLING.md](SCROLLING.md) — TUI navigation & scrolling design.
- [UI_UX_IMPROVEMENTS.md](UI_UX_IMPROVEMENTS.md) — rationale behind the interface.
- [CHANGES.md](CHANGES.md) — latest UX/feature notes.

## GitHub Projects v2 sync

`apply_task` can mirror every task into a GitHub Projects v2 board:

1. Copy `apply_task_projects.example.yaml` to `.apply_task_projects.yaml` and edit it:
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
2. Provide a token via `APPLY_TASK_GITHUB_TOKEN` (or `GITHUB_TOKEN`) with `project` scope.
3. Any `apply_task` save automatically creates/updates the corresponding Project draft item, including status, percentage, domain text, and a Markdown checklist of subtasks.

If the config or token is missing, the sync layer silently disables itself. Existing tasks will update as soon as they are touched; for older ones just run `apply_task show TASK-ID` → edit/save to trigger a sync.

Sample config lives in `apply_task_projects.example.yaml`.

Stay in sync with `apply_task` for every change and let GitHub Projects v2 mirror the exact state of your backlog.
