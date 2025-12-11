/**
 * Projects View - Project management and switching
 */

import {
  FolderOpen,
  Plus,
  CheckCircle2,
  Clock,
  Folder,
  Star,
  RefreshCw,
  ExternalLink,
  Archive,
  Trash2,
} from "lucide-react";
import { DropdownMenu } from "@/components/common/DropdownMenu";
import type { TaskListItem, Namespace } from "@/types/task";

interface ProjectsViewProps {
  tasks: TaskListItem[];
  projectName?: string;
  projectPath?: string;
  namespaces: Namespace[];
  isLoading?: boolean;
  onOpenProject?: () => void;
}

interface Project {
  id: string;
  name: string;
  path: string;
  taskCount: number;
  completedCount: number;
  lastOpened: Date;
  isActive: boolean;
  isFavorite?: boolean;
}

// Build current project from real API data
function getCurrentProject(
  projectName?: string,
  projectPath?: string,
  tasks: TaskListItem[] = []
): Project | null {
  if (!projectName && !projectPath) return null;

  const completed = tasks.filter((t) => t.status === "OK").length;

  return {
    id: "current",
    name: projectName || "Current Project",
    path: projectPath || "",
    taskCount: tasks.length,
    completedCount: completed,
    lastOpened: new Date(),
    isActive: true,
    isFavorite: true,
  };
}

