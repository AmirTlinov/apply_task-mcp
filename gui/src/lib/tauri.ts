/**
 * Canonical Tauri bridge for apply_task GUI.
 *
 * Single source of truth:
 * - All backend mutations go through AI intents (`ai_intent` → `tasks_<intent>`).
 * - Frontend types match Python serializers (plan_to_dict/task_to_dict/step_to_dict).
 */

import type { AIResponse, ContextData, MirrorData, ResumeData, RadarData, Suggestion } from "@/types/api";
import type { Plan, PlanListItem, StorageInfo, Task, TaskListItem, TaskStatus, Step } from "@/types/task";

// Check if we're running inside Tauri (Tauri 2.0 uses __TAURI_INTERNALS__)
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let tauriInvoke: typeof import("@tauri-apps/api/core").invoke | null = null;
const tauriInitPromise: Promise<void> = isTauri
  ? import("@tauri-apps/api/core").then((mod) => {
      tauriInvoke = mod.invoke;
    })
  : Promise.resolve();

function extractAIError(raw: unknown): string | null {
  const obj = (raw ?? {}) as Record<string, unknown>;
  const err = (obj.error ?? null) as Record<string, unknown> | null;
  const msg = typeof err?.message === "string" ? err.message : null;
  return msg && msg.trim().length > 0 ? msg : null;
}

async function invokeCommand<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  await tauriInitPromise;
  if (!isTauri || !tauriInvoke) {
    throw new Error(`Tauri command not available in browser mode: ${command}`);
  }
  return tauriInvoke<T>(command, args ?? {});
}

export interface BackendStorageModeResponse {
  success: boolean;
  mode: "global" | "local";
  restarted: boolean;
  error?: string;
}

export async function setBackendStorageMode(mode: "global" | "local"): Promise<BackendStorageModeResponse> {
  if (!isTauri) {
    return { success: true, mode, restarted: false };
  }
  const resp = await invokeCommand<{ success: boolean; mode: string; restarted: boolean; error?: string }>(
    "backend_set_storage_mode",
    { mode }
  );
  return {
    success: Boolean(resp?.success),
    mode: resp?.mode === "local" ? "local" : "global",
    restarted: Boolean(resp?.restarted),
    error: resp?.error,
  };
}

export async function aiIntent<T = unknown>(intent: string, params?: Record<string, unknown>): Promise<AIResponse<T>> {
  if (!isTauri) {
    // Minimal browser mock: return empty context to keep UI usable in dev.
    const mock: AIResponse<T> = {
      success: true,
      intent,
      result: {} as T,
      warnings: [],
      context: {},
      suggestions: [],
      meta: {},
      error: null,
      timestamp: new Date().toISOString(),
    };
    return mock;
  }
  return invokeCommand<AIResponse<T>>("ai_intent", { intent, params: params ?? {} });
}

export interface TaskListResponse {
  success: boolean;
  tasks: TaskListItem[];
  total: number;
  error?: string;
}

export async function listTasks(params?: {
  domain?: string;
  status?: string;
  parent?: string;
  compact?: boolean;
}): Promise<TaskListResponse> {
  const resp = await aiIntent<ContextData>("context", {
    include_all: true,
    compact: params?.compact ?? true,
    tasks_parent: params?.parent,
  });
  if (!resp.success) {
    return { success: false, tasks: [], total: 0, error: extractAIError(resp) || "Failed to load tasks" };
  }
  const all = Array.isArray(resp.result?.tasks) ? resp.result.tasks : [];
  let tasks = all.filter((t): t is TaskListItem => (t as TaskListItem).kind === "task");

  const statusFilter = String(params?.status || "").trim().toUpperCase();
  if (statusFilter) {
    tasks = tasks.filter((t) => String(t.status_code || t.status).toUpperCase() === statusFilter);
  }

  const domain = String(params?.domain || "").trim();
  if (domain) {
    tasks = tasks.filter((t) => (t.domain || "").startsWith(domain));
  }

  return { success: true, tasks, total: tasks.length };
}

export interface PlanListResponse {
  success: boolean;
  plans: PlanListItem[];
  total: number;
  error?: string;
}

export async function listPlans(params?: { domain?: string; compact?: boolean }): Promise<PlanListResponse> {
  const resp = await aiIntent<ContextData>("context", {
    include_all: true,
    compact: params?.compact ?? true,
    domain: params?.domain,
  });
  if (!resp.success) {
    return { success: false, plans: [], total: 0, error: extractAIError(resp) || "Failed to load plans" };
  }
  const all = Array.isArray(resp.result?.plans) ? resp.result.plans : [];
  const plans = all.filter((p): p is PlanListItem => (p as PlanListItem).kind === "plan");
  return { success: true, plans, total: plans.length };
}

