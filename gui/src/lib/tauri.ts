/**
 * Tauri API client wrapper
 *
 * Provides type-safe invoke wrappers for Rust commands.
 * Falls back to mock data when running in browser (not in Tauri).
 */

import type { Task, TaskListItem, TaskStatus, TaskStatusCode } from "@/types/task";

// Check if we're running inside Tauri (Tauri 2.0 uses __TAURI_INTERNALS__)
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

const DEBUG = import.meta.env.DEV;
const debugLog = (...args: unknown[]) => {
  if (DEBUG) {
    // eslint-disable-next-line no-console
    console.log(...args);
  }
};
const debugError = (...args: unknown[]) => {
  if (DEBUG) {
    // eslint-disable-next-line no-console
    console.error(...args);
  }
};

// Dynamic import for Tauri API (only when available)
let tauriInvoke: typeof import("@tauri-apps/api/core").invoke | null = null;

// Store the promise to ensure we wait for import completion
const tauriInitPromise: Promise<void> = isTauri
  ? import("@tauri-apps/api/core").then((mod) => {
    tauriInvoke = mod.invoke;
    debugLog("[Tauri] API initialized successfully");
  })
  : Promise.resolve();

// Mock data for browser development (mutable for status updates)
const MOCK_TASKS: TaskListItem[] = [
  {
    id: "TASK-001",
    title: "Implement user authentication system",
    status: "DONE",
    status_code: "OK",
    progress: 75,
    subtask_count: 4,
    completed_count: 3,
    tags: ["auth", "security"],
    domain: "backend",
    updated_at: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    id: "TASK-002",
    title: "Design dashboard UI components",
    status: "ACTIVE",
    status_code: "WARN",
    progress: 40,
    subtask_count: 6,
    completed_count: 2,
    tags: ["ui", "design"],
    domain: "frontend",
    updated_at: new Date(Date.now() - 7200000).toISOString(),
  },
  {
    id: "TASK-003",
    title: "Setup CI/CD pipeline",
    status: "TODO",
    status_code: "FAIL",
    progress: 20,
    subtask_count: 5,
    completed_count: 1,
    tags: ["devops", "automation"],
    domain: "infra",
    updated_at: new Date(Date.now() - 86400000).toISOString(),
  },
];

// Full mock task details for tasks_show
function getMockTaskDetail(taskId: string): Task | null {
  const listItem = MOCK_TASKS.find((t) => t.id === taskId);
  if (!listItem) return null;

  // Return full task with subtasks based on task ID
  const subtasksByTask: Record<string, Task["subtasks"]> = {
    "TASK-001": [
      {
        title: "Set up JWT token generation and validation",
        success_criteria: ["Tokens expire after 24h", "Refresh token mechanism"],
        tests: ["jest auth.test.ts"],
        blockers: [],
        completed: true,
        subtasks: [
          {
            title: "Implement access token generation",
            success_criteria: ["Uses RS256 algorithm", "Contains user claims"],
            tests: ["Unit test token payload"],
            blockers: [],
            completed: true,
          },
          {
            title: "Implement refresh token storage",
            success_criteria: ["Store in Redis", "Rotate on use"],
            tests: ["Integration test with Redis"],
            blockers: [],
            completed: true,
          },
        ],
      },
      {
        title: "Create login endpoint with rate limiting",
        success_criteria: ["Max 5 attempts per minute", "Return proper errors"],
        tests: ["Rate limit test", "Invalid creds test"],
        blockers: ["Redis connection required"],
        completed: false,
        subtasks: [
          {
            title: "Validate credentials against database",
            success_criteria: ["bcrypt comparison", "Timing-safe"],
            tests: ["Password validation test"],
            blockers: [],
            completed: true,
          },
          {
            title: "Implement rate limiter middleware",
            success_criteria: ["Use sliding window", "Per-IP tracking"],
            tests: ["Load test rate limiter"],
            blockers: ["Redis connection required"],
            completed: false,
          },
        ],
      },
      {
        title: "Integrate Google OAuth2 provider",
        success_criteria: ["Popup flow", "Account linking"],
        tests: ["E2E OAuth flow"],
        blockers: ["Google API credentials"],
        completed: false,
      },
    ],
    "TASK-002": [
      {
        title: "Create base component library",
        success_criteria: ["Button, Input, Card components"],
        tests: ["Storybook visual tests"],
        blockers: [],
        completed: true,
      },
      {
        title: "Build dashboard layout",
        success_criteria: ["Responsive grid", "Sidebar navigation"],
        tests: ["Layout snapshot tests"],
        blockers: [],
        completed: true,
      },
      {
        title: "Implement chart widgets",
        success_criteria: ["Line, Bar, Pie charts"],
        tests: ["Chart rendering tests"],
        blockers: [],
        completed: false,
      },
    ],
    "TASK-003": [
      {
        title: "Configure GitHub Actions workflow",
        success_criteria: ["Run on push", "Matrix builds"],
        tests: ["Workflow syntax validation"],
        blockers: [],
        completed: true,
      },
      {
        title: "Set up Docker build stage",
        success_criteria: ["Multi-stage build", "Layer caching"],
        tests: ["Build time benchmark"],
        blockers: ["Docker Hub credentials"],
        completed: false,
      },
    ],
  };

  return {
    id: listItem.id,
    title: listItem.title,
    description: `Full description for ${listItem.title}. This task involves multiple subtasks and has specific acceptance criteria.`,
    status: listItem.status,
    parent: "ROOT",
    tests: ["npm run test", "npm run e2e"],
    risks: ["Potential blockers from dependencies"],
    tags: listItem.tags || [],
    domain: listItem.domain,
    priority: listItem.status === "TODO" ? "HIGH" : "NORMAL",
    progress: listItem.progress,
    created_at: new Date(Date.now() - 7 * 86400000).toISOString(),
    updated_at: listItem.updated_at,
    subtasks: subtasksByTask[taskId] || [],
  };
}

