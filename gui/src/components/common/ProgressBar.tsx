import { cn } from "@/lib/utils";

interface ProgressBarProps {
  value: number;
  max?: number;
  size?: "sm" | "md";
  showLabel?: boolean;
  className?: string;
}

export function ProgressBar({
  value,
  max = 100,
  size = "md",
  showLabel = false,
  className,
}: ProgressBarProps) {
  const percentage = Math.min(Math.max((value / max) * 100, 0), 100);

  // Color based on progress
  const getProgressColor = (pct: number) => {
    if (pct >= 100) return "bg-[var(--color-status-ok)]";
    if (pct >= 50) return "bg-[var(--color-status-warn)]";
    return "bg-[var(--color-foreground-subtle)]";
  };

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        className={cn(
          "flex-1 rounded-full bg-[var(--color-background-muted)] overflow-hidden",
          size === "sm" ? "h-1" : "h-1.5"
        )}
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-[var(--duration-normal)] ease-[var(--ease-out)]",
            getProgressColor(percentage)
          )}
          style={{ width: `${percentage}%` }}
        />
      </div>
      {showLabel && (
        <span className="text-xs text-[var(--color-foreground-muted)] font-medium tabular-nums">
          {Math.round(percentage)}%
        </span>
      )}
    </div>
  );
}
