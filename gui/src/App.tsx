import { useState, useCallback } from "react";
import { MainLayout, Header } from "@/components/layout/MainLayout";
import { TaskList } from "@/features/tasks/components/TaskList";
import { KanbanBoard } from "@/features/board/components/KanbanBoard";
import { TimelineView } from "@/features/timeline/components/TimelineView";
import { DashboardView } from "@/features/dashboard/components/DashboardView";
import { ProjectsView } from "@/features/projects/components/ProjectsView";
import { SettingsView } from "@/features/settings/components/SettingsView";
import { TaskDetailModal } from "@/features/tasks/components/TaskDetailModal";
import { NewTaskModal } from "@/features/tasks/components/NewTaskModal";
import { useTasks } from "@/features/tasks/hooks/useTasks";
import { openProject } from "@/lib/tauri";

type ViewType = "board" | "list" | "timeline" | "dashboard" | "projects" | "settings";

function App() {
  const [currentView, setCurrentView] = useState<ViewType>("list");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [showNewTaskModal, setShowNewTaskModal] = useState(false);

  const { tasks, isLoading, error, projectName, projectPath, namespaces, refresh, updateTaskStatus, deleteTask } = useTasks();

  const handleViewChange = useCallback((view: string) => {
    setCurrentView(view as ViewType);
  }, []);

  const handleSearch = useCallback((query: string) => {
    setSearchQuery(query);
  }, []);

  const handleNewTask = useCallback(() => {
    setShowNewTaskModal(true);
  }, []);

  const handleTaskClick = useCallback((taskId: string) => {
    setSelectedTaskId(taskId);
  }, []);

  const handleOpenProject = useCallback(async () => {
    const result = await openProject();
    if (result.success && result.path) {
      // In a full implementation, this would switch to the new project
      // For now, just refresh to show the change
      console.log("Opened project:", result.path);
      refresh();
    }
  }, [refresh]);

  // Filter tasks by search query
  const filteredTasks = searchQuery
    ? tasks.filter(
      (t) =>
        t.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.id.toLowerCase().includes(searchQuery.toLowerCase())
    )
    : tasks;

  // Get view title
  const getViewTitle = () => {
    switch (currentView) {
      case "board":
        return "Board";
      case "list":
        return "Tasks";
      case "timeline":
        return "Timeline";
      case "dashboard":
        return "Dashboard";
      case "projects":
        return "Projects";
      case "settings":
        return "Settings";
      default:
        return "Tasks";
    }
  };

  return (
    <>
      <MainLayout
        currentView={currentView}
        onViewChange={handleViewChange}
        projectName={projectName ?? undefined}
      >
        <Header
          title={getViewTitle()}
          taskCount={filteredTasks.length}
          onSearch={handleSearch}
          onNewTask={handleNewTask}
          onRefresh={refresh}
          isLoading={isLoading}
        />

        <div
          style={{
            flex: 1,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {error ? (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: "32px",
              }}
            >
              <div style={{ textAlign: "center" }}>
                <div
                  style={{
                    color: "var(--color-status-fail)",
                    marginBottom: "8px",
                    fontWeight: 500,
                  }}
                >
                  Error
                </div>
                <p
                  style={{
                    fontSize: "14px",
                    color: "var(--color-foreground-muted)",
                  }}
                >
                  {error}
                </p>
                <button
                  onClick={refresh}
                  style={{
                    marginTop: "16px",
                    padding: "8px 16px",
                    borderRadius: "8px",
                    backgroundColor: "var(--color-primary)",
                    color: "white",
                    fontSize: "14px",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  Retry
                </button>
              </div>
            </div>
          ) : currentView === "list" ? (
            <TaskList
              tasks={filteredTasks}
              onTaskClick={handleTaskClick}
              onNewTask={handleNewTask}
              onStatusChange={updateTaskStatus}
              onDelete={deleteTask}
              isLoading={isLoading}
              searchQuery={searchQuery || undefined}
            />
          ) : currentView === "board" ? (
            <KanbanBoard
              tasks={filteredTasks}
              onTaskClick={handleTaskClick}
              onNewTask={handleNewTask}
              onStatusChange={updateTaskStatus}
              isLoading={isLoading}
            />
          ) : currentView === "timeline" ? (
            <TimelineView tasks={filteredTasks} isLoading={isLoading} />
          ) : currentView === "dashboard" ? (
            <DashboardView
              tasks={filteredTasks}
              projectName={projectName ?? undefined}
              isLoading={isLoading}
            />
          ) : currentView === "projects" ? (
            <ProjectsView
              tasks={tasks}
              projectName={projectName ?? undefined}
              projectPath={projectPath ?? undefined}
              namespaces={namespaces}
              isLoading={isLoading}
              onOpenProject={handleOpenProject}
            />
          ) : currentView === "settings" ? (
            <SettingsView isLoading={isLoading} />
          ) : (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--color-foreground-muted)",
                fontSize: "14px",
              }}
            >
              {getViewTitle()} view coming soon...
            </div>
          )}
        </div>
      </MainLayout>

      {/* Task Detail Modal */}
      <TaskDetailModal
        taskId={selectedTaskId ? (tasks.find((t) => t.id === selectedTaskId)?.task_id || selectedTaskId) : null}
        domain={selectedTaskId ? tasks.find((t) => t.id === selectedTaskId)?.domain : undefined}
        onClose={() => setSelectedTaskId(null)}
        onDelete={(taskId) => {
          deleteTask(taskId);
          setSelectedTaskId(null);
        }}
      />

      {/* New Task Modal */}
      <NewTaskModal
        isOpen={showNewTaskModal}
        onClose={() => setShowNewTaskModal(false)}
        onTaskCreated={refresh}
      />
    </>
  );
}

export default App;
