# Legacy Hotspots (desktop/devtools)

## Snapshot
- `projects_sync.py` — 1k+ LOC singleton with global caches and direct network/file I/O; coverage ~51% (full suite), heavy side effects and duplicated GraphQL plumbing.
- `apply_task` CLI wrapper — previously 11% coverage; manual dispatch table, history/path resolution tightly coupled to real FS/Git.
- `tasks.py` — 4k+ LOC monolith mixing CLI, TUI, sync, and network flows; coverage ~39%, many zero-covered branches around settings, automation, and Projects.

## Priorities
1) **Projects sync (P0)**: slice into adapters (GraphQL, issues API, cache persistence) + pure orchestration; inject rate limiter/session for testability; kill global caches; add deterministic fixtures for webhook/conflict handling.
2) **CLI wrapper (P1)**: keep dispatch table declarative, isolate FS/env lookups behind small helpers; ensure history/which logic is fully unit-tested and side-effect free with temp paths (baseline now at ≥90%).
3) **tasks.py monolith (P1)**: split into `interface/cli.py`, `interface/tui.py`, `infrastructure/projects.py`, `application/commands.py`; extract settings and Projects handlers first (highest churn/IO), then rendering/input pipelines; target file sizes <300 LOC per slice with coverage ≥90%.
4) **Cross-cutting (P2)**: remove dead branches (unused keybindings/scroll paths), cap cyclomatic complexity <=10 for handlers, and gate all auto-sync/network code behind feature flags with unit coverage.

## Done in this pass
- Documented hotspot map + priorities.
- Simplified CLI dispatch surface and added deterministic tests to lock behavior and coverage.