function formatDate(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (24 * 60 * 60 * 1000));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays} days ago`;
  return date.toLocaleDateString();
}

export function ProjectsView({
  tasks,
  projectName,
  projectPath,
  namespaces,
  isLoading = false,
  onOpenProject,
}: ProjectsViewProps) {
  if (isLoading) {
    return <ProjectsSkeleton />;
  }

  const currentProject = getCurrentProject(projectName, projectPath, tasks);

  // Convert namespaces to Project objects
  const allProjects: Project[] = namespaces.map((ns) => ({
    id: ns.namespace,
    name: ns.namespace,
    path: ns.path,
    taskCount: ns.task_count,
    completedCount: 0, // We don't have this info from backend
    lastOpened: new Date(),
    isActive: ns.namespace === projectName,
    isFavorite: ns.namespace === projectName,
  }));

  // Get other (non-active) projects
  const otherProjects = allProjects.filter((p) => !p.isActive);

  return (
    <div
      style={{
        flex: 1,
        overflowY: "auto",
        padding: "24px",
        display: "flex",
        flexDirection: "column",
        gap: "32px",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h2
            style={{
              fontSize: "20px",
              fontWeight: 600,
              color: "var(--color-foreground)",
              marginBottom: "4px",
            }}
          >
            Projects
          </h2>
          <p style={{ fontSize: "14px", color: "var(--color-foreground-muted)" }}>
            Manage and switch between your projects ({allProjects.length} total)
          </p>
        </div>

        <button
          onClick={onOpenProject}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "10px 16px",
            borderRadius: "8px",
            border: "none",
            backgroundColor: "var(--color-primary)",
            color: "white",
            fontSize: "13px",
            fontWeight: 500,
            cursor: "pointer",
            transition: "opacity 150ms ease",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.9")}
          onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
        >
          <Plus style={{ width: "16px", height: "16px" }} />
          Open Project
        </button>
      </div>

      {/* Current Project */}
      {currentProject && (
        <section>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              marginBottom: "16px",
            }}
          >
            <Star
              style={{ width: "14px", height: "14px", color: "var(--color-status-warn)", fill: "var(--color-status-warn)" }}
            />
            <h3 style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-foreground-muted)" }}>
              Current Project
            </h3>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
              gap: "12px",
            }}
          >
            <ProjectCard project={currentProject} />
          </div>
        </section>
      )}

      {/* All Projects */}
      {otherProjects.length > 0 && (
        <section>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              marginBottom: "16px",
            }}
          >
            <Folder style={{ width: "14px", height: "14px", color: "var(--color-foreground-muted)" }} />
            <h3 style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-foreground-muted)" }}>
              All Projects
            </h3>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
              gap: "12px",
            }}
          >
            {otherProjects.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        </section>
      )}

      {/* Empty state for no projects */}
      {allProjects.length === 0 && (
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
          <Folder style={{ width: "48px", height: "48px", opacity: 0.5 }} />
          <div style={{ fontSize: "16px", fontWeight: 500 }}>No projects yet</div>
          <div style={{ fontSize: "14px" }}>Open a folder to get started</div>
          <button
            onClick={onOpenProject}
            style={{
              marginTop: "8px",
              padding: "10px 20px",
              borderRadius: "8px",
              border: "none",
              backgroundColor: "var(--color-primary)",
              color: "white",
              fontSize: "14px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Open Project
          </button>
        </div>
      )}
    </div>
  );
}

interface ProjectCardProps {
  project: Project;
}

function ProjectCard({ project }: ProjectCardProps) {
  const progress = project.taskCount > 0
    ? Math.round((project.completedCount / project.taskCount) * 100)
    : 0;

  return (
    <div
      style={{
        padding: "16px",
        backgroundColor: project.isActive ? "var(--color-primary-subtle)" : "var(--color-background)",
        borderRadius: "12px",
        border: `1px solid ${project.isActive ? "var(--color-primary)" : "var(--color-border)"}`,
        cursor: "pointer",
        transition: "all 150ms ease",
      }}
      onMouseEnter={(e) => {
        if (!project.isActive) {
          e.currentTarget.style.borderColor = "var(--color-foreground-subtle)";
          e.currentTarget.style.transform = "translateY(-2px)";
        }
      }}
      onMouseLeave={(e) => {
        if (!project.isActive) {
          e.currentTarget.style.borderColor = "var(--color-border)";
          e.currentTarget.style.transform = "translateY(0)";
        }
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div
            style={{
              width: "36px",
              height: "36px",
              borderRadius: "8px",
              backgroundColor: project.isActive ? "var(--color-primary)" : "var(--color-background-muted)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <FolderOpen
              style={{
                width: "18px",
                height: "18px",
                color: project.isActive ? "white" : "var(--color-foreground-muted)",
              }}
            />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-foreground)" }}>
                {project.name}
              </span>
              {project.isActive && (
                <span
                  style={{
                    fontSize: "10px",
                    fontWeight: 500,
                    color: "var(--color-primary)",
                    backgroundColor: "var(--color-primary-subtle)",
                    padding: "2px 6px",
                    borderRadius: "999px",
                  }}
                >
                  Active
                </span>
              )}
            </div>
            <div
              style={{
                fontSize: "11px",
                color: "var(--color-foreground-muted)",
                fontFamily: "var(--font-mono)",
                marginTop: "2px",
              }}
            >
              {project.path}
            </div>
          </div>
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
              onClick={(e) => e.stopPropagation()}
            >
              <ExternalLink style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />
            </button>
          }
          items={[
            {
              label: "Refresh",
              icon: <RefreshCw style={{ width: "14px", height: "14px" }} />,
              onClick: () => console.log("Refresh project", project.id),
            },
            {
              label: "Open in terminal",
              icon: <ExternalLink style={{ width: "14px", height: "14px" }} />,
              onClick: () => console.log("Open in terminal", project.path),
            },
            { type: "separator" as const },
            {
              label: "Archive",
              icon: <Archive style={{ width: "14px", height: "14px" }} />,
              onClick: () => console.log("Archive project", project.id),
              disabled: project.isActive,
            },
            {
              label: "Remove",
              icon: <Trash2 style={{ width: "14px", height: "14px" }} />,
              onClick: () => console.log("Remove project", project.id),
              danger: true,
              disabled: project.isActive,
            },
          ]}
        />
      </div>

      {/* Stats */}
      <div style={{ display: "flex", alignItems: "center", gap: "16px", marginBottom: "12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <CheckCircle2 style={{ width: "12px", height: "12px", color: "var(--color-status-ok)" }} />
          <span style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
            {project.completedCount} / {project.taskCount}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <Clock style={{ width: "12px", height: "12px", color: "var(--color-foreground-subtle)" }} />
          <span style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
            {formatDate(project.lastOpened)}
          </span>
        </div>
      </div>

      {/* Progress */}
      <div
        style={{
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
            backgroundColor: progress === 100 ? "var(--color-status-ok)" : "var(--color-primary)",
            borderRadius: "999px",
            transition: "width 300ms ease",
          }}
        />
      </div>
    </div>
  );
}

function ProjectsSkeleton() {
  return (
    <div style={{ padding: "24px", display: "flex", flexDirection: "column", gap: "32px" }}>
      <div>
        <div className="skeleton" style={{ height: "24px", width: "120px", marginBottom: "8px", borderRadius: "4px" }} />
        <div className="skeleton" style={{ height: "16px", width: "250px", borderRadius: "4px" }} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }}>
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton" style={{ height: "140px", borderRadius: "12px" }} />
        ))}
      </div>
    </div>
  );
}
