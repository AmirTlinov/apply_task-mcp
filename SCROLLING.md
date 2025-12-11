# Horizontal scrolling in the TUI

## Features

- Horizontal scrolling lets you read long titles/descriptions without breaking the table layout.
- Works in both the task list and detail/subtask views.
- Only cell content moves; borders stay fixed.
- Footer displays the active offset.

## Controls

| Action            | Shortcut                               |
|-------------------|----------------------------------------|
| Scroll left       | `Shift + wheel up`, `[`, `Ctrl+←`       |
| Scroll right      | `Shift + wheel down`, `]`, `Ctrl+→`     |
| Reset offset      | `Esc` (when leaving details) or `Home` |

The maximum offset is 200 characters. Leaving detail mode automatically resets it.

## Implementation

- `self.horizontal_offset` stores the offset.
- Rendering helpers trim the text slice before padding.
- Columns remain aligned; formatting and colors are preserved.
- Applies to: titles, descriptions, notes, subtasks, next steps, dependencies, success criteria, problems, and risks.

```python
# Example for task titles
raw = task.name
if self.horizontal_offset > 0:
    raw = raw[self.horizontal_offset:] if len(raw) > self.horizontal_offset else ""
cell = raw[:title_w].ljust(title_w)
```

## How to test

```
./tasks.py tui
Enter a task → press ] a few times → text shifts left.
Press [ or Home to return.
```