/**
 * Invoke wrapper that uses Tauri API when available, mock otherwise
 */
async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  // Wait for Tauri API to be initialized (resolves immediately if not in Tauri)
  debugLog(`[invoke] Starting cmd=${cmd}, isTauri=${isTauri}, tauriInvoke=${!!tauriInvoke}`);
  await tauriInitPromise;
  debugLog(`[invoke] After init promise, isTauri=${isTauri}, tauriInvoke=${!!tauriInvoke}`);

  if (isTauri && tauriInvoke) {
    debugLog(`[Tauri] invoke: ${cmd}`, args);
    try {
      const result = await tauriInvoke<T>(cmd, args);
      debugLog(`[Tauri] invoke result for ${cmd}:`, result);
      return result;
    } catch (err) {
      debugError(`[Tauri] invoke error for ${cmd}:`, err);
      throw err;
    }
  }

  // Mock responses for browser development
  debugLog(`[Mock] invoke: ${cmd}`, args);

  switch (cmd) {
    case "tasks_list":
      return {
        success: true,
        tasks: MOCK_TASKS,
        total: MOCK_TASKS.length,
      } as T;

    case "tasks_storage":
      return {
        success: true,
        intent: "storage",
        result: {
          global_storage: "/home/mock/.tasks",
          global_exists: true,
          local_storage: "/mock/project/.tasks",
          local_exists: false,
          current_storage: "/home/mock/.tasks/apply_task",
          current_namespace: "apply_task",
          namespaces: [
            {
              namespace: "apply_task",
              path: "/home/mock/.tasks/apply_task",
              task_count: MOCK_TASKS.length,
            },
            {
              namespace: "other_project",
              path: "/home/mock/.tasks/other_project",
              task_count: 5,
            },
            {
              namespace: "demo_project",
              path: "/home/mock/.tasks/demo_project",
              task_count: 12,
            },
          ],
        },
      } as T;

    case "tasks_show": {
      const taskDetail = getMockTaskDetail(args?.task_id as string);
      return {
        success: !!taskDetail,
        task: taskDetail,
        error: taskDetail ? undefined : "Task not found",
      } as T;
    }

    case "tasks_update_status": {
      const { task_id, status } = args as { task_id: string; status: TaskStatus };
      const taskIndex = MOCK_TASKS.findIndex((t) => t.id === task_id);
      if (taskIndex >= 0) {
        const statusCodeMap: Record<TaskStatus, TaskStatusCode> = {
          DONE: "OK",
          ACTIVE: "WARN",
          TODO: "FAIL",
        };
        // Update mock data
        MOCK_TASKS[taskIndex] = {
          ...MOCK_TASKS[taskIndex],
          status,
          status_code: statusCodeMap[status],
          progress: status === "DONE" ? 100 : status === "ACTIVE" ? 50 : 0,
          updated_at: new Date().toISOString(),
        };
        debugLog(`[Mock] Updated task ${task_id} status to ${status}`);
        return {
          success: true,
          intent: "update_status",
          result: { task_id, status },
        } as T;
      }
      return {
        success: false,
        error: `Task ${task_id} not found`,
      } as T;
    }

    case "tasks_create": {
      const { title, domain, namespace, tags, subtasks } = args as {
        title: string;
        domain?: string;
        namespace?: string;
        tags?: string[];
        subtasks?: unknown[];
      };
      const newId = `TASK-${String(MOCK_TASKS.length + 1).padStart(3, "0")}`;
      const uiId = namespace ? `${namespace}/${newId}` : newId;
      const newTask: TaskListItem = {
        id: uiId,
        task_id: newId,
        title,
        status: "TODO",
        status_code: "FAIL",
        progress: 0,
        subtask_count: Array.isArray(subtasks) ? subtasks.length : 0,
        completed_count: 0,
        tags: tags || [],
        domain: domain || "general",
        namespace,
        updated_at: new Date().toISOString(),
      };
      MOCK_TASKS.unshift(newTask); // Add to beginning
      debugLog(`[Mock] Created task ${newId}: ${title}`);
      return {
        success: true,
        intent: "create",
        result: { taskId: newId, task: newTask },
      } as T;
    }

    case "tasks_template_subtasks": {
      const { count } = args as { count?: number };
      const n = Math.max(3, count ?? 3);
      const template = Array.from({ length: n }, (_v, i) => ({
        title: `Result ${i + 1}: describe measurable outcome`,
        criteria: ["Define measurable success criteria"],
        tests: ["Add relevant tests"],
        blockers: ["List blockers/dependencies"],
      }));
      return {
        success: true,
        intent: "template_subtasks",
        result: {
          success: true,
          payload: { type: "subtasks", count: n, template },
        },
      } as T;
    }

    case "tasks_send_signal": {
      const { signal, message } = args as { signal: string; message?: string };
      return {
        success: true,
        intent: "send_signal",
        result: { success: true, signal, message: message || "" },
      } as T;
    }

    case "tasks_delete": {
      const { task_id } = args as { task_id: string };
      const taskIndex = MOCK_TASKS.findIndex((t) => t.id === task_id);
      if (taskIndex >= 0) {
        MOCK_TASKS.splice(taskIndex, 1);
        debugLog(`[Mock] Deleted task ${task_id}`);
        return {
          success: true,
          intent: "delete",
          result: { task_id, deleted: true },
        } as T;
      }
      return {
        success: false,
        error: `Task ${task_id} not found`,
      } as T;
    }

    default:
      return {
        success: true,
        intent: cmd,
        result: { message: "Mock response" },
      } as T;
  }
}

