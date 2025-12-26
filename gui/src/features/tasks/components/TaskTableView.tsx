import { useCallback, useEffect, useMemo, useState } from "react";
import type { TaskListItem } from "@/types/task";
import { TaskListSkeleton } from "@/components/common/Skeleton";
import { EmptyState } from "@/components/common/EmptyState";
import { useKeyboardListNavigation } from "@/hooks/useKeyboardListNavigation";
import { CheckpointMarks } from "@/components/common/CheckpointMarks";
import { countStepTree } from "@/features/tasks/lib/stepCounts";
import { cn } from "@/lib/utils";
import { TASK_STATUS_UI } from "@/lib/taskStatus";

interface TaskTableViewProps {
  tasks: TaskListItem[];
  onTaskClick?: (taskId: string) => void;
  focusedTaskId?: string | null;
  onFocusChange?: (taskId: string | null) => void;
  onNewTask?: () => void;
  isLoading?: boolean;
  searchQuery?: string;
}

export function TaskTableView({
  tasks,
  onTaskClick,
  focusedTaskId,
  onFocusChange,
  onNewTask,
  isLoading = false,
  searchQuery,
}: TaskTableViewProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const effectiveSelectedId = focusedTaskId ?? selectedId;

  const taskIds = useMemo(() => tasks.map((t) => t.id), [tasks]);
  const stepCountsByTask = useMemo(() => {
    const map = new Map<string, { total: number; done: number }>();
    for (const task of tasks) {
      map.set(task.id, countStepTree(task.steps));
    }
    return map;
  }, [tasks]);

  const setActiveTaskId = useCallback(
    (taskId: string | null) => {
      setSelectedId(taskId);
      onFocusChange?.(taskId);
    },
    [onFocusChange]
  );

  useKeyboardListNavigation({
    enabled: tasks.length > 0,
    itemIds: taskIds,
    activeId: effectiveSelectedId,
    onActiveChange: setActiveTaskId,
    onActivate: (taskId) => {
      setActiveTaskId(taskId);
      onTaskClick?.(taskId);
    },
  });

  useEffect(() => {
    if (!effectiveSelectedId) return;
    const el = document.querySelector<HTMLElement>(`[data-task-id="${effectiveSelectedId}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [effectiveSelectedId]);

  if (isLoading) {
    return (
      <div className="flex-1 overflow-y-auto p-[var(--density-page-pad)]">
        <TaskListSkeleton count={6} />
      </div>
    );
  }

  if (tasks.length === 0 && !searchQuery) {
    return (
      <div className="flex flex-1 items-center justify-center p-[var(--density-page-pad)]">
        <EmptyState variant="tasks" onAction={onNewTask} />
      </div>
    );
  }

  if (tasks.length === 0 && searchQuery) {
    return (
      <div className="flex flex-1 items-center justify-center p-[var(--density-page-pad)]">
        <EmptyState
          variant="search"
          title="No matching tasks"
          description={`No tasks found for "${searchQuery}". Try a different search term.`}
        />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-[var(--density-page-pad)]">
      <div className="rounded-xl border border-border bg-card">
        <div className="grid grid-cols-[44px_36px_1fr_80px_70px_70px] items-center gap-2 border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-foreground-muted">
          <span>#</span>
          <span>Status</span>
          <span>Task</span>
          <span className="text-center">✓✓</span>
          <span className="text-center">%</span>
          <span className="text-center">Σ</span>
        </div>
        <div className="divide-y divide-border">
          {tasks.map((task, idx) => {
            const statusUi = TASK_STATUS_UI[task.status];
            const counts = stepCountsByTask.get(task.id) ?? { total: 0, done: 0 };
            const stepsTotal = counts.total;
            const stepsDone = counts.done;
            const progressRaw =
              typeof task.progress === "number"
                ? task.progress
                : stepsTotal > 0
                  ? Math.round((stepsDone / stepsTotal) * 100)
                  : 0;
            const progress = Math.max(0, Math.min(100, Math.round(progressRaw)));
            const criteriaOk = !!task.criteria_confirmed || !!task.criteria_auto_confirmed;
            const testsOk = !!task.tests_confirmed || !!task.tests_auto_confirmed;

            return (
              <div
                key={task.id}
                data-task-id={task.id}
                role="button"
                tabIndex={0}
                onClick={() => {
                  setSelectedId(task.id);
                  onFocusChange?.(task.id);
                  onTaskClick?.(task.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedId(task.id);
                    onFocusChange?.(task.id);
                    onTaskClick?.(task.id);
                  }
                }}
                className={cn(
                  "grid grid-cols-[44px_36px_1fr_80px_70px_70px] items-center gap-2 px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  effectiveSelectedId === task.id ? "bg-primary/10" : "hover:bg-background-subtle"
                )}
              >
                <span className="text-xs font-mono text-foreground-subtle">{idx + 1}</span>
                <span className="flex items-center">
                  <span className={cn("h-2.5 w-2.5 rounded-full", statusUi.classes.dot)} />
                </span>
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="truncate font-medium text-foreground" title={task.title}>
                    {task.title}
                  </span>
                  <span className="truncate text-xs text-foreground-subtle">{task.id}</span>
                </div>
                <span className="flex justify-center">
                  <CheckpointMarks criteriaOk={criteriaOk} testsOk={testsOk} />
                </span>
                <span className="text-center text-xs font-semibold tabular-nums text-foreground-muted">
                  {progress}%
                </span>
                <span className="text-center text-xs font-semibold tabular-nums text-foreground-muted">
                  {stepsDone}/{stepsTotal}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
