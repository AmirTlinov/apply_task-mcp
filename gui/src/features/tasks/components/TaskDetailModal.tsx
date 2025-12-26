/**
 * Task Detail Modal - Full task view with subtask tree and actions
 */

import { useState, useEffect, useMemo, useRef, useCallback, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  CheckCircle2,
  Circle,
  AlertCircle,
  X,
  Clock,
  ListTodo,
  ListChecks,
  PlayCircle,
  AlertTriangle,
  Check,
  Loader2,
  Trash2,
  Copy,
  ArrowUpRight,
  MoreHorizontal,
  FileText as NotesIcon,
} from "lucide-react";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { CheckpointMarks } from "@/components/common/CheckpointMarks";
import { ProgressBar } from "@/components/common/ProgressBar";
import { StepStatusBadge } from "@/components/common/StepStatusBadge";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { TASK_STATUS_UI } from "@/lib/taskStatus";
import { cn } from "@/lib/utils";
import { useKeyboardListNavigation } from "@/hooks/useKeyboardListNavigation";
import {
  showTask,
  updateTaskStatus as apiUpdateTaskStatus,
  closeTask as apiCloseTask,
  deleteTask as apiDeleteTask,
  mirrorList,
  getHandoff,
  getRadar,
  runValidatedSuggestion,
  setStepCompleted,
  addStepNote,
  setStepBlocked,
  verifyStep,
  defineStep,
  deleteStep,
  updateTaskNode,
  deleteTaskNode,
} from "@/lib/tauri";
import type { Step, StepStatus, Task, TaskNode, TaskStatus } from "@/types/task";
import type { MirrorItem, RadarData, Suggestion } from "@/types/api";
import { TaskContractSection } from "@/features/tasks/components/TaskContractSection";
import { TaskPlanSection } from "@/features/tasks/components/TaskPlanSection";
import { TaskNotesSection } from "@/features/tasks/components/TaskNotesSection";
import { TaskMetaSection } from "@/features/tasks/components/TaskMetaSection";
import { TaskRadarSection } from "@/features/tasks/components/TaskRadarSection";
import { toast } from "@/components/common/toast";
import { useSettingsStore } from "@/stores/settingsStore";

interface TaskDetailModalProps {
  taskId: string | null;
  domain?: string;
  /** Storage namespace for cross-namespace lookup (e.g., "idea_h") */
  namespace?: string;
  /** Optional focus path for Cards drill-down (e.g. "0.1.2") */
  subtaskPath?: string;
  onSubtaskPathChange?: (path?: string) => void;
  onClose: () => void;
  onDelete?: (taskId: string) => void;
  variant?: "page" | "panel";
}

const TASK_STATUS_ICON: Record<TaskStatus, typeof CheckCircle2> = {
  DONE: CheckCircle2,
  ACTIVE: Clock,
  TODO: Circle,
};

type PathSegment = { kind: "s" | "t"; index: number };

function parseTreePath(path: string | undefined): PathSegment[] {
  if (!path) return [];
  const rawParts = path.split(".").filter(Boolean);
  if (rawParts.length === 0) return [];
  const segments: PathSegment[] = [];
  for (const raw of rawParts) {
    if (!raw.includes(":")) return [];
    const [kindRaw, idxRaw] = raw.split(":");
    if (kindRaw !== "s" && kindRaw !== "t") return [];
    const idx = Number(idxRaw);
    if (!Number.isInteger(idx) || idx < 0) return [];
    segments.push({ kind: kindRaw, index: idx } as PathSegment);
  }
  if (segments.length === 0 || segments[0].kind !== "s") return [];
  for (let i = 1; i < segments.length; i++) {
    if (segments[i].kind === segments[i - 1].kind) return [];
  }
  return segments;
}

function appendTreePath(base: string | undefined, kind: "s" | "t", index: number): string {
  const prefix = base ? `${base}.` : "";
  return `${prefix}${kind}:${index}`;
}

function isTaskPath(path: string | undefined): boolean {
  if (!path) return false;
  const parts = path.split(".").filter(Boolean);
  if (parts.length === 0) return false;
  return parts[parts.length - 1].startsWith("t:");
}

interface TreeNode {
  kind: "step" | "task";
  path: string;
  step?: Step;
  task?: TaskNode;
  children: TreeNode[];
}

function buildTreeNodes(steps: Step[], basePath: string = ""): TreeNode[] {
  return steps.map((step, idx) => {
    const stepPath = appendTreePath(basePath, "s", idx);
    const planTasks = step.plan?.tasks ?? [];
    let children: TreeNode[] = [];
    if (planTasks.length > 0) {
      children = planTasks.map((task, taskIdx) => {
        const taskPath = appendTreePath(stepPath, "t", taskIdx);
        return {
          kind: "task",
          path: taskPath,
          task,
          children: buildTreeNodes(task.steps ?? [], taskPath),
        };
      });
    }
    return {
      kind: "step",
      path: stepPath,
      step,
      children,
    };
  });
}

function buildNodeIndex(nodes: TreeNode[]) {
  const nodeMap = new Map<string, TreeNode>();
  const parentMap = new Map<string, string | null>();

  const visit = (node: TreeNode, parent: string | null) => {
    nodeMap.set(node.path, node);
    parentMap.set(node.path, parent);
    for (const child of node.children) {
      visit(child, node.path);
    }
  };

  for (const node of nodes) {
    visit(node, null);
  }

  return { nodeMap, parentMap };
}

function normalizeTaskStatus(raw: unknown): TaskStatus {
  const value = String(raw || "").trim().toUpperCase();
  if (value === "DONE" || value === "ACTIVE" || value === "TODO") return value;
  return "TODO";
}

function mirrorStatusToTaskStatus(raw: string | undefined): TaskStatus {
  const value = String(raw || "").trim().toLowerCase();
  if (value === "completed") return "DONE";
  if (value === "in_progress") return "ACTIVE";
  return "TODO";
}

function stepStatusToTaskStatus(step: Step): TaskStatus {
  const status = deriveSubtaskStatus(step);
  if (status === "completed") return "DONE";
  if (status === "in_progress") return "ACTIVE";
  return "TODO";
}

function nodeFilterStatus(node: TreeNode): TaskStatus {
  if (node.kind === "task") {
    return normalizeTaskStatus(node.task?.status_code || node.task?.status);
  }
  return stepStatusToTaskStatus(node.step!);
}

function nodeMatchesFilter(node: TreeNode, filter: TaskStatus): boolean {
  return nodeFilterStatus(node) === filter;
}

function computeNodeVisibilityMap(
  nodes: TreeNode[] | undefined,
  filter: "ALL" | TaskStatus
): Map<string, boolean> {
  const visibility = new Map<string, boolean>();
  if (!nodes || nodes.length === 0) return visibility;
  if (filter === "ALL") return visibility;

  const visit = (node: TreeNode): boolean => {
    let childMatches = false;
    for (const child of node.children) {
      if (visit(child)) childMatches = true;
    }
    const selfMatches = nodeMatchesFilter(node, filter);
    const matches = selfMatches || childMatches;
    visibility.set(node.path, matches);
    return matches;
  };

  for (const node of nodes) {
    visit(node);
  }
  return visibility;
}

function isNodeVisibleAtPath(
  path: string,
  node: TreeNode,
  filterMode: "ALL" | TaskStatus,
  visibilityMap?: Map<string, boolean>
): boolean {
  if (filterMode === "ALL") return true;
  const fromMap = visibilityMap?.get(path);
  if (typeof fromMap === "boolean") return fromMap;
  return nodeMatchesFilter(node, filterMode as TaskStatus);
}

function buildNodeStatsMap(nodes: TreeNode[]): Map<string, { done: number; total: number }> {
  const map = new Map<string, { done: number; total: number }>();

  const visit = (node: TreeNode): { done: number; total: number } => {
    let done = 0;
    let total = 0;
    if (node.kind === "step") {
      total += 1;
      if (node.step?.completed) done += 1;
    }
    for (const child of node.children) {
      const childStats = visit(child);
      done += childStats.done;
      total += childStats.total;
    }
    map.set(node.path, { done, total });
    return { done, total };
  };

  for (const node of nodes) {
    visit(node);
  }
  return map;
}