export async function mirrorList(params: {
  task?: string;
  plan?: string;
  path?: string;
  kind?: "step" | "task";
  stepId?: string;
  taskNodeId?: string;
  limit?: number;
}): Promise<{ success: boolean; data?: MirrorData; error?: string }> {
  const resp = await aiIntent<MirrorData>("mirror", {
    task: params.task,
    plan: params.plan,
    path: params.path,
    kind: params.kind,
    step_id: params.stepId,
    task_node_id: params.taskNodeId,
    limit: params.limit,
  });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to load mirror list" };
  }
  return { success: true, data: resp.result as MirrorData };
}

export interface StorageResponse {
  success: boolean;
  storage?: StorageInfo;
  error?: string;
}

export async function getStorage(): Promise<StorageResponse> {
  const resp = await aiIntent<StorageInfo>("storage");
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to load storage" };
  }
  return { success: true, storage: resp.result as unknown as StorageInfo };
}

export interface TaskResponse {
  success: boolean;
  task?: Task;
  checkpoint_status?: { pending: string[]; ready: string[] };
  timeline?: unknown[];
  error?: string;
}

export async function showTask(taskId: string): Promise<TaskResponse> {
  const resp = await aiIntent<ResumeData>("resume", { task: taskId, compact: false, events_limit: 50 });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to load task" };
  }
  const task = resp.result?.task as unknown as Task | undefined;
  const checkpoint_status = resp.result?.checkpoint_status as unknown as { pending: string[]; ready: string[] } | undefined;
  const timeline = resp.result?.timeline as unknown[] | undefined;
  return { success: Boolean(task), task, checkpoint_status, timeline, error: task ? undefined : "Task not found" };
}

export async function resumeEntity(id: string): Promise<{ success: boolean; plan?: Plan; task?: Task; error?: string }> {
  const resp = await aiIntent<ResumeData>("resume", { task: id, compact: false, events_limit: 50 });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to resume" };
  }
  return {
    success: true,
    plan: (resp.result?.plan as unknown as Plan | undefined) ?? undefined,
    task: (resp.result?.task as unknown as Task | undefined) ?? undefined,
  };
}

export async function getRadar(params: {
  taskId: string;
  limit?: number;
  maxChars?: number;
}): Promise<{ success: boolean; data?: RadarData; error?: string }> {
  const resp = await aiIntent<RadarData>("radar", {
    task: params.taskId,
    limit: params.limit ?? 3,
    max_chars: params.maxChars ?? 12_000,
  });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to load radar" };
  }
  return { success: true, data: resp.result as unknown as RadarData };
}

export async function runValidatedSuggestion(suggestion: Suggestion): Promise<{ success: boolean; response?: AIResponse; error?: string }> {
  const validated = Boolean((suggestion as { validated?: boolean }).validated);
  if (!validated) {
    return { success: false, error: "Suggestion is not validated" };
  }
  const intent = String(suggestion.action || "").trim();
  if (!intent) return { success: false, error: "Suggestion has no action" };
  const resp = await aiIntent(intent, suggestion.params ?? {});
  if (!resp.success) {
    return { success: false, response: resp, error: extractAIError(resp) || "Failed to execute suggestion" };
  }
  return { success: true, response: resp };
}

export async function getHandoff(params: {
  taskId?: string;
  planId?: string;
  limit?: number;
  maxChars?: number;
}): Promise<{ success: boolean; data?: Record<string, unknown>; error?: string }> {
  const resp = await aiIntent<Record<string, unknown>>("handoff", {
    task: params.taskId,
    plan: params.planId,
    limit: params.limit,
    max_chars: params.maxChars,
  });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to export handoff" };
  }
  return { success: true, data: resp.result ?? {} };
}

export async function createEntity(params: {
  title: string;
  kind: "plan" | "task";
  parent?: string;
  priority?: "LOW" | "MEDIUM" | "HIGH";
  description?: string;
  context?: string;
  contract?: string;
  steps?: unknown[];
}): Promise<{ success: boolean; plan?: Plan; task?: Task; error?: string }> {
  const resp = await aiIntent<Record<string, unknown>>("create", {
    title: params.title,
    kind: params.kind,
    parent: params.parent,
    priority: params.priority,
    description: params.description,
    context: params.context,
    contract: params.contract,
    steps: params.steps,
  });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to create" };
  }
  const plan = (resp.result?.plan as unknown as Plan | undefined) ?? undefined;
  const task = (resp.result?.task as unknown as Task | undefined) ?? undefined;
  return { success: true, plan, task };
}

export async function updateTaskStatus(taskId: string, status: TaskStatus): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("complete", { task: taskId, status });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update status" };
  return { success: true };
}

