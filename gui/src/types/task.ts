/**
 * Task and SubTask types matching Python domain models
 * from core/task_detail.py
 */

/** Task status enum */
export type TaskStatus = "TODO" | "ACTIVE" | "DONE";

/** Internal backend status codes (legacy) */
export type TaskStatusCode = "OK" | "WARN" | "FAIL";

/** Phase 1: Subtask status enum */
export type SubtaskStatus = "pending" | "in_progress" | "blocked" | "completed";

/** Checkpoint state for subtasks */
export interface CheckpointState {
  done: boolean;
  note?: string;
  timestamp?: string;
}

/** Subtask checkpoints */
export interface SubtaskCheckpoints {
  criteria: CheckpointState;
  tests: CheckpointState;
  blockers: CheckpointState;
}

/** Subtask definition */
export interface SubTask {
  /** Subtask title (min 20 chars) */
  title: string;

  /** Success criteria list (matches backend 'success_criteria' field) */
  success_criteria?: string[];

  /** Test commands/assertions */
  tests: string[];

  /** Dependencies/blockers */
  blockers: string[];

  /** Checkpoint confirmations (from backend) */
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  blockers_resolved?: boolean;
  criteria_notes?: string[];
  tests_notes?: string[];
  blockers_notes?: string[];
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  blockers_auto_resolved?: boolean;

  /** Whether subtask is completed */
  completed: boolean;

  /** Checkpoint confirmations */
  checkpoints?: SubtaskCheckpoints;

  /** Nested subtasks (recursive) */
  subtasks?: SubTask[];

  /** Notes attached to subtask */
  notes?: string[];

  /** Path in tree (e.g., "0.1.2") */
  path?: string;

  /** Phase 1: Progress notes without completion */
  progress_notes?: string[];

  /** Phase 1: When work started (ISO timestamp) */
  started_at?: string | null;

  /** Phase 1: Whether subtask is blocked */
  blocked?: boolean;

  /** Phase 1: Reason for blocking */
  block_reason?: string;

  /** Phase 1: Computed status (pending → in_progress → blocked → completed) */
  computed_status?: SubtaskStatus;
}

/** Full task definition */
export interface Task {
  /** Task ID (e.g., "TASK-001") */
  id: string;

  /** Task title with optional tags */
  title: string;

  /** Task description */
  description: string;

  /** Current status */
  status: TaskStatus;
  /** Internal status code (for debugging/compat) */
  status_code?: TaskStatusCode;

  /** Parent task ID (or "ROOT") - may be null if no parent */
  parent: string | null;

  /** Test commands for the task - optional as MCP may not return it */
  tests?: string[];

  /** Known risks - optional as MCP may not return it */
  risks?: string[];

  /** List of subtasks - optional for minimal task views */
  subtasks?: SubTask[];

  /** Task dependencies */
  depends_on?: string[];

  /** Task tags */
  tags?: string[];

  /** Domain path (e.g., "core/api") */
  domain?: string;

  /** Sprint/phase */
  phase?: string;

  /** Component name */
  component?: string;

  /** Priority level */
  priority?: "LOW" | "NORMAL" | "HIGH" | "CRITICAL";

  /** Creation timestamp */
  created_at?: string;

  /** Last update timestamp */
  updated_at?: string;

  /** Progress percentage (0-100) */
  progress?: number;

  /** Notes/comments */
  notes?: string[];
}

/** Task list item (compact view) */
export interface TaskListItem {
  /** Unique ID for React (may include namespace prefix) */
  id: string;
  /** Original task ID for API calls */
  task_id?: string;
  title: string;
  status: TaskStatus;
  status_code?: TaskStatusCode;
  progress: number;
  subtask_count: number;
  completed_count: number;
  /** Task category/domain within namespace */
  domain?: string;
  /** Storage namespace (folder in ~/.tasks/) */
  namespace?: string;
  tags?: string[];
  updated_at?: string;
}

/** Task event for timeline */
export interface TaskEvent {
  id: string;
  task_id: string;
  event_type: "created" | "updated" | "completed" | "checkpoint" | "note";
  description: string;
  timestamp: string;
  user?: string;
  data?: Record<string, unknown>;
}

/** Namespace info from tasks_storage */
export interface Namespace {
  namespace: string;
  path: string;
  task_count: number;
}

/** Storage info response */
export interface StorageInfo {
  global_storage: string;
  global_exists: boolean;
  local_storage: string;
  local_exists: boolean;
  current_storage: string;
  current_namespace: string;
  namespaces: Namespace[];
}

/** Project/namespace info */
export interface Project {
  namespace: string;
  name: string;
  path: string;
  task_count: number;
  last_activity?: string;
}
