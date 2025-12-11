/**
 * API response types matching Python CLI output
 * from core/desktop/devtools/interface/cli_ai.py
 */

import type { Task, TaskListItem, TaskEvent, Project } from "./task";

/** Base API response structure */
export interface ApiResponse<T = unknown> {
  command: string;
  status: "OK" | "WARN" | "ERROR";
  message: string;
  timestamp: string;
  summary?: string;
  payload: T;
}

/** AI Intent response structure */
export interface AIResponse<T = unknown> {
  success: boolean;
  intent: string;
  result: T;
  suggestions?: string[];
  error_message?: string;
  recovery_hint?: Record<string, unknown>;
}

/** Context intent result */
export interface ContextResult {
  task?: Task;
  tasks?: TaskListItem[];
  summary: string;
  state?: {
    last_task?: string;
    domain?: string;
  };
  hints?: string[];
}

/** Resume intent result */
export interface ResumeResult {
  task: Task;
  timeline: TaskEvent[];
  dependencies: {
    depends_on: string[];
    blocked_by: string[];
    blocking: string[];
  };
  checkpoint_status: {
    pending: string[];
    ready: string[];
  };
}

/** List command payload */
export interface ListPayload {
  tasks: TaskListItem[];
  total: number;
  filters_applied?: string[];
}

/** Show command payload */
export interface ShowPayload {
  task: Task;
}

/** Create command result */
export interface CreateResult {
  task_id: string;
  message: string;
}

/** Storage intent result */
export interface StorageResult {
  tasks_dir: string;
  namespace: string;
  source: string;
  task_count: number;
}

/** History intent result */
export interface HistoryResult {
  events: TaskEvent[];
  total: number;
}

/** Batch operation */
export interface BatchOperation {
  intent: string;
  task?: string;
  path?: string;
  [key: string]: unknown;
}

/** Batch result */
export interface BatchResult {
  results: Array<{
    intent: string;
    success: boolean;
    result?: unknown;
    error?: string;
  }>;
  all_succeeded: boolean;
}

/** Projects list result */
export interface ProjectsResult {
  projects: Project[];
  current?: string;
}

/** JSON-RPC 2.0 request */
export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: Record<string, unknown>;
}

/** JSON-RPC 2.0 response */
export interface JsonRpcResponse<T = unknown> {
  jsonrpc: "2.0";
  id: number | string;
  result?: T;
  error?: {
    code: number;
    message: string;
    data?: unknown;
  };
}

/** JSON-RPC 2.0 notification */
export interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}
