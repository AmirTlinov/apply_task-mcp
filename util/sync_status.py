from typing import Dict, List, Tuple


def sync_status_fragments(snapshot: Dict[str, str], enabled: bool, flash: bool, filter_flash: bool) -> List[Tuple[str, str]]:
    """Единый формат отображения статуса синхронизации."""
    entries: List[Tuple[str, str]] = []
    if flash and not filter_flash:
        entries.append(("class:icon.warn", "Git Projects ■"))
        if filter_flash:
            return entries

    label = "Git Projects ■" if enabled else "Git Projects □"
    if snapshot.get("last_pull") or snapshot.get("last_push"):
        lp = snapshot.get("last_pull") or "—"
        lpsh = snapshot.get("last_push") or "—"
        label = f"{label} pull={lp} push={lpsh}"
    if snapshot.get("status_reason"):
        label = f"{label} ({snapshot.get('status_reason')})"

    style = "class:icon.check" if enabled else "class:text.dim"
    if snapshot.get("status_reason") and not enabled:
        style = "class:icon.warn"
    entries.append((style, label))
    return entries


__all__ = ["sync_status_fragments"]
