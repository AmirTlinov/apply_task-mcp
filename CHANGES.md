# Changes

## 2025-12-12 · GUI flagship polish

- Added command palette (`Cmd/Ctrl+K`) for navigation + quick actions and task search.
- Added keyboard navigation in Tasks list (`j/k`, `Enter`) with selection highlighting.
- Task status labels now unified across GUI/TUI/CLI/MCP as `TODO` / `ACTIVE` / `DONE`.
- Replaced `window.confirm` with consistent confirm dialogs for destructive actions.
- Added inline subtask title editing (double-click) via `define` intent support for `title`.
- Timeline items are clickable and open task details.
- Improved subtask checkpoint section responsiveness (buttons wrap, no clipping).

## 2025-12-07 · AI interface enhancements

### New AI intents
- `resume` — restore AI session context with timeline and dependencies after context loss
- `history` — view operation history or task event timeline with markdown format support
- `context` — now supports `format: "markdown"` for prompt-friendly output

### New CLI commands
- `task` — smart create with auto-parsing of #tags and @dependencies from title
- `edit` — edit task properties (description, tags, priority, phase, component, dependencies)
- `checkpoint` — interactive wizard for step-by-step checkpoint confirmation
- `analyze` — deep task analysis
- `add-dep` — add dependency between tasks

### New flags
- `--path` — support for nested subtasks (0.1.2 notation) in `ok`, `note`, `subtask` commands
- `--depends-on` — specify dependencies when creating/editing tasks
- `--validate-only` — dry run validation without creating task
- `--progress` — show completion progress in list command
- `--blocked` — filter tasks blocked by dependencies
- `--stale N` — filter tasks inactive for N days

### Batch operations
- `ok TASK 0,1,2` — batch complete multiple subtasks by indices
- `ok TASK --all` — complete all incomplete subtasks
- `batch` intent supports `atomic: true` for all-or-nothing operations

### Bug fixes
- Fixed `handle_history` AIResponse field violations (error→error_message, message→summary)
- Fixed `handle_resume` tuple unpacking for `get_blocked_by_dependencies`
- Fixed `handle_context` message field usage
- Added missing `params` field to `Suggestion` dataclass

## 2025-11-22 · Nested subtasks TUI
- Detail view now renders nested subtasks as an indented tree with `--path` prefixes (e.g., `0.1.2`), and selection is tracked by path.
- Subtask actions in TUI (toggle, edit, delete, open card) now honor nested paths; mouse/keyboard navigation works across depths.
- Added tree folding: use `←/→` in detail view to collapse/expand branches and follow children.

## 2025-11-21 · Docs hygiene
- Added `AGENTS.md` with hard rules and file aliases, linked from README.
- Added `automation` shortcuts (task-template/create/checkpoint/health/projects-health) with defaults in `.tmp`.
