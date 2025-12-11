"""Validation helpers for subtasks to keep tasks_app slim."""

from typing import Dict, List, Tuple

from core import SubTask
from core.desktop.devtools.application.task_manager import _flatten_subtasks

CHECKLIST_SECTIONS = [
    ("context", ["context", "контекст", "why", "motivation"], "Обоснование и цель", ["because", "зачем", "причина"]),
    ("criteria", ["criteria", "критерии", "definition of done", "acceptance"], "Критерии успеха", ["done", "accept"]),
    ("tests", ["tests", "тесты", "checks", "валид"], "Тесты/проверки", ["test", "check", "verify"]),
    ("blockers", ["blockers", "зависимости", "risks"], "Блокеры/зависимости", ["blocked", "risk", "завис"]),
]


def validate_subtasks_coverage(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    present: Dict[str, SubTask] = {}
    for st in subtasks:
        low = st.title.lower()
        for name, keywords, *_ in CHECKLIST_SECTIONS:
            if any(k in low for k in keywords):
                present.setdefault(name, st)

    missing = [name for name, *_ in CHECKLIST_SECTIONS if name not in present]
    return not missing, missing


def validate_subtasks_quality(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    present: Dict[str, SubTask] = {}
    for _, st in _flatten_subtasks(subtasks):
        low = st.title.lower()
        for name, keywords, _, anchors in CHECKLIST_SECTIONS:
            if any(k in low for k in keywords) and any(a in low for a in anchors):
                if name not in present or len(st.title) > len(present[name].title):
                    present[name] = st

    for name, _, desc, anchors in CHECKLIST_SECTIONS:
        st = present.get(name)
        if not st:
            continue
        text = st.title.strip()
        long_enough = len(text) >= 30
        has_colon = ":" in text
        has_any_anchor = any(a.lower() in text.lower() for a in anchors)
        if not (long_enough and has_colon and has_any_anchor):
            issues.append(f"{name}: добавь детали (>=30 символов, включи ':' и ключевые слова из темы)")
    return len(issues) == 0, issues


def validate_subtasks_structure(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    for idx, (_, st) in enumerate(_flatten_subtasks(subtasks), 1):
        missing = []
        if not st.success_criteria:
            missing.append("критерии")
        if not st.tests:
            missing.append("тесты")
        if not st.blockers:
            missing.append("блокеры")
        if missing:
            issues.append(f"Подзадача {idx}: добавь {', '.join(missing)}")
    return len(issues) == 0, issues
