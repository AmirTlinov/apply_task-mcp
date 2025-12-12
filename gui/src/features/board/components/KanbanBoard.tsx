/**
 * Kanban Board view - tasks organized by status columns with drag-drop support
 */

import { useState, useCallback } from "react";
import { Plus, MoreHorizontal, GripVertical, SortAsc, SortDesc, Filter, Archive } from "lucide-react";
import type { TaskListItem, TaskStatus } from "@/types/task";
import { EmptyState } from "@/components/common/EmptyState";
import { DropdownMenu } from "@/components/common/DropdownMenu";
import { toast } from "@/components/common/Toast";

interface KanbanBoardProps {
  tasks: TaskListItem[];
  onTaskClick?: (taskId: string) => void;
  onNewTask?: () => void;
  onStatusChange?: (taskId: string, newStatus: TaskStatus) => void;
  isLoading?: boolean;
}

type StatusColumn = "TODO" | "ACTIVE" | "DONE";

interface ColumnConfig {
  id: StatusColumn;
  title: string;
  statusFilter: TaskStatus[];
  targetStatus: TaskStatus;
  color: string;
  bgColor: string;
}

const columns: ColumnConfig[] = [
  {
    id: "TODO",
    title: "TODO",
    statusFilter: ["TODO"],
    targetStatus: "TODO",
    color: "var(--color-foreground-subtle)",
    bgColor: "var(--color-background-muted)",
  },
  {
    id: "ACTIVE",
    title: "ACTIVE",
    statusFilter: ["ACTIVE"],
    targetStatus: "ACTIVE",
    color: "var(--color-primary)",
    bgColor: "var(--color-primary-subtle)",
  },
  {
    id: "DONE",
    title: "DONE",
    statusFilter: ["DONE"],
    targetStatus: "DONE",
    color: "var(--color-status-ok)",
    bgColor: "var(--color-status-ok-subtle)",
  },
];

export function KanbanBoard({
  tasks,
  onTaskClick,
  onNewTask,
  onStatusChange,
  isLoading = false,
}: KanbanBoardProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draggedTaskId, setDraggedTaskId] = useState<string | null>(null);
  const [dragOverColumn, setDragOverColumn] = useState<StatusColumn | null>(null);
	  const [sortDirection, setSortDirection] = useState<"asc" | "desc" | null>(null);
	  const [columnFilters, setColumnFilters] = useState<Record<StatusColumn, string>>({
	    TODO: "",
	    ACTIVE: "",
	    DONE: "",
	  });
  const [editingFilterColumn, setEditingFilterColumn] = useState<StatusColumn | null>(null);
  const [hideDoneTasks, setHideDoneTasks] = useState(false);

  const handleClick = (taskId: string) => {
    setSelectedId(taskId);
    onTaskClick?.(taskId);
  };

  const handleDragStart = useCallback((taskId: string) => {
    setDraggedTaskId(taskId);
  }, []);

  const handleDragEnd = useCallback(() => {
    setDraggedTaskId(null);
    setDragOverColumn(null);
  }, []);

  const handleDragOver = useCallback((columnId: StatusColumn) => {
    setDragOverColumn(columnId);
  }, []);

  const handleDrop = useCallback((targetStatus: TaskStatus) => {
    if (draggedTaskId && onStatusChange) {
      const task = tasks.find(t => t.id === draggedTaskId);
      if (task && task.status !== targetStatus) {
        onStatusChange(draggedTaskId, targetStatus);
      }
    }
    setDraggedTaskId(null);
    setDragOverColumn(null);
  }, [draggedTaskId, tasks, onStatusChange]);

  const handleSort = (dir: "asc" | "desc") => {
    setSortDirection(dir);
    toast.info(dir === "asc" ? "Sorted A–Z" : "Sorted Z–A");
  };

  const toggleFilterForColumn = (columnId: StatusColumn) => {
    setEditingFilterColumn((prev) => (prev === columnId ? null : columnId));
  };

  const setFilterForColumn = (columnId: StatusColumn, value: string) => {
    setColumnFilters((prev) => ({ ...prev, [columnId]: value }));
  };

  const toggleHideDone = () => {
    setHideDoneTasks((prev) => {
      const next = !prev;
      toast.info(next ? "Done tasks hidden from board" : "Done tasks visible");
      return next;
    });
  };

  if (isLoading) {
    return (
      <div
        style={{
          display: "flex",
          gap: "20px",
          padding: "20px 24px",
          flex: 1,
          overflowX: "auto",
        }}
      >
        {columns.map((col) => (
          <KanbanColumnSkeleton key={col.id} title={col.title} />
        ))}
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <EmptyState variant="tasks" onAction={onNewTask} />
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        gap: "20px",
        padding: "20px 24px",
        flex: 1,
        overflowX: "auto",
        alignItems: "flex-start",
      }}
    >
      {columns.map((column) => {
        let columnTasks = tasks.filter((t) => column.statusFilter.includes(t.status));

        const filterValue = columnFilters[column.id];
        if (filterValue) {
          const q = filterValue.toLowerCase();
          columnTasks = columnTasks.filter(
            (t) => t.title.toLowerCase().includes(q) || t.id.toLowerCase().includes(q)
          );
        }

        if (column.id === "DONE" && hideDoneTasks) {
          columnTasks = [];
        }

        if (sortDirection) {
          columnTasks = [...columnTasks].sort((a, b) =>
            sortDirection === "asc"
              ? a.title.localeCompare(b.title)
              : b.title.localeCompare(a.title)
          );
        }

        return (
          <KanbanColumn
            key={column.id}
            config={column}
            tasks={columnTasks}
            selectedId={selectedId}
            draggedTaskId={draggedTaskId}
            isDragOver={dragOverColumn === column.id}
            onTaskClick={handleClick}
	            onNewTask={column.id === "TODO" ? onNewTask : undefined}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onDragOver={() => handleDragOver(column.id)}
            onDrop={() => handleDrop(column.targetStatus)}
            onSort={handleSort}
            onToggleFilter={() => toggleFilterForColumn(column.id)}
            filterValue={columnFilters[column.id]}
            isFilterEditing={editingFilterColumn === column.id}
            onFilterChange={(v) => setFilterForColumn(column.id, v)}
            hideDoneTasks={hideDoneTasks}
            onToggleHideDone={toggleHideDone}
          />
        );
      })}
    </div>
  );
}

