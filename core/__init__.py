"""
Core domain layer: entities and domain rules for tasks/subtasks.
Kept minimal; no side effects.
"""

__all__ = ["Status", "SubTask", "TaskDetail"]

from .status import Status
from .subtask import SubTask
from .task_detail import TaskDetail
