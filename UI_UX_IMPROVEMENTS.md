# UI/UX improvements — responsive TUI

## Summary

The TUI now clamps the task table to the terminal width: columns shrink and reflow without breaking borders, keeping Stat, Title, Progress, and Subtasks visible down to tight viewports.

## Responsive layout system

- `ColumnLayout` dataclass describes the set of columns per breakpoint.
- `ResponsiveLayoutManager` selects the layout based on terminal width.

| Width          | Columns rendered                                      |
|----------------|-------------------------------------------------------|
| < 56 chars     | Stat, Title                                           |
| 56–71          | Stat, Title, Progress                                 |
| 72–89          | Stat, Title, Progress, Subtasks (compact)             |
| 90–109         | Stat, Title, Progress, Subtasks (balanced)            |
| 110–139        | Stat, Title, Progress, Subtasks (roomy)               |
| ≥ 140          | Stat, Title, Progress, Subtasks (wide)                |

Subtasks remain visible down to 72 px thanks to the compact layout (see [SUBTASKS_VISIBILITY_FIX.md](SUBTASKS_VISIBILITY_FIX.md)).

## Task list refactor

`get_task_list_text()` was rewritten to use the responsive manager:

- `_format_cell` ensures consistent padding.
- `_get_status_info` centralizes icons/colors.
- `_apply_scroll` trims content by the horizontal offset.
- Width calculator distributes the remaining budget to flexible columns without exceeding the terminal size.
- Numeric columns (progress, subtasks) honor the longest values and stay within the viewport.
- Layout transitions are smooth; no abrupt jumps.

## Detail view width

```
content_width = max(40, term_width - 2)
```

Detail and single-subtask panels currently keep a two-character margin to preserve borders on narrow terminals. Further height/width tuning can be layered on top of this clamp.

## Architecture diagram

```
ResponsiveLayoutManager
  ↓ select_layout(width)
ColumnLayout instances
  ↓ calculate_widths(width)
get_task_list_text()
  ↓ renders cells with scroll + padding
```

## Testing

```
python3 test_responsive.py
Layout Selection      ✓
Width Calculation     ✓
Detail View Width     ✓
```

## Examples

**70 chars**
```
+----+----------------------------------+-----+
|Stat|Title                             |Prog |
+----+----------------------------------+-----+
| OK |Implement authentication          |100% |
|WARN|Add database migrations           | 45% |
```

**120 chars**
```
+----+-------------------------------+-----+------+
|Stat|Title                          |Prog |Subt  |
+----+-------------------------------+-----+------+
| OK |Implement authentication       |100% | 6/6  |
|WARN|Add database migrations        | 45% | 3/8  |
```

**180+ chars**
```
+----+--------------------------------------+-----+------+
|Stat|Title                                 |Prog |Subt  |
+----+--------------------------------------+-----+------+
| OK |Implement auth                        |100% | 6/6  |
|WARN|Add DB migrations                     | 45% | 3/8  |
```

## Metrics

- Table width is clamped to the terminal size even with long numbers.
- Breakpoints collapse cleanly from 4 → 3 → 2 columns (72 px and 56 px thresholds).
- Layout selection cost is O(1) across six breakpoints.

## Compatibility

- CLI commands unchanged.
- Themes and keyboard shortcuts work as before.
- `.task` files are backward compatible.
- Best viewed at ≥72 columns (full columns), graceful fallback below.

## Next ideas

1. Vertical responsiveness (hide footer on small heights).
2. User-defined breakpoints via `.apply_taskrc.yaml`.
