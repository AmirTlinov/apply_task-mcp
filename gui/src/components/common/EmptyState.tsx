/**
 * Empty state component for when there's no data to display
 */

import { Plus, Search, FolderOpen, CheckCircle2, Inbox } from "lucide-react";

type EmptyStateVariant = "tasks" | "search" | "filtered" | "completed" | "default";

interface EmptyStateProps {
  variant?: EmptyStateVariant;
  title?: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

const variantConfig: Record<
  EmptyStateVariant,
  {
    icon: React.ComponentType<{ style?: React.CSSProperties; className?: string }>;
    title: string;
    description: string;
    actionLabel?: string;
  }
> = {
  tasks: {
    icon: Inbox,
    title: "No tasks yet",
    description: "Create your first task to get started tracking your work",
    actionLabel: "Create Task",
  },
  search: {
    icon: Search,
    title: "No results found",
    description: "Try adjusting your search or filter to find what you're looking for",
  },
  filtered: {
    icon: FolderOpen,
    title: "No matching tasks",
    description: "No tasks match your current filters. Try changing your selection.",
  },
  completed: {
    icon: CheckCircle2,
    title: "All done!",
    description: "You've completed all your tasks. Time to celebrate! 🎉",
  },
  default: {
    icon: Inbox,
    title: "Nothing here",
    description: "This section is empty",
  },
};

export function EmptyState({
  variant = "default",
  title,
  description,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  const config = variantConfig[variant];
  const Icon = config.icon;
  const displayTitle = title ?? config.title;
  const displayDescription = description ?? config.description;
  const displayActionLabel = actionLabel ?? config.actionLabel;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
        textAlign: "center",
        minHeight: "320px",
      }}
    >
      {/* Animated icon container */}
      <div
        className="empty-state-icon"
        style={{
          width: "80px",
          height: "80px",
          borderRadius: "20px",
          backgroundColor: "var(--color-background-muted)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: "24px",
        }}
      >
        <Icon
          style={{
            width: "36px",
            height: "36px",
            color: "var(--color-foreground-subtle)",
          }}
        />
      </div>

      {/* Title */}
      <h3
        style={{
          fontSize: "18px",
          fontWeight: 600,
          color: "var(--color-foreground)",
          marginBottom: "8px",
          letterSpacing: "-0.01em",
        }}
      >
        {displayTitle}
      </h3>

      {/* Description */}
      <p
        style={{
          fontSize: "14px",
          color: "var(--color-foreground-muted)",
          maxWidth: "360px",
          lineHeight: 1.5,
          marginBottom: displayActionLabel ? "24px" : "0",
        }}
      >
        {displayDescription}
      </p>

      {/* Action button */}
      {displayActionLabel && onAction && (
        <button
          onClick={onAction}
          className="btn btn-primary"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            padding: "10px 20px",
            backgroundColor: "var(--color-primary)",
            color: "white",
            fontSize: "14px",
            fontWeight: 500,
            borderRadius: "8px",
            border: "none",
            cursor: "pointer",
          }}
        >
          <Plus style={{ width: "16px", height: "16px" }} />
          {displayActionLabel}
        </button>
      )}
    </div>
  );
}

/**
 * Minimal empty state for inline use
 */
export function EmptyStateInline({
  message = "No items",
}: {
  message?: string;
}) {
  return (
    <div
      style={{
        padding: "24px",
        textAlign: "center",
        color: "var(--color-foreground-muted)",
        fontSize: "14px",
      }}
    >
      {message}
    </div>
  );
}
