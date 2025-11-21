## Domain rules (Hexagonal Monolith)

The project follows a “hexagonal monolith with vertical feature slices”. To keep backlog, code, and infra aligned, follow the layout below:

```
.tasks/
  <domain>/
    <feature>/
      TASK-xxx.task
core/
  <domain>/<feature>/application/...
  <domain>/<feature>/domain/...
  <domain>/<feature>/infrastructure/...
  <domain>/<feature>/interface/...
```

### Core principles

1. **Domain = folder.** Every task must be created with `--domain=<domain>/<feature>` (`-F`). The path matches both the `.tasks/` subfolder and the corresponding package in code.
2. **Vertical slices.** Choose the domain first (`payments`, `chat`, `analytics`), then the feature (`refunds`, `session-runtime`).
3. **Hexagonal layers**
   - `application` – orchestration/use-cases.
   - `domain`/`core` – entities and policies.
   - `infrastructure` – adapters, storage, external IO.
   - `interface` – CLI/TUI/API entry points.
4. **Tasks map to artefacts.** Each `.tasks/domain/feature/TASK-xxx.task` must have code changes under the same package. No anonymous catch-all folders.
5. **Phase and component** help filtering in the TUI but never replace `--domain`.

### Choosing `--domain`

1. Inspect existing folders inside `.tasks/`.
2. If a domain is new, create the folder and update this file + README.
3. The TUI shows the domain path column; verify your tasks appear under the expected branch.

### Task creation example

```bash
apply_task "Implement refunds API #feature @TASK-042" \
  --domain payments/refunds \
  --parent TASK-010 \
  --description "Add refund orchestration flow" \
  --tests "pytest -q tests/payments/test_refunds.py" \
  --risks "pci scope;manual approval" \
  --subtasks @payload/refunds_subtasks.json
```

### Active domains

- `desktop/devtools` – CLI/TUI, GitHub Projects sync, внутренние инструменты автора.

### Adding a new domain

1. Создай папки `.tasks/<domain>/<feature>/` и `core/<domain>/<feature>/` с подкаталогами слоев (`application`, `domain`, `infrastructure`, `interface`).
2. Обнови эту таблицу и README, чтобы путь был явным.
3. При переносе существующего кода — перемести файлы в соответствующие слои целевого домена и поправь импорты.
