# Git-aware workflow

`apply_task` automatically finds the git root and uses the single backlog (`todo.machine.md` + `.tasks/`) from the root, no matter which subdirectory you run the command from.

## Search priority for `tasks.py`

1. **Git root (highest priority)** – `git rev-parse --show-toplevel` defines the root; the CLI looks for `tasks.py` there.
2. **Current directory** – used when the folder is not a git repo or no `tasks.py` lives in the root.
3. **Parent directories** – walk up from the current path but never above the git root.
4. **Script directory** – fallback: use the directory where the `apply_task` executable resides.

## Examples

### Deep inside the repo

```
my-project/               # git root
├── tasks.py
├── todo.machine.md
└── src/components/auth/

cd my-project/src/components/auth
apply_task "Fix auth bug #critical"   # stored in my-project/.tasks/
apply_task list                        # reads my-project/todo.machine.md
```

### Multiple repos side-by-side

```
workspace/
├── project-a/ (git)
└── project-b/ (git)

cd workspace/project-a/src
apply_task list   # touches project-a/.tasks/

cd ../project-b/tests
apply_task list   # touches project-b/.tasks/
```

### Non-git folder

```
cd ~/random-folder
apply_task list
# → "tasks.py not found". Initialize git or copy tasks.py here.
```

## Benefits

1. **Zero ambiguity** – you always manipulate the backlog of the project you are standing in.
2. **Convenience** – no need to jump to the root or pass file paths.
3. **Team coherence** – everyone works with the single `todo.machine.md` under version control.

## Bootstrapping a new project

```bash
cd my-new-project
git init
cp /path/to/task-tracker/tasks.py .
cp /path/to/task-tracker/requirements.txt .
mkdir .tasks && touch todo.machine.md
apply_task tui
```

## Troubleshooting

- `git rev-parse --show-toplevel` → shows the expected root. Ensure `tasks.py` is stored there.
- `apply_task list` from a subdirectory should still read the root backlog. If not, copy `tasks.py` to the root.
- Nested git roots/submodules: each repo needs its own `tasks.py`. When you `cd` into the submodule, its backlog takes over.
