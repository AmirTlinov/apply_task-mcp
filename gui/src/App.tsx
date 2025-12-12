import { useState, useCallback, useEffect, useRef } from "react";
import { MainLayout, Header } from "@/components/layout/MainLayout";
import { TaskList } from "@/features/tasks/components/TaskList";
import { KanbanBoard } from "@/features/board/components/KanbanBoard";
import { TimelineView } from "@/features/timeline/components/TimelineView";
import { DashboardView } from "@/features/dashboard/components/DashboardView";
import { ProjectsView } from "@/features/projects/components/ProjectsView";
import { SettingsView } from "@/features/settings/components/SettingsView";
import { TaskDetailModal } from "@/features/tasks/components/TaskDetailModal";
import { NewTaskModal } from "@/features/tasks/components/NewTaskModal";
import { CommandPalette, type CommandPaletteCommand } from "@/components/common/CommandPalette";
import { ToastContainer, toast } from "@/components/common/Toast";
import { useTasks } from "@/features/tasks/hooks/useTasks";
import { openProject } from "@/lib/tauri";
import { useAIStatus } from "@/hooks/useAIStatus";
import {
  LayoutList,
  LayoutGrid,
  Clock,
  BarChart3,
  FolderOpen,
  Settings as SettingsIcon,
  Plus,
  RefreshCw,
} from "lucide-react";

type ViewType = "board" | "list" | "timeline" | "dashboard" | "projects" | "settings";

