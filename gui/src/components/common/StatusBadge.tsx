import { cn } from "@/lib/utils";
import type { TaskStatus } from "@/types/task";

interface StatusBadgeProps {
  status: TaskStatus;
  size?: "sm" | "md";
  showLabel?: boolean;
}

const statusConfig: Record<
  TaskStatus,
  { label: string; bgColor: string; textColor: string; dotColor: string }
> = {
  OK: {
    label: "Done",
    bgColor: "bg-[var(--color-status-ok-subtle)]",
    textColor: "text-[var(--color-status-ok)]",
    dotColor: "bg-[var(--color-status-ok)]",
  },
  WARN: {
    label: "In Progress",
    bgColor: "bg-[var(--color-status-warn-subtle)]",
    textColor: "text-[var(--color-status-warn)]",
    dotColor: "bg-[var(--color-status-warn)]",
  },
  FAIL: {
    label: "Backlog",
    bgColor: "bg-[var(--color-status-fail-subtle)]",
    textColor: "text-[var(--color-status-fail)]",
    dotColor: "bg-[var(--color-status-fail)]",
  },
};

export function StatusBadge({
  status,
  size = "md",
  showLabel = true,
}: StatusBadgeProps) {
  const config = statusConfig[status];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full font-medium",
        config.bgColor,
        config.textColor,
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs"
      )}
    >
      <span
        className={cn(
          "rounded-full",
          config.dotColor,
          size === "sm" ? "w-1.5 h-1.5" : "w-2 h-2"
        )}
      />
      {showLabel && <span>{config.label}</span>}
    </span>
  );
}
