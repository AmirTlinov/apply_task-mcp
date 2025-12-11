/**
 * Dashboard View - Project metrics, charts, and health overview
 */

import {
  BarChart3,
  TrendingUp,
  CheckCircle2,
  Clock,
  AlertCircle,
  Target,
  Zap,
  Calendar,
} from "lucide-react";
import type { TaskListItem } from "@/types/task";

interface DashboardViewProps {
  tasks: TaskListItem[];
  projectName?: string;
  isLoading?: boolean;
}

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: typeof BarChart3;
  color: string;
  trend?: { value: number; isUp: boolean };
}

function MetricCard({ title, value, subtitle, icon: Icon, color, trend }: MetricCardProps) {
  return (
    <div
      style={{
        padding: "20px",
        backgroundColor: "var(--color-background)",
        borderRadius: "12px",
        border: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div
          style={{
            width: "40px",
            height: "40px",
            borderRadius: "10px",
            backgroundColor: `${color}15`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Icon style={{ width: "20px", height: "20px", color }} />
        </div>
        {trend && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "4px",
              fontSize: "12px",
              fontWeight: 500,
              color: trend.isUp ? "var(--color-status-ok)" : "var(--color-status-fail)",
            }}
          >
            <TrendingUp
              style={{
                width: "14px",
                height: "14px",
                transform: trend.isUp ? "none" : "rotate(180deg)",
              }}
            />
            {trend.value}%
          </div>
        )}
      </div>

      <div>
        <div
          style={{
            fontSize: "28px",
            fontWeight: 700,
            color: "var(--color-foreground)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </div>
        <div style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>{title}</div>
        {subtitle && (
          <div style={{ fontSize: "11px", color: "var(--color-foreground-subtle)", marginTop: "4px" }}>
            {subtitle}
          </div>
        )}
      </div>
    </div>
  );
}

interface ProgressBarProps {
  label: string;
  value: number;
  total: number;
  color: string;
}

function ProgressBar({ label, value, total, color }: ProgressBarProps) {
  const percentage = total > 0 ? (value / total) * 100 : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>{label}</span>
        <span style={{ fontSize: "12px", fontWeight: 500, color: "var(--color-foreground)" }}>
          {value} / {total}
        </span>
      </div>
      <div
        style={{
          height: "6px",
          backgroundColor: "var(--color-background-muted)",
          borderRadius: "999px",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${percentage}%`,
            height: "100%",
            backgroundColor: color,
            borderRadius: "999px",
            transition: "width 500ms ease",
          }}
        />
      </div>
    </div>
  );
}

export function DashboardView({ tasks, projectName, isLoading = false }: DashboardViewProps) {
  if (isLoading) {
    return <DashboardSkeleton />;
  }

  // Calculate metrics
  const total = tasks.length;
  const completed = tasks.filter((t) => t.status === "OK").length;
  const inProgress = tasks.filter((t) => t.status === "WARN").length;
  const blocked = tasks.filter((t) => t.status === "FAIL").length;

  const overallProgress = total > 0 ? Math.round((completed / total) * 100) : 0;
  const _avgProgress = total > 0
    ? Math.round(tasks.reduce((sum, t) => sum + (t.progress || 0), 0) / total)
    : 0;
  void _avgProgress; // Reserved for future use

  // Group by tags
  const tagCounts = new Map<string, number>();
  tasks.forEach((task) => {
    task.tags?.forEach((tag) => {
      tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
    });
  });
  const topTags = Array.from(tagCounts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div
      style={{
        flex: 1,
        overflowY: "auto",
        padding: "24px",
        display: "flex",
        flexDirection: "column",
        gap: "24px",
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: "8px" }}>
        <h2
          style={{
            fontSize: "20px",
            fontWeight: 600,
            color: "var(--color-foreground)",
            marginBottom: "4px",
          }}
        >
          {projectName || "Project"} Overview
        </h2>
        <p style={{ fontSize: "14px", color: "var(--color-foreground-muted)" }}>
          Track your project progress and performance metrics
        </p>
      </div>

      {/* Metrics Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: "16px",
        }}
      >
        <MetricCard
          title="Total Tasks"
          value={total}
          subtitle="All project tasks"
          icon={Target}
          color="var(--color-primary)"
        />
        <MetricCard
          title="Completed"
          value={completed}
          subtitle={`${overallProgress}% of total`}
          icon={CheckCircle2}
          color="var(--color-status-ok)"
        />
        <MetricCard
          title="In Progress"
          value={inProgress}
          subtitle="Currently active"
          icon={Clock}
          color="var(--color-status-warn)"
        />
        <MetricCard
          title="Blocked"
          value={blocked}
          subtitle="Need attention"
          icon={AlertCircle}
          color="var(--color-status-fail)"
        />
      </div>

      {/* Charts Section */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: "20px",
        }}
      >
        {/* Progress Overview */}
        <div
          style={{
            padding: "20px",
            backgroundColor: "var(--color-background)",
            borderRadius: "12px",
            border: "1px solid var(--color-border)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "20px" }}>
            <BarChart3 style={{ width: "16px", height: "16px", color: "var(--color-primary)" }} />
            <h3 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-foreground)" }}>
              Progress Overview
            </h3>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <ProgressBar
              label="Completed"
              value={completed}
              total={total}
              color="var(--color-status-ok)"
            />
            <ProgressBar
              label="In Progress"
              value={inProgress}
              total={total}
              color="var(--color-status-warn)"
            />
            <ProgressBar
              label="Blocked"
              value={blocked}
              total={total}
              color="var(--color-status-fail)"
            />
          </div>

          {/* Overall progress ring */}
          <div
            style={{
              marginTop: "24px",
              display: "flex",
              alignItems: "center",
              gap: "16px",
              padding: "16px",
              backgroundColor: "var(--color-background-subtle)",
              borderRadius: "10px",
            }}
          >
            <div
              style={{
                width: "60px",
                height: "60px",
                borderRadius: "50%",
                background: `conic-gradient(var(--color-primary) ${overallProgress * 3.6}deg, var(--color-background-muted) 0deg)`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  width: "48px",
                  height: "48px",
                  borderRadius: "50%",
                  backgroundColor: "var(--color-background-subtle)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "14px",
                  fontWeight: 600,
                  color: "var(--color-foreground)",
                }}
              >
                {overallProgress}%
              </div>
            </div>
            <div>
              <div style={{ fontSize: "14px", fontWeight: 500, color: "var(--color-foreground)" }}>
                Overall Progress
              </div>
              <div style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
                {completed} of {total} tasks completed
              </div>
            </div>
          </div>
        </div>

        {/* Tags Distribution */}
        <div
          style={{
            padding: "20px",
            backgroundColor: "var(--color-background)",
            borderRadius: "12px",
            border: "1px solid var(--color-border)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "20px" }}>
            <Zap style={{ width: "16px", height: "16px", color: "var(--color-status-warn)" }} />
            <h3 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-foreground)" }}>
              Top Tags
            </h3>
          </div>

          {topTags.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {topTags.map(([tag, count]) => (
                <div
                  key={tag}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "10px 12px",
                    backgroundColor: "var(--color-background-subtle)",
                    borderRadius: "8px",
                  }}
                >
                  <span
                    style={{
                      fontSize: "13px",
                      color: "var(--color-foreground-muted)",
                    }}
                  >
                    #{tag}
                  </span>
                  <span
                    style={{
                      fontSize: "13px",
                      fontWeight: 500,
                      color: "var(--color-foreground)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {count} tasks
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div
              style={{
                padding: "32px",
                textAlign: "center",
                color: "var(--color-foreground-muted)",
                fontSize: "13px",
              }}
            >
              No tags used yet
            </div>
          )}
        </div>
      </div>

      {/* Weekly Activity - Based on real task updated_at dates */}
      <WeeklyActivityChart tasks={tasks} />
    </div>
  );
}

// Calculate real weekly activity from task updated_at dates
function getWeeklyActivity(tasks: TaskListItem[]): Map<number, number> {
  const activity = new Map<number, number>();
  const now = new Date();
  const startOfWeek = new Date(now);
  startOfWeek.setDate(now.getDate() - now.getDay() + 1); // Monday
  startOfWeek.setHours(0, 0, 0, 0);

  // Initialize all days to 0
  for (let i = 0; i < 7; i++) {
    activity.set(i, 0);
  }

  // Count tasks updated this week
  tasks.forEach((task) => {
    if (!task.updated_at) return;
    const updatedDate = new Date(task.updated_at);
    if (updatedDate >= startOfWeek) {
      const dayOfWeek = (updatedDate.getDay() + 6) % 7; // 0=Mon, 6=Sun
      activity.set(dayOfWeek, (activity.get(dayOfWeek) || 0) + 1);
    }
  });

  return activity;
}

interface WeeklyActivityChartProps {
  tasks: TaskListItem[];
}

function WeeklyActivityChart({ tasks }: WeeklyActivityChartProps) {
  const activity = getWeeklyActivity(tasks);
  const maxActivity = Math.max(...Array.from(activity.values()), 1);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const today = (new Date().getDay() + 6) % 7; // 0=Mon, 6=Sun

  return (
    <div
      style={{
        padding: "20px",
        backgroundColor: "var(--color-background)",
        borderRadius: "12px",
        border: "1px solid var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "16px" }}>
        <Calendar style={{ width: "16px", height: "16px", color: "var(--color-foreground-muted)" }} />
        <h3 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-foreground)" }}>
          This Week
        </h3>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(7, 1fr)",
          gap: "8px",
        }}
      >
        {days.map((day, i) => {
          const count = activity.get(i) || 0;
          const intensity = count > 0 ? 0.2 + (count / maxActivity) * 0.5 : 0;
          const isToday = i === today;

          return (
            <div
              key={day}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <span
                style={{
                  fontSize: "11px",
                  color: isToday ? "var(--color-primary)" : "var(--color-foreground-muted)",
                  fontWeight: isToday ? 600 : 400,
                }}
              >
                {day}
              </span>
              <div
                style={{
                  width: "32px",
                  height: "32px",
                  borderRadius: "8px",
                  backgroundColor:
                    count > 0
                      ? `rgba(59, 130, 246, ${intensity})`
                      : "var(--color-background-muted)",
                  border: isToday ? "2px solid var(--color-primary)" : "none",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "11px",
                  fontWeight: 500,
                  color: count > 0 ? "var(--color-primary)" : "var(--color-foreground-subtle)",
                }}
              >
                {count}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div style={{ padding: "24px", display: "flex", flexDirection: "column", gap: "24px" }}>
      <div>
        <div className="skeleton" style={{ height: "24px", width: "200px", marginBottom: "8px", borderRadius: "4px" }} />
        <div className="skeleton" style={{ height: "16px", width: "300px", borderRadius: "4px" }} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton" style={{ height: "120px", borderRadius: "12px" }} />
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "20px" }}>
        <div className="skeleton" style={{ height: "280px", borderRadius: "12px" }} />
        <div className="skeleton" style={{ height: "280px", borderRadius: "12px" }} />
      </div>
    </div>
  );
}
