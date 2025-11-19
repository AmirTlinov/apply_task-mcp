# apply_task — deterministic operator interface

Use the `apply_task` CLI for every task, goal and requirement. The storage is `.tasks/` (one `TASK-xxx.task` per goal, domain folders allowed). The `.last` file caches `TASK@domain` to restore context for shorthand commands.

## Critical rules

- Work **only** through the CLI. Direct edits of `.tasks/` are forbidden.
- Each task must be decomposed into atomic (< 1 workday), measurable subtasks.
- Every subtask stores: `title`, `criteria` (measurable metrics/states), `tests` (commands + coverage/SLAs), `blockers` (mandatory dependencies/risks) and optional `notes`.
- Blockers cannot be empty: record external services, approvals, sequencing, risks.
- Tests must describe commands, coverage targets, datasets, expected metrics (≥85% coverage, explicit SLAs/perf). No placeholders or TBD wording.
- Always create tasks inside a domain folder via `--domain/-F` (or `--phase/--component`). See [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md) for the mandatory hexagonal layout.

## Structured CLI output

All non-interactive commands emit a single JSON block with the same shape:

```json
{
  "command": "list",
  "status": "OK",
  "message": "Backlog",
  "timestamp": "2025-11-19T12:34:56.789Z",
  "summary": "5 tasks",
  "payload": { "tasks": [ ... ] }
}
```

- `payload.task`/`payload.tasks` contain the full `TaskDetail`/`SubTask` objects (criteria, tests, blockers, checkpoint notes).
- Errors keep the same format with `status: "ERROR"`.
- Interactive flows (`tui`, `guided`) remain textual, but they still read/write the same files.

## Subtask input

- `--subtasks @/abs/path/file.json` — load JSON from file.
- `--subtasks -` — read JSON from STDIN (perfect for heredoc/pipes).
- `apply_task template subtasks --count N` — produce a valid JSON skeleton for ≥3 subtasks.
- All inputs pass flagship validation (≥20 characters, detailed criteria/tests/blockers).

## Macro commands & history

- `apply_task ok TASK IDX --criteria-note ... --tests-note ... --blockers-note ...` — confirm all checkpoints and close the subtask in one shot.
- `apply_task note TASK IDX --checkpoint {criteria|tests|blockers} --note "..." [--undo]` — add proof or roll back.
- `apply_task bulk --input payload.json` — execute a list of checkpoint updates/closures.
- `apply_task history [N]` + `apply_task replay N` — inspect and re-run previous commands.

## Creating a task (example)

```bash
apply_task "Task Title #tag" \
  --parent TASK-001 \
  --description "Concrete scope" \
  --tests "pytest -q;apply_task help" \
  --risks "risk1;risk2" \
  --subtasks @payload/subtasks.json
```

Required flags: `--parent`, `--description`, `--tests`, `--risks`, `--subtasks`. The subtasks payload must define ≥3 detailed entries; each entry must describe criteria/tests/blockers with measurable statements.

## Validation gates

1. **Structural**: YAML front matter must include `id`, `title`, `status`, `domain`, `phase/component (optional)`, `priority`, timestamps, and `progress`.
2. **Subtasks**: at least three entries, ≥20 characters each, explicit lists of criteria/tests/blockers.
3. **Tests**: include commands plus success metrics (coverage, SLA, perf, regression suites).
4. **Blockers**: list real dependencies (services, approvals, infrastructure) + mitigation ideas.
5. **Risks**: specify categories and detection triggers.
6. **Criteria/Tests/Blockers** cannot be confirmed without notes (proof). `apply_task ok/note/bulk` enforce this.

## Status discipline

| Status | Meaning                 | Allowed transitions |
|--------|-------------------------|---------------------|
| FAIL   | Backlog / blocked       | → WARN              |
| WARN   | In progress (work proof)| → OK / FAIL         |
| OK     | Done (all checkpoints)  | —                   |

- `apply_task start` moves FAIL → WARN only after all prerequisites recorded.
- `apply_task done` allowed only when **every** subtask has criteria/tests/blockers confirmed.

## TUI-specific reminders

- Dual hotkeys: latin (`q`, `w`, `e`, `r`, `j`, `k`) and Cyrillic equivalents.
- Mouse wheel scrolls the viewport; Shift+wheel scrolls horizontally.
- Clicking `[← Back]` returns from detail/subtask view.
- Footer always shows domain path, timestamps, duration, and legend.

## GitHub Projects v2 sync

- Copy `apply_task_projects.example.yaml` → `.apply_task_projects.yaml` and define the project owner/type/number plus field names.
- Provide a token via `APPLY_TASK_GITHUB_TOKEN` (or `GITHUB_TOKEN`) with access to Projects v2.
- Every `save_task` automatically upserts a draft issue inside the configured project; status, progress, domain, and the subtask checklist are updated on each change.
- Metadata (`project_item_id`, `project_draft_id`, per-subtask IDs) is stored in the `.task` front matter so sync is idempotent.
- If config or token are missing the sync layer is inert and the CLI continues to work offline.

## Linting & tests

Run the CLI tests before shipping changes:

```bash
PYTHONPATH=. pytest tests/test_tui_selection.py tests/test_tui_scroll.py tests/test_theme.py tests/test_apply_cli.py
```

Keep coverage ≥85% on modified code and never skip the flagship validation.