/** Task list response from Rust */
interface TaskListResponse {
  success: boolean;
  tasks: TaskListItem[];
  total: number;
  error?: string;
}

/** Task detail response from Rust */
interface TaskResponse {
  success: boolean;
  task?: Task;
  error?: string;
}

/** AI intent response from Rust */
interface AIIntentResponse {
  success: boolean;
  intent: string;
  result?: unknown;
  suggestions?: string[];
  error?: string;
}

/**
 * Get list of tasks
 */
export async function listTasks(params?: {
  domain?: string;
  status?: string;
  compact?: boolean;
  namespace?: string | null;
  allNamespaces?: boolean;
}): Promise<TaskListResponse> {
  return invoke<TaskListResponse>("tasks_list", {
    domain: params?.domain,
    status: params?.status,
    compact: params?.compact ?? true,
    namespace: params?.namespace ?? undefined,
    // Support both camelCase and snake_case for Tauri argument mapping
    allNamespaces: params?.allNamespaces ?? false,
    all_namespaces: params?.allNamespaces ?? false,
  });
}

/**
 * Get task details
 * @param taskId - The task ID (e.g., "TASK-001")
 * @param domain - Optional domain path within namespace (e.g., "core/api")
 * @param namespace - Optional namespace folder (e.g., "idea_h") for cross-namespace lookup
 */
export async function showTask(taskId: string, domain?: string, namespace?: string): Promise<TaskResponse> {
  return invoke<TaskResponse>("tasks_show", {
    taskId,
    task_id: taskId,
    domain,
    namespace,
  });
}

/**
 * Get current context for AI session
 */
export async function getContext(params?: {
  compact?: boolean;
  includeAll?: boolean;
}): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_context", {
    compact: params?.compact ?? true,
    includeAll: params?.includeAll ?? false,
  });
}

/**
 * Execute AI intent
 */
export async function executeIntent(
  intent: string,
  params?: Record<string, unknown>
): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("ai_intent", { intent, params });
}

/**
 * Create a new task
 */
export async function createTask(params: {
  title: string;
  description?: string;
  priority?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  parent?: string;
  tags?: string[];
  subtasks?: Array<{
    title: string;
    criteria?: string[];
    tests?: string[];
    blockers?: string[];
  }>;
  domain?: string;
  phase?: string;
  component?: string;
  context?: string;
  namespace?: string;
}): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_create", params);
}

