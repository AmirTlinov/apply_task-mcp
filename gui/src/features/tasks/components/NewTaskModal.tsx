/**
 * New Task Modal - Create new task form
 */

import { useState, useCallback, useEffect } from "react";
import { X, Plus, Loader2 } from "lucide-react";
import { createTask, getSubtasksTemplate } from "@/lib/tauri";
import type { Namespace } from "@/types/task";

interface NewTaskModalProps {
  isOpen: boolean;
  onClose: () => void;
  onTaskCreated?: () => void;
  namespaces?: Namespace[];
  selectedNamespace?: string | null;
  defaultNamespace?: string | null;
}

type DraftSubtask = {
  title: string;
  criteria: string[];
  tests: string[];
  blockers: string[];
};

function normalizeListInput(value: string): string[] {
  return value
    .split(/[\n;]+/g)
    .map((v) => v.trim())
    .filter(Boolean);
}

export function NewTaskModal({
  isOpen,
  onClose,
  onTaskCreated,
  namespaces = [],
  selectedNamespace = null,
  defaultNamespace = null,
}: NewTaskModalProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [domain, setDomain] = useState("");
  const [priority, setPriority] = useState<"LOW" | "MEDIUM" | "HIGH" | "CRITICAL">("MEDIUM");
  const [tagsText, setTagsText] = useState("");
  const [namespace, setNamespace] = useState("");
  const [subtasks, setSubtasks] = useState<DraftSubtask[]>([]);
  const [isTemplateLoading, setIsTemplateLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const initial =
      selectedNamespace ||
      defaultNamespace ||
      (namespaces.length === 1 ? namespaces[0].namespace : "");
    setNamespace(initial);
  }, [isOpen, selectedNamespace, defaultNamespace, namespaces]);

  const handleLoadTemplate = useCallback(async () => {
    setIsTemplateLoading(true);
    setError(null);
    try {
      const count = Math.max(3, subtasks.length || 3);
      const response = await getSubtasksTemplate(count);
      if (!response.success) {
        throw new Error(response.error || "Failed to load template");
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const payload = (response.result as any)?.payload;
      const template = payload?.template;
      if (!Array.isArray(template)) {
        throw new Error("Invalid template format");
      }
      setSubtasks(
        template.map((t: any) => ({
          title: String(t.title || ""),
          criteria: Array.isArray(t.criteria) ? t.criteria.map(String) : [],
          tests: Array.isArray(t.tests) ? t.tests.map(String) : [],
          blockers: Array.isArray(t.blockers) ? t.blockers.map(String) : [],
        }))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setIsTemplateLoading(false);
    }
  }, [subtasks.length]);

  const handleAddSubtask = useCallback(() => {
    setSubtasks((prev) => [
      ...prev,
      { title: "", criteria: [], tests: [], blockers: [] },
    ]);
  }, []);

  const handleRemoveSubtask = useCallback((index: number) => {
    setSubtasks((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();

    if (!title.trim()) {
      setError("Title is required");
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      const tags = normalizeListInput(tagsText.replace(/,/g, "\n"));
      const sanitizedSubtasks = subtasks
        .map((st) => ({
          title: st.title.trim(),
          criteria: st.criteria.map((c) => c.trim()).filter(Boolean),
          tests: st.tests.map((t) => t.trim()).filter(Boolean),
          blockers: st.blockers.map((b) => b.trim()).filter(Boolean),
        }))
        .filter((st) => st.title.length > 0);

      const response = await createTask({
        title: title.trim(),
        parent: "ROOT",
        description: description.trim() || undefined,
        priority,
        tags: tags.length ? tags : undefined,
        subtasks: sanitizedSubtasks.length ? sanitizedSubtasks : undefined,
        domain: domain.trim() || undefined,
        namespace: namespace.trim() || undefined,
      });

      if (response.success) {
        // Reset form
        setTitle("");
        setDescription("");
        setDomain("");
        setPriority("MEDIUM");
        setTagsText("");
        setSubtasks([]);
        onClose();
        onTaskCreated?.();
      } else {
        setError(response.error || "Failed to create task");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setIsSubmitting(false);
    }
  }, [title, description, domain, priority, tagsText, subtasks, namespace, onClose, onTaskCreated]);

  const handleClose = useCallback(() => {
    if (!isSubmitting) {
      setTitle("");
      setDescription("");
      setDomain("");
      setPriority("MEDIUM");
      setTagsText("");
      setSubtasks([]);
      setError(null);
      onClose();
    }
  }, [isSubmitting, onClose]);

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={handleClose}
    >
      <div
        style={{
          backgroundColor: "var(--color-background)",
          borderRadius: "12px",
          width: "480px",
          maxWidth: "90vw",
          maxHeight: "90vh",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "16px 20px",
            borderBottom: "1px solid var(--color-border)",
          }}
        >
          <h2
            style={{
              fontSize: "16px",
              fontWeight: 600,
              color: "var(--color-foreground)",
            }}
          >
            New Task
          </h2>
          <button
            onClick={handleClose}
            disabled={isSubmitting}
            style={{
              padding: "4px",
              borderRadius: "4px",
              border: "none",
              backgroundColor: "transparent",
              cursor: isSubmitting ? "not-allowed" : "pointer",
              opacity: isSubmitting ? 0.5 : 1,
            }}
          >
            <X style={{ width: "18px", height: "18px", color: "var(--color-foreground-muted)" }} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ flex: 1, overflow: "auto" }}>
          <div
            style={{
              padding: "20px",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
            }}
          >
            {/* Error message */}
            {error && (
              <div
                style={{
                  padding: "12px",
                  backgroundColor: "var(--color-status-fail-subtle)",
                  borderRadius: "8px",
                  fontSize: "13px",
                  color: "var(--color-status-fail)",
                }}
              >
                {error}
              </div>
            )}

            {/* Title */}
            <div>
              <label
                htmlFor="task-title"
                style={{
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 500,
                  color: "var(--color-foreground-muted)",
                  marginBottom: "6px",
                }}
              >
                Title <span style={{ color: "var(--color-status-fail)" }}>*</span>
              </label>
              <input
                id="task-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Enter task title..."
                disabled={isSubmitting}
                autoFocus
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-background)",
                  fontSize: "14px",
                  color: "var(--color-foreground)",
                  outline: "none",
                }}
              />
            </div>

            {/* Description */}
            <div>
              <label
                htmlFor="task-description"
                style={{
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 500,
                  color: "var(--color-foreground-muted)",
                  marginBottom: "6px",
                }}
              >
                Description
              </label>
              <textarea
                id="task-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe the task..."
                disabled={isSubmitting}
                rows={3}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-background)",
                  fontSize: "14px",
                  color: "var(--color-foreground)",
                  outline: "none",
                  resize: "vertical",
                  fontFamily: "inherit",
                }}
              />
            </div>

            {/* Domain */}
            <div>
              <label
                htmlFor="task-domain"
                style={{
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 500,
                  color: "var(--color-foreground-muted)",
                  marginBottom: "6px",
                }}
              >
                Domain
              </label>
              <input
                id="task-domain"
                type="text"
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="e.g., frontend, backend, infra..."
                disabled={isSubmitting}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-background)",
                  fontSize: "14px",
                  color: "var(--color-foreground)",
                  outline: "none",
                }}
              />
            </div>

            {/* Priority */}
            <div>
              <label
                htmlFor="task-priority"
                style={{
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 500,
                  color: "var(--color-foreground-muted)",
                  marginBottom: "6px",
                }}
              >
                Priority
              </label>
              <select
                id="task-priority"
                value={priority}
                onChange={(e) => setPriority(e.target.value as typeof priority)}
                disabled={isSubmitting}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-background)",
                  fontSize: "14px",
                  color: "var(--color-foreground)",
                  outline: "none",
                }}
              >
                <option value="LOW">LOW</option>
                <option value="MEDIUM">MEDIUM</option>
                <option value="HIGH">HIGH</option>
                <option value="CRITICAL">CRITICAL</option>
              </select>
            </div>

            {/* Tags */}
            <div>
              <label
                htmlFor="task-tags"
                style={{
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 500,
                  color: "var(--color-foreground-muted)",
                  marginBottom: "6px",
                }}
              >
                Tags (comma / newline separated)
              </label>
              <input
                id="task-tags"
                type="text"
                value={tagsText}
                onChange={(e) => setTagsText(e.target.value)}
                placeholder="e.g., ui, mcp, infra"
                disabled={isSubmitting}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-background)",
                  fontSize: "14px",
                  color: "var(--color-foreground)",
                  outline: "none",
                }}
              />
            </div>

            {/* Namespace */}
            {namespaces.length > 0 && (
              <div>
                <label
                  htmlFor="task-namespace"
                  style={{
                    display: "block",
                    fontSize: "12px",
                    fontWeight: 500,
                    color: "var(--color-foreground-muted)",
                    marginBottom: "6px",
                  }}
                >
                  Project / Namespace
                </label>
                <select
                  id="task-namespace"
                  value={namespace}
                  onChange={(e) => setNamespace(e.target.value)}
                  disabled={isSubmitting}
                  style={{
                    width: "100%",
                    padding: "10px 12px",
                    borderRadius: "8px",
                    border: "1px solid var(--color-border)",
                    backgroundColor: "var(--color-background)",
                    fontSize: "14px",
                    color: "var(--color-foreground)",
                    outline: "none",
                  }}
                >
                  <option value="">Current project</option>
                  {namespaces.map((ns) => (
                    <option key={ns.namespace} value={ns.namespace}>
                      {ns.namespace} ({ns.task_count})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Subtasks */}
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <label
                  style={{
                    display: "block",
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "var(--color-foreground)",
                  }}
                >
                  Subtasks
                </label>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    type="button"
                    onClick={handleLoadTemplate}
                    disabled={isSubmitting || isTemplateLoading}
                    style={{
                      padding: "6px 10px",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "transparent",
                      fontSize: "12px",
                      cursor: isSubmitting || isTemplateLoading ? "not-allowed" : "pointer",
                      opacity: isSubmitting || isTemplateLoading ? 0.6 : 1,
                    }}
                  >
                    {isTemplateLoading ? "Loading template..." : "Load template"}
                  </button>
                  <button
                    type="button"
                    onClick={handleAddSubtask}
                    disabled={isSubmitting}
                    style={{
                      padding: "6px 10px",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "transparent",
                      fontSize: "12px",
                      cursor: isSubmitting ? "not-allowed" : "pointer",
                      opacity: isSubmitting ? 0.6 : 1,
                    }}
                  >
                    Add subtask
                  </button>
                </div>
              </div>

              {subtasks.length === 0 && (
                <div style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
                  No subtasks yet. Add manually or load template.
                </div>
              )}

              {subtasks.map((st, idx) => (
                <div
                  key={idx}
                  style={{
                    border: "1px solid var(--color-border)",
                    borderRadius: "8px",
                    padding: "12px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "8px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div style={{ fontSize: "12px", fontWeight: 600 }}>
                      #{idx + 1}
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemoveSubtask(idx)}
                      disabled={isSubmitting}
                      style={{
                        padding: "4px 6px",
                        borderRadius: "6px",
                        border: "1px solid var(--color-border)",
                        backgroundColor: "transparent",
                        fontSize: "11px",
                        cursor: isSubmitting ? "not-allowed" : "pointer",
                      }}
                    >
                      Remove
                    </button>
                  </div>

                  <input
                    type="text"
                    value={st.title}
                    onChange={(e) =>
                      setSubtasks((prev) =>
                        prev.map((p, i) => (i === idx ? { ...p, title: e.target.value } : p))
                      )
                    }
                    placeholder="Subtask title..."
                    disabled={isSubmitting}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-background)",
                      fontSize: "13px",
                      color: "var(--color-foreground)",
                      outline: "none",
                    }}
                  />

                  <textarea
                    value={st.criteria.join("\n")}
                    onChange={(e) =>
                      setSubtasks((prev) =>
                        prev.map((p, i) =>
                          i === idx ? { ...p, criteria: normalizeListInput(e.target.value) } : p
                        )
                      )
                    }
                    placeholder="Success criteria (one per line)..."
                    rows={2}
                    disabled={isSubmitting}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-background)",
                      fontSize: "13px",
                      color: "var(--color-foreground)",
                      outline: "none",
                      resize: "vertical",
                      fontFamily: "inherit",
                    }}
                  />

                  <textarea
                    value={st.tests.join("\n")}
                    onChange={(e) =>
                      setSubtasks((prev) =>
                        prev.map((p, i) =>
                          i === idx ? { ...p, tests: normalizeListInput(e.target.value) } : p
                        )
                      )
                    }
                    placeholder="Tests (commands/assertions)..."
                    rows={2}
                    disabled={isSubmitting}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-background)",
                      fontSize: "13px",
                      color: "var(--color-foreground)",
                      outline: "none",
                      resize: "vertical",
                      fontFamily: "inherit",
                    }}
                  />

                  <textarea
                    value={st.blockers.join("\n")}
                    onChange={(e) =>
                      setSubtasks((prev) =>
                        prev.map((p, i) =>
                          i === idx ? { ...p, blockers: normalizeListInput(e.target.value) } : p
                        )
                      )
                    }
                    placeholder="Blockers/dependencies..."
                    rows={2}
                    disabled={isSubmitting}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-background)",
                      fontSize: "13px",
                      color: "var(--color-foreground)",
                      outline: "none",
                      resize: "vertical",
                      fontFamily: "inherit",
                    }}
                  />
                </div>
              ))}
            </div>
          </div>

          {/* Footer */}
          <div
            style={{
              padding: "16px 20px",
              borderTop: "1px solid var(--color-border)",
              display: "flex",
              justifyContent: "flex-end",
              gap: "12px",
            }}
          >
            <button
              type="button"
              onClick={handleClose}
              disabled={isSubmitting}
              style={{
                padding: "10px 16px",
                borderRadius: "8px",
                border: "1px solid var(--color-border)",
                backgroundColor: "transparent",
                fontSize: "13px",
                fontWeight: 500,
                color: "var(--color-foreground)",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                opacity: isSubmitting ? 0.5 : 1,
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !title.trim()}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                padding: "10px 16px",
                borderRadius: "8px",
                border: "none",
                backgroundColor: "var(--color-primary)",
                fontSize: "13px",
                fontWeight: 500,
                color: "white",
                cursor: isSubmitting || !title.trim() ? "not-allowed" : "pointer",
                opacity: isSubmitting || !title.trim() ? 0.7 : 1,
              }}
            >
              {isSubmitting ? (
                <>
                  <Loader2 style={{ width: "14px", height: "14px", animation: "spin 1s linear infinite" }} />
                  Creating...
                </>
              ) : (
                <>
                  <Plus style={{ width: "14px", height: "14px" }} />
                  Create Task
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
