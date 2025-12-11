"""Focusable line detection helpers to keep TaskTrackerTUI slim."""

from typing import List, Tuple


def focusable_line_indices(lines: List[List[Tuple[str, str]]], extract_group) -> List[int]:
    focusable: List[int] = []
    seen_groups: set[int] = set()
    border_chars = set("+-=─═│|")
    for idx, line in enumerate(lines):
        texts = "".join(text for _, text in line).strip()
        if not texts:
            continue
        if texts and all(ch in border_chars for ch in texts):
            continue
        group = extract_group(line)
        if group is not None:
            if group in seen_groups:
                continue
            seen_groups.add(group)
        if any((style or "") and ("header" in style or "label" in style or "status." in style) for style, _ in line):
            continue
        if texts.startswith("↑") or texts.startswith("↓") or texts.startswith("○"):
            continue
        focusable.append(idx)
    return focusable


__all__ = ["focusable_line_indices"]