interface KanbanColumnProps {
  config: ColumnConfig;
  tasks: TaskListItem[];
  selectedId: string | null;
  draggedTaskId: string | null;
  isDragOver: boolean;
  onTaskClick: (taskId: string) => void;
  onNewTask?: () => void;
  onDragStart: (taskId: string) => void;
  onDragEnd: () => void;
  onDragOver: () => void;
  onDrop: () => void;
  onSort: (dir: "asc" | "desc") => void;
  onToggleFilter: () => void;
  filterValue: string;
  isFilterEditing: boolean;
  onFilterChange: (value: string) => void;
  hideDoneTasks: boolean;
  onToggleHideDone: () => void;
}

function KanbanColumn({
  config,
  tasks,
  selectedId,
  draggedTaskId,
  isDragOver,
  onTaskClick,
  onNewTask,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onSort,
  onToggleFilter,
  filterValue,
  isFilterEditing,
  onFilterChange,
  hideDoneTasks,
  onToggleHideDone,
}: KanbanColumnProps) {
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    onDragOver();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    onDrop();
  };

  return (
    <div
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      style={{
        minWidth: "320px",
        maxWidth: "320px",
        display: "flex",
        flexDirection: "column",
        backgroundColor: isDragOver ? config.bgColor : "var(--color-background-subtle)",
        borderRadius: "12px",
        maxHeight: "calc(100vh - 180px)",
        border: isDragOver ? `2px dashed ${config.color}` : "2px solid transparent",
        transition: "all 150ms ease",
      }}
    >
      {/* Column header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 16px",
          borderBottom: "1px solid var(--color-border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              backgroundColor: config.color,
            }}
          />
          <span
            style={{
              fontSize: "14px",
              fontWeight: 600,
              color: "var(--color-foreground)",
            }}
          >
            {config.title}
          </span>
          <span
            style={{
              fontSize: "12px",
              fontWeight: 500,
              color: "var(--color-foreground-muted)",
              backgroundColor: "var(--color-background-muted)",
              padding: "2px 8px",
              borderRadius: "999px",
            }}
          >
            {tasks.length}
          </span>
        </div>
        <DropdownMenu
          trigger={
            <button
              style={{
                padding: "4px",
                borderRadius: "4px",
                border: "none",
                backgroundColor: "transparent",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <MoreHorizontal
                style={{
                  width: "16px",
                  height: "16px",
                  color: "var(--color-foreground-subtle)",
                }}
              />
            </button>
          }
	          items={[
	            {
	              label: "Sort A-Z",
	              icon: <SortAsc style={{ width: "14px", height: "14px" }} />,
	              onClick: () => onSort("asc"),
	            },
	            {
	              label: "Sort Z-A",
	              icon: <SortDesc style={{ width: "14px", height: "14px" }} />,
	              onClick: () => onSort("desc"),
	            },
	            { type: "separator" as const },
	            {
	              label: filterValue ? "Edit filter" : "Filter tasks",
	              icon: <Filter style={{ width: "14px", height: "14px" }} />,
	              onClick: onToggleFilter,
	            },
	            {
	              label: hideDoneTasks ? "Show done tasks" : "Hide done tasks",
	              icon: <Archive style={{ width: "14px", height: "14px" }} />,
	              onClick: onToggleHideDone,
	              disabled: config.id !== "DONE",
	            },
	          ]}
		        />
	      </div>

	      {isFilterEditing && (
	        <div
	          style={{
	            padding: "8px 12px",
	            borderBottom: "1px solid var(--color-border)",
	            backgroundColor: "var(--color-background)",
	          }}
	        >
	          <input
	            value={filterValue}
	            onChange={(e) => onFilterChange(e.target.value)}
	            placeholder="Filter tasks in this column..."
	            autoFocus
	            style={{
	              width: "100%",
	              fontSize: "12px",
	              padding: "6px 8px",
	              borderRadius: "6px",
	              border: "1px solid var(--color-border)",
	              backgroundColor: "var(--color-background)",
	            }}
	            onKeyDown={(e) => {
	              if (e.key === "Escape") {
	                onToggleFilter();
	              }
	            }}
	          />
	          {filterValue && (
	            <button
	              onClick={() => onFilterChange("")}
	              style={{
	                marginTop: "6px",
	                fontSize: "11px",
	                padding: "4px 6px",
	                borderRadius: "6px",
	                border: "none",
	                backgroundColor: "transparent",
	                color: "var(--color-foreground-muted)",
	                cursor: "pointer",
	              }}
	            >
	              Clear filter
	            </button>
	          )}
	        </div>
	      )}

	      {/* Column content */}
	      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
          minHeight: "100px",
        }}
      >
        {tasks.map((task) => (
          <KanbanCard
            key={task.id}
            task={task}
            isSelected={selectedId === task.id}
            isDragging={draggedTaskId === task.id}
            onClick={() => onTaskClick(task.id)}
            onDragStart={() => onDragStart(task.id)}
            onDragEnd={onDragEnd}
          />
        ))}

        {/* Drop zone indicator when empty */}
        {tasks.length === 0 && isDragOver && (
          <div
            style={{
              flex: 1,
              minHeight: "80px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: config.color,
              fontSize: "13px",
              fontWeight: 500,
            }}
          >
            Drop here
          </div>
        )}

        {/* Add task button */}
        {onNewTask && (
          <button
            onClick={onNewTask}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "10px 12px",
              borderRadius: "8px",
              border: "1px dashed var(--color-border)",
              backgroundColor: "transparent",
              color: "var(--color-foreground-muted)",
              fontSize: "13px",
              cursor: "pointer",
              transition: "all 150ms ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--color-primary)";
              e.currentTarget.style.color = "var(--color-primary)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--color-border)";
              e.currentTarget.style.color = "var(--color-foreground-muted)";
            }}
          >
            <Plus style={{ width: "14px", height: "14px" }} />
            Add task
          </button>
        )}
      </div>
    </div>
  );
}