export async function closeTask(params: {
  taskId: string;
  patches?: unknown[];
  expectedRevision?: number;
}): Promise<{ success: boolean; result?: unknown; error?: string }> {
  const resp = await aiIntent("close_task", {
    task: params.taskId,
    apply: true,
    patches: params.patches,
    strict_targeting: true,
    expected_target_id: params.taskId,
    expected_kind: "task",
    expected_revision: typeof params.expectedRevision === "number" ? params.expectedRevision : undefined,
  });
  if (!resp.success) return { success: false, result: resp.result, error: extractAIError(resp) || "Failed to close task" };
  return { success: true, result: resp.result };
}

export async function deleteTask(taskId: string): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("delete", { task: taskId });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to delete task" };
  return { success: true };
}

export async function editTask(params: {
  taskId: string;
  description?: string;
  context?: string;
  tags?: string[];
  priority?: "LOW" | "MEDIUM" | "HIGH";
  dependsOn?: string[];
  newDomain?: string;
}): Promise<{ success: boolean; result?: unknown; error?: string }> {
  const resp = await aiIntent("edit", {
    task: params.taskId,
    description: params.description,
    context: params.context,
    tags: params.tags,
    priority: params.priority,
    depends_on: params.dependsOn,
    new_domain: params.newDomain,
  });
  if (!resp.success) return { success: false, result: resp.result, error: extractAIError(resp) || "Failed to edit" };
  return { success: true, result: resp.result };
}

export async function setStepCompleted(params: {
  taskId: string;
  path: string;
  completed: boolean;
  force?: boolean;
}): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("progress", {
    task: params.taskId,
    path: params.path,
    completed: params.completed,
    force: params.force ?? false,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update step" };
  return { success: true };
}

export async function addStepNote(params: { taskId: string; path: string; note: string }): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("note", { task: params.taskId, path: params.path, note: params.note });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to add note" };
  return { success: true };
}

export async function setStepBlocked(params: {
  taskId: string;
  path: string;
  blocked: boolean;
  reason?: string;
}): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("block", { task: params.taskId, path: params.path, blocked: params.blocked, reason: params.reason });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update blocked state" };
  return { success: true };
}

