# AGENTS playbook (English, strict)

- **Workflow**: use `apply_task` only; keep tasks atomic (<1 day); log checkpoints with notes; run `pytest -q` before delivery.
- **Communication**: answer users in their language; code/docs in English, concise.
- **Architecture**: hexagonal monolith, vertical slices; domain = folder `domain/feature`; layers `application/domain/infrastructure/interface` (see `DOMAIN_STRUCTURE.md`).
- **Quality**: diff coverage ≥85%, cyclomatic complexity ≤10, no mocks/stubs in prod, one file = one responsibility, Conventional Commits.
- **GitHub Projects**: config `.apply_task_projects.yaml`, token `APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN`; without token sync is off, CLI works offline.
- **Devtools automation** (`automation`): `task-template` → `.tmp/subtasks.template.json`, `task-create` (validate-only default, auto-template), `checkpoint` (notes/ok from log), `health` (pytest → `.tmp/health.log`), `projects-health` (short sync status).

## Aliases
- `README.md` — what the tool is and how to start.
- `DOMAIN_STRUCTURE.md` — domain/layer layout.
- `SYNTAX.md` — CLI/JSON formats, required fields.
- `CHANGES.md` — UX/features history.
- `apply_task help` — mandatory CLI rules (for AI agents).