export function TaskDetailView({
  taskId,
  domain,
  namespace,
  subtaskPath,
  onSubtaskPathChange,
  onClose,
  onDelete,
  variant = "page",
}: TaskDetailModalProps) {
  const queryClient = useQueryClient();
  const [expandedDetailPaths, setExpandedDetailPaths] = useState<Set<string>>(new Set());
  const subtasksView = useSettingsStore((s) => s.subtasksViewMode);
  const setSubtasksView = useSettingsStore((s) => s.setSubtasksViewMode);
  const [cardsSelection, setCardsSelection] = useState<string | null>(null);
  const [tableSelectionPath, setTableSelectionPath] = useState<string | null>(null);
  const [subtasksFilter, setSubtasksFilter] = useState<"ALL" | TaskStatus>("ALL");
  const [checkpointDialog, setCheckpointDialog] = useState<{
    path: string;
    title: string;
    missing: Array<"criteria" | "tests">;
  } | null>(null);
  const [checkpointNotes, setCheckpointNotes] = useState<{ criteria: string; tests: string }>({
    criteria: "",
    tests: "",
  });
  const [isCompletingSubtask, setIsCompletingSubtask] = useState(false);
  const [taskDeleteOpen, setTaskDeleteOpen] = useState(false);
  const [isDeletingTask, setIsDeletingTask] = useState(false);
  const [isExportingHandoff, setIsExportingHandoff] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState<{ path: string; title: string; kind: "step" | "task" } | null>(null);
  const [isDeletingSubtask, setIsDeletingSubtask] = useState(false);
  const taskQueryKey = ["task", taskId, domain, namespace] as const;
  const isPanel = variant === "panel";

  const setDrilldownPath = (next: string | null) => {
    const nextPath = next || undefined;
    const current = subtaskPath || undefined;
    if (onSubtaskPathChange && nextPath !== current) {
      onSubtaskPathChange(nextPath);
    }
  };

  const setCardsPath = (next: string | null) => {
    setCardsSelection(next);
    setDrilldownPath(next);
  };

  useEffect(() => {
    setExpandedDetailPaths(new Set());
    setSubtasksFilter("ALL");
    setTableSelectionPath(null);
  }, [taskId]);

  useEffect(() => {
    const next = subtaskPath && subtaskPath.trim() ? subtaskPath.trim() : null;
    setCardsSelection((prev) => (prev === next ? prev : next));
  }, [subtaskPath]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (checkpointDialog || deleteDialog || taskDeleteOpen) return;
      e.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [checkpointDialog, deleteDialog, taskDeleteOpen, onClose]);

  const { data: task, isLoading, error } = useQuery({
    queryKey: taskQueryKey,
    queryFn: async () => {
      const response = await showTask(taskId!);
      if (!response.success || !response.task) {
        throw new Error(response.error || "Step not found");
      }
      return response.task;
    },
    enabled: !!taskId,
    // Auto-expand paths when task loads - handled via side effect in onSuccess if we want,
    // or just let state initialization handle it (which resets only on mount).
    // For now, we keep manual expansion state separate.
  });

  const handleExportHandoff = useCallback(async () => {
    if (!task?.id) return;
    setIsExportingHandoff(true);
    try {
      const resp = await getHandoff({ taskId: task.id });
      if (!resp.success || !resp.data) {
        throw new Error(resp.error || "Failed to export handoff");
      }
      const blob = new Blob([JSON.stringify(resp.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `apply-task-handoff-${task.id}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      toast.success("Handoff exported");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to export handoff";
      toast.error(message);
    } finally {
      setIsExportingHandoff(false);
    }
  }, [task?.id]);

  const treeNodes = useMemo(() => buildTreeNodes(task?.steps ?? []), [task?.steps]);
  const { nodeMap, parentMap } = useMemo(() => buildNodeIndex(treeNodes), [treeNodes]);

  const subtaskVisibility = useMemo(
    () => computeNodeVisibilityMap(treeNodes, subtasksFilter),
    [treeNodes, subtasksFilter]
  );

  const mirrorQueryKey = useMemo(() => ["mirror", taskId, subtaskPath] as const, [taskId, subtaskPath]);
  const mirrorQuery = useQuery({
    queryKey: mirrorQueryKey,
    queryFn: async () => {
      const resp = await mirrorList({
        task: taskId || undefined,
        path: subtaskPath && subtaskPath.trim().length > 0 ? subtaskPath.trim() : undefined,
      });
      if (!resp.success || !resp.data) {
        throw new Error(resp.error || "Failed to load steps");
      }
      return resp.data;
    },
    enabled: !!taskId,
  });

  const mirrorItems = useMemo(() => mirrorQuery.data?.items ?? [], [mirrorQuery.data]);
  const filteredMirrorItems = useMemo(() => {
    if (subtasksFilter === "ALL") return mirrorItems;
    return mirrorItems.filter((item) => mirrorStatusToTaskStatus(item.status) === subtasksFilter);
  }, [mirrorItems, subtasksFilter]);

  const radarQueryKey = useMemo(() => ["radar", taskId] as const, [taskId]);
  const radarQuery = useQuery({
    queryKey: radarQueryKey,
    queryFn: async () => {
      const resp = await getRadar({ taskId: taskId! });
      if (!resp.success || !resp.data) {
        throw new Error(resp.error || "Failed to load radar");
      }
      return resp.data;
    },
    enabled: !!taskId,
  });

  const runRadarNextMutation = useMutation({
    mutationFn: async (next: Suggestion) => {
      const resp = await runValidatedSuggestion(next);
      if (!resp.success) throw new Error(resp.error || "Failed to execute suggestion");
      return resp.response;
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to execute next step");
    },
    onSuccess: () => {
      toast.success("Next step executed");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: radarQueryKey });
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: mirrorQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  const closeTaskMutation = useMutation({
    mutationFn: async ({ taskId: id, expectedRevision }: { taskId: string; expectedRevision?: number }) => {
      const resp = await apiCloseTask({ taskId: id, expectedRevision });
      if (!resp.success) throw new Error(resp.error || "Failed to close task");
      return resp.result;
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to close task");
    },
    onSuccess: () => {
      toast.success("Task closed (DONE)");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: radarQueryKey });
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: mirrorQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  const radarNext = useMemo(() => radarQuery.data?.next?.[0], [radarQuery.data]);
  const runwayKnownClosed = useMemo(() => (radarQuery.data ? radarQuery.data.runway?.open === false : false), [radarQuery.data]);
  const radarNextIsOneCallClose = useMemo(() => {
    if (!radarNext) return false;
    if (!radarNext.validated) return false;
    if (radarNext.action !== "close_task") return false;
    const params = (radarNext.params ?? {}) as Record<string, unknown>;
    return Boolean(params.apply);
  }, [radarNext]);

  const handleRunRadarNext = useCallback(
    (next: Suggestion) => {
      runRadarNextMutation.mutate(next);
    },
    [runRadarNextMutation]
  );

  const handleCopyRadarNext = useCallback(async (next: Suggestion) => {
    try {
      const payload = { intent: next.action, ...(next.params ?? {}) };
      await navigator.clipboard.writeText(JSON.stringify(payload));
      toast.success("Copied");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to copy");
    }
  }, []);
  const listLabelForPath = useCallback((pathValue: string | null | undefined) => {
    const path = (pathValue || "").trim();
    if (!path) return "Steps";
    return isTaskPath(path) ? "Steps" : "Tasks";
  }, []);
  const tableListLabel = useMemo(() => listLabelForPath(subtaskPath), [listLabelForPath, subtaskPath]);
  const cardsListLabel = useMemo(() => listLabelForPath(cardsSelection), [cardsSelection, listLabelForPath]);
  const currentItemLabel = tableListLabel === "Tasks" ? "Task" : "Step";
  const currentEmptyLabel = tableListLabel === "Tasks" ? "No nested tasks yet." : "No nested steps yet.";

  useEffect(() => {
    setTableSelectionPath(null);
  }, [subtaskPath]);

  const tableItemIds = useMemo(
    () => filteredMirrorItems.map((item) => item.path || "").filter((path) => path.length > 0),
    [filteredMirrorItems]
  );

  useEffect(() => {
    if (subtasksView !== "table") return;
    if (tableItemIds.length === 0) {
      if (tableSelectionPath !== null) setTableSelectionPath(null);
      return;
    }
    if (!tableSelectionPath || !tableItemIds.includes(tableSelectionPath)) {
      setTableSelectionPath(tableItemIds[0] ?? null);
    }
  }, [subtasksView, tableItemIds, tableSelectionPath]);

  useEffect(() => {
    if (subtasksView !== "table") return;
    if (!tableSelectionPath) {
      setExpandedDetailPaths(new Set());
      return;
    }
    setExpandedDetailPaths(new Set([tableSelectionPath]));
  }, [subtasksView, tableSelectionPath]);

  useKeyboardListNavigation({
    enabled: subtasksView === "table" && tableItemIds.length > 0,
    itemIds: tableItemIds,
    activeId: tableSelectionPath,
    onActiveChange: setTableSelectionPath,
    onActivate: (path) => setDrilldownPath(path),
  });

  useEffect(() => {
    if (subtasksView !== "table") return;
    if (!tableSelectionPath) return;
    const el = document.querySelector<HTMLElement>(`[data-subtask-row=\"${tableSelectionPath}\"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [subtasksView, tableSelectionPath]);

  const updateStatusMutation = useMutation({
    mutationFn: async ({ taskId, status }: { taskId: string; status: TaskStatus }) => {
      const response = await apiUpdateTaskStatus(taskId, status);
      if (!response.success) throw new Error(response.error);
      return response;
    },
    onMutate: async ({ status }) => {
      await queryClient.cancelQueries({ queryKey: taskQueryKey });
      const previousTask = queryClient.getQueryData<Task>(taskQueryKey as unknown as readonly unknown[]);

      if (previousTask) {
        queryClient.setQueryData<Task>(taskQueryKey as unknown as readonly unknown[], {
          ...previousTask,
          status,
        });
      }
      // Also update list view if present
      await queryClient.cancelQueries({ queryKey: ["tasks"] });
      // We can't easily update list view without scanning, but invalidation will handle it.

      return { previousTask };
    },
    onError: (err, _variables, context) => {
      if (context?.previousTask) {
        queryClient.setQueryData(taskQueryKey as unknown as readonly unknown[], context.previousTask);
      }
      toast.error(err instanceof Error ? err.message : "Failed to update status");
    },
    onSuccess: () => {
      toast.success("Status updated");
    },
    onSettled: (_data, _error, variables) => {
      void variables;
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  function getStepByPath(path: string): Step | null {
    const node = nodeMap.get(path);
    if (!node || node.kind !== "step") return null;
    return node.step ?? null;
  }

  function areStepChildrenDone(step: Step): boolean {
    const planTasks = step.plan?.tasks ?? [];
    if (planTasks.length === 0) return true;
    if (planTasks.length > 0) {
      return planTasks.every((taskNode) => {
        if (taskNode.blocked) return false;
        if (taskNode.status_manual) {
          return normalizeTaskStatus(taskNode.status_code || taskNode.status) === "DONE";
        }
        const steps = taskNode.steps ?? [];
        if (steps.length === 0) return false;
        return steps.every((child) => child.completed && areStepChildrenDone(child));
      });
    }
    return false;
  }

  function computeMissingCheckpoints(step: Step): Array<"criteria" | "tests" | "children"> {
    const missing: Array<"criteria" | "tests" | "children"> = [];
    if (step.success_criteria?.length && !step.criteria_confirmed) {
      missing.push("criteria");
    }
    const testsOk =
      step.tests_confirmed ||
      step.tests_auto_confirmed ||
      !(step.tests && step.tests.length > 0);
    if (!testsOk) {
      missing.push("tests");
    }
    const childrenOk = areStepChildrenDone(step);
    if (!childrenOk) missing.push("children");
    return missing;
  }

  const handleConfirmAndComplete = async () => {
    if (!task || !checkpointDialog) return;
    setIsCompletingSubtask(true);
    try {
      const criteriaNote = checkpointDialog.missing.includes("criteria") ? checkpointNotes.criteria : undefined;
      const testsNote = checkpointDialog.missing.includes("tests") ? checkpointNotes.tests : undefined;

      if (criteriaNote !== undefined || testsNote !== undefined) {
        const verifyResp = await verifyStep({
          taskId: task.id,
          path: checkpointDialog.path,
          criteriaNote,
          testsNote,
        });
        if (!verifyResp.success) {
          throw new Error(verifyResp.error || "Failed to verify checkpoints");
        }
      }

      const doneResp = await setStepCompleted({ taskId: task.id, path: checkpointDialog.path, completed: true });
      if (!doneResp.success) throw new Error(doneResp.error || "Failed to complete step");
      toast.success("Step completed");
      setCheckpointDialog(null);
      setCheckpointNotes({ criteria: "", tests: "" });
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to complete step");
    } finally {
      setIsCompletingSubtask(false);
    }
  };

  const handleCheckpointConfirm = async (
    path: string,
    checkpoint: "criteria" | "tests",
    note: string
  ) => {
    if (!task) return;
    if (isTaskPath(path)) {
      toast.warning("Checkpoints are available only for steps");
      return;
    }
    try {
      const resp = await verifyStep({
        taskId: task.id,
        path,
        criteriaNote: checkpoint === "criteria" ? note : undefined,
        testsNote: checkpoint === "tests" ? note : undefined,
      });
      if (!resp.success) {
        throw new Error(resp.error || "Failed to confirm checkpoint");
      }
      toast.success("Checkpoint confirmed");
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to confirm checkpoint");
    }
  };

  const handleSubtaskDefine = async (
    path: string,
    updates: { title?: string; criteria?: string[]; tests?: string[]; blockers?: string[] }
  ) => {
    if (!task) return;
    try {
      if (isTaskPath(path)) {
        const resp = await updateTaskNode({
          taskId: task.id,
          path,
          title: updates.title,
        });
        if (!resp.success) {
          throw new Error(resp.error || "Failed to update task");
        }
        toast.success("Task updated");
      } else {
        const resp = await defineStep({
          taskId: task.id,
          path,
          title: updates.title,
          successCriteria: updates.criteria,
          tests: updates.tests,
          blockers: updates.blockers,
        });
        if (!resp.success) {
          throw new Error(resp.error || "Failed to update step");
        }
        toast.success("Step updated");
      }
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    }
  };

  const handleAddProgressNote = async (path: string, note: string): Promise<boolean> => {
    if (!task) return false;
    if (isTaskPath(path)) {
      toast.warning("Progress notes are available only for steps");
      return false;
    }
    const trimmed = String(note ?? "").trim();
    if (!trimmed) return false;
    try {
      const resp = await addStepNote({ taskId: task.id, path, note: trimmed });
      if (!resp.success) {
        throw new Error(resp.error || "Failed to add progress note");
      }
      toast.success("Note added");
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add progress note");
      return false;
    }
  };

  const handleSetSubtaskBlocked = async (
    path: string,
    blocked: boolean,
    reason?: string
  ): Promise<boolean> => {
    if (!task) return false;
    if (isTaskPath(path)) {
      toast.warning("Blocking applies to steps only");
      return false;
    }
    try {
      const resp = await setStepBlocked({
        taskId: task.id,
        path,
        blocked,
        reason: reason?.trim() || undefined,
      });
      if (!resp.success) {
        throw new Error(resp.error || "Failed to update block status");
      }
      toast.success(blocked ? "Step blocked" : "Step unblocked");
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update block status");
      return false;
    }
  };

  const requestDeleteSubtask = (path: string, title: string, kind: "step" | "task") => {
    setDeleteDialog({ path, title, kind });
  };

  const handleConfirmDeleteSubtask = async () => {
    if (!task || !deleteDialog) return;
    setIsDeletingSubtask(true);
    try {
      if (deleteDialog.kind === "task") {
        const resp = await deleteTaskNode({ taskId: task.id, path: deleteDialog.path });
        if (!resp.success) {
          throw new Error(resp.error || "Failed to delete task");
        }
        toast.success("Task deleted");
      } else {
        const resp = await deleteStep({ taskId: task.id, path: deleteDialog.path });
        if (!resp.success) {
          throw new Error(resp.error || "Failed to delete step");
        }
        toast.success("Step deleted");
      }
      setDeleteDialog(null);
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
    } finally {
      setIsDeletingSubtask(false);
    }
  };

  // Optimistic step toggle (with checkpoint gating)
  const handleSubtaskToggle = async (path: string, completed: boolean) => {
    if (!task) return;
    if (isTaskPath(path)) {
      toast.warning("Task status is derived from steps");
      return;
    }

    if (completed) {
      const st = getStepByPath(path);
      if (!st) {
        toast.error("Step not found");
        return;
      }
      const missing = computeMissingCheckpoints(st);
      if (missing.includes("children")) {
        toast.warning("Complete child steps first");
        return;
      }
      const checkpointsMissing = missing.filter((m) => m !== "children") as Array<"criteria" | "tests">;
      if (checkpointsMissing.length > 0) {
        setCheckpointNotes({ criteria: "", tests: "" });
        setCheckpointDialog({ path, title: st.title, missing: checkpointsMissing });
        return;
      }
    }

    const previousTask = queryClient.getQueryData<Task>(taskQueryKey as unknown as readonly unknown[]);

    queryClient.setQueryData<Task>(taskQueryKey as unknown as readonly unknown[], (old) => {
      if (!old) return old;
      const updated = JSON.parse(JSON.stringify(old)) as Task;
      const segments = parseTreePath(path);
      if (segments.length === 0 || segments[segments.length - 1].kind !== "s") {
        return updated;
      }
      let currentSteps: Step[] = updated.steps ?? [];
      let currentStep: Step | undefined;
      for (let i = 0; i < segments.length; i++) {
        const seg = segments[i];
        const isLast = i === segments.length - 1;
        if (seg.kind === "s") {
          currentStep = currentSteps[seg.index];
          if (!currentStep) return updated;
          if (isLast) {
            currentStep.completed = completed;
            return updated;
          }
        } else {
          const planTasks = currentStep?.plan?.tasks ?? [];
          const taskNode = planTasks[seg.index];
          if (!taskNode) return updated;
          currentSteps = taskNode.steps ?? [];
          currentStep = undefined;
        }
      }
      return updated;
    });

    try {
      const response = await setStepCompleted({ taskId: task.id, path, completed });
      if (!response.success) {
        throw new Error(response.error || "Failed to update step");
      }
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      if (previousTask) {
        queryClient.setQueryData(taskQueryKey as unknown as readonly unknown[], previousTask);
      }
      toast.error(err instanceof Error ? err.message : "Failed to update step");
    }
  };

  const handleStatusChange = (status: TaskStatus) => {
    if (!task) return;
    if (status !== "DONE") {
      updateStatusMutation.mutate({ taskId: task.id, status });
      return;
    }

    if (runwayKnownClosed && !radarNextIsOneCallClose) {
      toast.error("Runway closed — fix via Radar first");
      return;
    }
    if (radarNextIsOneCallClose && radarNext) {
      runRadarNextMutation.mutate(radarNext);
      return;
    }
    closeTaskMutation.mutate({ taskId: task.id, expectedRevision: task.revision });
  };

  const requestDeleteTask = () => {
    if (!task) return;
    setTaskDeleteOpen(true);
  };

  const handleConfirmDeleteTask = async () => {
    if (!task) return;
    setIsDeletingTask(true);
    try {
      const resp = await apiDeleteTask(task.id);
      if (!resp.success) {
        throw new Error(resp.error || "Failed to delete task");
      }
      toast.success("Task deleted");
      onDelete?.(task.id);
      setTaskDeleteOpen(false);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete task");
    } finally {
      setIsDeletingTask(false);
    }
  };

  const handleToggleDetails = (path: string) => {
    setExpandedDetailPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const openTableAtPath = (path: string) => {
    setSubtasksView("table");
    if (!path) return;
    setDrilldownPath(path);
  };

  // Don't render if no taskId
  if (!taskId) return null;

  if (isLoading) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-background">
        <div
          className={cn(
            "flex items-center gap-3 border-b border-border bg-background px-[var(--density-page-pad)]",
            isPanel ? "py-2" : "py-3"
          )}
        >
          <Button
            variant={isPanel ? "ghost" : "outline"}
            size="icon"
            onClick={onClose}
            className={cn(isPanel ? "h-8 w-8" : "h-9 w-9")}
            aria-label={isPanel ? "Close" : "Back"}
          >
            {isPanel ? <X className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
          <div className="text-sm font-medium text-foreground-muted">
            Loading…
          </div>
        </div>

        <div className="flex flex-1 items-center justify-center">
          <div className="flex flex-col items-center gap-3 p-[var(--density-page-pad)] text-center">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-foreground-muted">Loading task…</p>
          </div>
        </div>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-background">
        <div
          className={cn(
            "flex items-center gap-3 border-b border-border bg-background px-[var(--density-page-pad)]",
            isPanel ? "py-2" : "py-3"
          )}
        >
          <Button
            variant={isPanel ? "ghost" : "outline"}
            size="icon"
            onClick={onClose}
            className={cn(isPanel ? "h-8 w-8" : "h-9 w-9")}
            aria-label={isPanel ? "Close" : "Back"}
          >
            {isPanel ? <X className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
          <div className="text-sm font-medium text-foreground-muted">
            Task
          </div>
        </div>

        <div className="flex flex-1 items-center justify-center p-[var(--density-page-pad)]">
          <div className="flex flex-col items-center gap-3 text-center">
            <AlertCircle className="h-8 w-8 text-status-fail" />
            <p className="text-sm text-status-fail">
              {(error as Error)?.message || "Task not found"}
            </p>
            <Button variant="outline" onClick={onClose}>
              {isPanel ? "Close" : "Back"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const StatusIcon = TASK_STATUS_ICON[task.status];

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <Dialog
        open={!!checkpointDialog}
        onOpenChange={(open) => {
          if (open) return;
          if (isCompletingSubtask) return;
          setCheckpointDialog(null);
        }}
      >
        <DialogContent className="max-w-[560px]" hideClose={isCompletingSubtask}>
          <DialogHeader>
            <DialogTitle>Complete step</DialogTitle>
            {checkpointDialog?.title && (
              <div className="text-sm text-foreground-muted">
                {checkpointDialog.title}
              </div>
            )}
          </DialogHeader>

          <div className="space-y-3 px-[var(--density-page-pad)] pb-[var(--density-page-pad)]">
            <p className="text-sm text-foreground-muted">
              This step requires checkpoint confirmations:
            </p>

            {checkpointDialog?.missing.map((cp) => (
              <div
                key={cp}
                className="space-y-2 rounded-lg border border-border bg-background p-3"
              >
                <div className="text-sm font-semibold">
                  {cp === "criteria" ? "Criteria" : "Tests"}
                </div>
                <Textarea
                  value={checkpointNotes[cp]}
                  onChange={(e) =>
                    setCheckpointNotes((prev) => ({
                      ...prev,
                      [cp]: e.target.value,
                    }))
                  }
                  placeholder="Evidence / note..."
                  rows={2}
                  disabled={isCompletingSubtask}
                />
              </div>
            ))}
          </div>

          <DialogFooter className="sm:flex-row sm:justify-end">
            <Button
              variant="outline"
              onClick={() => setCheckpointDialog(null)}
              disabled={isCompletingSubtask}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                void handleConfirmAndComplete();
              }}
              disabled={isCompletingSubtask}
            >
              {isCompletingSubtask ? "Completing..." : "Confirm & complete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {deleteDialog && (
        <ConfirmDialog
          isOpen
          title={`Delete ${deleteDialog.kind} "${deleteDialog.title}"?`}
          description={
            deleteDialog.kind === "task"
              ? "This will remove the task and all nested steps. This action cannot be undone."
              : "This will remove the step and all nested children. This action cannot be undone."
          }
          confirmLabel="Delete"
          cancelLabel="Cancel"
          danger
          isLoading={isDeletingSubtask}
          onCancel={() => setDeleteDialog(null)}
          onConfirm={() => {
            void handleConfirmDeleteSubtask();
          }}
        />
      )}

      <div
        className={cn(
          "border-b border-border bg-background px-[var(--density-page-pad)]",
          isPanel ? "py-2" : "py-3"
        )}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <Button
              variant={isPanel ? "ghost" : "outline"}
              size="icon"
              onClick={onClose}
              className={cn(isPanel ? "h-8 w-8" : "h-9 w-9")}
              aria-label={isPanel ? "Close" : "Back"}
            >
              {isPanel ? <X className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </Button>

            <div className="min-w-0 flex-1 space-y-2 text-left">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-background-muted px-2 py-0.5 font-mono text-[11px] text-foreground-muted">
                  {task.id}
                </span>
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
                    TASK_STATUS_UI[task.status].classes.bg,
                    TASK_STATUS_UI[task.status].classes.text
                  )}
                >
                  <StatusIcon className="h-3.5 w-3.5" />
                  {TASK_STATUS_UI[task.status].label}
                </span>
              </div>
              <h1 className="pr-10 text-left text-lg font-semibold leading-snug tracking-tight">
                {task.title}
              </h1>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleExportHandoff}
              disabled={isExportingHandoff}
              aria-label="Export handoff"
            >
              <NotesIcon className="mr-2 h-4 w-4" />
              {isExportingHandoff ? "Exporting..." : "Handoff"}
            </Button>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="custom-scrollbar flex-1 overflow-y-auto px-[var(--density-page-pad)] pb-[var(--density-page-pad)] pt-3">
        <TaskRadarSection
          radar={radarQuery.data as RadarData | undefined}
          isLoading={radarQuery.isLoading}
          error={radarQuery.error instanceof Error ? radarQuery.error.message : radarQuery.error ? String(radarQuery.error) : undefined}
          isRunning={runRadarNextMutation.isPending || closeTaskMutation.isPending}
          onRunNext={handleRunRadarNext}
          onCopyNext={handleCopyRadarNext}
          onRefresh={() => {
            void radarQuery.refetch();
          }}
        />
        <TaskNotesSection task={task} />
        <TaskMetaSection task={task} />

        {/* Plan */}
        <TaskContractSection task={task} />
        <TaskPlanSection task={task} />

        {/* Steps */}
        {task.steps && task.steps.length > 0 && (
          <section className="mb-6">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground-muted">
                <ListTodo className="h-4 w-4" />
                <span>{tableListLabel} ({mirrorItems.length})</span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <div className="inline-flex rounded-lg border border-border bg-background-subtle p-1">
                  {(["ALL", "TODO", "ACTIVE", "DONE"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setSubtasksFilter(mode)}
                      className={cn(
                        "rounded-md px-2.5 py-1 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                        subtasksFilter === mode
                          ? "bg-background text-foreground"
                          : "text-foreground-muted hover:bg-background-hover"
                      )}
                      aria-pressed={subtasksFilter === mode}
                    >
                      {mode === "ALL" ? "All" : mode}
                    </button>
                  ))}
                </div>

                <div className="inline-flex rounded-lg border border-border bg-background-subtle p-1">
                  <button
                    type="button"
                    onClick={() => openTableAtPath(cardsSelection || "")}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      subtasksView === "table"
                        ? "bg-background text-foreground"
                        : "text-foreground-muted hover:bg-background-hover"
                    )}
                    aria-pressed={subtasksView === "table"}
                  >
                    Table
                  </button>
                  <button
                    type="button"
                    onClick={() => setSubtasksView("cards")}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      subtasksView === "cards"
                        ? "bg-background text-foreground"
                        : "text-foreground-muted hover:bg-background-hover"
                    )}
                    aria-pressed={subtasksView === "cards"}
                  >
                    Cards
                  </button>
                </div>
              </div>
            </div>

            {subtasksView === "table" ? (
              <>
                <SubtaskTableView
                  items={filteredMirrorItems}
                  currentPath={subtaskPath}
                  selectionPath={tableSelectionPath}
                  onSelectionChange={setTableSelectionPath}
                  onOpen={setDrilldownPath}
                  onGoUp={(path) => setDrilldownPath(path)}
                  onToggleComplete={handleSubtaskToggle}
                  nodeMap={nodeMap}
                  parentMap={parentMap}
                  listLabel={tableListLabel}
                  itemLabel={currentItemLabel}
                  emptyLabel={currentEmptyLabel}
                />
                {tableSelectionPath && nodeMap.get(tableSelectionPath) && (
                  <div className="mt-4">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                      Details
                    </div>
                    <SubtaskItem
                      node={nodeMap.get(tableSelectionPath)!}
                      depth={0}
                      expandedChildPaths={new Set()}
                      expandedDetailPaths={expandedDetailPaths}
                      activePath={tableSelectionPath}
                      onToggleChildren={() => {}}
                      onToggleDetails={handleToggleDetails}
                      onToggleComplete={handleSubtaskToggle}
                      onConfirmCheckpoint={handleCheckpointConfirm}
                      onAddProgressNote={handleAddProgressNote}
                      onSetBlocked={handleSetSubtaskBlocked}
                      onDefineSubtask={handleSubtaskDefine}
                      onRequestDelete={requestDeleteSubtask}
                      onOpen={(path) => setTableSelectionPath(path)}
                      filterMode="ALL"
                      drilldown
                    />
                  </div>
                )}
              </>
            ) : (
              <SubtaskCardsView
                nodes={treeNodes}
                selectionPath={cardsSelection}
                onSelectionChange={setCardsPath}
                onToggleComplete={handleSubtaskToggle}
                onOpenInTable={openTableAtPath}
                filterMode={subtasksFilter}
                visibilityMap={subtaskVisibility}
                nodeMap={nodeMap}
                parentMap={parentMap}
                listLabel={cardsListLabel}
              />
            )}
          </section>
        )}
      </div>

      <div className="border-t border-border bg-background px-[var(--density-page-pad)] py-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg border border-border bg-background-subtle p-1">
              {(["TODO", "ACTIVE", "DONE"] as TaskStatus[]).map((status) => {
                const isSelected = task.status === status;
                const ui = TASK_STATUS_UI[status];
                const disableDone = status === "DONE" && runwayKnownClosed && !radarNextIsOneCallClose && task.status !== "DONE";
                const isBusy = updateStatusMutation.isPending || runRadarNextMutation.isPending || closeTaskMutation.isPending;
                const disabled = isBusy || disableDone;

                return (
                  <button
                    key={status}
                    type="button"
                    onClick={() => handleStatusChange(status)}
                    disabled={disabled}
                    title={disableDone ? "Runway closed — fix via Radar first" : undefined}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      isSelected
                        ? cn(ui.classes.bg, ui.classes.text)
                        : "text-foreground-muted hover:bg-background-hover",
                      disabled && "cursor-not-allowed opacity-50 hover:bg-transparent"
                    )}
                  >
                    {ui.label}
                    {isSelected && <Check className="h-3.5 w-3.5" />}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="destructive" onClick={requestDeleteTask}>
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </Button>
            {!isPanel && (
              <Button variant="outline" onClick={onClose}>
                Back
              </Button>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        isOpen={taskDeleteOpen}
        title={`Delete task "${task.title}"?`}
        description="This will permanently remove the task and all its nested steps."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        danger
        isLoading={isDeletingTask}
        onCancel={() => {
          if (isDeletingTask) return;
          setTaskDeleteOpen(false);
        }}
        onConfirm={() => {
          void handleConfirmDeleteTask();
        }}
      />
    </div>
  );
}

export const TaskDetailModal = TaskDetailView;

interface SubtaskCardsViewProps {
  nodes: TreeNode[];
  selectionPath: string | null;
  onSelectionChange: (next: string | null) => void;
  onToggleComplete: (path: string, completed: boolean) => void;
  onOpenInTable: (path: string) => void;
  filterMode: "ALL" | TaskStatus;
  visibilityMap: Map<string, boolean>;
  nodeMap: Map<string, TreeNode>;
  parentMap: Map<string, string | null>;
  listLabel: string;
}

function deriveSubtaskStatus(subtask: Step): StepStatus {
  if (subtask.computed_status) return subtask.computed_status;
  if (subtask.completed) return "completed";
  if (subtask.blocked) return "blocked";
  if (subtask.started_at) return "in_progress";
  return "pending";
}

function SubtaskCardsView({
  nodes,
  selectionPath,
  onSelectionChange,
  onToggleComplete,
  onOpenInTable,
  filterMode,
  visibilityMap,
  nodeMap,
  parentMap,
  listLabel,
}: SubtaskCardsViewProps) {
  const [activeChildPath, setActiveChildPath] = useState<string | null>(null);

  const statsByPath = useMemo(() => buildNodeStatsMap(nodes), [nodes]);

  const normalizedSelection = useMemo(() => {
    let current = selectionPath && selectionPath.trim() ? selectionPath.trim() : "";
    if (!current) return "";
    while (current && !nodeMap.has(current)) {
      current = parentMap.get(current) ?? "";
    }
    return current;
  }, [selectionPath, nodeMap, parentMap]);

  useEffect(() => {
    const next = normalizedSelection || null;
    if (selectionPath === next) return;
    onSelectionChange(next);
  }, [normalizedSelection, selectionPath, onSelectionChange]);

  const focusPath = normalizedSelection;
  const focusNode = focusPath ? nodeMap.get(focusPath) : undefined;
  const focusChildren = focusNode ? focusNode.children : nodes;

  const crumbs = useMemo(() => {
    if (!focusPath) return [] as TreeNode[];
    const chain: TreeNode[] = [];
    let current: string | null = focusPath;
    while (current) {
      const node = nodeMap.get(current);
      if (!node) break;
      chain.push(node);
      current = parentMap.get(current) ?? null;
    }
    return chain.reverse();
  }, [focusPath, nodeMap, parentMap]);

  const focusStats = focusPath ? statsByPath.get(focusPath) : undefined;
  const focusProgressPct =
    focusStats && focusStats.total > 0
      ? Math.round((focusStats.done / focusStats.total) * 100)
      : focusNode?.kind === "step" && focusNode.step?.completed
        ? 100
        : 0;

  const visibleChildPaths = useMemo(() => {
    if (focusChildren.length === 0) return [] as string[];
    if (filterMode === "ALL") return focusChildren.map((child) => child.path);
    return focusChildren
      .filter((child) => isNodeVisibleAtPath(child.path, child, filterMode, visibilityMap))
      .map((child) => child.path);
  }, [filterMode, focusChildren, visibilityMap]);

  const effectiveActiveChildPath = useMemo(() => {
    if (visibleChildPaths.length === 0) return null;
    if (activeChildPath && visibleChildPaths.includes(activeChildPath)) return activeChildPath;
    return visibleChildPaths[0];
  }, [activeChildPath, visibleChildPaths]);

  useKeyboardListNavigation({
    enabled: visibleChildPaths.length > 0,
    itemIds: visibleChildPaths,
    activeId: effectiveActiveChildPath,
    onActiveChange: setActiveChildPath,
    onActivate: (path) => {
      onSelectionChange(path);
    },
  });

  useEffect(() => {
    if (!effectiveActiveChildPath) return;
    const el = document.querySelector<HTMLElement>(`[data-subtask-path="${effectiveActiveChildPath}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [effectiveActiveChildPath]);

  const hasVisibleChildren = visibleChildPaths.length > 0;
  const canGoUp = !!focusPath;
  const parentPath = canGoUp ? parentMap.get(focusPath) ?? null : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-background-subtle px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          {canGoUp && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8"
              onClick={() => onSelectionChange(parentPath)}
            >
              <ChevronLeft className="mr-1 h-4 w-4" />
              Up
            </Button>
          )}

          <nav
            aria-label={`${listLabel} breadcrumb`}
            className="flex min-w-0 flex-wrap items-center gap-1 text-sm"
          >
            <button
              type="button"
              onClick={() => onSelectionChange(null)}
              className={cn(
                "rounded-md px-2 py-1 text-xs font-semibold transition-colors",
                !focusPath
                  ? "bg-background text-foreground"
                  : "text-foreground-muted hover:bg-background-hover hover:text-foreground"
              )}
            >
              {listLabel}
            </button>
            {crumbs.map((crumb) => {
              const isCurrent = crumb.path === focusPath;
              const title = crumb.kind === "task" ? crumb.task?.title ?? "" : crumb.step?.title ?? "";
              return (
                <span key={crumb.path} className="inline-flex min-w-0 items-center gap-1">
                  <ChevronRight className="h-3 w-3 text-foreground-subtle" />
                  <button
                    type="button"
                    onClick={() => onSelectionChange(crumb.path)}
                    className={cn(
                      "min-w-0 max-w-[260px] truncate rounded-md px-2 py-1 text-xs font-semibold transition-colors",
                      isCurrent
                        ? "bg-background text-foreground"
                        : "text-foreground-muted hover:bg-background-hover hover:text-foreground"
                    )}
                    title={title}
                  >
                    {title || "Untitled"}
                  </button>
                </span>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => {
              if (focusPath) onOpenInTable(focusPath);
            }}
          >
            Table
          </Button>
        </div>
      </div>

      {focusNode && (
        <div className="rounded-xl border border-border bg-card p-[var(--density-card-pad)]">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                {focusNode.kind === "step" ? (
                  <StepStatusBadge status={deriveSubtaskStatus(focusNode.step!)} size="sm" />
                ) : (
                  (() => {
                    const status = nodeFilterStatus(focusNode);
                    const ui = TASK_STATUS_UI[status];
                    return (
                      <Badge className={cn("text-[10px]", ui.classes.bg, ui.classes.text)}>
                        {ui.label}
                      </Badge>
                    );
                  })()
                )}
                {focusNode.children.length > 0 && (
                  <span className="text-xs text-foreground-muted">
                    {focusNode.children.length} nested
                  </span>
                )}
              </div>
              <div
                className={cn(
                  "mt-1 truncate text-sm font-semibold",
                  focusNode.kind === "step" && focusNode.step?.completed
                    ? "text-foreground-muted line-through"
                    : "text-foreground"
                )}
                title={focusNode.kind === "task" ? focusNode.task?.title : focusNode.step?.title}
              >
                {focusNode.kind === "task" ? focusNode.task?.title : focusNode.step?.title}
              </div>
            </div>

            {focusNode.kind === "step" && (
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "h-8",
                  focusNode.step?.completed
                    ? "bg-status-ok/10 text-status-ok hover:bg-status-ok/20"
                    : "text-foreground-muted hover:text-foreground"
                )}
                onClick={() => {
                  void onToggleComplete(focusPath, !focusNode.step?.completed);
                }}
              >
                {focusNode.step?.completed ? "Mark incomplete" : "Mark complete"}
              </Button>
            )}
          </div>

          <div className="mt-3 flex items-center gap-3">
            <ProgressBar value={focusProgressPct} size="sm" />
            <span className="text-xs font-semibold tabular-nums text-foreground-muted">
              {focusStats ? `${focusStats.done}/${focusStats.total}` : "0/0"}
            </span>
          </div>
        </div>
      )}

      {hasVisibleChildren ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visibleChildPaths.map((childPath) => {
            const node = nodeMap.get(childPath);
            if (!node) return null;
            const stats = statsByPath.get(childPath) ?? { done: 0, total: 0 };
            return (
              <div key={childPath} data-subtask-path={childPath}>
                <SubtaskCard
                  node={node}
                  stats={stats}
                  isSelected={effectiveActiveChildPath === childPath}
                  onOpen={() => onSelectionChange(childPath)}
                  onToggleComplete={() => {
                    if (node.kind === "step" && node.step) {
                      void onToggleComplete(childPath, !node.step.completed);
                    }
                  }}
                />
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-foreground-muted">
          {(() => {
            const label = focusNode && focusNode.kind === "step" ? "tasks" : "steps";
            return focusNode ? `No nested ${label} yet.` : `No ${label} yet.`;
          })()}
        </div>
      )}
    </div>
  );
}

interface SubtaskTableViewProps {
  items: MirrorItem[];
  currentPath: string | null | undefined;
  selectionPath: string | null;
  onSelectionChange: (next: string | null) => void;
  onOpen: (path: string) => void;
  onGoUp: (path: string | null) => void;
  onToggleComplete: (path: string, completed: boolean) => void;
  nodeMap: Map<string, TreeNode>;
  parentMap: Map<string, string | null>;
  listLabel: string;
  itemLabel: string;
  emptyLabel: string;
}

function SubtaskTableView({
  items,
  currentPath,
  selectionPath,
  onSelectionChange,
  onOpen,
  onGoUp,
  onToggleComplete,
  nodeMap,
  parentMap,
  listLabel,
  itemLabel,
  emptyLabel,
}: SubtaskTableViewProps) {
  const crumbs = useMemo(() => {
    if (!currentPath) return [] as TreeNode[];
    const chain: TreeNode[] = [];
    let current: string | null = currentPath;
    while (current) {
      const node = nodeMap.get(current);
      if (!node) break;
      chain.push(node);
      current = parentMap.get(current) ?? null;
    }
    return chain.reverse();
  }, [currentPath, nodeMap, parentMap]);

  const parentPath = currentPath ? parentMap.get(currentPath) ?? null : null;

  const empty = items.length === 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-background-subtle px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          {currentPath && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8"
              onClick={() => onGoUp(parentPath)}
            >
              <ChevronLeft className="mr-1 h-4 w-4" />
              Up
            </Button>
          )}

          <nav
            aria-label={`${listLabel} breadcrumb`}
            className="flex min-w-0 flex-wrap items-center gap-1 text-sm"
          >
            <button
              type="button"
              onClick={() => onGoUp(null)}
              className={cn(
                "rounded-md px-2 py-1 text-xs font-semibold transition-colors",
                !currentPath
                  ? "bg-background text-foreground"
                  : "text-foreground-muted hover:bg-background-hover hover:text-foreground"
              )}
            >
              {listLabel}
            </button>
            {crumbs.map((crumb) => {
              const title = crumb.kind === "task" ? crumb.task?.title ?? "" : crumb.step?.title ?? "";
              const isCurrent = crumb.path === currentPath;
              return (
                <span key={crumb.path} className="inline-flex min-w-0 items-center gap-1">
                  <ChevronRight className="h-3 w-3 text-foreground-subtle" />
                  <button
                    type="button"
                    onClick={() => onGoUp(crumb.path)}
                    className={cn(
                      "min-w-0 max-w-[260px] truncate rounded-md px-2 py-1 text-xs font-semibold transition-colors",
                      isCurrent
                        ? "bg-background text-foreground"
                        : "text-foreground-muted hover:bg-background-hover hover:text-foreground"
                    )}
                    title={title}
                  >
                    {title || "Untitled"}
                  </button>
                </span>
              );
            })}
          </nav>
        </div>
      </div>

      {empty ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-foreground-muted">
          {emptyLabel}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card">
          <div className="grid grid-cols-[44px_40px_1fr_80px_70px_70px_40px] items-center gap-2 border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-foreground-muted">
            <span>#</span>
            <span>Status</span>
            <span>{itemLabel}</span>
            <span className="text-center">✓✓</span>
            <span className="text-center">%</span>
            <span className="text-center">Σ</span>
            <span />
          </div>
          <div className="divide-y divide-border">
            {items.map((item, idx) => {
              const path = item.path || "";
              if (!path) return null;
              const status = mirrorStatusToTaskStatus(item.status);
              const statusUi = TASK_STATUS_UI[status];
              const isSelected = selectionPath === path;
              const criteriaOk = !!item.criteria_confirmed || !!item.criteria_auto_confirmed;
              const testsOk = !!item.tests_confirmed || !!item.tests_auto_confirmed;
              const isStep = item.kind === "step";
              const isCompleted = item.status === "completed";
              const isBlocked = !!item.blocked && !isCompleted;

              return (
                <div
                  key={path}
                  data-subtask-row={path}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelectionChange(path)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpen(path);
                    }
                  }}
                  className={cn(
                    "grid grid-cols-[44px_40px_1fr_80px_70px_70px_40px] items-center gap-2 px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                    isSelected ? "bg-primary/10" : "hover:bg-background-subtle"
                  )}
                >
                  <span className="text-xs font-mono text-foreground-subtle">{idx + 1}</span>
                  <span className="flex items-center">
                    {isStep ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleComplete(path, !isCompleted);
                        }}
                        className="flex h-5 w-5 items-center justify-center rounded-md hover:bg-background-hover"
                        aria-label={isCompleted ? "Mark incomplete" : "Mark complete"}
                      >
                        {isCompleted ? (
                          <CheckCircle2 className="h-5 w-5 text-status-ok" />
                        ) : isBlocked ? (
                          <AlertCircle className="h-5 w-5 text-status-fail" />
                        ) : (
                          <Circle className="h-5 w-5 text-foreground/30" />
                        )}
                      </button>
                    ) : (
                      <span className={cn("h-2.5 w-2.5 rounded-full", statusUi.classes.dot)} />
                    )}
                  </span>
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="truncate font-medium text-foreground" title={item.title}>
                      {item.title}
                    </span>
                    {item.id ? (
                      <span className="truncate text-xs text-foreground-subtle">{item.id}</span>
                    ) : null}
                  </div>
                  <span className="flex justify-center">
                    <CheckpointMarks criteriaOk={criteriaOk} testsOk={testsOk} />
                  </span>
                  <span className="text-center text-xs font-semibold tabular-nums text-foreground-muted">
                    {Math.max(0, Math.min(100, Math.round(item.progress || 0)))}%
                  </span>
                  <span className="text-center text-xs font-semibold tabular-nums text-foreground-muted">
                    {item.children_done}/{item.children_total}
                  </span>
                  <span className="flex justify-end">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-foreground-muted"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpen(path);
                      }}
                      aria-label="Open"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

interface SubtaskCardProps {
  node: TreeNode;
  stats: { done: number; total: number };
  isSelected: boolean;
  onOpen: () => void;
  onToggleComplete: () => void;
}

function SubtaskCard({
  node,
  stats,
  isSelected,
  onOpen,
  onToggleComplete,
}: SubtaskCardProps) {
  const hasChildren = node.children.length > 0;
  const isStep = node.kind === "step";
  const step = node.step;
  const task = node.task;
  const status = isStep ? deriveSubtaskStatus(step!) : undefined;
  const progressPct =
    stats.total > 0
      ? Math.round((stats.done / stats.total) * 100)
      : isStep && step?.completed
        ? 100
        : 0;

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "group w-full rounded-xl border border-border bg-card p-[var(--density-card-pad)] text-left transition-colors",
        isSelected ? "ring-1 ring-primary/40" : "hover:bg-background-subtle"
      )}
    >
      <div
        className={cn(
          "flex items-start justify-between gap-2",
          isStep && step?.completed ? "text-foreground-muted" : "text-foreground"
        )}
      >
        <div className="flex min-w-0 items-start gap-2">
          {isStep ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onToggleComplete();
              }}
              className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md hover:bg-background-hover"
            >
              {step?.completed ? (
                <CheckCircle2 className="h-5 w-5 text-status-ok" />
              ) : status === "blocked" ? (
                <AlertCircle className="h-5 w-5 text-status-fail" />
              ) : (
                <Circle className="h-5 w-5 text-foreground/30" />
              )}
            </button>
          ) : (
            (() => {
              const taskStatus = nodeFilterStatus(node);
              const Icon = TASK_STATUS_ICON[taskStatus];
              return <Icon className="mt-0.5 h-5 w-5 text-foreground/50" />;
            })()
          )}

          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div
                className="truncate text-sm font-semibold"
                title={isStep ? step?.title : task?.title}
              >
                {isStep ? step?.title : task?.title}
              </div>
              {isStep ? (
                <StepStatusBadge status={status!} size="sm" />
              ) : (
                (() => {
                  const taskStatus = nodeFilterStatus(node);
                  const ui = TASK_STATUS_UI[taskStatus];
                  return (
                    <Badge className={cn("text-[10px]", ui.classes.bg, ui.classes.text)}>
                      {ui.label}
                    </Badge>
                  );
                })()
              )}
            </div>
            {hasChildren && (
              <div className="text-xs text-foreground-muted">
                {node.children.length} nested
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <ProgressBar value={progressPct} size="sm" />
        <span className="text-xs font-semibold tabular-nums text-foreground-muted">
          {stats.done}/{stats.total}
        </span>
      </div>
    </button>
  );
}
export interface SubtaskItemProps {
  node: TreeNode;
  depth: number;
  expandedChildPaths: Set<string>;
  expandedDetailPaths: Set<string>;
  activePath?: string;
  onToggleChildren: (path: string) => void;
  onToggleDetails: (path: string) => void;
  onToggleComplete: (path: string, completed: boolean) => void;
  onConfirmCheckpoint: (
    path: string,
    checkpoint: "criteria" | "tests",
    note: string
  ) => Promise<void>;
  onAddProgressNote: (path: string, note: string) => Promise<boolean>;
  onSetBlocked: (path: string, blocked: boolean, reason?: string) => Promise<boolean>;
  onDefineSubtask: (
    path: string,
    updates: { title?: string; criteria?: string[]; tests?: string[]; blockers?: string[] }
  ) => Promise<void>;
  onRequestDelete: (path: string, title: string, kind: "step" | "task") => void;
  onOpen: (path: string) => void;
  onOpenInCards?: (path: string) => void;
  variant?: "detail" | "tree";
  filterMode?: "ALL" | TaskStatus;
  visibilityMap?: Map<string, boolean>;
  drilldown?: boolean;
}

export function SubtaskItem({
  node,
  depth,
  expandedChildPaths,
  expandedDetailPaths,
  activePath,
  onToggleChildren,
  onToggleDetails,
  onToggleComplete,
  onConfirmCheckpoint,
  onAddProgressNote,
  onSetBlocked,
  onDefineSubtask,
  onRequestDelete,
  onOpen,
  onOpenInCards,
  variant = "detail",
  filterMode = "ALL",
  visibilityMap,
  drilldown = false,
}: SubtaskItemProps) {
  const path = node.path;
  if (!isNodeVisibleAtPath(path, node, filterMode, visibilityMap)) {
    return null;
  }

  const hasChildren = node.children.length > 0;
  const canExpandChildren = hasChildren && !drilldown;
  const childrenExpanded = canExpandChildren && expandedChildPaths.has(path);
  const detailsExpanded = expandedDetailPaths.has(path);
  const isTreeVariant = variant === "tree";
  const isActive = activePath === path;

  const openPrimary = (targetPath: string) => {
    (onOpenInCards ?? onOpen)(targetPath);
  };

  if (node.kind === "task") {
    const task = node.task;
    if (!task) return null;
    const status = nodeFilterStatus(node);
    const Icon = task.blocked ? AlertCircle : TASK_STATUS_ICON[status];
    const statusUi = TASK_STATUS_UI[status];
    const title = task.title || "Untitled";
    const done = status === "DONE";

    return (
      <div>
        <div
          data-subtask-path={path}
          role="button"
          tabIndex={0}
          onClick={() => openPrimary(path)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              openPrimary(path);
            }
          }}
          className={cn(
            "group flex cursor-pointer select-none items-start gap-2 rounded-lg px-3 py-1 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            isActive
              ? "bg-primary/10 ring-1 ring-primary/20"
              : done
                ? "bg-status-ok/10"
                : "hover:bg-background-subtle"
          )}
          style={{ paddingLeft: `${12 + depth * 24}px` }}
        >
          {canExpandChildren ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onToggleChildren(path);
              }}
              className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md text-foreground-muted hover:bg-background-hover"
            >
              {childrenExpanded ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </button>
          ) : (
            <div className="h-5 w-5" />
          )}

          <div className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md text-foreground/60">
            <Icon className={cn("h-5 w-5", task.blocked ? "text-status-fail" : "")} />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => openPrimary(path)}
                  className={cn(
                    "block w-full cursor-pointer truncate text-left text-sm leading-snug hover:underline",
                    done ? "text-foreground-muted line-through" : "text-foreground"
                  )}
                  title={title}
                >
                  {title}
                </button>
              </div>
              <Badge className={cn("text-[10px]", statusUi.classes.bg, statusUi.classes.text)}>
                {statusUi.label}
              </Badge>
            </div>
          </div>

          <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-foreground-muted"
              onClick={(e) => {
                e.stopPropagation();
                openPrimary(path);
              }}
              aria-label="Open task"
              title="Open"
            >
              <ArrowUpRight className="h-4 w-4" />
            </Button>
            {!isTreeVariant && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-foreground-muted"
                    onClick={(e) => e.stopPropagation()}
                    aria-label="Task actions"
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onSelect={() => {
                      void navigator.clipboard.writeText(title);
                    }}
                  >
                    <Copy className="mr-2 h-4 w-4" />
                    Copy title
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    className="text-status-fail focus:text-status-fail"
                    onSelect={() => onRequestDelete(path, title, "task")}
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    Delete task
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {canExpandChildren && childrenExpanded && (
          <div className="mt-1">
            {node.children.map((child) => (
              <SubtaskItem
                key={child.path}
                node={child}
                depth={depth + 1}
                expandedChildPaths={expandedChildPaths}
                expandedDetailPaths={expandedDetailPaths}
                onToggleChildren={onToggleChildren}
                onToggleDetails={onToggleDetails}
                onToggleComplete={onToggleComplete}
                onConfirmCheckpoint={onConfirmCheckpoint}
                onAddProgressNote={onAddProgressNote}
                onSetBlocked={onSetBlocked}
                onDefineSubtask={onDefineSubtask}
                onRequestDelete={onRequestDelete}
                activePath={activePath}
                onOpen={onOpen}
                onOpenInCards={onOpenInCards}
                variant={variant}
                filterMode={filterMode}
                visibilityMap={visibilityMap}
                drilldown={drilldown}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  const subtask = node.step;
  if (!subtask) return null;

  const isBlocked = subtask.blockers && subtask.blockers.length > 0 && !subtask.completed;
  const isBlockedPhase1 = subtask.blocked ?? isBlocked;
  const blockReason = subtask.block_reason || (subtask.blockers && subtask.blockers[0]);

  const criteriaOk =
    !(subtask.success_criteria && subtask.success_criteria.length > 0) ||
    !!subtask.criteria_confirmed;
  const testsOk =
    !!subtask.tests_confirmed ||
    !!subtask.tests_auto_confirmed ||
    !(subtask.tests && subtask.tests.length > 0);
  const needsCheckpoints = !subtask.completed && !(criteriaOk && testsOk);
  const missingCheckpointsCount = subtask.completed ? 0 : Number(!criteriaOk) + Number(!testsOk);
  const hasDetails =
    needsCheckpoints ||
    (subtask.success_criteria && subtask.success_criteria.length > 0) ||
    (subtask.tests && subtask.tests.length > 0) ||
    (subtask.blockers && subtask.blockers.length > 0) ||
    (subtask.progress_notes && subtask.progress_notes.length > 0) ||
    (subtask.criteria_notes && subtask.criteria_notes.length > 0) ||
    (subtask.tests_notes && subtask.tests_notes.length > 0);
  const canShowDetails = !isTreeVariant && hasDetails;

  const [editingSection, setEditingSection] = useState<"criteria" | "tests" | "blockers" | null>(null);
  const [draftText, setDraftText] = useState("");
  const [savingSection, setSavingSection] = useState<"criteria" | "tests" | "blockers" | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(subtask.title);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const [draftProgressNote, setDraftProgressNote] = useState("");
  const [savingProgressNote, setSavingProgressNote] = useState(false);
  const [draftBlockReason, setDraftBlockReason] = useState("");
  const [savingBlock, setSavingBlock] = useState(false);

  useEffect(() => {
    if (!editingTitle) {
      setDraftTitle(subtask.title);
    }
  }, [subtask.title, editingTitle]);

  useEffect(() => {
    if (editingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingTitle]);

  const saveTitle = async () => {
    const next = draftTitle.trim();
    if (!next || next === subtask.title) {
      setEditingTitle(false);
      setDraftTitle(subtask.title);
      return;
    }
    setEditingTitle(false);
    await onDefineSubtask(path, { title: next });
  };

  const renderEditableListSection = (
    key: "criteria" | "tests" | "blockers",
    label: string,
    icon: ReactNode,
    items: string[] | undefined,
    confirmed: boolean,
    auto: boolean,
    notes: string[] | undefined,
    onSave: (next: string[]) => Promise<void>
  ) => {
    const isEditing = editingSection === key;
    const isCheckpoint = key === "criteria" || key === "tests";
    const status = !isCheckpoint ? "DATA" : confirmed ? "OK" : auto ? "AUTO" : "TODO";
    const statusClass = !isCheckpoint
      ? "bg-foreground/5 text-foreground-muted"
      : confirmed
        ? "bg-status-ok/10 text-status-ok"
        : auto
          ? "bg-primary/10 text-primary"
          : "bg-status-warn/10 text-status-warn";
    const canConfirm = isCheckpoint && !confirmed && !auto;

    const list = items ?? [];
    const isSaving = savingSection === key;

    return (
      <div
        className="rounded-lg border border-border bg-background p-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2 text-xs font-semibold">
            {icon}
            <span className="truncate">{label}</span>
            <span
              className={cn(
                "ml-1 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold tracking-wide",
                statusClass
              )}
            >
              {status}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {canConfirm && (
              <Button
                variant="outline"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  if (key === "criteria" || key === "tests") {
                    void onConfirmCheckpoint(path, key, "");
                  }
                }}
              >
                Confirm
              </Button>
            )}
            {!isEditing && (
              <Button
                variant="outline"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingSection(key);
                  setDraftText(list.join("\n"));
                }}
              >
                Edit
              </Button>
            )}
          </div>
        </div>

        {notes && notes.length > 0 && (
          <div className="mt-2 space-y-1 text-xs text-foreground-muted">
            {notes.slice(-2).map((n, i) => (
              <div key={i}>• {n}</div>
            ))}
          </div>
        )}

        {isEditing ? (
          <div className="mt-2 space-y-2">
            <Textarea
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              placeholder="One item per line"
              rows={Math.min(6, Math.max(3, list.length || 3))}
              className="font-mono text-xs"
            />

            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingSection(null);
                  setDraftText("");
                }}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={isSaving}
                onClick={async (e) => {
                  e.stopPropagation();
                  const next = draftText
                    .split("\n")
                    .map((l) => l.trim())
                    .filter(Boolean);
                  setSavingSection(key);
                  try {
                    await onSave(next);
                    setEditingSection(null);
                    setDraftText("");
                  } finally {
                    setSavingSection(null);
                  }
                }}
              >
                {isSaving ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        ) : list.length > 0 ? (
          <ul
            className="mt-2 ml-4 list-disc space-y-1 text-sm"
            onClick={(e) => {
              e.stopPropagation();
              setEditingSection(key);
              setDraftText(list.join("\n"));
            }}
          >
            {list.map((item, i) => (
              <li key={i} className="text-sm text-foreground">
                {key === "tests" ? (
                  <code className="font-mono text-xs">{item}</code>
                ) : (
                  item
                )}
              </li>
            ))}
          </ul>
        ) : (
          <button
            type="button"
            className="mt-2 text-left text-sm text-foreground-muted hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              setEditingSection(key);
              setDraftText("");
            }}
          >
            None (click to add)
          </button>
        )}
      </div>
    );
  };

  return (
    <div>
      <div
        data-subtask-path={path}
        role="button"
        tabIndex={0}
        onClick={() => openPrimary(path)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPrimary(path);
          }
        }}
        className={cn(
          "group flex cursor-pointer select-none items-start gap-2 rounded-lg px-3 py-1 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          isActive
            ? "bg-primary/10 ring-1 ring-primary/20"
            : subtask.completed
              ? "bg-status-ok/10"
              : "hover:bg-background-subtle"
        )}
        style={{ paddingLeft: `${12 + depth * 24}px` }}
      >
        {canExpandChildren ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onToggleChildren(path);
            }}
            className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md text-foreground-muted hover:bg-background-hover"
          >
            {childrenExpanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </button>
        ) : (
          <div className="h-5 w-5" />
        )}

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggleComplete(path, !subtask.completed);
          }}
          className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md hover:bg-background-hover"
        >
          {subtask.completed ? (
            <CheckCircle2 className="h-5 w-5 text-status-ok" />
          ) : isBlockedPhase1 ? (
            <AlertCircle className="h-5 w-5 text-status-fail" />
          ) : (
            <Circle className="h-5 w-5 text-foreground/30" />
          )}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              {editingTitle && !isTreeVariant ? (
                <Input
                  ref={titleInputRef}
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onBlur={() => void saveTitle()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void saveTitle();
                    }
                    if (e.key === "Escape") {
                      e.preventDefault();
                      setEditingTitle(false);
                      setDraftTitle(subtask.title);
                    }
                  }}
                  className="h-7 px-2 text-sm"
                />
              ) : isTreeVariant ? (
                <button
                  type="button"
                  onClick={() => openPrimary(path)}
                  className={cn(
                    "block w-full cursor-pointer truncate text-left text-sm leading-snug hover:underline",
                    subtask.completed
                      ? "text-foreground-muted line-through"
                      : "text-foreground"
                  )}
                  title={subtask.title}
                >
                  {subtask.title}
                </button>
              ) : (
                <div
                  className={cn(
                    "truncate text-sm leading-snug",
                    subtask.completed
                      ? "text-foreground-muted line-through"
                      : "text-foreground"
                  )}
                >
                  {subtask.title}
                </div>
              )}
            </div>

            {subtask.computed_status && (
              <StepStatusBadge
                status={subtask.computed_status}
                size="sm"
                showLabel={false}
              />
            )}

            {subtask.progress_notes && subtask.progress_notes.length > 0 && (
              <span
                className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary"
                title={`${subtask.progress_notes.length} progress note(s)`}
              >
                <NotesIcon className="h-3 w-3" />
                <span>{subtask.progress_notes.length}</span>
              </span>
            )}

            {canShowDetails && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleDetails(path);
                }}
                className={cn(
                  "relative mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-md hover:bg-background-hover",
                  detailsExpanded
                    ? "bg-background-hover text-foreground"
                    : needsCheckpoints
                      ? "text-status-warn"
                      : "text-foreground-muted"
                )}
                aria-label={detailsExpanded ? "Hide checks" : "Show checks"}
                title={
                  detailsExpanded
                    ? "Hide checks"
                    : missingCheckpointsCount > 0
                      ? `Checks (${missingCheckpointsCount} missing)`
                      : "Checks"
                }
              >
                <ListChecks className="h-4 w-4" />
                {!detailsExpanded && missingCheckpointsCount > 0 && (
                  <span className="absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-status-warn px-1 text-[10px] font-bold leading-none text-white">
                    {missingCheckpointsCount}
                  </span>
                )}
              </button>
            )}
          </div>

          {subtask.started_at && (
            <div className="mt-1 flex items-center gap-1 text-xs text-foreground-muted">
              <Clock className="h-3 w-3" />
              Started {new Date(subtask.started_at).toLocaleString()}
            </div>
          )}

          {isBlockedPhase1 && blockReason && (
            <div className="mt-1 flex items-center gap-1 text-xs text-status-fail">
              <AlertCircle className="h-3.5 w-3.5" />
              <span className="truncate">{blockReason}</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-foreground-muted"
            onClick={(e) => {
              e.stopPropagation();
              openPrimary(path);
            }}
            aria-label="Open step"
            title="Open"
          >
            <ArrowUpRight className="h-4 w-4" />
          </Button>
          {!isTreeVariant && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-foreground-muted"
                  onClick={(e) => e.stopPropagation()}
                  aria-label="Step actions"
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onSelect={() => {
                    void navigator.clipboard.writeText(subtask.title);
                  }}
                >
                  <Copy className="mr-2 h-4 w-4" />
                  Copy title
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="text-status-fail focus:text-status-fail"
                  onSelect={() => onRequestDelete(path, subtask.title, "step")}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete step
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      {canShowDetails && detailsExpanded && (
        <div
          className="mt-2 space-y-3 rounded-lg border border-border bg-background-subtle p-3"
          style={{ marginLeft: `${12 + depth * 24 + 28}px` }}
          onClick={(e) => e.stopPropagation()}
        >
          {renderEditableListSection(
            "criteria",
            "Success criteria",
            <Check className="h-3.5 w-3.5 text-status-ok" />,
            subtask.success_criteria,
            criteriaOk,
            false,
            subtask.criteria_notes,
            async (next) => onDefineSubtask(path, { criteria: next })
          )}

          {renderEditableListSection(
            "tests",
            "Tests",
            <PlayCircle className="h-3.5 w-3.5 text-status-ok" />,
            subtask.tests,
            !!subtask.tests_confirmed,
            !!subtask.tests_auto_confirmed && !(subtask.tests && subtask.tests.length > 0),
            subtask.tests_notes,
            async (next) => onDefineSubtask(path, { tests: next })
          )}

          {renderEditableListSection(
            "blockers",
            "Blockers / dependencies",
            <AlertTriangle className="h-3.5 w-3.5 text-status-warn" />,
            subtask.blockers,
            true,
            false,
            undefined,
            async (next) => onDefineSubtask(path, { blockers: next })
          )}

          {subtask.progress_notes && subtask.progress_notes.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-foreground-muted">
                <NotesIcon className="h-3.5 w-3.5 text-primary" />
                Progress notes
              </div>
              <ul className="ml-4 list-disc space-y-1 text-sm text-foreground">
                {subtask.progress_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-xs font-semibold text-foreground-muted">
                <NotesIcon className="h-3.5 w-3.5 text-primary" />
                Add progress note
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                disabled={savingProgressNote || draftProgressNote.trim().length === 0}
                onClick={async () => {
                  const note = draftProgressNote.trim();
                  if (!note || savingProgressNote) return;
                  setSavingProgressNote(true);
                  try {
                    const ok = await onAddProgressNote(path, note);
                    if (ok) setDraftProgressNote("");
                  } finally {
                    setSavingProgressNote(false);
                  }
                }}
              >
                Add
              </Button>
            </div>
            <Textarea
              value={draftProgressNote}
              onChange={(e) => setDraftProgressNote(e.target.value)}
              placeholder="Short implementation update…"
              rows={2}
              className="resize-y"
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-xs font-semibold text-foreground-muted">
                <AlertCircle className="h-3.5 w-3.5 text-status-fail" />
                Manual block
              </div>
              <Button
                variant="outline"
                size="sm"
                className={cn("h-8", isBlockedPhase1 ? "text-status-fail" : "")}
                disabled={savingBlock}
                onClick={async () => {
                  if (savingBlock) return;
                  setSavingBlock(true);
                  try {
                    if (isBlockedPhase1) {
                      await onSetBlocked(path, false);
                    } else {
                      await onSetBlocked(path, true, draftBlockReason.trim() || undefined);
                      setDraftBlockReason("");
                    }
                  } finally {
                    setSavingBlock(false);
                  }
                }}
                title={isBlockedPhase1 ? "Unblock step" : "Block step"}
              >
                {isBlockedPhase1 ? "Unblock" : "Block"}
              </Button>
            </div>
            {!isBlockedPhase1 && (
              <Input
                value={draftBlockReason}
                onChange={(e) => setDraftBlockReason(e.target.value)}
                placeholder="Reason (optional)…"
              />
            )}
          </div>
        </div>
      )}

      {canExpandChildren && childrenExpanded && (
        <div className="mt-1">
          {node.children.map((child) => (
            <SubtaskItem
              key={child.path}
              node={child}
              depth={depth + 1}
              expandedChildPaths={expandedChildPaths}
              expandedDetailPaths={expandedDetailPaths}
              onToggleChildren={onToggleChildren}
              onToggleDetails={onToggleDetails}
              onToggleComplete={onToggleComplete}
              onConfirmCheckpoint={onConfirmCheckpoint}
              onAddProgressNote={onAddProgressNote}
              onSetBlocked={onSetBlocked}
              onDefineSubtask={onDefineSubtask}
              onRequestDelete={onRequestDelete}
              activePath={activePath}
              onOpen={onOpen}
              onOpenInCards={onOpenInCards}
              variant={variant}
              filterMode={filterMode}
              visibilityMap={visibilityMap}
              drilldown={drilldown}
            />
          ))}
        </div>
      )}
    </div>
  );
}