/**
 * Update task status
 * @param taskId - The task ID (e.g., "TASK-001")
 * @param status - New status
 * @param domain - Optional namespace for cross-namespace operations
 */
export async function updateTaskStatus(
  taskId: string,
  status: TaskStatus,
  domain?: string,
  namespace?: string
): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_update_status", {
    taskId,
    task_id: taskId,
    status,
    domain,
    namespace,
  });
}

export async function completeCheckpoint(params: {
  taskId: string;
  path: string;
  checkpoint: "criteria" | "tests" | "blockers";
  note: string;
  domain?: string;
  namespace?: string;
}): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_checkpoint", {
    task_id: params.taskId,
    path: params.path,
    checkpoint: params.checkpoint,
    note: params.note,
    domain: params.domain,
    namespace: params.namespace,
  });
}

/**
 * Define or update criteria/tests/blockers for a subtask
 */
export async function defineSubtask(params: {
	  taskId: string;
	  path: string;
	  title?: string;
	  criteria?: string[];
	  tests?: string[];
	  blockers?: string[];
	  domain?: string;
	  namespace?: string;
}): Promise<AIIntentResponse> {
	  return executeIntent("define", {
	    task: params.taskId,
	    path: params.path,
	    title: params.title,
	    criteria: params.criteria,
	    tests: params.tests,
	    blockers: params.blockers,
	    domain: params.domain,
	    namespace: params.namespace,
	  });
}

/**
 * Delete a subtask at path
 */
export async function deleteSubtask(params: {
  taskId: string;
  path: string;
  domain?: string;
  namespace?: string;
}): Promise<AIIntentResponse> {
  return executeIntent("delete", {
    task: params.taskId,
    path: params.path,
    domain: params.domain,
    namespace: params.namespace,
  });
}

/**
 * Toggle subtask completion
 */
export async function toggleSubtask(
  taskId: string,
  path: string,
  completed: boolean,
  domain?: string,
  namespace?: string
): Promise<AIIntentResponse> {
  return executeIntent("progress", {
    task: taskId,
    path,
    completed,
    domain,
    namespace,
  });
}

/**
 * Get storage info
 */
export async function getStorage(): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_storage");
}

/**
 * Get AI session status (current op / plan / recent history)
 */
export async function getAIStatus(): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_ai_status");
}

/**
 * Get subtasks template from backend
 */
export async function getSubtasksTemplate(count = 3): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_template_subtasks", { count });
}

/**
 * Send user signal to AI (pause/resume/stop/skip/message)
 */
export async function sendAISignal(
  signal: "pause" | "resume" | "stop" | "skip" | "message",
  message?: string
): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_send_signal", { signal, message });
}

/**
 * Resume session with context
 */
export async function resumeSession(taskId?: string): Promise<AIIntentResponse> {
  return executeIntent("resume", { task: taskId });
}

/**
 * Get task history/timeline
 */
export async function getHistory(params?: {
  taskId?: string;
  limit?: number;
  format?: "json" | "markdown";
}): Promise<AIIntentResponse> {
  return executeIntent("history", {
    task: params?.taskId,
    limit: params?.limit,
    format: params?.format,
  });
}

/**
 * Open a project folder
 * In Tauri: Opens native folder picker
 * In browser: Shows a prompt for path input
 */
export async function openProject(): Promise<{ success: boolean; path?: string; error?: string }> {
  await tauriInitPromise;

  if (isTauri && tauriInvoke) {
    return tauriInvoke<{ success: boolean; path?: string; error?: string }>("open_project");
  }

  // Mock for browser - use prompt
  const path = window.prompt("Enter project folder path:", "/path/to/project");
  if (path) {
    debugLog(`[Mock] Opening project: ${path}`);
    return { success: true, path };
  }
  return { success: false, error: "Cancelled" };
}

/**
 * Open a local path in the system file manager (Tauri) or new tab (browser).
 */
export async function openPath(path: string): Promise<{ success: boolean; error?: string }> {
  if (!path) return { success: false, error: "No path provided" };
  try {
    if (isTauri) {
      const { openPath: openNativePath } = await import("@tauri-apps/plugin-opener");
      await openNativePath(path);
      return { success: true };
    }
    window.open(path, "_blank", "noopener,noreferrer");
    return { success: true };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : "Failed to open path" };
  }
}

/**
 * Delete a task
 * @param taskId - The task ID (e.g., "TASK-001")
 * @param domain - Optional namespace for cross-namespace operations
 */
export async function deleteTask(taskId: string, domain?: string, namespace?: string): Promise<AIIntentResponse> {
  return invoke<AIIntentResponse>("tasks_delete", {
    taskId,
    task_id: taskId,
    domain,
    namespace,
  });
}
