import { useState } from "react";
import { TaskCard } from "./TaskCard";
import { TaskListSkeleton } from "@/components/common/Skeleton";
import { EmptyState } from "@/components/common/EmptyState";
import type { TaskListItem, TaskStatus } from "@/types/task";

interface TaskListProps {
  tasks: TaskListItem[];
  onTaskClick?: (taskId: string) => void;
  onNewTask?: () => void;
  onStatusChange?: (taskId: string, status: TaskStatus) => void;
  onDelete?: (taskId: string) => void;
  isLoading?: boolean;
  searchQuery?: string;
}

export function TaskList({
  tasks,
  onTaskClick,
  onNewTask,
  onStatusChange,
  onDelete,
  isLoading = false,
  searchQuery,
}: TaskListProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const handleClick = (taskId: string) => {
    setSelectedId(taskId);
    onTaskClick?.(taskId);
  };

  // Skeleton loading state
  if (isLoading) {
    return (
      <div
        style={{
          padding: "20px 24px",
          flex: 1,
          overflowY: "auto",
        }}
      >
        <TaskListSkeleton count={6} />
      </div>
    );
  }

  // Empty state - no tasks at all
  if (tasks.length === 0 && !searchQuery) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <EmptyState
          variant="tasks"
          onAction={onNewTask}
        />
      </div>
    );
  }

  // Empty state - search with no results
  if (tasks.length === 0 && searchQuery) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <EmptyState
          variant="search"
          title="No matching tasks"
          description={`No tasks found for "${searchQuery}". Try a different search term.`}
        />
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gap: "16px",
        padding: "20px 24px",
        gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
        alignContent: "start",
        overflowY: "auto",
        flex: 1,
      }}
    >
      {tasks.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          onClick={() => handleClick(task.id)}
          onStatusChange={onStatusChange ? (status) => onStatusChange(task.id, status) : undefined}
          onDelete={onDelete ? () => onDelete(task.id) : undefined}
          isSelected={selectedId === task.id}
        />
      ))}
    </div>
  );
}
