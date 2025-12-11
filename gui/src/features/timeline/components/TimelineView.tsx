/**
 * Timeline View - Activity history and events chronology
 */

import { useState } from "react";
import {
  Clock,
  CheckCircle2,
  AlertCircle,
  Play,
  Plus,
  FileText,
  GitBranch,
  MessageSquare,
  Filter,
} from "lucide-react";
import type { TaskListItem } from "@/types/task";

interface TimelineViewProps {
  tasks: TaskListItem[];
  isLoading?: boolean;
}

type EventType = "created" | "started" | "completed" | "blocked" | "comment" | "subtask";

interface TimelineEvent {
  id: string;
  type: EventType;
  taskId: string;
  taskTitle: string;
  timestamp: Date;
  description?: string;
}

const eventConfig: Record<EventType, { icon: typeof Clock; color: string; label: string }> = {
  created: { icon: Plus, color: "var(--color-primary)", label: "Created" },
  started: { icon: Play, color: "var(--color-status-warn)", label: "Started" },
  completed: { icon: CheckCircle2, color: "var(--color-status-ok)", label: "Completed" },
  blocked: { icon: AlertCircle, color: "var(--color-status-fail)", label: "Blocked" },
  comment: { icon: MessageSquare, color: "var(--color-foreground-muted)", label: "Comment" },
  subtask: { icon: GitBranch, color: "var(--color-primary)", label: "Subtask" },
};

// Generate events from real task data using updated_at timestamps
function generateEventsFromTasks(tasks: TaskListItem[]): TimelineEvent[] {
  const events: TimelineEvent[] = [];

  tasks.forEach((task) => {
    // Use real updated_at timestamp if available, otherwise skip
    const timestamp = task.updated_at ? new Date(task.updated_at) : null;
    if (!timestamp) return;

    // Map current status to event type
    const statusEventMap: Record<string, EventType> = {
      OK: "completed",
      WARN: "started",
      FAIL: "blocked",
    };

    const eventType = statusEventMap[task.status] || "created";

    events.push({
      id: `${task.id}-${eventType}`,
      type: eventType,
      taskId: task.id,
      taskTitle: task.title,
      timestamp,
      description: task.status === "FAIL" && task.progress && task.progress > 0
        ? "Task blocked or pending"
        : undefined,
    });

    // If task has subtasks completed, add subtask events
    if (task.completed_count && task.completed_count > 0 && task.subtask_count && task.subtask_count > 0) {
      events.push({
        id: `${task.id}-subtask-progress`,
        type: "subtask",
        taskId: task.id,
        taskTitle: task.title,
        timestamp: new Date(timestamp.getTime() - 1000), // Slightly before main event
        description: `${task.completed_count} of ${task.subtask_count} subtasks completed`,
      });
    }
  });

  // Sort by timestamp descending (most recent first)
  return events.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
}

function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

function groupEventsByDate(events: TimelineEvent[]): Map<string, TimelineEvent[]> {
  const groups = new Map<string, TimelineEvent[]>();

  events.forEach((event) => {
    const dateKey = event.timestamp.toDateString();
    const existing = groups.get(dateKey) || [];
    groups.set(dateKey, [...existing, event]);
  });

  return groups;
}

