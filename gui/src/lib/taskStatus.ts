import type { TaskStatus } from "@/types/task";

export const TASK_STATUS_UI: Record<
  TaskStatus,
  {
    label: TaskStatus;
    colors: { text: string; bg: string; dot: string };
    classes: { bg: string; text: string; dot: string };
  }
> = {
  DONE: {
    label: "DONE",
    colors: {
      text: "var(--color-status-ok)",
      bg: "var(--color-status-ok-subtle)",
      dot: "var(--color-status-ok)",
    },
    classes: {
      bg: "bg-[var(--color-status-ok-subtle)]",
      text: "text-[var(--color-status-ok)]",
      dot: "bg-[var(--color-status-ok)]",
    },
  },
  ACTIVE: {
    label: "ACTIVE",
    colors: {
      text: "var(--color-primary)",
      bg: "var(--color-primary-subtle)",
      dot: "var(--color-primary)",
    },
    classes: {
      bg: "bg-[var(--color-primary-subtle)]",
      text: "text-[var(--color-primary)]",
      dot: "bg-[var(--color-primary)]",
    },
  },
  TODO: {
    label: "TODO",
    colors: {
      text: "var(--color-foreground-muted)",
      bg: "var(--color-background-muted)",
      dot: "var(--color-foreground-subtle)",
    },
    classes: {
      bg: "bg-[var(--color-background-muted)]",
      text: "text-[var(--color-foreground-muted)]",
      dot: "bg-[var(--color-foreground-subtle)]",
    },
  },
};

export function getTaskStatusLabel(status: TaskStatus): TaskStatus {
  return TASK_STATUS_UI[status].label;
}
