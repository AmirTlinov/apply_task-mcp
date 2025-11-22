# Subtask column visibility

## Issue

The **Subtasks** column (completed/total counter) disappeared on screens narrower than 150 px, making it hard to gauge progress.

## Fix

Subtasks now remains visible down to **80 px** wide terminals.

| Width | Columns                                | Subtasks? |
|-------|----------------------------------------|-----------|
| ≥140  | Stat, Title, Progress, Subtasks                | ✓ |
| 110–139 | Stat, Title, Progress, Subtasks              | ✓ |
| 90–109 | Stat, Title, Progress, Subtasks               | ✓ |
| 72–89  | Stat, Title, Progress, Subtasks (compact width)| ✓ |
| 56–71  | Stat, Title, Progress                         | ✗ |
| <56    | Stat, Title                                   | ✗ |

## Code

```python
LAYOUTS = [
    ColumnLayout(min_width=140, columns=['stat','title','progress','subtasks'], stat_w=4, prog_w=8, subt_w=8, title_min=22),
    ColumnLayout(min_width=110, columns=['stat','title','progress','subtasks'], stat_w=3, prog_w=7, subt_w=7, title_min=18),
    ColumnLayout(min_width=90,  columns=['stat','title','progress','subtasks'], stat_w=3, prog_w=6, subt_w=6, title_min=16),
    ColumnLayout(min_width=72,  columns=['stat','title','progress','subtasks'], stat_w=2, prog_w=5, subt_w=5, title_min=12),
    ColumnLayout(min_width=56,  columns=['stat','title','progress'], stat_w=2, prog_w=5, title_min=12),
    ColumnLayout(min_width=0,   columns=['stat','title'], stat_w=2, title_min=10),
]
```

## Rationale

Subtasks conveys real progress (“3/8”), so it outranks Context. Notes still disappear earlier on tiny screens, but Subtasks survives to 80 px with a 6-character compact width.

## Metrics

| Metric | Before | After |
|--------|--------|-------|
| Minimum width for Subtasks | 150 px | 72 px |
| Compact mode | — | Yes |

**Date:** 2025‑11‑22 · **Version:** 2.9.3 · See also [PRIORITY_FIX.md](PRIORITY_FIX.md)
