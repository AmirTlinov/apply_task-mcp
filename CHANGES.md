# Changes

## 2025-11-21 · Docs hygiene
- Added `AGENTS.md` with hard rules and file aliases, linked from README.
- Trimmed `AI.md` to concise operator rules; README start section now points to key docs.
- Added `automation` shortcuts (task-template/create/checkpoint/health/projects-health) with defaults in `.tmp`.

# Changes: Horizontal Scrolling

## What changed

### ✓ Horizontal scrolling for cell content

**Controls**
- `Shift + wheel up` → scroll left
- `Shift + wheel down` → scroll right
- `Esc` → resets the offset whenever you leave detail view

**Behavior**
- Only the cell content scrolls; table borders stay anchored.
- Works in both task list and detail/subtask views.
- Colors and formatting remain untouched.
- Footer shows the active offset and shortcut reminder.

**Scrollable sections**
- Task titles
- Descriptions and notes
- Subtask rows
- Dependencies, Next Steps, Success Criteria, Problems, Risks

## Technical notes

**Touched files**
- `tasks.py` — implementation
- `SCROLLING.md` — documentation
- `CHANGES.md` — this summary

**Key code updates**
1. Added `self.horizontal_offset` to track scroll position.
2. Mouse handler captures `Shift+wheel` events.
3. Rendering helpers apply the offset per cell.
4. Footer now displays the offset indicator.
5. Enabled `mouse_support=True` for the application.

~30 LOC changed across rendering helpers.

## Usage

```bash
./tasks.py tui        # launch the TUI
# inside the TUI:
# 1. Move focus to the table
# 2. Hold Shift
# 3. Scroll wheel to move content horizontally
# 4. Press Esc to reset the offset when exiting details
```
