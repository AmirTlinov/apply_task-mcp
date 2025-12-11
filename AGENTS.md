# AGENTS playbook (English, strict)

## Core principles

- **Workflow**: use `apply_task` only; keep tasks atomic (<1 day); log checkpoints with notes; run `pytest -q` before delivery.
- **Communication**: answer users in their language; code/docs in English, concise.
- **Architecture**: hexagonal monolith, vertical slices; domain = folder `domain/feature`; layers `application/domain/infrastructure/interface` (see `DOMAIN_STRUCTURE.md`).
- **Quality**: diff coverage ≥85%, cyclomatic complexity ≤10, no mocks/stubs in prod, one file = one responsibility, Conventional Commits.

## Storage

Tasks live in `~/.tasks/<namespace>` derived from git remote (or folder name). Local `.tasks` inside the repo is ignored. TUI starts with project picker from global store.

## Key commands

```bash
# Task lifecycle
apply_task create "..." --parent ROOT --description "..." --tests "..." --risks "..." --subtasks @file.json
apply_task task "Title #tag @TASK-001"  # smart create (auto-parses tags & deps)
apply_task edit TASK-001 --description "..." --priority HIGH --depends-on TASK-002
apply_task update TASK-001 WARN|OK|FAIL

# Checkpoints
apply_task ok TASK-001 0 --criteria-note "..." --tests-note "..." --blockers-note "..."
apply_task ok TASK-001 0,1,2       # batch
apply_task ok TASK-001 --all       # all incomplete
apply_task checkpoint TASK-001 --auto  # wizard

# Navigation
apply_task list --blocked --stale 7 --progress
apply_task analyze TASK-001
apply_task suggest | quick | next
```

## AI interface

For AI agents, use the JSON API:

```bash
apply_task ai '{"intent": "context", "compact": true}'
apply_task ai '{"intent": "resume"}'
apply_task ai '{"intent": "history", "format": "markdown"}'
```

**Intents**: `context`, `resume`, `create`, `decompose`, `define`, `verify`, `done`, `progress`, `delete`, `complete`, `batch`, `undo`, `redo`, `history`, `storage`, `migrate`.

## MCP server

```bash
apply_task mcp  # Start MCP stdio server for Claude Code
```

## GitHub Projects

Config `.apply_task_projects.yaml`, token `APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN`; without token sync is off, CLI works offline.

## Devtools automation

```bash
apply_task automation task-template  # → .tmp/subtasks.template.json
apply_task automation task-create    # validate-only default, auto-template
apply_task automation checkpoint     # notes/ok from log
apply_task automation health         # pytest → .tmp/health.log
apply_task automation projects-health  # short sync status
```

## File aliases

- `README.md` — what the tool is and how to start.
- `DOMAIN_STRUCTURE.md` — domain/layer layout.
- `SYNTAX.md` — CLI/JSON formats, required fields.
- `CHANGES.md` — UX/features history.
- `apply_task help` — mandatory CLI rules (for AI agents).
