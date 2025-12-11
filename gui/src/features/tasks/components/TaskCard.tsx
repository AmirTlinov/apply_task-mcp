import { useState, useEffect } from "react";
import { CheckCircle2, Circle, ChevronRight, Check, AlertTriangle, X, Trash2 } from "lucide-react";
import type { TaskListItem, TaskStatus } from "@/types/task";
import { showTask } from "@/lib/tauri";

// DEBUG: Auto-test showTask on first card mount
let hasTestedShowTask = false;

interface TaskCardProps {
  task: TaskListItem;
  onClick?: () => void;
  onStatusChange?: (status: TaskStatus) => void;
  onDelete?: () => void;
  isSelected?: boolean;
}

const statusConfig: Record<
  string,
  { bg: string; text: string; dot: string; dotClass: string }
> = {
  OK: {
    bg: "var(--color-status-ok-subtle)",
    text: "var(--color-status-ok)",
    dot: "var(--color-status-ok)",
    dotClass: "status-dot status-dot-ok",
  },
  WARN: {
    bg: "var(--color-status-warn-subtle)",
    text: "var(--color-status-warn)",
    dot: "var(--color-status-warn)",
    dotClass: "status-dot status-dot-warn",
  },
  FAIL: {
    bg: "var(--color-status-fail-subtle)",
    text: "var(--color-status-fail)",
    dot: "var(--color-status-fail)",
    dotClass: "status-dot status-dot-fail",
  },
};

function formatRelativeTime(date: string): string {
  const now = new Date();
  const then = new Date(date);
  const diffMs = now.getTime() - then.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return then.toLocaleDateString();
}

interface StatusButtonProps {
  status: TaskStatus;
  isCurrentStatus: boolean;
  onClick: () => void;
}

function StatusButton({ status, isCurrentStatus, onClick }: StatusButtonProps) {
  const configs: Record<TaskStatus, { icon: typeof Check; color: string; bgColor: string; label: string }> = {
    OK: { icon: Check, color: "var(--color-status-ok)", bgColor: "var(--color-status-ok-subtle)", label: "Mark as Done" },
    WARN: { icon: AlertTriangle, color: "var(--color-status-warn)", bgColor: "var(--color-status-warn-subtle)", label: "Mark In Progress" },
    FAIL: { icon: X, color: "var(--color-status-fail)", bgColor: "var(--color-status-fail-subtle)", label: "Mark as Blocked" },
  };

  const config = configs[status];
  const Icon = config.icon;

  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={config.label}
      disabled={isCurrentStatus}
      style={{
        width: "28px",
        height: "28px",
        borderRadius: "6px",
        border: "none",
        backgroundColor: isCurrentStatus ? config.bgColor : "var(--color-background)",
        cursor: isCurrentStatus ? "default" : "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: isCurrentStatus ? 0.5 : 1,
        transition: "all 150ms ease",
      }}
      onMouseEnter={(e) => {
        if (!isCurrentStatus) {
          e.currentTarget.style.backgroundColor = config.bgColor;
          e.currentTarget.style.transform = "scale(1.1)";
        }
      }}
      onMouseLeave={(e) => {
        if (!isCurrentStatus) {
          e.currentTarget.style.backgroundColor = "var(--color-background)";
          e.currentTarget.style.transform = "scale(1)";
        }
      }}
    >
      <Icon style={{ width: "14px", height: "14px", color: config.color }} />
    </button>
  );
}

