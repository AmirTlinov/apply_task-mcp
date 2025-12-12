import { cn } from "@/lib/utils";
import type { TaskStatus } from "@/types/task";
import { TASK_STATUS_UI } from "@/lib/taskStatus";

interface StatusBadgeProps {
  status: TaskStatus;
  size?: "sm" | "md";
  showLabel?: boolean;
}

export function StatusBadge({
  status,
  size = "md",
  showLabel = true,
}: StatusBadgeProps) {
  const config = TASK_STATUS_UI[status];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full font-medium",
        config.classes.bg,
        config.classes.text,
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs"
      )}
    >
      <span
        className={cn(
          "rounded-full",
          config.classes.dot,
          size === "sm" ? "w-1.5 h-1.5" : "w-2 h-2"
        )}
      />
      {showLabel && <span>{config.label}</span>}
    </span>
  );
}