export function TimelineView({ tasks, isLoading = false }: TimelineViewProps) {
  const [filter, setFilter] = useState<EventType | "all">("all");

  if (isLoading) {
    return <TimelineSkeleton />;
  }

  const allEvents = generateEventsFromTasks(tasks);
  const filteredEvents = filter === "all" ? allEvents : allEvents.filter((e) => e.type === filter);
  const groupedEvents = groupEventsByDate(filteredEvents);

  if (allEvents.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: "16px",
          color: "var(--color-foreground-muted)",
        }}
      >
        <Clock style={{ width: "48px", height: "48px", opacity: 0.5 }} />
        <div style={{ fontSize: "16px", fontWeight: 500 }}>No activity yet</div>
        <div style={{ fontSize: "14px" }}>Events will appear here as you work on tasks</div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Filter bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "16px 24px",
          borderBottom: "1px solid var(--color-border)",
          backgroundColor: "var(--color-background)",
        }}
      >
        <Filter style={{ width: "14px", height: "14px", color: "var(--color-foreground-muted)" }} />
        <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>Filter:</span>
        {(["all", "created", "started", "completed", "blocked"] as const).map((type) => (
          <button
            key={type}
            onClick={() => setFilter(type)}
            style={{
              padding: "4px 12px",
              borderRadius: "999px",
              border: "none",
              fontSize: "12px",
              fontWeight: 500,
              cursor: "pointer",
              backgroundColor: filter === type ? "var(--color-primary)" : "var(--color-background-muted)",
              color: filter === type ? "white" : "var(--color-foreground-muted)",
              transition: "all 150ms ease",
            }}
          >
            {type === "all" ? "All" : eventConfig[type].label}
          </button>
        ))}
      </div>

      {/* Timeline */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "24px",
        }}
      >
        {Array.from(groupedEvents.entries()).map(([dateKey, events]) => (
          <div key={dateKey} style={{ marginBottom: "32px" }}>
            {/* Date header */}
            <div
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: "var(--color-foreground-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: "16px",
                paddingLeft: "28px",
              }}
            >
              {new Date(dateKey).toLocaleDateString("en-US", {
                weekday: "long",
                month: "short",
                day: "numeric",
              })}
            </div>

            {/* Events */}
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              {events.map((event, idx) => (
                <TimelineEventItem
                  key={event.id}
                  event={event}
                  isLast={idx === events.length - 1}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface TimelineEventItemProps {
  event: TimelineEvent;
  isLast: boolean;
}

function TimelineEventItem({ event, isLast }: TimelineEventItemProps) {
  const config = eventConfig[event.type];
  const Icon = config.icon;

  return (
    <div
      style={{
        display: "flex",
        gap: "12px",
        position: "relative",
      }}
    >
      {/* Timeline line */}
      {!isLast && (
        <div
          style={{
            position: "absolute",
            left: "9px",
            top: "24px",
            bottom: "-2px",
            width: "2px",
            backgroundColor: "var(--color-border)",
          }}
        />
      )}

      {/* Icon */}
      <div
        style={{
          width: "20px",
          height: "20px",
          borderRadius: "50%",
          backgroundColor: config.color,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          zIndex: 1,
        }}
      >
        <Icon style={{ width: "10px", height: "10px", color: "white" }} />
      </div>

      {/* Content */}
      <div
        style={{
          flex: 1,
          padding: "4px 0 16px",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px", marginBottom: "4px" }}>
          <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--color-foreground)" }}>
            {config.label}
          </span>
          <span style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
            {formatRelativeTime(event.timestamp)}
          </span>
        </div>

        <div
          style={{
            fontSize: "13px",
            color: "var(--color-foreground-muted)",
            display: "flex",
            alignItems: "center",
            gap: "6px",
          }}
        >
          <FileText style={{ width: "12px", height: "12px" }} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              backgroundColor: "var(--color-background-muted)",
              padding: "1px 6px",
              borderRadius: "4px",
            }}
          >
            {event.taskId}
          </span>
          <span>{event.taskTitle}</span>
        </div>

        {event.description && (
          <div
            style={{
              fontSize: "12px",
              color: "var(--color-foreground-subtle)",
              marginTop: "4px",
              fontStyle: "italic",
            }}
          >
            {event.description}
          </div>
        )}
      </div>
    </div>
  );
}

function TimelineSkeleton() {
  return (
    <div style={{ padding: "24px", display: "flex", flexDirection: "column", gap: "24px" }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} style={{ display: "flex", gap: "12px" }}>
          <div className="skeleton" style={{ width: "20px", height: "20px", borderRadius: "50%" }} />
          <div style={{ flex: 1 }}>
            <div className="skeleton" style={{ height: "14px", width: "120px", marginBottom: "8px", borderRadius: "4px" }} />
            <div className="skeleton" style={{ height: "12px", width: "80%", borderRadius: "4px" }} />
          </div>
        </div>
      ))}
    </div>
  );
}
