from typing import Dict, List, Tuple


def sync_status_fragments(snapshot: Dict[str, str], enabled: bool, flash: bool, filter_flash: bool) -> List[Tuple[str, str]]:
    """Unified status label for Git Projects."""
    entries: List[Tuple[str, str]] = []
    has_issue = bool(snapshot.get("status_reason"))
    if flash and not filter_flash:
        entries.append(("class:icon.warn", "Git Projects ■"))
        if filter_flash:
            return entries

    label = "Git Projects ■" if enabled else "Git Projects □"
    if has_issue:
        label = f"{label} !"
    elif snapshot.get("last_pull") or snapshot.get("last_push"):
        lp = snapshot.get("last_pull") or "—"
        lpsh = snapshot.get("last_push") or "—"
        label = f"{label} pull={lp} push={lpsh}"

    style = "class:status.fail" if has_issue else ("class:icon.check" if enabled else "class:text.dim")
    entries.append((style, label))
    return entries


__all__ = ["sync_status_fragments"]
