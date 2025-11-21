from dataclasses import dataclass, field
from typing import List
from .status import Status


@dataclass
class SubTask:
    completed: bool
    title: str
    success_criteria: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    criteria_confirmed: bool = False
    tests_confirmed: bool = False
    blockers_resolved: bool = False
    criteria_notes: List[str] = field(default_factory=list)
    tests_notes: List[str] = field(default_factory=list)
    blockers_notes: List[str] = field(default_factory=list)
    project_item_id: str = ""

    def ready_for_completion(self) -> bool:
        return self.criteria_confirmed and self.tests_confirmed and self.blockers_resolved

    def status_value(self) -> Status:
        if self.completed:
            return Status.OK
        if self.ready_for_completion():
            return Status.WARN
        return Status.FAIL

    def is_valid_flagship(self) -> tuple[bool, list[str]]:
        """Quality checks matching legacy validation."""
        issues: list[str] = []
        if not self.success_criteria:
            issues.append(f"'{self.title}': нет критериев выполнения")
        if not self.tests:
            issues.append(f"'{self.title}': нет тестов для проверки")
        if not self.blockers:
            issues.append(f"'{self.title}': нет блокеров/зависимостей")
        if len(self.title) < 20:
            issues.append(f"'{self.title}': слишком короткое описание (минимум 20 символов)")
        atomic_violators = ["и затем", "потом", "после этого", "далее", ", и ", " and then", " then "]
        if any(v in self.title.lower() for v in atomic_violators):
            issues.append(f"'{self.title}': не атомарна (разбей на несколько подзадач)")
        return len(issues) == 0, issues

    def to_markdown(self) -> str:
        lines = [f"- [{'x' if self.completed else ' '}] {self.title}"]
        if self.success_criteria:
            lines.append("  - Критерии: " + "; ".join(self.success_criteria))
        if self.tests:
            lines.append("  - Тесты: " + "; ".join(self.tests))
        if self.blockers:
            lines.append("  - Блокеры: " + "; ".join(self.blockers))
        status_tokens = [
            f"Критерии={'OK' if self.criteria_confirmed else 'TODO'}",
            f"Тесты={'OK' if self.tests_confirmed else 'TODO'}",
            f"Блокеры={'OK' if self.blockers_resolved else 'TODO'}",
        ]
        lines.append("  - Чекпоинты: " + "; ".join(status_tokens))
        if self.criteria_notes:
            lines.append("  - Отметки критериев: " + "; ".join(self.criteria_notes))
        if self.tests_notes:
            lines.append("  - Отметки тестов: " + "; ".join(self.tests_notes))
        if self.blockers_notes:
            lines.append("  - Отметки блокеров: " + "; ".join(self.blockers_notes))
        return "\n".join(lines)
