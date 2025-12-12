"""Interface-level constants for tasks CLI/TUI."""

from core.desktop.devtools.interface.constants_i18n import LANG_PACK  # noqa: F401

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

AI_HELP = """apply_task — hardline rules for AI agents

1) Operate only via apply_task. Never edit .tasks/ directly. Track context via .last (TASK@domain).
2) Decompose: root task + nested subtasks (any depth). Every subtask: title ≥20 chars; success_criteria; tests; blockers. Checkpoints only through ok/note/bulk --path.
3) Create tasks: set domain (--domain/-F). Generate subtasks template (apply_task template subtasks --count N > .tmp/subtasks.json), fill criteria/tests/blockers. Create: apply_task create "Title #tags" --domain <d> --description "<what/why/acceptance>" --tests "<proj tests>" --risks "<proj risks>" --subtasks @.tmp/subtasks.json. Add nested: apply_task subtask TASK --add "<title>" --criteria \"...;...\" --tests \"...;...\" --blockers \"...;...\" --parent-path 0.1 (path like 0.1.2).
4) Maintain subtasks: add via subtask; checkpoints via ok --path X.Y (criteria/tests/blockers with notes); complete via subtask --done --path only if all checkpoints OK; note progress with note --path.
5) Statuses: start at TODO -> ACTIVE when working -> DONE only when all subtasks done. Commands: apply_task update TASK TODO|ACTIVE|DONE (aliases FAIL|WARN|OK).
6) Quality gates: diff coverage ≥85%; cyclomatic complexity ≤10; no mocks/stubs in prod; one file = one responsibility; prefer <300 LOC. Before delivery run pytest -q and log executed tests. Always keep explicit blockers/tests/criteria on every node.
7) GitHub Projects: config .apply_task_projects.yaml; token APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN. If no token/remote, sync is off; CLI works offline.
Language: reply to user in their language unless asked otherwise. Task text/notes follow user language; code/tests/docs stay in English. Explicit blockers/tests/criteria on every node. No checkpoints — no done.
"""