function App() {
  const [currentView, setCurrentView] = useState<ViewType>("list");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [showNewTaskModal, setShowNewTaskModal] = useState(false);
  const [selectedNamespace, setSelectedNamespace] = useState<string | null>(null);
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);

  const { tasks, isLoading, error, projectName, projectPath, namespaces, refresh, updateTaskStatus, deleteTask } = useTasks({
    namespace: selectedNamespace,
    allNamespaces: selectedNamespace === null,
  });
  const { data: aiStatus } = useAIStatus();

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

  const handleOpenCommandPalette = useCallback(() => {
    setIsPaletteOpen(true);
  }, []);

  const handleOpenProject = useCallback(async () => {
    const result = await openProject();
    if (result.success && result.path) {
      toast.success(`Opened project: ${result.path}`);
      refresh();
    } else if (result.error) {
      toast.error(result.error);
    }
  }, [refresh]);

  // Global keyboard shortcuts
  const awaitingGoRef = useRef(false);
  const goTimeoutRef = useRef<number | null>(null);
  const showNewTaskModalRef = useRef(showNewTaskModal);
  const selectedTaskIdRef = useRef(selectedTaskId);
  const paletteOpenRef = useRef(isPaletteOpen);
  const currentViewRef = useRef(currentView);
  const focusedTaskIdRef = useRef(focusedTaskId);
  const visibleTaskIdsRef = useRef<string[]>([]);

  useEffect(() => {
    showNewTaskModalRef.current = showNewTaskModal;
  }, [showNewTaskModal]);

  useEffect(() => {
    selectedTaskIdRef.current = selectedTaskId;
  }, [selectedTaskId]);

  useEffect(() => {
    paletteOpenRef.current = isPaletteOpen;
  }, [isPaletteOpen]);

  useEffect(() => {
    currentViewRef.current = currentView;
  }, [currentView]);

  useEffect(() => {
    focusedTaskIdRef.current = focusedTaskId;
  }, [focusedTaskId]);

  useEffect(() => {
    const isEditableTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (isEditableTarget(e.target)) return;
      if (paletteOpenRef.current) return;

      const key = e.key.toLowerCase();

      if (key === "escape") {
        if (showNewTaskModalRef.current) {
          e.preventDefault();
          setShowNewTaskModal(false);
          return;
        }
        if (selectedTaskIdRef.current) {
          e.preventDefault();
          setSelectedTaskId(null);
          return;
        }
        return;
      }

      if ((e.metaKey || e.ctrlKey) && key === "n") {
        e.preventDefault();
        setShowNewTaskModal(true);
        return;
      }

      if (awaitingGoRef.current) {
        awaitingGoRef.current = false;
        if (goTimeoutRef.current !== null) {
          window.clearTimeout(goTimeoutRef.current);
          goTimeoutRef.current = null;
        }
        switch (key) {
          case "b":
            setCurrentView("board");
            break;
          case "l":
            setCurrentView("list");
            break;
          case "t":
            setCurrentView("timeline");
            break;
          case "d":
            setCurrentView("dashboard");
            break;
          default:
            break;
        }
        return;
      }

      if (
        currentViewRef.current === "list" &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        (key === "j" || key === "k" || key === "arrowdown" || key === "arrowup" || key === "enter")
      ) {
        const ids = visibleTaskIdsRef.current;
        if (ids.length === 0) return;

        if (key === "enter") {
          const targetId = focusedTaskIdRef.current ?? ids[0];
          setSelectedTaskId(targetId);
          return;
        }

        const delta = key === "j" || key === "arrowdown" ? 1 : -1;
        const currentId = focusedTaskIdRef.current;
        const currentIndex = currentId ? ids.indexOf(currentId) : -1;
        const nextIndex =
          currentIndex < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, currentIndex + delta));
        setFocusedTaskId(ids[nextIndex]);
        return;
      }

      if (!e.metaKey && !e.ctrlKey && !e.altKey && key === "g") {
        awaitingGoRef.current = true;
        goTimeoutRef.current = window.setTimeout(() => {
          awaitingGoRef.current = false;
          goTimeoutRef.current = null;
        }, 1200);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (goTimeoutRef.current !== null) {
        window.clearTimeout(goTimeoutRef.current);
        goTimeoutRef.current = null;
      }
    };
  }, []);

  // Filter tasks by namespace and search query
  const filteredTasks = tasks
    .filter((t) => {
      // Filter by namespace if selected
      if (selectedNamespace && t.namespace && t.namespace !== selectedNamespace) {
        return false;
      }
      return true;
    })
    .filter((t) => {
      // Filter by search query
      if (!searchQuery) return true;
      return (
        t.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.id.toLowerCase().includes(searchQuery.toLowerCase())
      );
    });

  useEffect(() => {
    visibleTaskIdsRef.current = filteredTasks.map((t) => t.id);
    if (currentView !== "list") return;
    if (filteredTasks.length === 0) {
      setFocusedTaskId(null);
      return;
    }
    if (focusedTaskId && filteredTasks.some((t) => t.id === focusedTaskId)) return;
    setFocusedTaskId(filteredTasks[0].id);
  }, [currentView, filteredTasks, focusedTaskId]);

  const paletteCommands = useCallback<() => CommandPaletteCommand[]>(
    () => [
      {
        id: "new-task",
        label: "Create new task",
        description: "Create a task in the current project",
        icon: <Plus style={{ width: "14px", height: "14px", color: "var(--color-primary)" }} />,
        shortcut: "⌘ N",
        keywords: ["create", "new", "task"],
        onSelect: () => setShowNewTaskModal(true),
      },
      {
        id: "go-list",
        label: "Go to Tasks",
        description: "Task list view",
        icon: <LayoutList style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        shortcut: "g l",
        keywords: ["tasks", "list"],
        onSelect: () => setCurrentView("list"),
      },
      {
        id: "go-board",
        label: "Go to Board",
        description: "Kanban view",
        icon: <LayoutGrid style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        shortcut: "g b",
        keywords: ["board", "kanban"],
        onSelect: () => setCurrentView("board"),
      },
      {
        id: "go-timeline",
        label: "Go to Timeline",
        description: "Activity feed",
        icon: <Clock style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        shortcut: "g t",
        keywords: ["timeline", "activity"],
        onSelect: () => setCurrentView("timeline"),
      },
      {
        id: "go-dashboard",
        label: "Go to Dashboard",
        description: "Summary & analytics",
        icon: <BarChart3 style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        shortcut: "g d",
        keywords: ["dashboard", "stats"],
        onSelect: () => setCurrentView("dashboard"),
      },
      {
        id: "go-projects",
        label: "Go to Projects",
        description: "Switch active project",
        icon: <FolderOpen style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        keywords: ["projects", "namespace"],
        onSelect: () => setCurrentView("projects"),
      },
      {
        id: "go-settings",
        label: "Go to Settings",
        description: "Appearance & preferences",
        icon: <SettingsIcon style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        keywords: ["settings", "preferences"],
        onSelect: () => setCurrentView("settings"),
      },
      {
        id: "refresh",
        label: "Refresh data",
        description: "Reload tasks and storage info",
        icon: <RefreshCw style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />,
        keywords: ["refresh", "reload"],
        onSelect: () => {
          void refresh();
        },
      },
    ],
    [refresh]
  );

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
          onCommandPalette={handleOpenCommandPalette}
          isLoading={isLoading}
          namespaces={namespaces}
          selectedNamespace={selectedNamespace}
          onNamespaceChange={setSelectedNamespace}
          aiStatus={aiStatus}
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
	              focusedTaskId={focusedTaskId}
	              onFocusChange={setFocusedTaskId}
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
	            <TimelineView tasks={filteredTasks} isLoading={isLoading} onTaskClick={handleTaskClick} />
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
              onRefresh={refresh}
              onSelectNamespace={(ns) => {
                setSelectedNamespace(ns);
                setCurrentView("list");
              }}
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
      {(() => {
        const selectedTask = selectedTaskId ? tasks.find((t) => t.id === selectedTaskId) : null;
        const apiTaskId = selectedTask?.task_id || null;

        return (
          <TaskDetailModal
            taskId={apiTaskId}
            domain={selectedTask?.domain}
            namespace={selectedTask?.namespace}
            onClose={() => setSelectedTaskId(null)}
            onDelete={() => {
              if (selectedTask) {
                deleteTask(selectedTask.id);
              }
              setSelectedTaskId(null);
            }}
          />
        );
      })()}

      {/* New Task Modal */}
	      <NewTaskModal
	        isOpen={showNewTaskModal}
	        onClose={() => setShowNewTaskModal(false)}
	        onTaskCreated={refresh}
	        namespaces={namespaces}
	        selectedNamespace={selectedNamespace}
	        defaultNamespace={projectName}
	      />

	      <CommandPalette
	        isOpen={isPaletteOpen}
	        tasks={tasks}
	        commands={paletteCommands()}
	        onSelectTask={(id) => {
	          setFocusedTaskId(id);
	          setSelectedTaskId(id);
	        }}
	        onClose={() => setIsPaletteOpen(false)}
	      />

	      {/* Toast Notifications */}
	      <ToastContainer />
	    </>
  );
}

export default App;