export function TaskCard({ task, onClick, onStatusChange, onDelete, isSelected = false }: TaskCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const config = statusConfig[task.status] || statusConfig.FAIL;

  // DEBUG: Auto-test showTask on first card mount (only once globally)
  useEffect(() => {
    if (!hasTestedShowTask && task.id) {
      hasTestedShowTask = true;
      console.log("[TaskCard] AUTO-TEST: Calling showTask for", task.id);
      showTask(task.id)
        .then((response) => {
          console.log("[TaskCard] AUTO-TEST showTask SUCCESS:", response);
        })
        .catch((err) => {
          console.error("[TaskCard] AUTO-TEST showTask ERROR:", err);
        });
    }
  }, [task.id]);

  const handleCardClick = () => {
    console.log("[TaskCard] Card clicked, task.id:", task.id);

    // DEBUG: Direct invoke test - bypasses React state chain
    // If this shows in terminal logs, invoke works from click handlers
    showTask(task.id)
      .then((response) => {
        console.log("[TaskCard] DEBUG showTask response:", response);
      })
      .catch((err) => {
        console.error("[TaskCard] DEBUG showTask error:", err);
      });

    onClick?.();
  };
  const progress = task.progress || 0;
  const allCompleted =
    task.completed_count === task.subtask_count && task.subtask_count > 0;

  // Dynamic shadow based on state
  const getShadow = () => {
    if (isSelected) return "0 0 0 3px var(--color-primary-subtle)";
    return "none";
  };

  const getHoverShadow = () => {
    return "var(--shadow-md)";
  };

  const handleStatusChange = (newStatus: TaskStatus) => {
    if (task.status !== newStatus) {
      onStatusChange?.(newStatus);
    }
  };

  return (
    <div
      className="task-card"
      onClick={handleCardClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      tabIndex={0}
      role="button"
      aria-pressed={isSelected}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.();
        }
      }}
      style={{
        padding: "16px",
        border: `1px solid ${isSelected ? "var(--color-primary)" : "var(--color-border)"}`,
        borderRadius: "12px",
        backgroundColor: isSelected
          ? "var(--color-primary-subtle)"
          : "var(--color-background)",
        cursor: "pointer",
        boxShadow: isHovered ? getHoverShadow() : getShadow(),
        borderColor: isHovered && !isSelected ? "var(--color-foreground-subtle)" : undefined,
        position: "relative",
        transition: "all 150ms ease",
      }}
    >
      {/* Quick Actions - appear on hover */}
      {isHovered && (onStatusChange || onDelete) && (
        <div
          style={{
            position: "absolute",
            top: "12px",
            right: "12px",
            display: "flex",
            gap: "4px",
            padding: "4px",
            borderRadius: "8px",
            backgroundColor: "var(--color-background-subtle)",
            border: "1px solid var(--color-border)",
            boxShadow: "var(--shadow-sm)",
            zIndex: 10,
          }}
        >
          {onStatusChange && (
            <>
              <StatusButton
                status="OK"
                isCurrentStatus={task.status === "OK"}
                onClick={() => handleStatusChange("OK")}
              />
              <StatusButton
                status="WARN"
                isCurrentStatus={task.status === "WARN"}
                onClick={() => handleStatusChange("WARN")}
              />
              <StatusButton
                status="FAIL"
                isCurrentStatus={task.status === "FAIL"}
                onClick={() => handleStatusChange("FAIL")}
              />
            </>
          )}
          {onDelete && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm(`Delete task "${task.title}"?`)) {
                  onDelete();
                }
              }}
              title="Delete task"
              style={{
                width: "28px",
                height: "28px",
                borderRadius: "6px",
                border: "none",
                backgroundColor: "var(--color-background)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginLeft: onStatusChange ? "4px" : 0,
                transition: "all 150ms ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = "var(--color-status-fail-subtle)";
                e.currentTarget.style.transform = "scale(1.1)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = "var(--color-background)";
                e.currentTarget.style.transform = "scale(1)";
              }}
            >
              <Trash2 style={{ width: "14px", height: "14px", color: "var(--color-status-fail)" }} />
            </button>
          )}
        </div>
      )}

      {/* Header: ID, Status, Updated */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "10px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {/* Task ID badge */}
          <span
            style={{
              fontSize: "11px",
              fontFamily: "var(--font-mono)",
              color: "var(--color-foreground-muted)",
              backgroundColor: "var(--color-background-muted)",
              padding: "3px 8px",
              borderRadius: "6px",
              fontWeight: 500,
              letterSpacing: "0.02em",
            }}
          >
            {task.id}
          </span>

          {/* Status badge with animated dot */}
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "5px",
              fontSize: "11px",
              fontWeight: 600,
              color: config.text,
              backgroundColor: config.bg,
              padding: "3px 10px",
              borderRadius: "999px",
              textTransform: "uppercase",
              letterSpacing: "0.03em",
            }}
          >
            <span
              className={config.dotClass}
              style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                backgroundColor: config.dot,
              }}
            />
            {task.status}
          </span>
        </div>

        {/* Timestamp */}
        {task.updated_at && !isHovered && (
          <span
            style={{
              fontSize: "11px",
              color: "var(--color-foreground-subtle)",
              fontWeight: 400,
            }}
          >
            {formatRelativeTime(task.updated_at)}
          </span>
        )}
      </div>

      {/* Title */}
      <h3
        style={{
          fontSize: "15px",
          fontWeight: 500,
          color: "var(--color-foreground)",
          marginBottom: "10px",
          lineHeight: 1.45,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          letterSpacing: "-0.01em",
        }}
      >
        {task.title}
      </h3>

      {/* Tags */}
      {task.tags && task.tags.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "6px",
            marginBottom: "14px",
          }}
        >
          {task.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="task-tag"
              style={{
                fontSize: "11px",
                color: "var(--color-foreground-muted)",
                backgroundColor: "var(--color-background-muted)",
                padding: "3px 8px",
                borderRadius: "6px",
                cursor: "default",
              }}
            >
              #{tag}
            </span>
          ))}
          {task.tags.length > 3 && (
            <span
              style={{
                fontSize: "11px",
                color: "var(--color-foreground-subtle)",
                padding: "3px 4px",
              }}
            >
              +{task.tags.length - 3}
            </span>
          )}
        </div>
      )}

      {/* Footer: Progress & Subtasks */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingTop: "4px",
        }}
      >
        <div
          style={{ display: "flex", alignItems: "center", gap: "16px", flex: 1 }}
        >
          {/* Progress bar with shimmer */}
          <div style={{ flex: 1, maxWidth: "140px" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
              }}
            >
              <div
                style={{
                  flex: 1,
                  height: "5px",
                  backgroundColor: "var(--color-background-muted)",
                  borderRadius: "999px",
                  overflow: "hidden",
                  position: "relative",
                }}
              >
                <div
                  className={progress > 0 && progress < 100 ? "progress-bar-animated" : ""}
                  style={{
                    width: `${progress}%`,
                    height: "100%",
                    backgroundColor:
                      progress === 100
                        ? "var(--color-status-ok)"
                        : "var(--color-primary)",
                    borderRadius: "999px",
                    transition: "width 400ms cubic-bezier(0.32, 0.72, 0, 1)",
                  }}
                />
              </div>
              <span
                style={{
                  fontSize: "12px",
                  fontWeight: 600,
                  color:
                    progress === 100
                      ? "var(--color-status-ok)"
                      : "var(--color-foreground-muted)",
                  minWidth: "36px",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {progress}%
              </span>
            </div>
          </div>

          {/* Subtask count */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "5px",
              fontSize: "12px",
              color: allCompleted
                ? "var(--color-status-ok)"
                : "var(--color-foreground-muted)",
              fontWeight: 500,
            }}
          >
            {allCompleted ? (
              <CheckCircle2
                style={{
                  width: "15px",
                  height: "15px",
                  color: "var(--color-status-ok)",
                }}
              />
            ) : (
              <Circle
                style={{
                  width: "15px",
                  height: "15px",
                  opacity: 0.6,
                }}
              />
            )}
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              {task.completed_count}/{task.subtask_count}
            </span>
          </div>
        </div>

        {/* Domain badge */}
        {task.domain && (
          <span
            style={{
              fontSize: "11px",
              color: "var(--color-foreground-subtle)",
              marginLeft: "12px",
              maxWidth: "90px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontWeight: 400,
            }}
          >
            {task.domain}
          </span>
        )}

        {/* Animated arrow */}
        <ChevronRight
          className="task-card-arrow"
          style={{
            width: "18px",
            height: "18px",
            color: "var(--color-foreground-subtle)",
            marginLeft: "8px",
            opacity: 0.4,
            flexShrink: 0,
          }}
        />
      </div>
    </div>
  );
}