interface KanbanCardProps {
  task: TaskListItem;
  isSelected: boolean;
  isDragging: boolean;
  onClick: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}

function KanbanCard({ task, isSelected, isDragging, onClick, onDragStart, onDragEnd }: KanbanCardProps) {
  const progress = task.progress || 0;

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", task.id);
    onDragStart();
  };

  return (
    <div
      className="task-card"
      draggable
      onDragStart={handleDragStart}
      onDragEnd={onDragEnd}
      onClick={onClick}
      tabIndex={0}
      role="button"
      style={{
        padding: "12px",
        borderRadius: "12px",
        backgroundColor: isSelected ? "var(--color-primary-subtle)" : "var(--color-background)",
        border: "none",
        cursor: isDragging ? "grabbing" : "grab",
        boxShadow: isSelected
          ? "0 0 0 2px var(--color-primary), var(--shadow-md)"
          : isDragging
            ? "var(--shadow-lg)"
            : "var(--shadow-sm)",
        opacity: isDragging ? 0.7 : 1,
        transform: isDragging ? "rotate(2deg) scale(1.02)" : "none",
        transition: "all 180ms cubic-bezier(0.32, 0.72, 0, 1)",
      }}
      onMouseEnter={(e) => {
        if (!isSelected && !isDragging) {
          e.currentTarget.style.boxShadow = "var(--shadow-md)";
          e.currentTarget.style.transform = "translateY(-2px)";
        }
      }}
      onMouseLeave={(e) => {
        if (!isSelected && !isDragging) {
          e.currentTarget.style.boxShadow = "var(--shadow-sm)";
          e.currentTarget.style.transform = "none";
        }
      }}
    >
      {/* Task ID with drag handle */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "8px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <GripVertical
            style={{
              width: "12px",
              height: "12px",
              color: "var(--color-foreground-subtle)",
              opacity: 0.6,
            }}
          />
          <span
            style={{
              fontSize: "11px",
              fontFamily: "var(--font-mono)",
              color: "var(--color-foreground-muted)",
              backgroundColor: "var(--color-background-muted)",
              padding: "2px 6px",
              borderRadius: "4px",
            }}
          >
            {task.id}
          </span>
        </div>
      </div>

      {/* Title */}
      <h4
        style={{
          fontSize: "13px",
          fontWeight: 500,
          color: "var(--color-foreground)",
          lineHeight: 1.4,
          marginBottom: "10px",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {task.title}
      </h4>

      {/* Tags */}
      {task.tags && task.tags.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "4px",
            marginBottom: "10px",
          }}
        >
          {task.tags.slice(0, 2).map((tag) => (
            <span
              key={tag}
              style={{
                fontSize: "10px",
                color: "var(--color-foreground-muted)",
                backgroundColor: "var(--color-background-muted)",
                padding: "2px 6px",
                borderRadius: "4px",
              }}
            >
              #{tag}
            </span>
          ))}
        </div>
      )}

      {/* Progress */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
        }}
      >
        <div
          style={{
            flex: 1,
            height: "4px",
            backgroundColor: "var(--color-background-muted)",
            borderRadius: "999px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${progress}%`,
              height: "100%",
              backgroundColor:
                progress === 100 ? "var(--color-status-ok)" : "var(--color-primary)",
              borderRadius: "999px",
              transition: "width 300ms ease",
            }}
          />
        </div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 500,
            color: "var(--color-foreground-muted)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {progress}%
        </span>
      </div>
    </div>
  );
}

function KanbanColumnSkeleton({ title }: { title: string }) {
  return (
    <div
      style={{
        minWidth: "320px",
        maxWidth: "320px",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "var(--color-background-subtle)",
        borderRadius: "12px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          padding: "14px 16px",
          borderBottom: "1px solid var(--color-border)",
        }}
      >
        <div
          className="skeleton"
          style={{ width: "8px", height: "8px", borderRadius: "50%" }}
        />
        <span
          style={{
            fontSize: "14px",
            fontWeight: 600,
            color: "var(--color-foreground)",
          }}
        >
          {title}
        </span>
      </div>
      <div
        style={{
          padding: "12px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
        }}
      >
        {[1, 2].map((i) => (
          <div
            key={i}
            className="skeleton"
            style={{ height: "100px", borderRadius: "10px" }}
          />
        ))}
      </div>
    </div>
  );
}