export async function addTaskNode(params: {
  taskId: string;
  parentStep: string;
  title: string;
  status?: string;
  priority?: "LOW" | "MEDIUM" | "HIGH";
  description?: string;
  context?: string;
  successCriteria?: string[];
  dependencies?: string[];
  nextSteps?: string[];
  problems?: string[];
  risks?: string[];
  blocked?: boolean;
  blockers?: string[];
  statusManual?: boolean;
}): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("task_add", {
    task: params.taskId,
    parent_step: params.parentStep,
    title: params.title,
    status: params.status,
    priority: params.priority,
    description: params.description,
    context: params.context,
    success_criteria: params.successCriteria,
    dependencies: params.dependencies,
    next_steps: params.nextSteps,
    problems: params.problems,
    risks: params.risks,
    blocked: params.blocked,
    blockers: params.blockers,
    status_manual: params.statusManual,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to add task node" };
  return { success: true };
}

export async function updateTaskNode(params: {
  taskId: string;
  path: string;
  title?: string;
  status?: string;
  priority?: "LOW" | "MEDIUM" | "HIGH";
  description?: string;
  context?: string;
  successCriteria?: string[];
  dependencies?: string[];
  nextSteps?: string[];
  problems?: string[];
  risks?: string[];
  blocked?: boolean;
  blockers?: string[];
  statusManual?: boolean;
}): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("task_define", {
    task: params.taskId,
    path: params.path,
    title: params.title,
    status: params.status,
    priority: params.priority,
    description: params.description,
    context: params.context,
    success_criteria: params.successCriteria,
    dependencies: params.dependencies,
    next_steps: params.nextSteps,
    problems: params.problems,
    risks: params.risks,
    blocked: params.blocked,
    blockers: params.blockers,
    status_manual: params.statusManual,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update task node" };
  return { success: true };
}

export async function deleteTaskNode(params: { taskId: string; path: string }): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("task_delete", { task: params.taskId, path: params.path });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to delete task node" };
  return { success: true };
}

export async function verifyStep(params: {
  taskId: string;
  path: string;
  criteriaNote?: string;
  testsNote?: string;
}): Promise<{ success: boolean; error?: string }> {
  const checkpoints: Record<string, unknown> = {};
  if (params.criteriaNote !== undefined) {
    checkpoints.criteria = { confirmed: true, note: params.criteriaNote };
  }
  if (params.testsNote !== undefined) {
    checkpoints.tests = { confirmed: true, note: params.testsNote };
  }
  const resp = await aiIntent("verify", { task: params.taskId, path: params.path, checkpoints });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to verify" };
  return { success: true };
}

export async function verifyCheckpoint(params: {
  taskId: string;
  kind: "step" | "task" | "plan" | "task_detail";
  path?: string;
  stepId?: string;
  taskNodeId?: string;
  checkpoint: "criteria" | "tests";
  confirmed: boolean;
  note?: string;
}): Promise<{ success: boolean; plan?: Plan; task?: Task; step?: Step; error?: string }> {
  const checkpoints: Record<string, unknown> = {
    [params.checkpoint]: { confirmed: params.confirmed, note: params.note ?? "" },
  };
  const resp = await aiIntent<Record<string, unknown>>("verify", {
    task: params.taskId,
    kind: params.kind,
    path: params.path,
    step_id: params.stepId,
    task_node_id: params.taskNodeId,
    checkpoints,
  });
  if (!resp.success) {
    return { success: false, error: extractAIError(resp) || "Failed to verify" };
  }
  const result = resp.result as Record<string, unknown>;
  return {
    success: true,
    plan: result.plan as Plan | undefined,
    task: result.task as Task | undefined,
    step: result.step as Step | undefined,
  };
}

export async function defineStep(params: {
  taskId: string;
  path: string;
  title?: string;
  successCriteria?: string[];
  tests?: string[];
  blockers?: string[];
}): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("define", {
    task: params.taskId,
    path: params.path,
    title: params.title,
    success_criteria: params.successCriteria,
    tests: params.tests,
    blockers: params.blockers,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update step" };
  return { success: true };
}

export async function deleteStep(params: { taskId: string; path: string }): Promise<{ success: boolean; error?: string }> {
  const resp = await aiIntent("delete", { task: params.taskId, path: params.path });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to delete step" };
  return { success: true };
}

export async function updatePlan(params: {
  planId: string;
  doc?: string;
  steps?: string[];
  current?: number;
  advance?: boolean;
}): Promise<{ success: boolean; plan?: Plan; error?: string }> {
  const resp = await aiIntent<{ plan?: Plan }>("plan", {
    plan: params.planId,
    doc: params.doc,
    steps: params.steps,
    current: params.current,
    advance: params.advance ?? false,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update plan" };
  return { success: true, plan: (resp.result?.plan as unknown as Plan | undefined) ?? undefined };
}

export async function updateContract(params: { planId: string; current: string }): Promise<{ success: boolean; plan?: Plan; error?: string }> {
  const resp = await aiIntent<{ plan?: Plan }>("contract", { plan: params.planId, current: params.current });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to update contract" };
  return { success: true, plan: (resp.result?.plan as unknown as Plan | undefined) ?? undefined };
}

export interface OperationHistoryEntry {
  id: string;
  intent: string;
  task_id?: string;
  timestamp?: number;
  datetime?: string;
  undone?: boolean;
}

export interface OperationHistoryState {
  operations: OperationHistoryEntry[];
  can_undo: boolean;
  can_redo: boolean;
}

export async function getOperationHistory(params?: { limit?: number }): Promise<{ success: boolean; history?: OperationHistoryState; error?: string }> {
  const resp = await aiIntent<{ operations?: OperationHistoryEntry[]; can_undo?: boolean; can_redo?: boolean }>("history", {
    limit: params?.limit ?? 50,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to load history" };
  return {
    success: true,
    history: {
      operations: Array.isArray(resp.result?.operations) ? resp.result.operations : [],
      can_undo: Boolean(resp.result?.can_undo),
      can_redo: Boolean(resp.result?.can_redo),
    },
  };
}

export async function undoLastOperation(): Promise<{ success: boolean; undo?: unknown; error?: string }> {
  const resp = await aiIntent("undo");
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to undo" };
  return { success: true, undo: resp.result };
}

export async function redoLastOperation(): Promise<{ success: boolean; redo?: unknown; error?: string }> {
  const resp = await aiIntent("redo");
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to redo" };
  return { success: true, redo: resp.result };
}

export interface TaskTimelineEventRecord {
  timestamp: string;
  event_type: string;
  actor?: string;
  target?: string;
  data?: Record<string, unknown>;
}

export async function getTaskTimelineEvents(params: {
  taskId: string;
  limit?: number;
}): Promise<{ success: boolean; events?: TaskTimelineEventRecord[]; error?: string }> {
  const resp = await aiIntent<ResumeData>("resume", {
    task: params.taskId,
    compact: false,
    include_steps: false,
    events_limit: params.limit ?? 50,
  });
  if (!resp.success) return { success: false, error: extractAIError(resp) || "Failed to load timeline" };
  const events = (resp.result?.timeline as unknown as TaskTimelineEventRecord[] | undefined) ?? [];
  return { success: true, events: Array.isArray(events) ? events : [] };
}

export async function openProject(): Promise<{ success: boolean; path?: string; error?: string }> {
  const path = window.prompt("Enter project folder path:", "");
  if (!path) return { success: false, error: "Cancelled" };
  return { success: true, path };
}

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
