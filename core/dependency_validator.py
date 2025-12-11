"""Dependency validation with cycle detection.

Pure domain logic for validating task dependencies.
No I/O operations - receives task data as parameters.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class DependencyError:
    """Represents a dependency validation error."""

    task_id: str
    error_type: str  # "missing", "cycle", "self"
    details: str

    def __str__(self) -> str:
        return f"{self.task_id}: {self.error_type} - {self.details}"


def validate_dependency_exists(
    task_id: str, depends_on: List[str], existing_ids: Set[str]
) -> List[DependencyError]:
    """Validate that all dependencies reference existing tasks.

    Args:
        task_id: The task being validated
        depends_on: List of task IDs this task depends on
        existing_ids: Set of all existing task IDs

    Returns:
        List of errors for missing dependencies
    """
    errors = []
    for dep_id in depends_on:
        if dep_id == task_id:
            errors.append(
                DependencyError(task_id, "self", f"Task cannot depend on itself")
            )
        elif dep_id not in existing_ids:
            errors.append(
                DependencyError(task_id, "missing", f"Dependency '{dep_id}' not found")
            )
    return errors


def detect_cycle(
    task_id: str,
    depends_on: List[str],
    dependency_graph: Dict[str, List[str]],
) -> Optional[List[str]]:
    """Detect if adding these dependencies would create a cycle.

    Uses DFS to find cycles in the dependency graph.

    Args:
        task_id: The task being validated
        depends_on: List of task IDs this task would depend on
        dependency_graph: Current dependency graph {task_id: [dep_ids]}

    Returns:
        List of task IDs forming the cycle, or None if no cycle
    """
    # Build temporary graph with the new dependencies
    graph = {k: list(v) for k, v in dependency_graph.items()}
    graph[task_id] = depends_on

    # DFS with path tracking
    visited: Set[str] = set()
    rec_stack: Set[str] = set()
    path: List[str] = []

    def dfs(node: str) -> Optional[List[str]]:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                cycle = dfs(neighbor)
                if cycle:
                    return cycle
            elif neighbor in rec_stack:
                # Found cycle - extract it from path
                cycle_start = path.index(neighbor)
                return path[cycle_start:] + [neighbor]

        path.pop()
        rec_stack.remove(node)
        return None

    # Start DFS from the task being validated
    cycle = dfs(task_id)
    return cycle


def validate_dependencies(
    task_id: str,
    depends_on: List[str],
    existing_ids: Set[str],
    dependency_graph: Dict[str, List[str]],
) -> Tuple[List[DependencyError], Optional[List[str]]]:
    """Full dependency validation.

    Args:
        task_id: The task being validated
        depends_on: List of task IDs this task depends on
        existing_ids: Set of all existing task IDs
        dependency_graph: Current dependency graph {task_id: [dep_ids]}

    Returns:
        Tuple of (errors, cycle_path or None)
    """
    errors = validate_dependency_exists(task_id, depends_on, existing_ids)

    # Only check for cycles if all dependencies exist
    if not errors:
        cycle = detect_cycle(task_id, depends_on, dependency_graph)
    else:
        cycle = None

    return errors, cycle


def build_dependency_graph(tasks: List[Tuple[str, List[str]]]) -> Dict[str, List[str]]:
    """Build dependency graph from list of (task_id, depends_on) tuples.

    Args:
        tasks: List of (task_id, depends_on_list) tuples

    Returns:
        Dictionary mapping task_id to list of dependency IDs
    """
    return {task_id: deps for task_id, deps in tasks}


def get_blocked_by_dependencies(
    task_id: str,
    depends_on: List[str],
    task_statuses: Dict[str, str],
) -> List[str]:
    """Get list of incomplete dependencies that block this task.

    Args:
        task_id: The task to check
        depends_on: List of task IDs this task depends on
        task_statuses: Dictionary mapping task_id to status

    Returns:
        List of dependency task IDs that are not yet complete (status != "OK")
    """
    blocking = []
    for dep_id in depends_on:
        status = task_statuses.get(dep_id)
        if status is None or status != "OK":
            blocking.append(dep_id)
    return blocking


def topological_sort(
    task_ids: List[str],
    dependency_graph: Dict[str, List[str]],
) -> List[str]:
    """Sort tasks in dependency order (dependencies first).

    Uses Kahn's algorithm for topological sorting.

    Args:
        task_ids: List of task IDs to sort
        dependency_graph: Dependency graph {task_id: [dep_ids]}

    Returns:
        List of task IDs in dependency order (dependencies come before dependents)

    Raises:
        ValueError: If cycle detected (should be pre-validated)
    """
    # Build in-degree map
    in_degree: Dict[str, int] = {tid: 0 for tid in task_ids}
    for tid in task_ids:
        for dep in dependency_graph.get(tid, []):
            if dep in in_degree:
                in_degree[tid] += 1

    # Find tasks with no dependencies
    queue = [tid for tid in task_ids if in_degree[tid] == 0]
    result: List[str] = []

    while queue:
        # Take task with no remaining dependencies
        current = queue.pop(0)
        result.append(current)

        # Reduce in-degree for dependents
        for tid in task_ids:
            if current in dependency_graph.get(tid, []):
                in_degree[tid] -= 1
                if in_degree[tid] == 0:
                    queue.append(tid)

    if len(result) != len(task_ids):
        raise ValueError("Cycle detected in dependency graph")

    return result


__all__ = [
    "DependencyError",
    "validate_dependency_exists",
    "detect_cycle",
    "validate_dependencies",
    "build_dependency_graph",
    "get_blocked_by_dependencies",
    "topological_sort",
]
