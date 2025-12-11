/**
 * New Task Modal - Create new task form
 */

import { useState, useCallback } from "react";
import { X, Plus, Loader2 } from "lucide-react";
import { createTask } from "@/lib/tauri";

interface NewTaskModalProps {
  isOpen: boolean;
  onClose: () => void;
  onTaskCreated?: () => void;
}

export function NewTaskModal({ isOpen, onClose, onTaskCreated }: NewTaskModalProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [domain, setDomain] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();

    if (!title.trim()) {
      setError("Title is required");
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      const response = await createTask({
        title: title.trim(),
        parent: "ROOT",
        description: description.trim(),
        tests: [],
        risks: [],
        subtasks: [],
        domain: domain.trim() || undefined,
      });

      if (response.success) {
        // Reset form
        setTitle("");
        setDescription("");
        setDomain("");
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
  }, [title, description, domain, onClose, onTaskCreated]);

  const handleClose = useCallback(() => {
    if (!isSubmitting) {
      setTitle("");
      setDescription("");
      setDomain("");
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
