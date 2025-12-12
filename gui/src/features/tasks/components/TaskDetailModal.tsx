/**
 * Task Detail Modal - Full task view with subtask tree and actions
 */

import { useState, useEffect, useRef, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X,
  ChevronRight,
  ChevronDown,
  CheckCircle2,
  Circle,
  AlertCircle,
  Clock,
  Tag,
  Calendar,
  ListTodo,
  FileText,
  PlayCircle,
  AlertTriangle,
  Check,
  Loader2,
  Trash2,
  Copy,
  ArrowUpRight,
  FileText as NotesIcon,
	} from "lucide-react";
	import { DropdownMenu } from "@/components/common/DropdownMenu";
	import { ConfirmDialog } from "@/components/common/ConfirmDialog";
	import { SubtaskStatusBadge } from "@/components/common/SubtaskStatusBadge";
	import { TASK_STATUS_UI } from "@/lib/taskStatus";
import {
  showTask,
  updateTaskStatus as apiUpdateTaskStatus,
  toggleSubtask,
  completeCheckpoint,
  defineSubtask,
  deleteSubtask,
} from "@/lib/tauri";
import type { Task, SubTask, TaskStatus } from "@/types/task";
import { toast } from "@/components/common/Toast";

interface TaskDetailModalProps {
  taskId: string | null;
  domain?: string;
  /** Storage namespace for cross-namespace lookup (e.g., "idea_h") */
  namespace?: string;
  onClose: () => void;
  onDelete?: (taskId: string) => void;
}

const statusConfig: Record<
  TaskStatus,
  { icon: typeof CheckCircle2; color: string; bgColor: string; label: string }
> = {
  DONE: {
    icon: CheckCircle2,
    color: TASK_STATUS_UI.DONE.colors.text,
    bgColor: TASK_STATUS_UI.DONE.colors.bg,
    label: TASK_STATUS_UI.DONE.label,
  },
  ACTIVE: {
    icon: Clock,
    color: TASK_STATUS_UI.ACTIVE.colors.text,
    bgColor: TASK_STATUS_UI.ACTIVE.colors.bg,
    label: TASK_STATUS_UI.ACTIVE.label,
  },
  TODO: {
    icon: Circle,
    color: TASK_STATUS_UI.TODO.colors.text,
    bgColor: TASK_STATUS_UI.TODO.colors.bg,
    label: TASK_STATUS_UI.TODO.label,
  },
};

export function TaskDetailModal({
  taskId,
  domain,
  namespace,
  onClose,
  onDelete,
}: TaskDetailModalProps) {
  const queryClient = useQueryClient();
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set(["0", "1"]));
  const [checkpointDialog, setCheckpointDialog] = useState<{
    path: string;
    title: string;
    missing: Array<"criteria" | "tests" | "blockers">;
  } | null>(null);
	  const [checkpointNotes, setCheckpointNotes] = useState<{ criteria: string; tests: string; blockers: string }>({
	    criteria: "",
	    tests: "",
	    blockers: "",
	  });
	  const [isCompletingSubtask, setIsCompletingSubtask] = useState(false);
	  const [taskDeleteOpen, setTaskDeleteOpen] = useState(false);
	  const [deleteDialog, setDeleteDialog] = useState<{ path: string; title: string } | null>(null);
	  const [isDeletingSubtask, setIsDeletingSubtask] = useState(false);
	  const taskQueryKey = ["task", taskId, domain, namespace] as const;

  const { data: task, isLoading, error } = useQuery({
    queryKey: taskQueryKey,
    queryFn: async () => {
      const response = await showTask(taskId!, domain, namespace);
      if (!response.success || !response.task) {
        throw new Error(response.error || "Task not found");
      }
      return response.task;
    },
    enabled: !!taskId,
    // Auto-expand paths when task loads - handled via side effect in onSuccess if we want,
    // or just let state initialization handle it (which resets only on mount).
    // For now, we keep manual expansion state separate.
  });

  const updateStatusMutation = useMutation({
    mutationFn: async ({ taskId, status }: { taskId: string; status: TaskStatus }) => {
      const response = await apiUpdateTaskStatus(taskId, status, domain, namespace);
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

  function getSubtaskByPath(subtasks: SubTask[] | undefined, path: string): SubTask | null {
    if (!subtasks) return null;
    const indices = path.split(".").map((p) => Number(p));
    let current: SubTask[] = subtasks;
    let node: SubTask | undefined;
    for (const idx of indices) {
      node = current[idx];
      if (!node) return null;
      current = node.subtasks ?? [];
    }
    return node ?? null;
  }

  function computeMissingCheckpoints(subtask: SubTask): Array<"criteria" | "tests" | "blockers" | "children"> {
    const missing: Array<"criteria" | "tests" | "blockers" | "children"> = [];
    if (!subtask.criteria_confirmed) {
      missing.push("criteria");
    }
    const testsOk =
      subtask.tests_confirmed ||
      subtask.tests_auto_confirmed ||
      !(subtask.tests && subtask.tests.length > 0);
    if (!testsOk) {
      missing.push("tests");
    }
    const blockersOk =
      subtask.blockers_resolved ||
      subtask.blockers_auto_resolved ||
      !(subtask.blockers && subtask.blockers.length > 0);
    if (!blockersOk) {
      missing.push("blockers");
    }
    const childrenOk = (subtask.subtasks ?? []).every((ch) => ch.completed);
    if (!childrenOk) {
      missing.push("children");
    }
    return missing;
  }

  const handleConfirmAndComplete = async () => {
    if (!task || !checkpointDialog) return;
    setIsCompletingSubtask(true);
    try {
      for (const checkpoint of checkpointDialog.missing) {
        const note = checkpointNotes[checkpoint] || "";
        const resp = await completeCheckpoint({
          taskId: task.id,
          path: checkpointDialog.path,
          checkpoint,
          note,
          domain,
          namespace,
        });
        if (!resp.success) {
          throw new Error(resp.error || `Failed to confirm ${checkpoint}`);
        }
      }
      const doneResp = await toggleSubtask(task.id, checkpointDialog.path, true, domain, namespace);
      if (!doneResp.success) {
        throw new Error(doneResp.error || "Failed to complete subtask");
      }
      toast.success("Subtask completed");
      setCheckpointDialog(null);
      setCheckpointNotes({ criteria: "", tests: "", blockers: "" });
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to complete subtask");
    } finally {
      setIsCompletingSubtask(false);
    }
  };

  const handleCheckpointConfirm = async (
    path: string,
    checkpoint: "criteria" | "tests" | "blockers",
    note: string
  ) => {
    if (!task) return;
    const label = checkpoint === "criteria" ? "Criteria" : checkpoint === "tests" ? "Tests" : "Blockers";
    try {
      const resp = await completeCheckpoint({
        taskId: task.id,
        path,
        checkpoint,
        note,
        domain,
        namespace,
      });
      if (!resp.success) {
        throw new Error(resp.error || `Failed to confirm ${label.toLowerCase()}`);
      }
      toast.success(`${label} confirmed`);
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Failed to confirm ${label.toLowerCase()}`);
    }
  };

  const handleSubtaskDefine = async (
    path: string,
    updates: { title?: string; criteria?: string[]; tests?: string[]; blockers?: string[] }
  ) => {
    if (!task) return;
    try {
      const resp = await defineSubtask({
        taskId: task.id,
        path,
        title: updates.title,
        criteria: updates.criteria,
        tests: updates.tests,
        blockers: updates.blockers,
        domain,
        namespace,
      });
      if (!resp.success) {
        throw new Error(resp.error || "Failed to update subtask");
      }
      toast.success("Subtask updated");
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update subtask");
    }
  };

  const requestDeleteSubtask = (path: string, title: string) => {
    setDeleteDialog({ path, title });
  };

  const handleConfirmDeleteSubtask = async () => {
    if (!task || !deleteDialog) return;
    setIsDeletingSubtask(true);
    try {
      const resp = await deleteSubtask({
        taskId: task.id,
        path: deleteDialog.path,
        domain,
        namespace,
      });
      if (!resp.success) {
        throw new Error(resp.error || "Failed to delete subtask");
      }
      toast.success("Subtask deleted");
      setDeleteDialog(null);
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete subtask");
    } finally {
      setIsDeletingSubtask(false);
    }
  };

  // Optimistic subtask toggle (with checkpoint gating)
  const handleSubtaskToggle = async (path: string, completed: boolean) => {
    if (!task) return;

    if (completed) {
      const st = getSubtaskByPath(task.subtasks, path);
      if (!st) {
        toast.error("Subtask not found");
        return;
      }
      const missing = computeMissingCheckpoints(st);
      if (missing.includes("children")) {
        toast.warning("Complete child subtasks first");
        return;
      }
      const checkpointsMissing = missing.filter((m) => m !== "children") as Array<"criteria" | "tests" | "blockers">;
      if (checkpointsMissing.length > 0) {
        setCheckpointNotes({ criteria: "", tests: "", blockers: "" });
        setCheckpointDialog({ path, title: st.title, missing: checkpointsMissing });
        return;
      }
    }

    const previousTask = queryClient.getQueryData<Task>(taskQueryKey as unknown as readonly unknown[]);

    queryClient.setQueryData<Task>(taskQueryKey as unknown as readonly unknown[], (old) => {
      if (!old) return old;
      const updated = JSON.parse(JSON.stringify(old)) as Task;
      const indices = path.split(".").map(Number);
      let current: SubTask[] = updated.subtasks ?? [];
      for (let i = 0; i < indices.length - 1; i++) {
        if (!current[indices[i]] || !current[indices[i]].subtasks) break;
        current = current[indices[i]].subtasks!;
      }
      const targetIndex = indices[indices.length - 1];
      if (current[targetIndex]) {
        current[targetIndex].completed = completed;
      }
      return updated;
    });

    try {
      const response = await toggleSubtask(task.id, path, completed, domain, namespace);
      if (!response.success) {
        throw new Error(response.error || "Failed to update subtask");
      }
      queryClient.invalidateQueries({ queryKey: taskQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (err) {
      if (previousTask) {
        queryClient.setQueryData(taskQueryKey as unknown as readonly unknown[], previousTask);
      }
      toast.error(err instanceof Error ? err.message : "Failed to update subtask");
    }
  };

  const handleStatusChange = (status: TaskStatus) => {
    if (!task) return;
    updateStatusMutation.mutate({ taskId: task.id, status });
  };

  const requestDeleteTask = () => {
    if (!task) return;
    setTaskDeleteOpen(true);
  };

  const handleConfirmDeleteTask = () => {
    if (!task) return;
    onDelete?.(task.id);
    setTaskDeleteOpen(false);
    onClose();
  };

  const handleToggleExpand = (path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  // Don't render if no taskId
  if (!taskId) return null;

  if (isLoading) {
    return (
      <ModalOverlay onClose={onClose}>
        <Loader2
          style={{
            width: "32px",
            height: "32px",
            color: "var(--color-primary)",
            animation: "spin 1s linear infinite",
            margin: "0 auto 16px",
          }}
        />
        <p style={{ color: "var(--color-foreground-muted)", fontSize: "14px" }}>
          Loading task...
        </p>
      </ModalOverlay>
    );
  }

  if (error || !task) {
    return (
      <ModalOverlay onClose={onClose}>
        <AlertCircle
          style={{
            width: "32px",
            height: "32px",
            color: "var(--color-status-fail)",
            margin: "0 auto 16px",
          }}
        />
        <p style={{ color: "var(--color-status-fail)", fontSize: "14px", marginBottom: "16px" }}>
          {(error as Error)?.message || "Task not found"}
        </p>
        <button
          onClick={onClose}
          style={{
            padding: "8px 16px",
            borderRadius: "8px",
            border: "1px solid var(--color-border)",
            backgroundColor: "transparent",
            color: "var(--color-foreground)",
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </ModalOverlay>
    );
  }

  const StatusIcon = statusConfig[task.status].icon;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
        padding: "24px",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
	      <div
	        style={{
	          width: "100%",
	          maxWidth: "720px",
	          maxHeight: "90vh",
	          backgroundColor: "var(--color-background)",
	          borderRadius: "16px",
	          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.25)",
	          position: "relative",
	          display: "flex",
	          flexDirection: "column",
	          overflow: "hidden",
	        }}
	      >
		        {checkpointDialog && (
		          <div
	            style={{
	              position: "absolute",
	              inset: 0,
	              backgroundColor: "rgba(0,0,0,0.45)",
	              display: "flex",
	              alignItems: "center",
	              justifyContent: "center",
	              zIndex: 150,
	            }}
	            onClick={() => !isCompletingSubtask && setCheckpointDialog(null)}
	          >
	            <div
	              onClick={(e) => e.stopPropagation()}
	              style={{
	                width: "520px",
	                maxWidth: "90vw",
	                backgroundColor: "var(--color-background)",
	                borderRadius: "12px",
	                border: "1px solid var(--color-border)",
	                boxShadow: "var(--shadow-lg)",
	                padding: "16px",
	                display: "flex",
	                flexDirection: "column",
	                gap: "12px",
	              }}
	            >
	              <div style={{ fontSize: "14px", fontWeight: 600 }}>
	                Complete subtask
	              </div>
	              <div style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>
	                {checkpointDialog.title}
	              </div>
	              <div style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
	                This subtask requires checkpoint confirmations:
	              </div>

	              {checkpointDialog.missing.map((cp) => (
	                <div
	                  key={cp}
	                  style={{
	                    border: "1px solid var(--color-border)",
	                    borderRadius: "8px",
	                    padding: "10px",
	                    display: "flex",
	                    flexDirection: "column",
	                    gap: "6px",
	                  }}
	                >
	                  <div style={{ fontSize: "12px", fontWeight: 600 }}>
	                    {cp === "criteria" ? "Criteria" : cp === "tests" ? "Tests" : "Blockers"}
	                  </div>
	                  <textarea
	                    value={checkpointNotes[cp]}
	                    onChange={(e) =>
	                      setCheckpointNotes((prev) => ({ ...prev, [cp]: e.target.value }))
	                    }
	                    placeholder="Evidence / note..."
	                    rows={2}
	                    disabled={isCompletingSubtask}
	                    style={{
	                      width: "100%",
	                      padding: "8px 10px",
	                      borderRadius: "6px",
	                      border: "1px solid var(--color-border)",
	                      backgroundColor: "var(--color-background)",
	                      fontSize: "12px",
	                      outline: "none",
	                      resize: "vertical",
	                      fontFamily: "inherit",
	                    }}
	                  />
	                </div>
	              ))}

	              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
	                <button
	                  type="button"
	                  onClick={() => setCheckpointDialog(null)}
	                  disabled={isCompletingSubtask}
	                  style={{
	                    padding: "8px 12px",
	                    borderRadius: "8px",
	                    border: "1px solid var(--color-border)",
	                    backgroundColor: "transparent",
	                    fontSize: "12px",
	                    cursor: isCompletingSubtask ? "not-allowed" : "pointer",
	                  }}
	                >
	                  Cancel
	                </button>
	                <button
	                  type="button"
	                  onClick={handleConfirmAndComplete}
	                  disabled={isCompletingSubtask}
	                  style={{
	                    padding: "8px 12px",
	                    borderRadius: "8px",
	                    border: "none",
	                    backgroundColor: "var(--color-primary)",
	                    color: "white",
	                    fontSize: "12px",
	                    cursor: isCompletingSubtask ? "not-allowed" : "pointer",
	                    opacity: isCompletingSubtask ? 0.7 : 1,
	                  }}
	                >
	                  {isCompletingSubtask ? "Completing..." : "Confirm & complete"}
	                </button>
	              </div>
	            </div>
		          </div>
		        )}

		        {deleteDialog && (
		          <div
		            style={{
		              position: "absolute",
		              inset: 0,
		              backgroundColor: "rgba(0,0,0,0.45)",
		              display: "flex",
		              alignItems: "center",
		              justifyContent: "center",
		              zIndex: 160,
		            }}
		            onClick={() => !isDeletingSubtask && setDeleteDialog(null)}
		          >
		            <div
		              onClick={(e) => e.stopPropagation()}
		              style={{
		                width: "480px",
		                maxWidth: "90vw",
		                backgroundColor: "var(--color-background)",
		                borderRadius: "12px",
		                border: "1px solid var(--color-border)",
		                boxShadow: "var(--shadow-lg)",
		                padding: "16px",
		                display: "flex",
		                flexDirection: "column",
		                gap: "12px",
		              }}
		            >
		              <div style={{ fontSize: "14px", fontWeight: 600 }}>Delete subtask</div>
		              <div style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>
		                {deleteDialog.title}
		              </div>
		              <div style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
		                This will remove the subtask and all nested children. This action cannot be undone.
		              </div>
		              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
		                <button
		                  type="button"
		                  onClick={() => setDeleteDialog(null)}
		                  disabled={isDeletingSubtask}
		                  style={{
		                    padding: "8px 12px",
		                    borderRadius: "8px",
		                    border: "1px solid var(--color-border)",
		                    backgroundColor: "transparent",
		                    fontSize: "12px",
		                    cursor: isDeletingSubtask ? "not-allowed" : "pointer",
		                  }}
		                >
		                  Cancel
		                </button>
		                <button
		                  type="button"
		                  onClick={handleConfirmDeleteSubtask}
		                  disabled={isDeletingSubtask}
		                  style={{
		                    padding: "8px 12px",
		                    borderRadius: "8px",
		                    border: "none",
		                    backgroundColor: "var(--color-status-fail)",
		                    color: "white",
		                    fontSize: "12px",
		                    cursor: isDeletingSubtask ? "not-allowed" : "pointer",
		                    opacity: isDeletingSubtask ? 0.7 : 1,
		                  }}
		                >
		                  {isDeletingSubtask ? "Deleting..." : "Delete"}
		                </button>
		              </div>
		            </div>
		          </div>
		        )}

		        {/* Header */}
	        <div
	          style={{
	            padding: "20px 24px",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "flex-start",
            gap: "16px",
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
              <span
                style={{
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  color: "var(--color-foreground-muted)",
                  backgroundColor: "var(--color-background-muted)",
                  padding: "3px 8px",
                  borderRadius: "4px",
                }}
              >
                {task.id}
              </span>
	              <div
	                style={{
	                  display: "flex",
	                  alignItems: "center",
	                  gap: "6px",
	                  padding: "4px 10px",
	                  borderRadius: "999px",
	                  backgroundColor: statusConfig[task.status].bgColor,
	                }}
	              >
                <StatusIcon
                  style={{ width: "14px", height: "14px", color: statusConfig[task.status].color }}
                />
                <span
                  style={{
                    fontSize: "12px",
                    fontWeight: 500,
                    color: statusConfig[task.status].color,
                  }}
                >
                  {statusConfig[task.status].label}
                </span>
              </div>
            </div>
            <h2
              style={{
                fontSize: "18px",
                fontWeight: 600,
                color: "var(--color-foreground)",
                lineHeight: 1.4,
              }}
            >
              {task.title}
            </h2>
          </div>
          <button
            onClick={onClose}
            style={{
              padding: "8px",
              borderRadius: "8px",
              border: "none",
              backgroundColor: "transparent",
              cursor: "pointer",
              color: "var(--color-foreground-muted)",
            }}
          >
            <X style={{ width: "20px", height: "20px" }} />
          </button>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: "auto", padding: "24px" }}>
          {/* Description */}
          {task.description && (
            <div style={{ marginBottom: "24px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                <FileText style={{ width: "14px", height: "14px", color: "var(--color-foreground-muted)" }} />
                <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--color-foreground-muted)" }}>
                  Description
                </span>
              </div>
              <p style={{ fontSize: "14px", color: "var(--color-foreground)", lineHeight: 1.6 }}>
                {task.description}
              </p>
            </div>
          )}

          {/* Meta info */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "16px",
              marginBottom: "24px",
              padding: "16px",
              backgroundColor: "var(--color-background-subtle)",
              borderRadius: "10px",
            }}
          >
            {task.priority && (
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <AlertTriangle style={{ width: "14px", height: "14px", color: "var(--color-status-warn)" }} />
                <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>
                  {task.priority}
                </span>
              </div>
            )}
            {task.domain && (
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <Tag style={{ width: "14px", height: "14px", color: "var(--color-primary)" }} />
                <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>
                  {task.domain}
                </span>
              </div>
            )}
            {task.updated_at && (
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <Calendar style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />
                <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>
                  Updated {new Date(task.updated_at).toLocaleDateString()}
                </span>
              </div>
            )}
          </div>

          {/* Tags */}
          {task.tags && task.tags.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "24px" }}>
              {task.tags.map((tag) => (
                <span
                  key={tag}
                  style={{
                    fontSize: "12px",
                    color: "var(--color-primary)",
                    backgroundColor: "var(--color-primary-subtle)",
                    padding: "4px 10px",
                    borderRadius: "999px",
                  }}
                >
                  #{tag}
                </span>
              ))}
            </div>
          )}

          {/* Subtasks */}
          {task.subtasks && task.subtasks.length > 0 && (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
                <ListTodo style={{ width: "14px", height: "14px", color: "var(--color-foreground-muted)" }} />
                <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--color-foreground-muted)" }}>
                  Subtasks ({task.subtasks.length})
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                {task.subtasks.map((subtask, index) => (
		                  <SubtaskItem
		                    key={index}
		                    subtask={subtask}
		                    path={String(index)}
		                    depth={0}
		                    expandedPaths={expandedPaths}
		                    onToggleExpand={handleToggleExpand}
		                    onToggleComplete={handleSubtaskToggle}
		                    onConfirmCheckpoint={handleCheckpointConfirm}
		                    onDefineSubtask={handleSubtaskDefine}
		                    onRequestDelete={requestDeleteSubtask}
		                  />
                ))}
              </div>
            </div>
          )}

          {/* Tests */}
          {task.tests && task.tests.length > 0 && (
            <div style={{ marginTop: "24px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
                <PlayCircle style={{ width: "14px", height: "14px", color: "var(--color-status-ok)" }} />
                <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--color-foreground-muted)" }}>
                  Tests
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {task.tests.map((test, i) => (
                  <code
                    key={i}
                    style={{
                      fontSize: "12px",
                      fontFamily: "var(--font-mono)",
                      color: "var(--color-foreground)",
                      backgroundColor: "var(--color-background-muted)",
                      padding: "8px 12px",
                      borderRadius: "6px",
                    }}
                  >
                    {test}
                  </code>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "16px 24px",
            borderTop: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", gap: "8px" }}>
            {(["TODO", "ACTIVE", "DONE"] as TaskStatus[]).map((status) => (
              <button
                key={status}
                onClick={() => handleStatusChange(status)}
                style={{
                  padding: "8px 14px",
	                  borderRadius: "8px",
	                  border: `1px solid ${task.status === status ? statusConfig[status].color : "var(--color-border)"}`,
	                  backgroundColor: task.status === status ? statusConfig[status].bgColor : "transparent",
	                  color: task.status === status ? statusConfig[status].color : "var(--color-foreground-muted)",
	                  fontSize: "13px",
	                  fontWeight: 500,
	                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  transition: "all 150ms ease",
                }}
              >
                {statusConfig[status].label}
                {task.status === status && <Check style={{ width: "14px", height: "14px" }} />}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
	            {onDelete && (
	              <button
	                onClick={requestDeleteTask}
	                style={{
	                  padding: "8px 14px",
	                  borderRadius: "8px",
                  border: "1px solid var(--color-status-fail)",
                  backgroundColor: "transparent",
                  color: "var(--color-status-fail)",
                  fontSize: "13px",
                  fontWeight: 500,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  transition: "all 150ms ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--color-status-fail)";
                  e.currentTarget.style.color = "white";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = "transparent";
                  e.currentTarget.style.color = "var(--color-status-fail)";
                }}
              >
                <Trash2 style={{ width: "14px", height: "14px" }} />
	                Delete
	              </button>
	            )}
            <button
              onClick={onClose}
              style={{
                padding: "8px 16px",
                borderRadius: "8px",
                border: "none",
                backgroundColor: "var(--color-primary)",
                color: "white",
                fontSize: "13px",
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              Close
            </button>
          </div>
	        </div>
	      </div>

	      {onDelete && task && (
	        <ConfirmDialog
	          isOpen={taskDeleteOpen}
	          title={`Delete task "${task.title}"?`}
	          description="This will permanently remove the task and all its subtasks."
	          confirmLabel="Delete"
	          cancelLabel="Cancel"
	          danger
	          onCancel={() => setTaskDeleteOpen(false)}
	          onConfirm={handleConfirmDeleteTask}
	        />
	      )}
	    </div>
	  );
}

function ModalOverlay({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
        padding: "24px",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "400px",
          backgroundColor: "var(--color-background)",
          borderRadius: "16px",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.25)",
          padding: "32px",
          textAlign: "center",
        }}
      >
        {children}
      </div>
    </div>
  );
}

interface SubtaskItemProps {
  subtask: SubTask;
  path: string;
  depth: number;
  expandedPaths: Set<string>;
  onToggleExpand: (path: string) => void;
  onToggleComplete: (path: string, completed: boolean) => void;
  onConfirmCheckpoint: (
    path: string,
    checkpoint: "criteria" | "tests" | "blockers",
    note: string
  ) => Promise<void>;
  onDefineSubtask: (
    path: string,
    updates: { title?: string; criteria?: string[]; tests?: string[]; blockers?: string[] }
  ) => Promise<void>;
  onRequestDelete: (path: string, title: string) => void;
}

function SubtaskItem({
  subtask,
  path,
  depth,
  expandedPaths,
  onToggleExpand,
  onToggleComplete,
  onConfirmCheckpoint,
  onDefineSubtask,
  onRequestDelete,
}: SubtaskItemProps) {
  const hasChildren = subtask.subtasks && subtask.subtasks.length > 0;
  const isExpanded = expandedPaths.has(path);
  const isBlocked = subtask.blockers && subtask.blockers.length > 0 && !subtask.completed;

  // Phase 1: Use blocked flag and block_reason if available
  const isBlockedPhase1 = subtask.blocked ?? isBlocked;
  const blockReason = subtask.block_reason || (subtask.blockers && subtask.blockers[0]);

  const criteriaOk = !!subtask.criteria_confirmed;
  const testsOk = !!subtask.tests_confirmed || !!subtask.tests_auto_confirmed;
  const blockersOk = !!subtask.blockers_resolved || !!subtask.blockers_auto_resolved;
  const needsCheckpoints = !(criteriaOk && testsOk && blockersOk);

  const hasDetails =
    needsCheckpoints ||
    (subtask.success_criteria && subtask.success_criteria.length > 0) ||
    (subtask.tests && subtask.tests.length > 0) ||
    (subtask.blockers && subtask.blockers.length > 0) ||
    (subtask.progress_notes && subtask.progress_notes.length > 0) ||
    (subtask.criteria_notes && subtask.criteria_notes.length > 0) ||
    (subtask.tests_notes && subtask.tests_notes.length > 0) ||
    (subtask.blockers_notes && subtask.blockers_notes.length > 0);

	  const isExpandable = hasChildren || hasDetails;

	const [editingSection, setEditingSection] = useState<"criteria" | "tests" | "blockers" | null>(null);
	const [draftText, setDraftText] = useState("");
	const [savingSection, setSavingSection] = useState<"criteria" | "tests" | "blockers" | null>(null);
	const [editingTitle, setEditingTitle] = useState(false);
	const [draftTitle, setDraftTitle] = useState(subtask.title);
	const titleInputRef = useRef<HTMLInputElement>(null);

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
	    const status = confirmed ? "OK" : auto ? "AUTO" : "TODO";
	    const statusColor = confirmed
	      ? "var(--color-status-ok)"
	      : auto
	        ? "var(--color-primary)"
	        : "var(--color-status-warn)";
	    const statusBg = confirmed
	      ? "var(--color-status-ok-subtle)"
	      : auto
	        ? "var(--color-primary-subtle)"
	        : "var(--color-status-warn-subtle)";
	    const canConfirm = !confirmed && !auto;

		    const list = items ?? [];

	    return (
	      <div
	        style={{
	          padding: "8px 10px",
	          backgroundColor: "var(--color-background)",
	          border: "1px solid var(--color-border-subtle)",
	          borderRadius: "8px",
	          display: "flex",
	          flexDirection: "column",
	          gap: "6px",
	        }}
	        onClick={(e) => e.stopPropagation()}
	      >
		        <div
		          style={{
		            display: "flex",
		            alignItems: "center",
		            justifyContent: "space-between",
		            gap: "8px",
		            flexWrap: "wrap",
		          }}
		        >
		          <div
		            style={{
		              display: "flex",
		              alignItems: "center",
		              gap: "6px",
		              fontSize: "12px",
		              fontWeight: 600,
		              flex: 1,
		              minWidth: 0,
		            }}
		          >
		            {icon}
		            <span>{label}</span>
	            <span
	              style={{
	                fontSize: "10px",
	                fontWeight: 700,
	                color: statusColor,
	                backgroundColor: statusBg,
	                padding: "2px 6px",
	                borderRadius: "999px",
	                letterSpacing: "0.02em",
	                marginLeft: "4px",
	              }}
	            >
	              {status}
	            </span>
	          </div>
		          <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
	            {canConfirm && (
	              <button
	                onClick={(e) => {
	                  e.stopPropagation();
	                  void onConfirmCheckpoint(path, key, "");
	                }}
	                style={{
	                  fontSize: "11px",
	                  padding: "4px 8px",
	                  borderRadius: "6px",
	                  border: "1px solid var(--color-border)",
	                  backgroundColor: "transparent",
	                  color: "var(--color-foreground)",
	                  cursor: "pointer",
	                }}
	              >
	                Confirm
	              </button>
	            )}
	            {!isEditing && (
	              <button
	                onClick={(e) => {
	                  e.stopPropagation();
	                  setEditingSection(key);
	                  setDraftText(list.join("\n"));
	                }}
	                style={{
	                  fontSize: "11px",
	                  padding: "4px 8px",
	                  borderRadius: "6px",
	                  border: "1px solid var(--color-border)",
	                  backgroundColor: "transparent",
	                  color: "var(--color-foreground-muted)",
	                  cursor: "pointer",
	                }}
	              >
	                Edit
	              </button>
	            )}
	          </div>
		        </div>

		        {notes && notes.length > 0 && (
		          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
		            {notes.slice(-2).map((n, i) => (
		              <div key={i} style={{ fontSize: "11px", color: "var(--color-foreground-muted)" }}>
		                • {n}
		              </div>
		            ))}
		          </div>
		        )}

		        {isEditing ? (
	          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
	            <textarea
	              value={draftText}
	              onChange={(e) => setDraftText(e.target.value)}
	              placeholder="One item per line"
	              rows={Math.min(6, Math.max(3, list.length || 3))}
	              style={{
	                width: "100%",
	                fontSize: "12px",
	                padding: "8px",
	                borderRadius: "6px",
	                border: "1px solid var(--color-border)",
	                backgroundColor: "var(--color-background)",
	                color: "var(--color-foreground)",
	                fontFamily: "var(--font-mono)",
	                resize: "vertical",
	              }}
	            />
	            <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
	              <button
	                onClick={(e) => {
	                  e.stopPropagation();
	                  setEditingSection(null);
	                  setDraftText("");
	                }}
	                style={{
	                  fontSize: "12px",
	                  padding: "6px 10px",
	                  borderRadius: "6px",
	                  border: "1px solid var(--color-border)",
	                  backgroundColor: "transparent",
	                  cursor: "pointer",
	                }}
	              >
	                Cancel
	              </button>
	              <button
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
	                disabled={savingSection === key}
	                style={{
	                  fontSize: "12px",
	                  padding: "6px 12px",
	                  borderRadius: "6px",
	                  border: "none",
	                  backgroundColor: "var(--color-primary)",
	                  color: "white",
	                  cursor: savingSection === key ? "default" : "pointer",
	                  opacity: savingSection === key ? 0.6 : 1,
	                }}
	              >
	                {savingSection === key ? "Saving…" : "Save"}
	              </button>
	            </div>
	          </div>
	        ) : list.length > 0 ? (
	          <ul
	            style={{
	              margin: 0,
	              paddingLeft: "16px",
	              display: "flex",
	              flexDirection: "column",
	              gap: "2px",
	              cursor: "text",
	            }}
	            onClick={(e) => {
	              e.stopPropagation();
	              setEditingSection(key);
	              setDraftText(list.join("\n"));
	            }}
	          >
	            {list.map((item, i) => (
	              <li key={i} style={{ fontSize: "12px", color: "var(--color-foreground)" }}>
	                {key === "tests" ? (
	                  <code style={{ fontFamily: "var(--font-mono)" }}>{item}</code>
	                ) : (
	                  item
	                )}
	              </li>
	            ))}
	          </ul>
	        ) : (
	          <div
	            style={{ fontSize: "12px", color: "var(--color-foreground-muted)", cursor: "pointer" }}
	            onClick={(e) => {
	              e.stopPropagation();
	              setEditingSection(key);
	              setDraftText("");
	            }}
	          >
	            None (click to add)
	          </div>
	        )}
	      </div>

		    );
		  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "8px",
          padding: "10px 12px",
          paddingLeft: `${12 + depth * 24}px`,
          borderRadius: "8px",
          backgroundColor: subtask.completed ? "var(--color-status-ok-subtle)" : "transparent",
          transition: "background-color 150ms ease",
        }}
        onMouseEnter={(e) => {
          if (!subtask.completed) {
            e.currentTarget.style.backgroundColor = "var(--color-background-subtle)";
          }
          const actionsEl = e.currentTarget.querySelector(".subtask-actions") as HTMLElement;
          if (actionsEl) actionsEl.style.opacity = "1";
        }}
        onMouseLeave={(e) => {
          if (!subtask.completed) {
            e.currentTarget.style.backgroundColor = "transparent";
          }
          const actionsEl = e.currentTarget.querySelector(".subtask-actions") as HTMLElement;
          if (actionsEl) actionsEl.style.opacity = "0";
        }}
      >
        {/* Expand/collapse button */}
        {isExpandable ? (
          <button
            onClick={() => onToggleExpand(path)}
            style={{
              padding: "2px",
              border: "none",
              backgroundColor: "transparent",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--color-foreground-muted)",
            }}
          >
            {isExpanded ? (
              <ChevronDown style={{ width: "16px", height: "16px" }} />
            ) : (
              <ChevronRight style={{ width: "16px", height: "16px" }} />
            )}
          </button>
        ) : (
          <div style={{ width: "20px" }} />
        )}

        {/* Checkbox */}
        <button
          onClick={() => onToggleComplete(path, !subtask.completed)}
          style={{
            padding: "2px",
            border: "none",
            backgroundColor: "transparent",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {subtask.completed ? (
            <CheckCircle2
              style={{ width: "18px", height: "18px", color: "var(--color-status-ok)" }}
            />
          ) : isBlockedPhase1 ? (
            <AlertCircle
              style={{ width: "18px", height: "18px", color: "var(--color-status-fail)" }}
            />
          ) : (
            <Circle
              style={{ width: "18px", height: "18px", color: "var(--color-foreground-subtle)" }}
            />
          )}
        </button>

        {/* Content */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
	            {editingTitle ? (
	              <input
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
	                style={{
	                  width: "100%",
	                  fontSize: "13px",
	                  lineHeight: 1.4,
	                  padding: "2px 4px",
	                  borderRadius: "4px",
	                  border: "1px solid var(--color-border)",
	                  backgroundColor: "var(--color-background)",
	                  color: "var(--color-foreground)",
	                }}
	              />
	            ) : (
	              <div
	                onDoubleClick={(e) => {
	                  e.stopPropagation();
	                  setEditingTitle(true);
	                }}
	                style={{
	                  fontSize: "13px",
	                  color: subtask.completed
	                    ? "var(--color-foreground-muted)"
	                    : "var(--color-foreground)",
	                  textDecoration: subtask.completed ? "line-through" : "none",
	                  lineHeight: 1.4,
	                  flex: 1,
	                  cursor: "text",
	                }}
	              >
	                {subtask.title}
	              </div>
	            )}

            {/* Phase 1: Computed status badge */}
            {subtask.computed_status && (
              <SubtaskStatusBadge status={subtask.computed_status} size="sm" showLabel={false} />
            )}

            {/* Phase 1: Progress notes count */}
            {subtask.progress_notes && subtask.progress_notes.length > 0 && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "3px",
                  fontSize: "11px",
                  color: "var(--color-primary)",
                  backgroundColor: "var(--color-primary-subtle)",
                  padding: "2px 6px",
                  borderRadius: "4px",
                }}
                title={`${subtask.progress_notes.length} progress note(s)`}
              >
                <NotesIcon style={{ width: "10px", height: "10px" }} />
                <span>{subtask.progress_notes.length}</span>
              </div>
            )}
          </div>

          {/* Phase 1: Started timestamp */}
          {subtask.started_at && (
            <div
              style={{
                fontSize: "11px",
                color: "var(--color-foreground-subtle)",
                marginTop: "2px",
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
            >
              <Clock style={{ width: "10px", height: "10px" }} />
              Started {new Date(subtask.started_at).toLocaleString()}
            </div>
          )}

          {/* Phase 1: Block reason */}
          {isBlockedPhase1 && blockReason && (
            <div
              style={{
                fontSize: "11px",
                color: "var(--color-status-fail)",
                marginTop: "4px",
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
            >
              <AlertCircle style={{ width: "12px", height: "12px" }} />
              {blockReason}
            </div>
          )}
        </div>

        {/* Actions */}
        <div
          className="subtask-actions"
          style={{
            opacity: 0,
            transition: "opacity 150ms ease",
          }}
        >
          <DropdownMenu
            trigger={
              <button
                style={{
                  padding: "4px",
                  border: "none",
                  backgroundColor: "transparent",
                  cursor: "pointer",
                  color: "var(--color-foreground-subtle)",
                  borderRadius: "4px",
                }}
                onClick={(e) => e.stopPropagation()}
              >
                <ArrowUpRight style={{ width: "14px", height: "14px" }} />
              </button>
            }
		            items={[
		              {
		                label: "Copy title",
		                icon: <Copy style={{ width: "14px", height: "14px" }} />,
		                onClick: () => navigator.clipboard.writeText(subtask.title),
		              },
		              { type: "separator" as const },
		              {
		                label: "Delete subtask",
		                icon: <Trash2 style={{ width: "14px", height: "14px" }} />,
		                onClick: () => onRequestDelete(path, subtask.title),
		                danger: true,
		              },
		            ]}
	          />
	        </div>
	      </div>

	      {/* Subtask details / checkpoints */}
	      {isExpanded && hasDetails && (
	        <div
	          style={{
	            marginLeft: `${12 + depth * 24 + 28}px`,
	            marginTop: "4px",
	            padding: "10px 12px",
	            backgroundColor: "var(--color-background-subtle)",
	            borderRadius: "8px",
	            border: "1px solid var(--color-border-subtle)",
	            display: "flex",
	            flexDirection: "column",
	            gap: "10px",
	          }}
	          onClick={(e) => e.stopPropagation()}
	        >
		          {renderEditableListSection(
	            "criteria",
	            "Success criteria",
	            <Check style={{ width: "12px", height: "12px", color: "var(--color-status-ok)" }} />,
	            subtask.success_criteria,
	            criteriaOk,
	            false,
		            subtask.criteria_notes,
		            async (next) => onDefineSubtask(path, { criteria: next })
		          )}

	          {renderEditableListSection(
	            "tests",
	            "Tests",
	            <PlayCircle style={{ width: "12px", height: "12px", color: "var(--color-status-ok)" }} />,
		            subtask.tests,
		            !!subtask.tests_confirmed,
		            !!subtask.tests_auto_confirmed && !(subtask.tests && subtask.tests.length > 0),
		            subtask.tests_notes,
		            async (next) => onDefineSubtask(path, { tests: next })
		          )}

	          {renderEditableListSection(
	            "blockers",
	            "Blockers / dependencies",
	            <AlertTriangle style={{ width: "12px", height: "12px", color: "var(--color-status-warn)" }} />,
		            subtask.blockers,
		            !!subtask.blockers_resolved,
		            !!subtask.blockers_auto_resolved && !(subtask.blockers && subtask.blockers.length > 0),
		            subtask.blockers_notes,
		            async (next) => onDefineSubtask(path, { blockers: next })
		          )}

	          {subtask.progress_notes && subtask.progress_notes.length > 0 && (
	            <div>
	              <div
	                style={{
	                  fontSize: "12px",
	                  fontWeight: 600,
	                  color: "var(--color-foreground-muted)",
	                  marginBottom: "4px",
	                  display: "flex",
	                  alignItems: "center",
	                  gap: "6px",
	                }}
	              >
	                <NotesIcon style={{ width: "12px", height: "12px", color: "var(--color-primary)" }} />
	                Progress notes
	              </div>
	              <ul
	                style={{
	                  margin: 0,
	                  paddingLeft: "16px",
	                  display: "flex",
	                  flexDirection: "column",
	                  gap: "2px",
	                }}
	              >
	                {subtask.progress_notes.map((n, i) => (
	                  <li key={i} style={{ fontSize: "12px", color: "var(--color-foreground)" }}>
	                    {n}
	                  </li>
	                ))}
	              </ul>
	            </div>
	          )}
	        </div>
	      )}

	      {/* Nested subtasks */}
	      {hasChildren && isExpanded && (
	        <div>
	          {subtask.subtasks!.map((child, index) => (
	            <SubtaskItem
	              key={index}
	              subtask={child}
	              path={`${path}.${index}`}
	              depth={depth + 1}
	              expandedPaths={expandedPaths}
		              onToggleExpand={onToggleExpand}
		              onToggleComplete={onToggleComplete}
		              onConfirmCheckpoint={onConfirmCheckpoint}
		              onDefineSubtask={onDefineSubtask}
		              onRequestDelete={onRequestDelete}
		            />
	          ))}
	        </div>
	      )}
    </div>
  );
}
